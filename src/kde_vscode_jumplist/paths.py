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
    "user_applications_dir",
    "user_bin_dir",
]


def data_dir() -> Path:
    """Directory holding everything this tool saves.

    ``pinned.json`` (yours, hand-curated), ``entries.json`` (the ID to entry
    map a click resolves through) and ``sync.lock`` all live here:
    ``~/.config/kde-vscode-jumplist``.
    """
    return xdg_config_home() / APP_NAME


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
