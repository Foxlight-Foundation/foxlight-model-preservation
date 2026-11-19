//! Sovereign for Windows: a window around the bundled, independent Library.
//!
//! It does what the Mac app does (desktop/macos/Sources/Sovereign/main.swift):
//! start the bundled Python with the Library on 127.0.0.1, read the one-time
//! address it prints, show only that address in a private WebView2, offer the
//! system folder and save dialogs, and stop the Library when the window
//! closes. The Library stops cleanly when its standard input closes; a job
//! object ends it if this process dies first.

#![windows_subsystem = "windows"]

use std::cell::RefCell;
use std::io::{BufRead, BufReader, Read};
use std::os::windows::io::AsRawHandle;
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc;
use std::sync::Mutex;

use webview2_com::Microsoft::Web::WebView2::Win32::*;
use webview2_com::*;
use windows::core::{w, Interface, BOOL, HSTRING, PCWSTR, PWSTR};
use windows::Win32::Foundation::*;
use windows::Win32::Graphics::Gdi::*;
use windows::Win32::System::Com::*;
use windows::Win32::System::JobObjects::*;
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::System::Threading::{CreateMutexW, CREATE_NO_WINDOW};
use windows::Win32::UI::HiDpi::GetDpiForWindow;
use windows::Win32::UI::Shell::Common::COMDLG_FILTERSPEC;
use windows::Win32::UI::Shell::*;
use windows::Win32::UI::WindowsAndMessaging::*;

const CLASS: PCWSTR = w!("Foxlight.Sovereign.Window");
const WM_LIBRARY_READY: u32 = WM_APP + 1;
const WM_LIBRARY_EXITED: u32 = WM_APP + 2;
/// A dialog the page asked for, shown from the window's own message loop.
const WM_DIALOG: u32 = WM_APP + 3;
const QUIT_TIMER: usize = 1;
const QUIT_WAIT_MS: u32 = 30_000;
const STARTUP_LIMIT: usize = 4096;
const OPENING: &str = "Opening your Library\u{2026}";
const STOPPED: &str = "Sovereign stopped. Your saved copies are kept. Reopen the application to \
continue. Another running copy may already own this storage.";
const NO_RUNTIME: &str = "The bundled runtime is missing. Reinstall Sovereign.";
const NO_START: &str = "Sovereign could not start its bundled runtime. Reinstall the application.";
const NO_WEBVIEW: &str = "Sovereign needs the Microsoft Edge WebView2 Runtime, which is part of \
Windows 11. It is missing on this computer.\n\nOpen Microsoft's download page now? Install the \
Evergreen Runtime, then open Sovereign again.";
const WEBVIEW_DOWNLOAD: &str = "https://developer.microsoft.com/microsoft-edge/webview2/";
/// The page asks for folders through the same bridge the Mac app provides
/// (window.webkit.messageHandlers.<name>.postMessage, answered with a promise).
const BRIDGE: &str = r#"(() => {
  if (window.top !== window || !window.chrome || !window.chrome.webview) return;
  const webview = window.chrome.webview, pending = new Map();
  let next = 0;
  webview.addEventListener('message', (event) => {
    const reply = event.data, waiting = pending.get(reply && reply.id);
    if (!waiting) return;
    pending.delete(reply.id);
    if (reply.error) waiting.reject(new Error(reply.error)); else waiting.resolve(reply.path);
  });
  const handler = (name) => ({ postMessage: (body) => new Promise((resolve, reject) => {
    const id = ++next;
    pending.set(id, { resolve, reject });
    webview.postMessage({ id, name, body });
  }) });
  Object.defineProperty(window, 'webkit', { value: Object.freeze({ messageHandlers: Object.freeze({
    chooseBackupFolder: handler('chooseBackupFolder'),
    chooseLibraryFolder: handler('chooseLibraryFolder'),
  }) }) });
})();"#;

/// The one-time address from the Library's startup line, for the UI thread.
static ADDRESS: Mutex<Option<String>> = Mutex::new(None);

struct App {
    window: HWND,
    child: Option<Child>,
    stdin: Option<ChildStdin>,
    controller: Option<ICoreWebView2Controller>,
    webview: Option<ICoreWebView2>,
    base: Option<String>,
    message: Option<&'static str>,
    quitting: bool,
    font: HFONT,
}

/// Dialog work queued by WebView2 callbacks. Showing a modal dialog inside a
/// callback would run a nested message loop under it, which WebView2 does not
/// support, so the callback queues the work and returns.
enum Dialog {
    Folder {
        id: u64,
        library: bool,
        reply: ICoreWebView2,
    },
    Save {
        args: ICoreWebView2DownloadStartingEventArgs,
        deferral: ICoreWebView2Deferral,
        name: &'static str,
        kind: &'static str,
        pattern: &'static str,
    },
}

thread_local! {
    static DIALOGS: RefCell<std::collections::VecDeque<Dialog>> =
        const { RefCell::new(std::collections::VecDeque::new()) };
}

fn queue_dialog(window: HWND, dialog: Dialog) {
    DIALOGS.with(|queue| queue.borrow_mut().push_back(dialog));
    unsafe {
        let _ = PostMessageW(Some(window), WM_DIALOG, WPARAM(0), LPARAM(0));
    }
}

/// Show the next queued dialog and complete the request that asked for it.
fn show_dialog(window: HWND) -> windows::core::Result<()> {
    let Some(dialog) = DIALOGS.with(|queue| queue.borrow_mut().pop_front()) else {
        return Ok(());
    };
    match dialog {
        Dialog::Folder { id, library, reply } => {
            let chosen = choose_folder(window, library).map(|folder| {
                folder
                    .join(if library {
                        "Sovereign Library"
                    } else {
                        "Sovereign Backups"
                    })
                    .to_string_lossy()
                    .into_owned()
            });
            let message = serde_json::json!({ "id": id, "path": chosen });
            unsafe { reply.PostWebMessageAsJson(&HSTRING::from(message.to_string())) }
        }
        Dialog::Save {
            args,
            deferral,
            name,
            kind,
            pattern,
        } => {
            let result = match choose_save_path(window, name, kind, pattern) {
                Some(path) => unsafe {
                    args.SetResultFilePath(&HSTRING::from(path.as_os_str()))
                        .and_then(|()| args.SetHandled(true))
                },
                None => unsafe { args.SetCancel(true) },
            };
            // Always release the download, even if setting its path failed.
            let completed = unsafe { deferral.Complete() };
            result.and(completed)
        }
    }
}

thread_local! {
    static APP: RefCell<Option<App>> = const { RefCell::new(None) };
}

fn with_app<T>(f: impl FnOnce(&mut App) -> T) -> Option<T> {
    APP.with(|cell| cell.borrow_mut().as_mut().map(f))
}

fn main() {
    let arguments: Vec<String> = std::env::args().collect();
    if arguments.iter().any(|a| a == "--check-webview2") {
        // For build checks: report the runtime without opening any window.
        std::process::exit(if webview_available() { 0 } else { 3 });
    }
    if let Err(error) = run(&arguments) {
        fatal(&format!("Sovereign could not open its window ({error})."));
    }
}

fn run(arguments: &[String]) -> windows::core::Result<()> {
    unsafe { CoInitializeEx(None, COINIT_APARTMENTTHREADED).ok()? };
    // One window per signed-in person: a second launch brings the first forward.
    let _single = unsafe { CreateMutexW(None, true, w!("Local\\Foxlight.Sovereign.App"))? };
    if unsafe { GetLastError() } == ERROR_ALREADY_EXISTS {
        if let Ok(existing) = unsafe { FindWindowW(CLASS, PCWSTR::null()) } {
            unsafe {
                let _ = ShowWindow(existing, SW_RESTORE);
                let _ = SetForegroundWindow(existing);
            }
        }
        return Ok(());
    }
    if !webview_available() {
        let answer = unsafe {
            MessageBoxW(
                None,
                &HSTRING::from(NO_WEBVIEW),
                w!("Sovereign"),
                MB_YESNO | MB_ICONWARNING,
            )
        };
        if answer == IDYES {
            open_external(WEBVIEW_DOWNLOAD);
        }
        return Ok(());
    }
    let window = create_window()?;
    let font = message_font();
    APP.with(|cell| {
        *cell.borrow_mut() = Some(App {
            window,
            child: None,
            stdin: None,
            controller: None,
            webview: None,
            base: None,
            message: Some(OPENING),
            quitting: false,
            font,
        })
    });
    unsafe {
        let _ = ShowWindow(window, SW_SHOWDEFAULT);
        let _ = UpdateWindow(window);
    }
    start_library(window, arguments);
    if let Err(error) = create_webview(window) {
        fatal(&format!("Sovereign could not open its window ({error})."));
        return Ok(());
    }
    let mut message = MSG::default();
    unsafe {
        while GetMessageW(&mut message, None, 0, 0).as_bool() {
            let _ = TranslateMessage(&message);
            DispatchMessageW(&message);
        }
    }
    Ok(())
}

fn webview_available() -> bool {
    if std::env::var_os("SOVEREIGN_TEST_WEBVIEW2_MISSING").is_some() {
        return false;
    }
    let mut version = PWSTR::null();
    let found =
        unsafe { GetAvailableCoreWebView2BrowserVersionString(PCWSTR::null(), &mut version) };
    let version = take_pwstr(version);
    found.is_ok() && !version.is_empty()
}

fn install_directory() -> Option<PathBuf> {
    std::env::current_exe()
        .ok()?
        .parent()
        .map(Path::to_path_buf)
}

/// Start the bundled Python with the Library; never the person's own Python.
fn start_library(window: HWND, arguments: &[String]) {
    let Some(root) = install_directory() else {
        return show_message(NO_RUNTIME);
    };
    let python = root.join("python").join("python.exe");
    let site = root.join("site-packages");
    if !python.is_file() || !site.is_dir() {
        return show_message(NO_RUNTIME);
    }
    let mut command = Command::new(&python);
    // Isolated (-I) ignores PYTHON* variables, user packages and the working
    // folder; -B never writes bytecode into the install folder.
    command.args([
        "-I",
        "-B",
        "-c",
        "import sys,runpy;sys.path.insert(0,sys.argv.pop(1));\
         runpy.run_module('foxlight_preservation_library.desktop',run_name='__main__')",
    ]);
    command.arg(&site);
    command.args(["--no-browser", "--stop-on-input-close"]);
    if let Some(index) = arguments.iter().position(|a| a == "--data-directory") {
        if let Some(path) = arguments.get(index + 1) {
            if Path::new(path).is_absolute() {
                command.arg("--home").arg(path);
            }
        }
    }
    command
        .current_dir(&root)
        .env("PYTHONUTF8", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .creation_flags(CREATE_NO_WINDOW.0);
    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(_) => return show_message(NO_START),
    };
    end_with_this_process(&child);
    let stdin = child.stdin.take();
    let stdout = child.stdout.take();
    with_app(|app| {
        app.child = Some(child);
        app.stdin = stdin;
    });
    let handle = window.0 as isize;
    if let Some(stdout) = stdout {
        std::thread::spawn(move || read_startup(stdout, handle));
    }
}

/// A job object ends the Library if this process dies without closing it.
fn end_with_this_process(child: &Child) {
    unsafe {
        let Ok(job) = CreateJobObjectW(None, PCWSTR::null()) else {
            return;
        };
        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let set = SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &limits as *const _ as *const core::ffi::c_void,
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        );
        if set.is_err() || AssignProcessToJobObject(job, HANDLE(child.as_raw_handle())).is_err() {
            let _ = CloseHandle(job);
        }
        // Otherwise the handle stays open for the life of this process.
    }
}

/// Read one bounded startup line, then drain output until the Library exits.
fn read_startup(stdout: impl Read, window: isize) {
    let mut reader = BufReader::new(stdout);
    let mut line = Vec::new();
    let found = reader
        .by_ref()
        .take(STARTUP_LIMIT as u64)
        .read_until(b'\n', &mut line)
        .ok()
        .and_then(|_| std::str::from_utf8(&line).ok())
        .and_then(|text| text.trim_end().strip_prefix("Sovereign: "))
        .filter(|address| valid_address(address))
        .map(str::to_owned);
    let window = HWND(window as *mut core::ffi::c_void);
    if let Some(address) = found {
        *ADDRESS.lock().unwrap() = Some(address);
        unsafe {
            let _ = PostMessageW(Some(window), WM_LIBRARY_READY, WPARAM(0), LPARAM(0));
        }
    }
    let _ = std::io::copy(&mut reader, &mut std::io::sink());
    unsafe {
        let _ = PostMessageW(Some(window), WM_LIBRARY_EXITED, WPARAM(0), LPARAM(0));
    }
}

/// Exactly http://127.0.0.1:<port>/s/<64 lowercase hex>/
fn valid_address(address: &str) -> bool {
    let Some(rest) = address.strip_prefix("http://127.0.0.1:") else {
        return false;
    };
    let Some((port, path)) = rest.split_once('/') else {
        return false;
    };
    let ticket = path.strip_prefix("s/").and_then(|p| p.strip_suffix('/'));
    !port.is_empty()
        && port.len() <= 5
        && port.bytes().all(|b| b.is_ascii_digit())
        && port.parse::<u16>().is_ok_and(|p| p > 0)
        && ticket.is_some_and(|t| {
            t.len() == 64 && t.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
        })
}

/// Only the Library's own pages: same origin, under its ticket path.
fn permitted(base: &str, uri: &str) -> bool {
    uri == base || uri == base.trim_end_matches('/') || uri.starts_with(base)
}

fn create_window() -> windows::core::Result<HWND> {
    unsafe {
        let instance: HINSTANCE = GetModuleHandleW(None)?.into();
        let class = WNDCLASSW {
            style: CS_HREDRAW | CS_VREDRAW,
            lpfnWndProc: Some(window_procedure),
            hInstance: instance,
            hIcon: LoadIconW(Some(instance), PCWSTR(1 as _)).unwrap_or_default(),
            hCursor: LoadCursorW(None, IDC_ARROW)?,
            hbrBackground: GetSysColorBrush(COLOR_WINDOW),
            lpszClassName: CLASS,
            ..Default::default()
        };
        if RegisterClassW(&class) == 0 {
            return Err(windows::core::Error::from_thread());
        }
        let window = CreateWindowExW(
            WINDOW_EX_STYLE::default(),
            CLASS,
            w!("Sovereign"),
            WS_OVERLAPPEDWINDOW,
            CW_USEDEFAULT,
            CW_USEDEFAULT,
            1180,
            820,
            None,
            None,
            Some(instance),
            None,
        )?;
        // Size for this monitor's scale; the default above is at 100%.
        let scale = GetDpiForWindow(window).max(96) as i32;
        let _ = SetWindowPos(
            window,
            None,
            0,
            0,
            1180 * scale / 96,
            820 * scale / 96,
            SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE,
        );
        Ok(window)
    }
}

fn message_font() -> HFONT {
    unsafe {
        let mut metrics = NONCLIENTMETRICSW {
            cbSize: std::mem::size_of::<NONCLIENTMETRICSW>() as u32,
            ..Default::default()
        };
        let read = SystemParametersInfoW(
            SPI_GETNONCLIENTMETRICS,
            metrics.cbSize,
            Some(&mut metrics as *mut _ as *mut core::ffi::c_void),
            SYSTEM_PARAMETERS_INFO_UPDATE_FLAGS(0),
        );
        if read.is_ok() {
            metrics.lfMessageFont.lfHeight = metrics.lfMessageFont.lfHeight * 4 / 3;
            CreateFontIndirectW(&metrics.lfMessageFont)
        } else {
            HFONT::default()
        }
    }
}

fn create_webview(window: HWND) -> windows::core::Result<()> {
    // A private browser profile that keeps nothing between runs (InPrivate),
    // like the Mac app's non-persistent store. Its folder is never the Library.
    let profile = std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(std::env::temp_dir)
        .join("Foxlight")
        .join("Sovereign WebView2");
    let profile = HSTRING::from(profile.as_os_str());
    let (sender, receiver) = mpsc::channel();
    CreateCoreWebView2EnvironmentCompletedHandler::wait_for_async_operation(
        Box::new(move |handler| unsafe {
            CreateCoreWebView2EnvironmentWithOptions(PCWSTR::null(), &profile, None, &handler)
                .map_err(webview2_com::Error::WindowsError)
        }),
        Box::new(move |result, environment| {
            result?;
            sender.send(environment).expect("environment receiver");
            Ok(())
        }),
    )
    .map_err(as_windows_error)?;
    let environment = receiver
        .recv()
        .map_err(|_| windows::core::Error::from(E_UNEXPECTED))?
        .ok_or_else(|| windows::core::Error::from(E_POINTER))?;
    let environment: ICoreWebView2Environment10 = environment.cast()?;
    let options = unsafe { environment.CreateCoreWebView2ControllerOptions()? };
    unsafe { options.SetIsInPrivateModeEnabled(true)? };
    let (sender, receiver) = mpsc::channel();
    CreateCoreWebView2ControllerCompletedHandler::wait_for_async_operation(
        Box::new(move |handler| unsafe {
            environment
                .CreateCoreWebView2ControllerWithOptions(window, &options, &handler)
                .map_err(webview2_com::Error::WindowsError)
        }),
        Box::new(move |result, controller| {
            result?;
            sender.send(controller).expect("controller receiver");
            Ok(())
        }),
    )
    .map_err(as_windows_error)?;
    let controller = receiver
        .recv()
        .map_err(|_| windows::core::Error::from(E_UNEXPECTED))?
        .ok_or_else(|| windows::core::Error::from(E_POINTER))?;
    let webview = unsafe { controller.CoreWebView2()? };
    unsafe {
        let settings = webview.Settings()?;
        settings.SetAreDevToolsEnabled(false)?;
        settings.SetIsStatusBarEnabled(false)?;
        settings.SetIsWebMessageEnabled(true)?;
        controller.SetIsVisible(false)?;
        webview.AddScriptToExecuteOnDocumentCreated(&HSTRING::from(BRIDGE), None)?;
    }
    watch_navigation(&webview)?;
    watch_messages(&webview, window)?;
    watch_downloads(&webview, window)?;
    with_app(|app| {
        app.controller = Some(controller);
        app.webview = Some(webview);
    });
    resize(window);
    // The address may already have arrived while the browser was starting.
    show_library();
    Ok(())
}

fn as_windows_error(error: webview2_com::Error) -> windows::core::Error {
    match error {
        webview2_com::Error::WindowsError(error) => error,
        _ => windows::core::Error::from(E_FAIL),
    }
}

fn current_base() -> Option<String> {
    with_app(|app| app.base.clone()).flatten()
}

fn watch_navigation(webview: &ICoreWebView2) -> windows::core::Result<()> {
    let mut token = 0i64;
    let handler = NavigationStartingEventHandler::create(Box::new(|_, args| {
        let Some(args) = args else { return Ok(()) };
        let mut uri = PWSTR::null();
        unsafe { args.Uri(&mut uri)? };
        let uri = take_pwstr(uri);
        if current_base().is_some_and(|base| permitted(&base, &uri)) {
            return Ok(());
        }
        unsafe { args.SetCancel(true)? };
        let mut user = BOOL::default();
        unsafe { args.IsUserInitiated(&mut user)? };
        if user.as_bool() && uri.starts_with("https://") {
            open_external(&uri);
        }
        Ok(())
    }));
    unsafe { webview.add_NavigationStarting(&handler, &mut token)? };
    // Links meant for a new window open in the default browser instead.
    let handler = NewWindowRequestedEventHandler::create(Box::new(|_, args| {
        let Some(args) = args else { return Ok(()) };
        unsafe { args.SetHandled(true)? };
        let mut uri = PWSTR::null();
        unsafe { args.Uri(&mut uri)? };
        let uri = take_pwstr(uri);
        let mut user = BOOL::default();
        unsafe { args.IsUserInitiated(&mut user)? };
        if user.as_bool() && uri.starts_with("https://") {
            open_external(&uri);
        }
        Ok(())
    }));
    unsafe { webview.add_NewWindowRequested(&handler, &mut token)? };
    Ok(())
}

fn watch_messages(webview: &ICoreWebView2, window: HWND) -> windows::core::Result<()> {
    let mut token = 0i64;
    let handler = WebMessageReceivedEventHandler::create(Box::new(move |sender, args| {
        let (Some(sender), Some(args)) = (sender, args) else {
            return Ok(());
        };
        let mut source = PWSTR::null();
        unsafe { args.Source(&mut source)? };
        let source = take_pwstr(source);
        let mut json = PWSTR::null();
        unsafe { args.WebMessageAsJson(&mut json)? };
        let json = take_pwstr(json);
        let Ok(message) = serde_json::from_str::<serde_json::Value>(&json) else {
            return Ok(());
        };
        let Some(id) = message.get("id").and_then(serde_json::Value::as_u64) else {
            return Ok(());
        };
        let name = message
            .get("name")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("");
        let empty = message
            .get("body")
            .and_then(serde_json::Value::as_object)
            .is_some_and(|b| b.is_empty());
        let library = name == "chooseLibraryFolder";
        if !(library || name == "chooseBackupFolder")
            || !empty
            || !current_base().is_some_and(|base| permitted(&base, &source))
        {
            let refusal = serde_json::json!({ "id": id, "error": "Folder selection is available only from Sovereign." });
            unsafe { sender.PostWebMessageAsJson(&HSTRING::from(refusal.to_string()))? };
        } else {
            queue_dialog(
                window,
                Dialog::Folder {
                    id,
                    library,
                    reply: sender,
                },
            );
        }
        Ok(())
    }));
    unsafe { webview.add_WebMessageReceived(&handler, &mut token)? };
    Ok(())
}

fn choose_folder(window: HWND, library: bool) -> Option<PathBuf> {
    unsafe {
        let dialog: IFileOpenDialog =
            CoCreateInstance(&FileOpenDialog, None, CLSCTX_INPROC_SERVER).ok()?;
        let options = dialog.GetOptions().ok()?;
        dialog
            .SetOptions(options | FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST)
            .ok()?;
        let title = if library {
            w!("Choose a connected drive, network share or folder for model copies")
        } else {
            w!("Choose a connected drive or folder for backups")
        };
        dialog.SetTitle(title).ok()?;
        dialog.Show(Some(window)).ok()?;
        let item = dialog.GetResult().ok()?;
        let path = take_pwstr(item.GetDisplayName(SIGDN_FILESYSPATH).ok()?);
        (!path.is_empty()).then(|| PathBuf::from(path))
    }
}

fn watch_downloads(webview: &ICoreWebView2, window: HWND) -> windows::core::Result<()> {
    let webview: ICoreWebView2_4 = webview.cast()?;
    let mut token = 0i64;
    let handler = DownloadStartingEventHandler::create(Box::new(move |_, args| {
        let Some(args) = args else { return Ok(()) };
        let mut suggested = PWSTR::null();
        unsafe { args.ResultFilePath(&mut suggested)? };
        let suggested = take_pwstr(suggested);
        // The same fixed names as the Mac app; no page text names the file.
        let (name, kind, pattern) = if suggested.ends_with(".foxhandoff") {
            (
                "Sovereign-sharing.foxhandoff",
                "Sovereign sharing handoff",
                "*.foxhandoff",
            )
        } else {
            (
                "Sovereign-recovery.foxkit",
                "Sovereign recovery kit",
                "*.foxkit",
            )
        };
        // Hold the download until the save dialog has answered.
        let deferral = unsafe { args.GetDeferral()? };
        queue_dialog(
            window,
            Dialog::Save {
                args,
                deferral,
                name,
                kind,
                pattern,
            },
        );
        Ok(())
    }));
    unsafe { webview.add_DownloadStarting(&handler, &mut token)? };
    Ok(())
}

fn choose_save_path(window: HWND, name: &str, kind: &str, pattern: &str) -> Option<PathBuf> {
    unsafe {
        let dialog: IFileSaveDialog =
            CoCreateInstance(&FileSaveDialog, None, CLSCTX_INPROC_SERVER).ok()?;
        let kind = HSTRING::from(kind);
        let pattern = HSTRING::from(pattern);
        let filter = [COMDLG_FILTERSPEC {
            pszName: PCWSTR(kind.as_ptr()),
            pszSpec: PCWSTR(pattern.as_ptr()),
        }];
        dialog.SetFileTypes(&filter).ok()?;
        dialog.SetFileName(&HSTRING::from(name)).ok()?;
        let options = dialog.GetOptions().ok()?;
        dialog
            .SetOptions(options | FOS_OVERWRITEPROMPT | FOS_FORCEFILESYSTEM)
            .ok()?;
        dialog.Show(Some(window)).ok()?;
        let item = dialog.GetResult().ok()?;
        let path = take_pwstr(item.GetDisplayName(SIGDN_FILESYSPATH).ok()?);
        (!path.is_empty()).then(|| PathBuf::from(path))
    }
}

/// Navigate once both the browser and the Library's address are ready.
fn show_library() {
    let Some(address) = ADDRESS.lock().unwrap().clone() else {
        return;
    };
    let ready = with_app(|app| {
        if app.base.is_some() || app.message == Some(STOPPED) {
            return None;
        }
        let webview = app.webview.clone()?;
        let controller = app.controller.clone()?;
        app.base = Some(address.clone());
        app.message = None;
        Some((webview, controller))
    })
    .flatten();
    if let Some((webview, controller)) = ready {
        unsafe {
            let _ = controller.SetIsVisible(true);
            let _ = webview.Navigate(&HSTRING::from(address));
            let _ = controller.MoveFocus(COREWEBVIEW2_MOVE_FOCUS_REASON_PROGRAMMATIC);
        }
    }
}

fn show_message(message: &'static str) {
    let window = with_app(|app| {
        app.message = Some(message);
        if let Some(controller) = &app.controller {
            unsafe {
                let _ = controller.SetIsVisible(false);
            }
        }
        app.window
    });
    if let Some(window) = window {
        unsafe {
            let _ = InvalidateRect(Some(window), None, true);
        }
    }
}

fn resize(window: HWND) {
    let controller = with_app(|app| app.controller.clone()).flatten();
    if let Some(controller) = controller {
        let mut bounds = RECT::default();
        unsafe {
            let _ = GetClientRect(window, &mut bounds);
            let _ = controller.SetBounds(bounds);
        }
    }
}

fn paint(window: HWND) {
    let (message, font) =
        with_app(|app| (app.message, app.font)).unwrap_or((None, HFONT::default()));
    unsafe {
        let mut paint = PAINTSTRUCT::default();
        let context = BeginPaint(window, &mut paint);
        let mut area = RECT::default();
        let _ = GetClientRect(window, &mut area);
        FillRect(context, &area, GetSysColorBrush(COLOR_WINDOW));
        if let Some(message) = message {
            let previous = SelectObject(context, font.into());
            SetBkMode(context, TRANSPARENT);
            SetTextColor(context, COLORREF(GetSysColor(COLOR_WINDOWTEXT)));
            let width = (area.right - area.left).min(560);
            let mut text: Vec<u16> = message.encode_utf16().collect();
            let mut bounds = RECT {
                left: (area.right - width) / 2,
                top: 0,
                right: (area.right + width) / 2,
                bottom: area.bottom,
            };
            let mut measure = bounds;
            DrawTextW(
                context,
                &mut text,
                &mut measure,
                DT_CALCRECT | DT_CENTER | DT_WORDBREAK,
            );
            bounds.top = ((area.bottom - (measure.bottom - measure.top)) / 2).max(0);
            DrawTextW(context, &mut text, &mut bounds, DT_CENTER | DT_WORDBREAK);
            SelectObject(context, previous);
        }
        let _ = EndPaint(window, &paint);
    }
}

/// Ask the Library to stop by closing its input, then wait for it to exit.
fn request_close(window: HWND) {
    let running = with_app(|app| {
        app.quitting = true;
        drop(app.stdin.take());
        app.child
            .as_mut()
            .is_some_and(|child| matches!(child.try_wait(), Ok(None)))
    })
    .unwrap_or(false);
    if running {
        unsafe {
            let _ = ShowWindow(window, SW_HIDE);
            SetTimer(Some(window), QUIT_TIMER, QUIT_WAIT_MS, None);
        }
    } else {
        unsafe {
            let _ = DestroyWindow(window);
        }
    }
}

extern "system" fn window_procedure(window: HWND, message: u32, w: WPARAM, l: LPARAM) -> LRESULT {
    match message {
        WM_SIZE => {
            resize(window);
            LRESULT(0)
        }
        WM_SETFOCUS => {
            if let Some(controller) = with_app(|app| app.controller.clone()).flatten() {
                unsafe {
                    let _ = controller.MoveFocus(COREWEBVIEW2_MOVE_FOCUS_REASON_PROGRAMMATIC);
                }
            }
            LRESULT(0)
        }
        WM_GETMINMAXINFO => {
            let scale = unsafe { GetDpiForWindow(window) }.max(96) as i32;
            let info = unsafe { &mut *(l.0 as *mut MINMAXINFO) };
            info.ptMinTrackSize = POINT {
                x: 720 * scale / 96,
                y: 520 * scale / 96,
            };
            LRESULT(0)
        }
        WM_DPICHANGED => {
            let suggested = unsafe { &*(l.0 as *const RECT) };
            unsafe {
                let _ = SetWindowPos(
                    window,
                    None,
                    suggested.left,
                    suggested.top,
                    suggested.right - suggested.left,
                    suggested.bottom - suggested.top,
                    SWP_NOZORDER | SWP_NOACTIVATE,
                );
            }
            LRESULT(0)
        }
        WM_PAINT => {
            paint(window);
            LRESULT(0)
        }
        WM_DIALOG => {
            let _ = show_dialog(window);
            LRESULT(0)
        }
        WM_LIBRARY_READY => {
            show_library();
            LRESULT(0)
        }
        WM_LIBRARY_EXITED => {
            let quitting = with_app(|app| {
                if let Some(child) = app.child.as_mut() {
                    let _ = child.wait();
                }
                app.quitting
            })
            .unwrap_or(true);
            if quitting {
                unsafe {
                    let _ = DestroyWindow(window);
                }
            } else {
                show_message(STOPPED);
            }
            LRESULT(0)
        }
        WM_TIMER if w.0 == QUIT_TIMER => {
            // The Library did not stop in time: end it. Saved copies are
            // written atomically, so an interrupted save resumes later.
            with_app(|app| {
                if let Some(child) = app.child.as_mut() {
                    let _ = child.kill();
                    let _ = child.wait();
                }
            });
            unsafe {
                let _ = DestroyWindow(window);
            }
            LRESULT(0)
        }
        WM_CLOSE => {
            request_close(window);
            LRESULT(0)
        }
        WM_DESTROY => {
            // Take the browser out first: closing it may call back into us.
            let controller = with_app(|app| {
                app.webview = None;
                app.controller.take()
            })
            .flatten();
            if let Some(controller) = controller {
                unsafe {
                    let _ = controller.Close();
                }
            }
            unsafe { PostQuitMessage(0) };
            LRESULT(0)
        }
        _ => unsafe { DefWindowProcW(window, message, w, l) },
    }
}

fn open_external(uri: &str) {
    unsafe {
        ShellExecuteW(
            None,
            w!("open"),
            &HSTRING::from(uri),
            PCWSTR::null(),
            PCWSTR::null(),
            SW_SHOWNORMAL,
        );
    }
}

fn fatal(message: &str) {
    unsafe {
        MessageBoxW(
            None,
            &HSTRING::from(message),
            w!("Sovereign"),
            MB_OK | MB_ICONERROR,
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const TICKET: &str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

    #[test]
    fn only_the_exact_loopback_ticket_address_is_accepted() {
        assert!(valid_address(&format!("http://127.0.0.1:8123/s/{TICKET}/")));
        for bad in [
            format!("http://127.0.0.1:8123/s/{TICKET}"),
            format!("http://localhost:8123/s/{TICKET}/"),
            format!("https://127.0.0.1:8123/s/{TICKET}/"),
            format!("http://127.0.0.1:0/s/{TICKET}/"),
            format!("http://127.0.0.1:70000/s/{TICKET}/"),
            format!("http://127.0.0.1:8123/s/{}/", TICKET.to_uppercase()),
            format!("http://127.0.0.1:8123/s/{TICKET}0/"),
            format!("http://127.0.0.1:8123/x/{TICKET}/"),
            format!("http://127.0.0.1:8123@evil/s/{TICKET}/"),
        ] {
            assert!(!valid_address(&bad), "{bad}");
        }
    }

    #[test]
    fn navigation_stays_under_the_ticket() {
        let base = format!("http://127.0.0.1:8123/s/{TICKET}/");
        assert!(permitted(&base, &base));
        assert!(permitted(&base, &format!("{base}#/find")));
        assert!(permitted(&base, &format!("{base}api/library")));
        assert!(permitted(&base, base.trim_end_matches('/')));
        assert!(!permitted(
            &base,
            &format!("http://127.0.0.1:8123/s/{TICKET}x/")
        ));
        assert!(!permitted(&base, "http://127.0.0.1:8123/"));
        assert!(!permitted(&base, "https://example.com/"));
    }
}
