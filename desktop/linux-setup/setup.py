#!/usr/bin/python3
"""Native, unprivileged setup window for the pinned independent Ubuntu app.

Only the separate package-owned helper runs with administrator permission.
Passwords belong exclusively to Ubuntu's existing polkit authentication agent.
"""

from __future__ import annotations

import os
import runpy
import subprocess
import tempfile
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

SETUP = runpy.run_path("/usr/share/foxlight-sovereign-setup/installer.py")
DOWNLOAD_PROGRAM = """
import runpy, sys
from pathlib import Path
setup = runpy.run_path('/usr/share/foxlight-sovereign-setup/installer.py')
setup['download'](sys.argv[1], Path(sys.argv[2]), sys.argv[3], int(sys.argv[4]))
"""


class DownloadError(RuntimeError):
    """Keep technical download diagnostics separate from the human-facing message."""

    def __init__(self, details: str) -> None:
        super().__init__(
            "The download could not be verified. Check your network connection "
            "and try again. Installation details have the technical message."
        )
        self.details = details[-4096:]


def download(
    url: str, destination: Path, digest: str, limit: int, cancelled: threading.Event
) -> bool:
    """Run the unchanged verifier in an owned, promptly cancellable user process."""
    if cancelled.is_set():
        return False
    with subprocess.Popen(  # noqa: S603 - fixed isolated code, arguments never a shell
        [
            "/usr/bin/python3",
            "-I",
            "-B",
            "-c",
            DOWNLOAD_PROGRAM,
            url,
            str(destination),
            digest,
            str(limit),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ) as process:
        while True:
            if cancelled.is_set():
                process.terminate()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)
                return False
            try:
                output, _ = process.communicate(timeout=0.25)
            except subprocess.TimeoutExpired:
                continue
            if process.returncode:
                raise DownloadError(output)
            return True


def permission_command(directory: Path) -> list[str]:
    """Request normal OS authorization for only the fixed installed helper."""
    return [
        "/usr/bin/pkexec",
        "--disable-internal-agent",
        "/usr/libexec/sovereign-setup-install",
        str(directory),
    ]


class SetupWindow(Gtk.Window):
    """Review, verify, authorize and launch without collecting a password."""

    def __init__(self) -> None:
        super().__init__(title="Install Sovereign")
        self.set_default_size(600, 620)
        self.set_border_width(28)
        self.set_icon_name("ai.foxlight.Sovereign.Setup")
        self.busy = False
        self.authorizing = False
        self.ready = False
        self.cancelled = threading.Event()
        self.log = ""
        self.connect("delete-event", self.close_requested)
        self.connect("destroy", Gtk.main_quit)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.add(box)
        brand = Gtk.Box(spacing=12)
        brand.pack_start(
            Gtk.Image.new_from_icon_name(
                "ai.foxlight.Sovereign.Setup", Gtk.IconSize.DIALOG
            ),
            False,
            False,
            0,
        )
        brand.pack_start(self.label("Sovereign · by Foxlight"), False, False, 0)
        box.pack_start(brand, False, False, 0)
        self.heading = self.label("Keep a copy. Keep access.")
        self.heading.set_markup("<big><b>Keep a copy. Keep access.</b></big>")
        box.pack_start(self.heading, False, False, 0)
        box.pack_start(
            self.label(
                "Keep AI models and the information about where they came from. "
                "Recover your saved copies even if their original source disappears."
            ),
            False,
            False,
            0,
        )
        box.pack_start(
            self.label(
                "Setup installs Sovereign and connects its signed Foxlight software "
                "updates. Ubuntu may also install missing desktop libraries."
            ),
            False,
            False,
            0,
        )
        box.pack_start(
            self.label(
                "• No Skulk, account or GPU required.\n"
                "• Models download only when you choose to preserve one.\n"
                "• Sharing stays off until you choose to enable it.\n"
                "• Removing the app keeps your Library and recovery files."
            ),
            False,
            False,
            0,
        )
        self.status = self.label("Checking this computer…")
        self.status.set_selectable(True)
        box.pack_start(self.status, False, False, 0)
        self.spinner = Gtk.Spinner()
        box.pack_start(self.spinner, False, False, 0)
        self.details = Gtk.TextView(editable=False, cursor_visible=False)
        self.details.set_monospace(True)
        self.details.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(110)
        scroll.add(self.details)
        expander = Gtk.Expander(label="Installation details")
        expander.add(scroll)
        box.pack_start(expander, True, True, 0)
        actions = Gtk.ButtonBox(orientation=Gtk.Orientation.HORIZONTAL)
        actions.set_layout(Gtk.ButtonBoxStyle.END)
        self.cancel = Gtk.Button(label="Cancel")
        self.cancel.connect("clicked", self.close_requested)
        actions.add(self.cancel)
        self.primary = Gtk.Button(label="Install Sovereign")
        self.primary.get_style_context().add_class("suggested-action")
        self.primary.connect("clicked", self.start)
        actions.add(self.primary)
        box.pack_end(actions, False, False, 0)
        try:
            self.preflight()
        except Exception as error:
            self.status.set_text(str(error))
            self.primary.set_sensitive(False)
            self.cancel.set_label("Close")

    @staticmethod
    def label(text: str) -> Gtk.Label:
        """Create wrapping, left-aligned native text with accessible content."""
        label = Gtk.Label(label=text, xalign=0)
        label.set_line_wrap(True)
        label.set_max_width_chars(65)
        return label

    def preflight(self) -> str:
        """Check compatibility and preserve customized settings before downloads."""
        if os.geteuid() == 0:
            raise RuntimeError("Open Install Sovereign as your normal Ubuntu user.")
        selected = SETUP["architecture"]()
        SETUP["check_configuration"]()
        self.status.set_text(
            "Ready for Ubuntu 24.04. Click Install Sovereign to begin. "
            "Ubuntu will ask an administrator for permission. Use the password "
            "for the account named in that window."
        )
        return selected

    def close_requested(self, *_: object) -> bool:
        """Cancel before elevation; never interrupt APT partway through changes."""
        if self.busy:
            if self.authorizing:
                self.status.set_text(
                    "Please finish or cancel Ubuntu’s permission window, or wait "
                    "for installation to finish. Do not turn off this computer."
                )
            else:
                self.cancelled.set()
                self.status.set_text("Stopping setup before system changes…")
                self.cancel.set_sensitive(False)
            return True
        self.destroy()
        return True

    def start(self, *_: object) -> None:
        """Begin explicit installation, or launch the installed app as this user."""
        if self.ready:
            subprocess.Popen(  # noqa: S603 - fixed installed native identity
                ["/usr/bin/sovereign"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.destroy()
            return
        if self.busy:
            return
        self.busy = True
        self.authorizing = False
        self.cancelled.clear()
        self.log = ""
        self.details.get_buffer().set_text("")
        self.primary.set_sensitive(False)
        self.cancel.set_label("Cancel")
        self.spinner.start()
        threading.Thread(target=self.install, daemon=True).start()

    def update(self, text: str) -> None:
        """Update native status only on GTK's main thread."""
        GLib.idle_add(self.status.set_text, text)

    def append_log(self, text: str) -> None:
        """Retain bounded diagnostic text, never credentials or remote markup."""
        self.log = (self.log + text)[-65536:]
        GLib.idle_add(self.details.get_buffer().set_text, self.log)

    def install(self) -> None:
        """Verify as the user, then run the fixed helper via Ubuntu authorization."""
        try:
            # Main-thread initial check is repeated here to catch changed settings.
            selected = SETUP["architecture"]()
            SETUP["check_configuration"]()
            with tempfile.TemporaryDirectory(prefix="sovereign-setup-") as name:
                directory = Path(name)
                self.update("1 of 3 · Downloading and verifying Sovereign. No models.")
                for url, filename, digest, limit in (
                    (
                        SETUP["REPOSITORY"] + "/" + SETUP["KEY_PATH"].name,
                        SETUP["KEY_PATH"].name,
                        SETUP["KEY_SHA256"],
                        1024 * 1024,
                    ),
                    (
                        SETUP["RELEASE"]
                        + "/foxlight-sovereign_"
                        + SETUP["VERSION"]
                        + "_"
                        + selected
                        + ".deb",
                        "foxlight-sovereign_"
                        + SETUP["VERSION"]
                        + "_"
                        + selected
                        + ".deb",
                        SETUP["PACKAGES"][selected],
                        256 * 1024 * 1024,
                    ),
                ):
                    if self.cancelled.is_set():
                        break
                    if not download(
                        url, directory / filename, digest, limit, self.cancelled
                    ):
                        self.cancelled.set()
                        break
                if self.cancelled.is_set():
                    GLib.idle_add(self.finished, "cancelled", "")
                    return
                # Only the main thread changes this state. It atomically checks
                # cancellation before allowing the worker to request elevation.
                authorized = threading.Event()
                GLib.idle_add(self.permission_stage, authorized)
                authorized.wait()
                if self.cancelled.is_set():
                    GLib.idle_add(self.finished, "cancelled", "")
                    return
                with subprocess.Popen(  # noqa: S603 - exact package-owned helper
                    permission_command(directory),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                ) as process:
                    if process.stdout is None:
                        raise RuntimeError(
                            "Ubuntu's installation details are unavailable."
                        )
                    for line in process.stdout:
                        self.append_log(line)
                        if "2 of 3" in line:
                            self.update("2 of 3 · Checking signed software updates…")
                        elif "3 of 3" in line:
                            self.update(
                                "3 of 3 · Installing Sovereign and desktop libraries…"
                            )
                    result = process.wait()
                if result in (126, 127):
                    GLib.idle_add(
                        self.finished,
                        "error",
                        "Ubuntu did not grant administrator permission. "
                        "You can try again. If no permission window appeared, "
                        "sign in to your Ubuntu desktop and reopen setup.",
                    )
                elif result:
                    raise RuntimeError(
                        "Ubuntu could not finish installation. Check your connection "
                        "and close other software installers, then try again. "
                        "Installation details have Ubuntu’s message."
                    )
                else:
                    GLib.idle_add(self.finished, "ready", "")
        except Exception as error:
            self.append_log(getattr(error, "details", str(error)) + "\n")
            GLib.idle_add(self.finished, "error", str(error))

    def permission_stage(self, event: threading.Event) -> None:
        """Commit to the system stage only if the user's cancellation has not won."""
        if not self.cancelled.is_set():
            self.authorizing = True
            self.cancel.set_sensitive(False)
            self.status.set_text(
                "Both downloads verified. Ubuntu will ask for permission to install. "
                "Use the password for the account named in that window. "
                "Sovereign does not collect it."
            )
        event.set()

    def finished(self, outcome: str, message: str) -> None:
        """Offer a clear next action without hiding failures or replaying a job."""
        self.busy = self.authorizing = False
        self.spinner.stop()
        self.cancel.set_sensitive(True)
        self.cancel.set_label("Close")
        self.primary.set_sensitive(True)
        if outcome == "ready":
            self.ready = True
            self.heading.set_markup("<big><b>Sovereign is ready.</b></big>")
            self.status.set_text(
                "Open Sovereign to browse models or recover a saved kit. "
                "You can find it in Applications later. Updates arrive through "
                "Ubuntu’s normal software updates."
            )
            self.primary.set_label("Open Sovereign")
        else:
            self.primary.set_label("Try again")
            self.status.set_text(
                "Setup cancelled before system changes. Nothing was installed."
                if outcome == "cancelled"
                else "Setup stopped. " + message + " Verification stays required. "
                "Custom settings, your Library and saved kits are retained."
            )


if __name__ == "__main__":
    window = SetupWindow()
    window.show_all()
    Gtk.main()
