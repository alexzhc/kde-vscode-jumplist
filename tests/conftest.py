"""Shared test fixtures."""

from __future__ import annotations

import configparser
import json
import os
import sqlite3
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from kde_vscode_jumplist import config

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
    the single data directory (``data_dir``, defaulting to
    ``$XDG_CONFIG_HOME/<app>``).
    """
    config_home = tmp_path / "config"
    data = tmp_path / "data"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.setenv("XDG_DATA_DIRS", "/usr/share")
    monkeypatch.setenv("HOME", str(tmp_path))
    for d in (config_home, data):
        d.mkdir(parents=True)
    # The configuration is looked up in the redirected XDG_CONFIG_HOME, so the
    # file the autouse fixture wrote in the *other* directory no longer counts.
    write_test_config(config_home)
    return {"config": config_home, "data": data, "home": tmp_path}


def write_test_config(config_home: Path) -> Path:
    """Put a default configuration under ``config_home`` and drop the cache.

    The tool refuses to run without a config.toml, so a test that redirects
    $XDG_CONFIG_HOME has to supply one there as well; this is where an install
    would have put its own copy.
    """
    path = config_home / config.APP_NAME / config.CONFIG_FILE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(settings_text(), encoding="utf-8")
    config.forget()
    return path


def standard_settings() -> dict[str, object]:
    """The settings every test starts from: all ten keys, with usual values.

    The two required paths follow the redirected XDG variables, read here rather
    than at import time so a test that moves $XDG_CONFIG_HOME gets a
    configuration pointing there -- which is what an installed one would do.
    """
    home = Path(os.environ.get("HOME") or Path.home())
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    data_home = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    return {
        "data_dir": str(config_home / config.APP_NAME),
        "apps_dir": str(data_home / "applications"),
        # Empty is this tool's "detect it" for the VS Code settings.
        "vscode_dir": "",
        "state_db": "",
        "shared_db": "",
        "desktop": "",
        "exec": "",
        "max_recents": 10,
        "exclude_kinds": ["workspace"],
        "pinned_position": "above",
    }


def render_toml_value(value: object) -> str:
    """A Python value as TOML, for the settings helpers below."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(render_toml_value(item) for item in value) + "]"
    return json.dumps(str(value))


def settings_text(**overrides: object) -> str:
    """A complete configuration as TOML, with ``overrides`` applied.

    Callable with no arguments for the standard one. Overrides are rendered by
    type, so ``max_recents="ten"`` produces the (invalid) TOML a test of the
    validation wants, without hand-writing the rest of the file.
    """
    settings = {**standard_settings(), **overrides}
    return "".join(
        f"{key} = {render_toml_value(value)}\n" for key, value in settings.items()
    )


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep ambient state out of every test.

    Two things are redirected. The configuration file, because the tool refuses
    to run without one and the developer's own settings must not decide what a
    test sees -- it is written where an install would put it. And the working
    directory, because ``./config.toml`` is looked for *first*: the checkout's
    own file would otherwise shadow it.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_BIN_HOME", str(tmp_path / "bin"))
    monkeypatch.setenv("HOME", str(tmp_path))
    # The --config override and the parsed file are module state, so a test that
    # changes either cannot leak into the next one.
    monkeypatch.setattr(config, "_override", None)
    return write_test_config(tmp_path / "xdg-config")


@pytest.fixture()
def write_config() -> Callable[..., Path]:
    """Write settings into this test's configuration file.

    Every key is written, with the standard values for the ones the test does
    not name, so a call site reads like the settings it is about:
    ``write_config(max_recents=3)``.
    """

    def write(**settings: object) -> Path:
        path = config.installed_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(settings_text(**settings), encoding="utf-8")
        config.forget()
        return path

    return write


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
