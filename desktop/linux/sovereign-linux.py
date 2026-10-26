"""Linux native lifecycle and file dialogs around the independent Library.

Only the distro Python imports GI. Model operations run in the bundled isolated
runtime. This process installs/starts no service, router or sharing worker.
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import threading
from pathlib import Path
from urllib.parse import unquote, urlsplit

STARTUP = re.compile(r"Sovereign: (http://127\.0\.0\.1:[0-9]{1,5}/s/[0-9a-f]{64}/)\n")


def permitted(base: str, address: str) -> bool:
    """Keep page navigation and downloads inside this owner's exact ticket."""
    try:
        origin, target = urlsplit(base), urlsplit(address)
        path = unquote(target.path)
        return (
            target.scheme == origin.scheme == "http"
            and target.hostname == origin.hostname == "127.0.0.1"
            and target.port == origin.port
            and target.username is None
            and target.password is None
            and path.startswith(origin.path)
            and not any(part in (".", "..") for part in path.split("/"))
            and "\\" not in path
        )
    except ValueError:
        return False


def owner_environment() -> dict[str, str]:
    """Omit ambient Python, proxies and loaders from the owned child runtime."""
    environment = {
        "HOME": str(Path.home()),
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
    }
    # Preserve explicit per-user storage choices and desktop folder reveal.
    for name in (
        "XDG_DATA_HOME",
        "XDG_RUNTIME_DIR",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "DBUS_SESSION_BUS_ADDRESS",
    ):
        if name in os.environ:
            environment[name] = os.environ[name]
    return environment


def main() -> None:
    """Open a native window and cooperatively stop only its exact child on Quit."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-directory", type=Path)
    args = parser.parse_args()
    if args.data_directory is not None and not args.data_directory.is_absolute():
        parser.error("data directory must be absolute")
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import Gio, GLib, Gtk, WebKit2

    class Window:
        def __init__(self) -> None:
            self.base = ""
            self.quitting = False
            self.process = None
            self.window = Gtk.Window(title="Sovereign")
            self.window.set_default_size(1180, 820)
            self.window.set_size_request(720, 520)
            self.window.set_icon_name("ai.foxlight.Sovereign")
            self.window.connect("delete-event", self.quit)
            self.status = Gtk.Label(label="Opening your Library…")
            self.window.add(self.status)
            self.window.show_all()
            resources = Path(__file__).resolve().parent
            command = [
                str(resources / "python/bin/python3"),
                "-I",
                "-B",
                str(resources / "native-cli.py"),
                "foxlight-sovereign",
                "--no-browser",
            ]
            if args.data_directory is not None:
                command.extend(["--home", str(args.data_directory)])
            try:
                self.process = subprocess.Popen(  # noqa: S603 - fixed installed child
                    command,
                    cwd=resources,
                    env=owner_environment(),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
            except OSError:
                self.status.set_text(
                    "Sovereign could not start. Reinstall the application. "
                    "Your saved copies are kept."
                )
                return
            threading.Thread(target=self.observe, daemon=True).start()
            GLib.timeout_add_seconds(30, self.startup_timeout)

        def observe(self) -> None:
            # Drain the pipe without logging the private ticket or unbounded data.
            line = self.process.stdout.readline(4097)
            match = STARTUP.fullmatch(line.decode("utf-8", errors="replace"))
            if match and 0 < urlsplit(match[1]).port <= 65535:
                GLib.idle_add(self.ready, match[1])
            self.process.stdout.close()
            self.process.wait()
            GLib.idle_add(self.stopped)

        def startup_timeout(self) -> bool:
            if not self.base and self.process.poll() is None:
                self.status.set_text(
                    "Opening is taking longer than expected. You can close this "
                    "window safely; your saved copies are kept."
                )
            return False

        def ready(self, address: str) -> bool:
            if self.quitting:
                return False
            self.base = address
            context = WebKit2.WebContext.new_ephemeral()
            # Never persist the session ticket or relax WebKit's sandbox/TLS.
            context.connect("download-started", self.download)
            self.view = WebKit2.WebView.new_with_context(context)
            self.view.connect("decide-policy", self.policy)
            self.view.connect(
                "permission-request", lambda _, request: request.deny() or True
            )
            self.window.remove(self.status)
            self.window.add(self.view)
            self.window.show_all()
            self.view.load_uri(address)
            return False

        def policy(self, view, decision, kind) -> bool:
            if kind in (
                WebKit2.PolicyDecisionType.NAVIGATION_ACTION,
                WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION,
            ):
                action = decision.get_navigation_action()
                address = action.get_request().get_uri()
                if (
                    permitted(self.base, address)
                    and kind == WebKit2.PolicyDecisionType.NAVIGATION_ACTION
                ):
                    decision.use()
                else:
                    decision.ignore()
                    if action.is_user_gesture() and urlsplit(address).scheme == "https":
                        Gio.AppInfo.launch_default_for_uri(address, None)
            elif kind == WebKit2.PolicyDecisionType.RESPONSE:
                if not permitted(self.base, decision.get_response().get_uri()):
                    decision.ignore()
                elif decision.is_mime_type_supported():
                    decision.use()
                else:
                    decision.download()
            else:
                decision.ignore()
            return True

        def download(self, context, download) -> None:
            # Only exact authenticated local artifacts can reach a native Save.
            if not permitted(self.base, download.get_request().get_uri()):
                download.cancel()
                return

            def destination(item, suggested) -> bool:
                if not permitted(self.base, item.get_response().get_uri()):
                    item.cancel()
                    return True
                dialog = Gtk.FileChooserDialog(
                    title="Save recovery kit",
                    transient_for=self.window,
                    action=Gtk.FileChooserAction.SAVE,
                )
                dialog.add_buttons(
                    "Cancel", Gtk.ResponseType.CANCEL, "Save", Gtk.ResponseType.ACCEPT
                )
                dialog.set_do_overwrite_confirmation(True)
                dialog.set_current_name("Sovereign-recovery.foxkit")
                if dialog.run() == Gtk.ResponseType.ACCEPT:
                    item.set_allow_overwrite(True)
                    item.set_destination(Path(dialog.get_filename()).as_uri())
                else:
                    item.cancel()
                dialog.destroy()
                return True

            download.connect("decide-destination", destination)

        def quit(self, *_args) -> bool:
            if not self.quitting:
                self.quitting = True
                self.window.set_sensitive(False)
                if self.process is not None and self.process.poll() is None:
                    self.process.terminate()
                else:
                    Gtk.main_quit()
            return True

        def stopped(self) -> bool:
            if self.quitting:
                Gtk.main_quit()
            else:
                if hasattr(self, "view"):
                    self.window.remove(self.view)
                    self.window.add(self.status)
                self.status.set_text(
                    "Sovereign stopped. Your saved copies are kept. Reopen to "
                    "continue. Another copy may already own this Library."
                )
                self.window.show_all()
            return False

    window = Window()
    for event in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(
            GLib.PRIORITY_DEFAULT, event, lambda: window.quit() and False
        )
    Gtk.main()


if __name__ == "__main__":
    main()
