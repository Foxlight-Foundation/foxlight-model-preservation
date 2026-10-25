import AppKit
import SwiftUI
import WebKit

/// Native lifecycle and file dialogs around the shared, independent Library.
@main
struct SovereignApplication: App {
    @NSApplicationDelegateAdaptor(ApplicationDelegate.self) var application
    var body: some Scene {
        WindowGroup("Sovereign") {
            LibraryWindow(owner: application.owner)
                .frame(minWidth: 720, minHeight: 520)
        }
        .defaultSize(width: 1180, height: 820)
    }
}

@MainActor
final class ApplicationDelegate: NSObject, NSApplicationDelegate {
    let owner = LocalProcess()
    func applicationDidFinishLaunching(_ notification: Notification) { owner.start() }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard owner.process?.isRunning == true else { return .terminateNow }
        owner.quitting = true
        owner.process?.terminate()
        return .terminateLater
    }
}

@MainActor
final class LocalProcess: ObservableObject {
    @Published var url: URL?
    @Published var error: String?
    var process: Process?
    var quitting = false

    func start() {
        guard process == nil, let resources = Bundle.main.resourceURL else { return }
        let python = resources.appendingPathComponent("python/bin/python3")
        let site = resources.appendingPathComponent("site-packages")
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            error = "The bundled runtime is missing. Reinstall Sovereign."
            return
        }
        let child = Process()
        child.executableURL = python
        // Isolated Python ignores PYTHONPATH, user packages and the working folder.
        child.arguments = ["-I", "-c", "import sys,runpy;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('foxlight_preservation_library.desktop',run_name='__main__')", site.path, "--no-browser"]
        if let index = CommandLine.arguments.firstIndex(of: "--data-directory"), index + 1 < CommandLine.arguments.count {
            let path = CommandLine.arguments[index + 1]
            if path.hasPrefix("/") { child.arguments?.append(contentsOf: ["--home", path]) }
        }
        child.environment = ["HOME": NSHomeDirectory(), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "en_US.UTF-8"]
        child.currentDirectoryURL = resources
        let output = Pipe()
        child.standardOutput = output
        child.standardError = FileHandle.nullDevice
        child.standardInput = FileHandle.nullDevice
        // Only a bounded startup line is read; the private ticket is never logged.
        let startup = StartupLine()
        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            guard let line = startup.append(handle.availableData), line.hasPrefix("Sovereign: ") else { return }
            let address = line.dropFirst("Sovereign: ".count).trimmingCharacters(in: .whitespacesAndNewlines)
            guard let url = URL(string: address), url.scheme == "http", url.host == "127.0.0.1", url.port != nil, url.absoluteString.range(of: #"^http://127\.0\.0\.1:[0-9]+/s/[0-9a-f]{64}/$"#, options: .regularExpression) != nil else { return }
            handle.readabilityHandler = nil
            DispatchQueue.main.async { self?.url = url }
        }
        child.terminationHandler = { [weak self] _ in
            output.fileHandleForReading.readabilityHandler = nil
            DispatchQueue.main.async {
                if self?.quitting == true { NSApp.reply(toApplicationShouldTerminate: true) }
                else { self?.error = "Sovereign stopped. Your saved copies are kept. Reopen the application to continue. Another running copy may already own this storage." }
            }
        }
        process = child
        do { try child.run() }
        catch { self.error = "Sovereign could not start its bundled runtime. Reinstall the application." }
    }
}

struct LibraryWindow: View {
    @ObservedObject var owner: LocalProcess
    var body: some View {
        Group {
            if let error = owner.error {
                VStack(spacing: 16) { Image(systemName: "externaldrive.badge.exclamationmark").font(.largeTitle); Text(error).multilineTextAlignment(.center).frame(maxWidth: 460) }.padding(40)
            } else if let url = owner.url { LibraryWebView(url: url) }
            else { VStack(spacing: 16) { ProgressView(); Text("Opening your Library…") }.padding(40) }
        }
    }
}

struct LibraryWebView: NSViewRepresentable {
    let url: URL
    func makeCoordinator() -> Navigation { Navigation(base: url) }
    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        let view = WKWebView(frame: .zero, configuration: configuration)
        view.navigationDelegate = context.coordinator
        view.uiDelegate = context.coordinator
        view.load(URLRequest(url: url))
        return view
    }
    func updateNSView(_ view: WKWebView, context: Context) {}

    final class Navigation: NSObject, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
        let base: URL
        init(base: URL) { self.base = base }
        func permitted(_ url: URL) -> Bool {
            url.scheme == base.scheme && url.host == base.host && url.port == base.port && (url.path == base.path || url.path.hasPrefix(base.path + "/"))
        }
        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            guard let url = action.request.url else { decisionHandler(.cancel); return }
            if permitted(url) { decisionHandler(.allow) }
            else {
                if action.navigationType == .linkActivated && url.scheme == "https" { NSWorkspace.shared.open(url) }
                decisionHandler(.cancel)
            }
        }
        func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
            guard let url = response.response.url, permitted(url) else { decisionHandler(.cancel); return }
            decisionHandler(response.canShowMIMEType ? .allow : .download)
        }
        func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }
        func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
        func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
            let panel = NSOpenPanel()
            panel.canChooseFiles = true; panel.canChooseDirectories = false; panel.allowsMultipleSelection = false
            panel.begin { result in completionHandler(result == .OK ? panel.urls : nil) }
        }
        func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
            let panel = NSSavePanel()
            panel.nameFieldStringValue = "Sovereign-recovery.foxkit"
            panel.begin { result in completionHandler(result == .OK ? panel.url : nil) }
        }
    }
}

/// A pipe may split one print across reads. Keep its private startup line bounded.
private final class StartupLine: @unchecked Sendable {
    private let lock = NSLock()
    private var buffer = Data()
    func append(_ data: Data) -> String? {
        lock.lock(); defer { lock.unlock() }
        guard buffer.count + data.count <= 4096 else { return nil }
        buffer.append(data)
        guard let end = buffer.firstIndex(of: 10) else { return nil }
        return String(data: buffer[..<end], encoding: .utf8)
    }
}
