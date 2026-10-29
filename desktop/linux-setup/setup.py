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
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

RESOURCES = Path("/usr/share/foxlight-sovereign-setup")
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
        self.set_default_size(640, 680)
        self.set_name("sovereign-setup")
        self.set_icon_name("ai.foxlight.Sovereign.Setup")
        branded = self.apply_style()
        self.busy = False
        self.authorizing = False
        self.ready = False
        self.cancelled = threading.Event()
        self.log = ""
        self.connect("delete-event", self.close_requested)
        self.connect("destroy", Gtk.main_quit)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root)
        # Only the middle scrolls. The primary action remains reachable with
        # large desktop text, expanded diagnostics and a small laptop display.
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        root.pack_start(scroll, True, True, 0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_border_width(24)
        scroll.add(box)
        brand = Gtk.Box(spacing=12)
        if branded:
            logo = Gtk.Image.new_from_pixbuf(
                GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    str(RESOURCES / "wordmark.svg"), 162, 39, True
                )
            )
        else:
            logo = Gtk.Image.new_from_icon_name(
                "ai.foxlight.Sovereign.Setup", Gtk.IconSize.DIALOG
            )
        logo.get_accessible().set_name("Sovereign by Foxlight")
        brand.pack_start(logo, False, False, 0)
        if not branded:
            brand.pack_start(self.label("Sovereign"), False, False, 0)
        badge = self.label("UBUNTU SETUP")
        badge.set_line_wrap(False)
        badge.get_style_context().add_class("setup-eyebrow")
        brand.pack_end(badge, False, False, 0)
        box.pack_start(brand, False, False, 0)
        intro = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.heading = self.label("Keep a copy. Keep access.")
        self.heading.get_style_context().add_class("setup-heading")
        intro.pack_start(self.heading, False, False, 0)
        self.introduction = self.label(
            "Keep the AI models you choose. Recover your saved copies, "
            "even if their original source disappears."
        )
        self.introduction.get_style_context().add_class("setup-introduction")
        intro.pack_start(self.introduction, False, False, 0)
        box.pack_start(intro, False, False, 0)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card.get_style_context().add_class("setup-card")
        self.feature_copy: list[tuple[Gtk.Label, Gtk.Label]] = []
        for icon, title, description in (
            (
                "system-search-symbolic",
                "A home for your models",
                "Find useful models or open a recovery kit.",
            ),
            (
                "software-update-available-symbolic",
                "Verified app. Signed updates.",
                "Setup checks the app, enables software updates and may "
                "add desktop libraries.",
            ),
            (
                "preferences-system-privacy-symbolic",
                "You stay in control",
                "No models download or sharing starts until you choose.",
            ),
        ):
            card.pack_start(self.feature(icon, title, description), False, False, 0)
        box.pack_start(card, False, False, 0)
        self.notice = Gtk.Box(spacing=12)
        self.notice.get_style_context().add_class("setup-notice")
        self.spinner = Gtk.Spinner()
        self.spinner.set_valign(Gtk.Align.START)
        self.notice.pack_start(self.spinner, False, False, 0)
        self.status = self.label("Checking this computer…")
        self.notice.pack_start(self.status, True, True, 0)
        box.pack_start(self.notice, False, False, 0)
        self.details = Gtk.TextView(editable=False, cursor_visible=False)
        self.details.set_monospace(True)
        self.details.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_min_content_height(110)
        log_scroll.set_max_content_height(180)
        log_scroll.add(self.details)
        expander = Gtk.Expander(label="Installation details")
        expander.get_style_context().add_class("setup-details")
        expander.add(log_scroll)
        box.pack_start(expander, False, False, 0)
        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        footer.get_style_context().add_class("setup-footer")
        note = self.label("No account. No Skulk. No GPU required.")
        note.get_style_context().add_class("setup-footnote")
        footer.pack_start(note, False, False, 0)
        actions = Gtk.Box(spacing=12)
        self.cancel = Gtk.Button(label="Cancel")
        self.cancel.get_style_context().add_class("setup-secondary")
        self.cancel.connect("clicked", self.close_requested)
        actions.pack_start(self.cancel, False, False, 0)
        self.primary = Gtk.Button(label="Install Sovereign")
        self.primary.get_style_context().add_class("suggested-action")
        self.primary.connect("clicked", self.start)
        self.primary.set_can_default(True)
        actions.pack_end(self.primary, False, False, 0)
        footer.pack_start(actions, False, False, 0)
        root.pack_end(footer, False, False, 0)
        self.primary.grab_default()
        # The first focusable item in the middle is the details expander. Give
        # focus to the visible action so GTK does not scroll the welcome away
        # at large text sizes merely to reveal that collapsed expander.
        self.primary.grab_focus()
        try:
            self.preflight()
        except Exception as error:
            self.notice.get_style_context().add_class("setup-error")
            self.status.set_text(str(error))
            self.primary.set_sensitive(False)
            self.cancel.set_label("Close")

    def apply_style(self) -> bool:
        """Use local brand art and colors, retaining the OS high-contrast theme."""
        settings = Gtk.Settings.get_default()
        theme = settings.get_property("gtk-theme-name") if settings else ""
        theme = str(theme) + os.environ.get("GTK_THEME", "")
        if "highcontrast" in theme.replace("-", "").lower():
            return False
        provider = Gtk.CssProvider()
        provider.load_from_path(str(RESOURCES / "setup.css"))
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        return True

    def feature(self, icon: str, title: str, description: str) -> Gtk.Box:
        """Pair a readable benefit with supporting text and a decorative OS icon."""
        row = Gtk.Box(spacing=14)
        image = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR)
        image.set_valign(Gtk.Align.START)
        image.get_style_context().add_class("setup-feature-icon")
        row.pack_start(image, False, False, 0)
        copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        heading = self.label(title)
        heading.get_style_context().add_class("setup-feature-title")
        copy.pack_start(heading, False, False, 0)
        detail = self.label(description)
        detail.get_style_context().add_class("setup-feature-detail")
        copy.pack_start(detail, False, False, 0)
        self.feature_copy.append((heading, detail))
        row.pack_start(copy, True, True, 0)
        return row

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
            "Ready for Ubuntu 24.04. Ubuntu will ask an administrator "
            "for permission to install."
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
        self.heading.set_text("Installing Sovereign…")
        self.introduction.set_text(
            "Installing the app and its software updates. "
            "You’ll choose which models to keep once it’s ready."
        )
        context = self.notice.get_style_context()
        context.remove_class("setup-error")
        context.remove_class("setup-ready")
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
                        "You can try again. If no window appeared, "
                        "reopen setup from your Ubuntu desktop.",
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
            self.notice.get_style_context().add_class("setup-ready")
            self.heading.set_text("Sovereign is ready.")
            self.introduction.set_text(
                "Find something worth keeping. Or bring a saved model back, "
                "with its information about where it came from."
            )
            for (title, description), (heading, detail) in zip(
                (
                    ("Find a useful model", "Browse models by what they can do."),
                    (
                        "Keep a copy you control",
                        "Preserve a model with its files and provenance, or recover "
                        "one from a saved kit.",
                    ),
                    (
                        "Come back anytime",
                        "Sovereign is in Applications. Signed app updates arrive "
                        "through Ubuntu’s normal software updates.",
                    ),
                ),
                self.feature_copy,
                strict=True,
            ):
                heading.set_text(title)
                detail.set_text(description)
            self.status.set_text(
                "Open Sovereign to browse models or recover a saved kit. "
                "You can find it in Applications later. Updates arrive through "
                "Ubuntu’s normal software updates."
            )
            self.primary.set_label("Open Sovereign")
        else:
            self.notice.get_style_context().add_class("setup-error")
            self.primary.set_label("Try again")
            self.heading.set_text("Setup needs attention.")
            self.introduction.set_text(
                "Your Library and saved recovery kits are retained. "
                "Read the message below, then try again when you’re ready."
            )
            self.status.set_text(
                "Setup cancelled before system changes. Nothing was installed. "
                "Choose Try again when you’re ready."
                if outcome == "cancelled"
                else message
                if len(message) <= 240
                else "Setup couldn’t finish. Installation details explain the problem. "
                "Your Library and saved kits are retained."
            )
        # GTK grows to fit temporary messages but does not automatically shrink
        # afterward. Keep the next action within a laptop's first viewport.
        self.resize(640, 680)


if __name__ == "__main__":
    window = SetupWindow()
    window.show_all()
    Gtk.main()
