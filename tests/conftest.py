"""Shared test fixtures."""

from __future__ import annotations

import configparser
import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

VENDOR_DESKTOP = """\
[Desktop Entry]
Name=Visual Studio Code
Comment=Code Editing. Redefined.
GenericName=Text Editor
Exec=/usr/share/code/code --unity-launch %F
Icon=vscode
Type=Application
StartupNotify=false
StartupWMClass=Code
Categories=TextEditor;Development;IDE;
MimeType=text/plain;inode/directory;application/x-code-workspace;
Actions=new-empty-window;

[Desktop Action new-empty-window]
Name=New Empty Window
Exec=/usr/share/code/code --new-window %F
Icon=vscode
"""


@pytest.fixture()
def xdg_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Redirect the XDG dirs into tmp_path so tests never touch the user env.

    There is no state dir: entries.json and the lock live with pinned.json in
    the single data directory (``$XDG_CONFIG_HOME/<app>``).
    """
    config_home = tmp_path / "config"
    data = tmp_path / "data"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.setenv("XDG_DATA_DIRS", "/usr/share")
    monkeypatch.setenv("HOME", str(tmp_path))
    for d in (config_home, data):
        d.mkdir(parents=True)
    return {"config": config_home, "data": data, "home": tmp_path}


@pytest.fixture(autouse=True)
def _isolated_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep ambient state out of every test.

    The XDG directories are redirected into tmp_path, so the tool's own files
    -- pinned.json, entries.json, the lock and the generated .desktop -- are
    written inside the test's temporary directory rather than in the developer's
    home. The working directory goes there too, so a test that creates files
    relative to it cannot collide with the checkout.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_BIN_HOME", str(tmp_path / "bin"))
    monkeypatch.setenv("HOME", str(tmp_path))
    # FORK is cleared as well: it steers the data directory, the installed
    # binary and the generated menus, so an ambient export (say
    # ``make test FORK=BUDDY``) must not steer the suite. Tests that are about
    # a fork set it themselves.
    monkeypatch.delenv("FORK", raising=False)


@pytest.fixture()
def qt_modules() -> SimpleNamespace:
    """The PyQt6 modules a dialog is built from, or a skip.

    A headless run is given Qt's own offscreen platform rather than skipped, so
    the windows are still built and laid out in a container. It has to be set
    here because Qt reads it when the QApplication is created.
    """
    QtCore = pytest.importorskip("PyQt6.QtCore", reason="PyQt6 is not installed")
    QtGui = pytest.importorskip("PyQt6.QtGui")
    QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return SimpleNamespace(QtCore=QtCore, QtGui=QtGui, QtWidgets=QtWidgets)


def parse_raw(text: str) -> configparser.RawConfigParser:
    """Parse a desktop file or systemd unit, preserving key case and repeats.

    Shared because raw-reading these files is the same operation with the same
    two pitfalls (case folding, and repeated keys like multiple Environment=).
    """
    parser = configparser.RawConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read_string(text)
    return parser


@pytest.fixture()
def vendor_desktop(tmp_path: Path) -> Path:
    path = tmp_path / "vendor" / "code.desktop"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(VENDOR_DESKTOP, encoding="utf-8")
    return path


def make_state_db(path: Path, payload: dict) -> Path:
    """Create a VS Code-like state.vscdb with the given recent-paths JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE ItemTable (key TEXT UNIQUE, value BLOB)")
        connection.execute(
            "INSERT INTO ItemTable (key, value) VALUES (?, ?)",
            ("history.recentlyOpenedPathsList", json.dumps(payload)),
        )
        connection.commit()
    finally:
        connection.close()
    return path


CURRENT_SCHEMA_PAYLOAD = {
    "entries": [
        {
            "folderUri": {"$mid": 1, "path": "/home/user/project", "scheme": "file"},
            "label": "project",
        },
        {
            "workspace": {
                "id": "abc123",
                "configPath": {"$mid": 1, "path": "/home/user/proj/app.code-workspace", "scheme": "file"},
            },
            "label": "app (Workspace)",
        },
        {"fileUri": {"$mid": 1, "path": "/home/user/notes.md", "scheme": "file"}, "label": "notes.md"},
        {
            "folderUri": {
                "$mid": 1,
                "authority": "ssh-remote+devbox",
                "path": "/srv/code/remote-proj",
                "scheme": "vscode-remote",
            },
            "label": "remote-proj [SSH: devbox]",
        },
        {"fileUri": {"$mid": 1, "path": "/home/user/repo/COMMIT_EDITMSG", "scheme": "file"}},
    ]
}

LEGACY_SCHEMA_PAYLOAD = {
    "workspaces": [
        {"folderUri": "file:///home/user/legacy-folder", "label": "legacy-folder"},
        {"workspace": {"id": "x", "configPath": "file:///home/user/legacy.code-workspace"}},
    ],
    "files": [
        {"fileUri": "file:///home/user/legacy.txt", "label": "legacy.txt"},
    ],
}
