"""Open entries in VS Code safely (no shell), including remote URIs."""

from __future__ import annotations

import logging
import shlex
import subprocess
from pathlib import Path

from .discovery import Installation, discover_installations
from .models import ENTRY_FILE, ENTRY_FOLDER, ENTRY_WORKSPACE, MenuEntry

log = logging.getLogger(__name__)


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


def open_entry(entry: MenuEntry, installations: list[Installation] | None = None) -> int:
    """Launch VS Code for ``entry``. Returns the process exit status."""
    installations = installations if installations is not None else discover_installations()
    installation = next((i for i in installations if i.variant == entry.source), None)
    if installation is None:
        installation = installations[0] if installations else None
    if installation is None:
        log.error("no VS Code installation found to open %s", entry.uri)
        return 127

    command = build_command(installation, entry)
    log.info("launching: %s", command)
    try:
        process = subprocess.Popen(  # noqa: S603 - argv list, no shell
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
