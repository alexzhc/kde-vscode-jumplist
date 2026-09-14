"""XDG base directory helpers.

Only the variables the tool actually honours are read here, and they are read
at call time so tests can redirect them. They live apart from
:mod:`kde_vscode_jumplist.paths` because the configuration file has to be found
before any of the tool's own paths can be resolved.
"""

from __future__ import annotations

import os
from pathlib import Path


def xdg_config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def xdg_data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def xdg_data_dirs() -> list[Path]:
    raw = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    return [Path(p) for p in raw.split(":") if p]


def user_bin_dir() -> Path:
    """Where ``install`` drops the executable (``~/.local/bin`` by default).

    ``XDG_BIN_HOME`` is not an official XDG variable, but it is the widely used
    spelling, and honoring it keeps tests from touching the real directory.
    """
    return Path(os.environ.get("XDG_BIN_HOME") or Path.home() / ".local" / "bin")
