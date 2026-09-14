"""Where the tool's own files live.

Everything here is a configuration value: the paths come from ``config.toml``
(see :mod:`kde_vscode_jumplist.config` for how the file is found), read at call
time so an edit is picked up without a restart.

Nothing in this module writes anything; the helpers that do create their parent
directories themselves.
"""

from __future__ import annotations

from pathlib import Path

from . import config
from .xdg import user_bin_dir

__all__ = [
    "data_dir",
    "entries_path",
    "pinned_path",
    "lock_path",
    "user_applications_dir",
    "user_bin_dir",
]


def data_dir() -> Path:
    """Directory holding everything this tool saves.

    ``pinned.json`` (yours, hand-curated), ``entries.json`` (the ID to entry
    map a click resolves through) and ``sync.lock`` all live here. Defaults to
    ``~/.config/kde-vscode-jumplist``.
    """
    return config.current().data_dir


def user_applications_dir() -> Path:
    """Where the generated .desktop file goes, for Plasma to pick up."""
    return config.current().apps_dir


def pinned_path() -> Path:
    return data_dir() / "pinned.json"


def entries_path() -> Path:
    return data_dir() / "entries.json"


def lock_path() -> Path:
    return data_dir() / "sync.lock"
