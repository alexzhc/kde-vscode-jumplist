"""Where the tool's own files live.

The two directories are fixed rather than configured: this tool keeps its state
under ``~/.config/kde-vscode-jumplist`` and writes the generated ``.desktop``
file to ``~/.local/share/applications``. Both are the XDG base directories, read
at call time, and both are absolute -- nothing here depends on the working
directory, so a command run from a checkout writes to the same place as the
service does.

Nothing in this module writes anything; the helpers that do create their parent
directories themselves, and :func:`ensure_data_dir` creates the data directory
up front for callers that would rather not rely on that.
"""

from __future__ import annotations

from pathlib import Path

from . import APP_NAME
from .xdg import user_bin_dir, xdg_config_home, xdg_data_home

__all__ = [
    "data_dir",
    "ensure_data_dir",
    "entries_path",
    "pinned_path",
    "lock_path",
    "binary_name",
    "all_binary_names",
    "user_applications_dir",
    "user_bin_dir",
]


def _current_fork() -> str:
    """The fork the environment selected.

    Deferred import: :mod:`kde_vscode_jumplist.discovery` imports this module,
    so the dependency only works this way round.
    """
    from .discovery import current_fork

    return current_fork()


# State directory name per fork, under $XDG_CONFIG_HOME: each fork keeps its
# own pinned entries, entry cache and lock, so two watchers can run at once.
DATA_DIR_NAMES: dict[str, str] = {
    "VSCODE": APP_NAME,
    "BUDDY": "kde-buddy-jumplist",
}

# Executable name per fork, under $XDG_BIN_HOME: one binary per fork, so a
# menu's actions keep launching even when the other family is not installed.
BINARY_NAMES: dict[str, str] = {
    "VSCODE": APP_NAME,
    "BUDDY": "kde-buddy-jumplist",
}


def data_dir() -> Path:
    """Directory holding everything this tool saves for the selected fork.

    ``pinned.json`` (yours, hand-curated), ``entries.json`` (the ID to entry
    map a click resolves through) and ``sync.lock`` all live here:
    ``~/.config/kde-vscode-jumplist`` for the VS Code family,
    ``~/.config/kde-buddy-jumplist`` for CodeBuddy CN.
    """
    return xdg_config_home() / DATA_DIR_NAMES.get(_current_fork(), APP_NAME)


def binary_name() -> str:
    """The executable name the selected fork installs under."""
    return BINARY_NAMES.get(_current_fork(), APP_NAME)


def all_binary_names() -> tuple[str, ...]:
    """Every fork's executable name, the selected fork's first."""
    name = binary_name()
    return (name, *(other for other in BINARY_NAMES.values() if other != name))


def ensure_data_dir() -> Path:
    """The data directory, created if it is not there yet.

    Called once as the CLI starts: the writers below create their own parent
    directories anyway, but a first run should not need a write to happen
    before the tool has somewhere to keep things.
    """
    path = data_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_applications_dir() -> Path:
    """Where the generated .desktop file goes, for Plasma to pick up."""
    return xdg_data_home() / "applications"


def pinned_path() -> Path:
    return data_dir() / "pinned.json"


def entries_path() -> Path:
    return data_dir() / "entries.json"


def lock_path() -> Path:
    return data_dir() / "sync.lock"
