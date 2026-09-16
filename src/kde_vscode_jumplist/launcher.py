"""Open entries in VS Code safely (no shell), including remote URIs."""

from __future__ import annotations

import logging
import shlex
import shutil
import subprocess
from pathlib import Path

from .discovery import Installation, discover_all_installations
from .models import ENTRY_FILE, ENTRY_FOLDER, ENTRY_WORKSPACE, MenuEntry

log = logging.getLogger(__name__)

# What shows a directory in the desktop's file manager. Asking the desktop
# rather than naming Dolphin, for the same reason the jump list does not name an
# editor: the session decides which one it uses, and the tool stays out of it.
FILE_MANAGER = "xdg-open"


def build_command(installation: Installation, entry: MenuEntry) -> list[str]:
    """Build the argv that opens ``entry`` with the given installation.

    Uses ``--folder-uri`` for folders and ``--file-uri`` for files and
    ``.code-workspace`` configs — the same flags VS Code uses for its
    Windows jump list, so local and remote URIs both work.
    """
    if installation.flatpak_app:
        base = ["flatpak", "run", "--file-forwarding", installation.flatpak_app]
    elif installation.executable:
        base = shlex.split(installation.executable)
    else:
        base = ["code"]

    if entry.kind == ENTRY_FOLDER:
        args = ["--folder-uri", entry.uri]
    elif entry.kind in (ENTRY_FILE, ENTRY_WORKSPACE):
        args = ["--file-uri", entry.uri]
    else:
        raise ValueError(f"unknown entry kind: {entry.kind!r}")

    # Local file:// URIs can be passed as plain paths for Flatpak forwarding.
    if installation.flatpak_app and entry.uri.startswith("file://"):
        local = Path(entry.uri[len("file://") :])
        return [*base, "@@", str(local), "@@"]
    return [*base, *args]


def _spawn(command: list[str]) -> int:
    """Start ``command`` detached, with no shell and no output.

    Shared by both launchers so neither can drift into using a shell or into
    inheriting this process's streams: a click has to survive the CLI exiting.
    """
    log.info("launching: %s", command)
    try:
        subprocess.Popen(  # noqa: S603 - argv list, no shell
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as error:
        log.error("failed to launch %s: %s", command[0], error)
        return 127
    return 0


def open_entry(entry: MenuEntry, installations: list[Installation] | None = None) -> int:
    """Launch VS Code for ``entry``. Returns the process exit status.

    The default search ignores :envvar:`FORK`: an entry names the editor it
    came from, and a menu click runs under Plasma, which has no FORK of its
    own — so the entry's own installation is looked for across every fork,
    falling back to whatever is installed.
    """
    installations = (
        installations if installations is not None else discover_all_installations()
    )
    installation = next((i for i in installations if i.variant == entry.source), None)
    if installation is None:
        installation = installations[0] if installations else None
    if installation is None:
        log.error("no VS Code installation found to open %s", entry.uri)
        return 127

    return _spawn(build_command(installation, entry))


def open_folder(folder: Path) -> int:
    """Show ``folder`` in the desktop's file manager. Returns the exit status.

    Asked of the desktop rather than naming Dolphin, for the same reason the
    jump list does not name an editor: which file manager a session uses is the
    session's business, and this tool stays out of it.

    The directory is deliberately not checked for existence. A path whose file
    is gone but whose directory survived still opens; one whose whole tree is
    gone is reported by the desktop itself, which is what a click on a stale
    entry should do rather than the tool quietly deciding nothing happened.
    """
    handler = shutil.which(FILE_MANAGER)
    if handler is None:
        log.error("%s is not installed, cannot show %s", FILE_MANAGER, folder)
        return 127
    return _spawn([handler, str(folder)])
