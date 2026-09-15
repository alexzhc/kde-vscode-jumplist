"""XDG base directory helpers.

Only the variables the tool actually honours are read here, and they are read
at call time so tests can redirect them.
"""

from __future__ import annotations

import os
from pathlib import Path


def _base_dir(variable: str, default: Path) -> Path:
    """One XDG base directory: the variable when it names an absolute path.

    A relative value is ignored rather than resolved. XDG requires an absolute
    path, and resolving one against the working directory would let where a
    command happened to be run from decide where the tool keeps its files --
    which is exactly how a checkout ends up with a stray data directory in it.
    The fallback is the specification's own default, under ``$HOME``.
    """
    value = os.environ.get(variable, "").strip()
    if value:
        expanded = Path(value).expanduser()
        if expanded.is_absolute():
            return expanded
    return default


def xdg_config_home() -> Path:
    """``$XDG_CONFIG_HOME``, or ``~/.config`` as the specification says."""
    return _base_dir("XDG_CONFIG_HOME", Path.home() / ".config")


def xdg_data_home() -> Path:
    """``$XDG_DATA_HOME``, or ``~/.local/share`` as the specification says."""
    return _base_dir("XDG_DATA_HOME", Path.home() / ".local" / "share")


def xdg_data_dirs() -> list[Path]:
    raw = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    return [Path(p) for p in raw.split(":") if p]


def user_bin_dir() -> Path:
    """Where ``install`` drops the executable (``~/.local/bin`` by default).

    ``XDG_BIN_HOME`` is not an official XDG variable, but it is the widely used
    spelling, and honoring it keeps tests from touching the real directory.
    """
    return _base_dir("XDG_BIN_HOME", Path.home() / ".local" / "bin")
