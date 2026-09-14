"""The tool's configuration, read from ``config.toml``.

Everything that used to be an environment variable lives here: where the tool's
own files go, which VS Code data is read, and how the menu is laid out.

The file is found in this order, and the first one that exists wins:

1. ``./config.toml`` -- a checkout, or an install that carries its own file;
2. ``~/.config/kde-vscode-jumplist/config.toml`` -- where ``install`` puts it.

``--config PATH`` (the CLI option, which the generated menu actions carry) names
one explicitly instead of searching.

The file is the only source of the settings: nothing here has a built-in default
to fall back on, so a missing file or a missing key is an error naming what is
wrong. That is deliberate -- a default would have to write to somewhere in the
user's home, and running that by accident is worse than stopping. The
``excluded`` kinds and the menu layout are settings like any other, so they too
have to be stated in the file.

A setting whose value is "auto-detect" (``vscode_dir`` and the exact-file
settings) is written as an empty string: absent would be indistinguishable from
a typo, and the file would no longer be a complete description of the run.

Values are read at call time, and re-read when the file changes, so editing the
configuration reaches the running watcher without restarting it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from . import APP_NAME
from .models import ENTRY_FILE, ENTRY_FOLDER, ENTRY_WORKSPACE
from .util import atomic_write_text
from .xdg import xdg_config_home

log = logging.getLogger(__name__)

CONFIG_FILE_NAME = "config.toml"

# The values every setting starts from. A file, not a table in this module: it
# has to name every setting, and tests/test_config.py checks it against
# KNOWN_KEYS so "Restore Defaults" cannot quietly leave one behind.
DEFAULTS_FILE_NAME = "defaults.toml"

# Entry kinds VS Code records, for validating `exclude_kinds`.
ENTRY_KINDS = (ENTRY_FOLDER, ENTRY_FILE, ENTRY_WORKSPACE)

# The settings, all of which the file must name. `exec` is a command rather than
# a path, but it is written the same way, so it is grouped with the paths.
#
# `data_dir` and `apps_dir` are where this tool puts its own files, so they have
# to point somewhere; the other path settings describe a VS Code installation
# that is found by detection anyway, so an empty value is meaningful there and
# means "detect".
REQUIRED_PATH_KEYS = ("data_dir", "apps_dir")
DETECT_PATH_KEYS = ("vscode_dir", "state_db", "shared_db", "desktop", "exec")
PATH_KEYS = (*REQUIRED_PATH_KEYS, *DETECT_PATH_KEYS)
INT_KEYS = ("max_recents",)
LIST_KEYS = ("exclude_kinds",)
STRING_KEYS = ("pinned_position",)
KNOWN_KEYS = (*PATH_KEYS, *INT_KEYS, *LIST_KEYS, *STRING_KEYS)

# `pinned_position`: which block comes first in the menu. Both spellings are
# accepted, plus a few obvious synonyms, so a value typed from memory is not
# rejected outright.
_ABOVE_VALUES = frozenset({"above", "top", "first", "up"})
_BELOW_VALUES = frozenset({"below", "bottom", "last", "down"})

# Settings earlier versions named differently, and what they are called now.
#
# An installed configuration is copied once and then left alone -- `install`
# never overwrites the settings a running service is using -- so a renamed key
# would otherwise leave that file permanently unusable, with an error that does
# not say why. The old spelling is still read, with a warning naming its
# replacement, so upgrading does not require hand-editing the file.
RENAMED_KEYS = {
    # "favorites" became "pinned" throughout; only the key is affected, since
    # the file names under data_dir are not settings.
    "favorites_position": "pinned_position",
}


class ConfigError(Exception):
    """A configuration file that is missing, unreadable or not usable."""


@dataclass(frozen=True)
class Config:
    """One parsed configuration file, with defaults resolved."""

    path: Path
    data_dir: Path
    apps_dir: Path
    vscode_dir: Path | None
    state_db: Path | None
    shared_db: Path | None
    desktop: Path | None
    exec_path: Path | None
    max_recents: int
    exclude_kinds: frozenset[str]
    pinned_position: str


# The active file (from `use`) and the last parse, kept so a menu build does not
# re-read the file for every entry. Invalidated by the file's size and mtime, so
# an edit is picked up without a restart.
_override: Path | None = None
_cache: tuple[Path, int, int, Config] | None = None


def installed_path() -> Path:
    """The user's own configuration file, which ``install`` writes."""
    return xdg_config_home() / APP_NAME / CONFIG_FILE_NAME


def search_paths() -> tuple[Path, ...]:
    """The files :func:`path` looks at, in order."""
    return (Path.cwd() / CONFIG_FILE_NAME, installed_path())


def path() -> Path | None:
    """The configuration file in use, or ``None`` when there is none.

    A ``--config`` path is returned as given (it may not exist: that is
    :func:`current`'s error to report, with the path the user typed).
    """
    if _override is not None:
        return _override
    return next((candidate for candidate in search_paths() if candidate.is_file()), None)


def use(value: str | os.PathLike[str] | None) -> None:
    """Use ``value`` as the configuration file; ``None`` searches again.

    ``~`` is expanded and the result made absolute against the working
    directory, since the value is typed by hand and is also written into the
    systemd unit and the generated menu actions.
    """
    global _override
    _override = None if value is None else Path(os.path.expanduser(str(value))).absolute()
    forget()


def forget() -> None:
    """Drop the cached parse, so the next access reads the file again."""
    global _cache
    _cache = None


def current() -> Config:
    """The active configuration, re-read when the file changes.

    Raises :class:`ConfigError` when there is nothing to read. The settings have
    no built-in defaults to fall back on, so an absent file stops the command
    with a message naming the places it looked rather than proceeding against
    somewhere in the user's home that nobody chose.
    """
    found = path()
    if found is None:
        raise ConfigError(_missing_message())
    if not found.is_file():
        raise ConfigError(f"no configuration file at {found}")
    stat = found.stat()
    if _cache is not None and _cache[:3] == (found, stat.st_mtime_ns, stat.st_size):
        return _cache[3]
    parsed = read(found)
    _remember(found, stat.st_mtime_ns, stat.st_size, parsed)
    return parsed


def read(path: Path) -> Config:
    """Parse ``path`` without caching it."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise ConfigError(f"cannot read {path}: {error.strerror or error}") from None
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"cannot parse {path}: {error}") from None
    return _from_table(path, raw)


def _remember(path: Path, mtime_ns: int, size: int, parsed: Config) -> None:
    global _cache
    _cache = (path, mtime_ns, size, parsed)


def _missing_message() -> str:
    looked_in = "\n".join(f"  {candidate}" for candidate in search_paths())
    return (
        f"no {CONFIG_FILE_NAME} found; looked in:\n{looked_in}\n"
        f"create one (the project's config.toml documents every setting) or pass --config PATH"
    )


def _from_table(path: Path, raw: dict) -> Config:
    """Turn a parsed TOML table into a :class:`Config`, validating the values.

    Every known setting must be present. A file that names only some of them is
    reported as such rather than quietly completed with defaults, because the
    values it left out are exactly the ones that decide where the tool writes.
    """
    _apply_renames(path, raw)

    missing = [key for key in KNOWN_KEYS if key not in raw]
    if missing:
        # Not through _invalid: the sentence already reads as one message, and
        # every missing key is reported at once so the file is not fixed one
        # key per run.
        raise ConfigError(f"{path}: missing settings: {', '.join(missing)}")

    for key in raw:
        if key not in KNOWN_KEYS:
            log.warning("%s: ignoring unknown setting %r", path, key)

    def path_value(key: str) -> Path | None:
        value = raw[key]
        if not isinstance(value, str):
            raise _invalid(path, key, f"must be a path, not {_kind(value)}")
        text = value.strip()
        if not text:
            if key in REQUIRED_PATH_KEYS:
                raise _invalid(path, key, 'must be a path (use "" only where "detect" is meant)')
            return None  # this setting's "detect"
        expanded = Path(os.path.expanduser(text))
        if not expanded.is_absolute():
            # Relative to the file, not to the working directory: the file stays
            # self-contained when a checkout's configuration is copied to the
            # user's config directory by `install`.
            expanded = path.parent / expanded
        return Path(os.path.abspath(expanded))

    max_recents = raw["max_recents"]
    if isinstance(max_recents, bool) or not isinstance(max_recents, int):
        raise _invalid(path, "max_recents", f"must be a whole number, not {_kind(max_recents)}")
    if max_recents < 0:
        raise _invalid(path, "max_recents", "must not be negative (use 0 for no recents)")

    return Config(
        path=path,
        data_dir=path_value("data_dir"),
        apps_dir=path_value("apps_dir"),
        vscode_dir=path_value("vscode_dir"),
        state_db=path_value("state_db"),
        shared_db=path_value("shared_db"),
        desktop=path_value("desktop"),
        exec_path=path_value("exec"),
        max_recents=max_recents,
        exclude_kinds=_kinds(path, raw["exclude_kinds"]),
        pinned_position=_position(path, raw["pinned_position"]),
    )


def _apply_renames(path: Path, raw: dict) -> None:
    """Move any settings still under an older name to their current one.

    Applied before the missing-key check, so a file written by an earlier
    version is complete rather than reported as missing whatever was renamed.
    Two things can go wrong locally, so both are said out loud: the new name may
    be absent (the old value is used, with a warning), or both names may be
    present (the new one wins, and the leftover is named).
    """
    for old, new in RENAMED_KEYS.items():
        if old not in raw:
            continue
        value = raw.pop(old)
        if new in raw:
            log.warning("%s: ignoring %s, which %s replaced", path, old, new)
            continue
        raw[new] = value
        log.warning("%s: %s was renamed to %s; update the file", path, old, new)


def _kinds(path: Path, value: object) -> frozenset[str]:
    """``exclude_kinds``: a list of entry kinds, empty for "exclude nothing"."""
    if not isinstance(value, list):
        raise _invalid(path, "exclude_kinds", f"must be a list, not {_kind(value)}")
    kinds: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise _invalid(path, "exclude_kinds", f"must list entry kinds, not {_kind(item)}")
        kind = item.strip()
        if kind not in ENTRY_KINDS:
            raise _invalid(
                path,
                "exclude_kinds",
                f"has unknown kind {item!r} (expected one of {', '.join(ENTRY_KINDS)})",
            )
        kinds.add(kind)
    return frozenset(kinds)


def _position(path: Path, value: object) -> str:
    """``pinned_position``: normalized to "above" or "below"."""
    if not isinstance(value, str):
        raise _invalid(path, "pinned_position", f"must be a string, not {_kind(value)}")
    text = value.strip().lower()
    if text in _ABOVE_VALUES:
        return "above"
    if text in _BELOW_VALUES:
        return "below"
    raise _invalid(path, "pinned_position", f'must be "above" or "below", not {value!r}')


def _invalid(path: Path, key: str, message: str) -> ConfigError:
    return ConfigError(f"{path}: {key} {message}")


def _kind(value: object) -> str:
    """TOML type name, for an error message that says what was found."""
    return {
        str: "a string",
        int: "a whole number",
        float: "a number",
        bool: "a boolean",
        list: "a list",
        dict: "a table",
    }.get(type(value), type(value).__name__)


# --- reading and writing the file back ------------------------------------
#
# The Settings window edits this file, so the two operations it needs live here
# rather than in the window: what a setting may be is decided by this module,
# and a dialog that wrote the file itself would be a second opinion about it.


def defaults_text() -> str:
    """The shipped ``defaults.toml``, as text.

    Read from the package rather than from the checkout, so an installed copy --
    including the single-file executable -- has it too. See DEFAULTS_FILE_NAME.
    """
    from importlib.resources import files

    return (files(__package__) / DEFAULTS_FILE_NAME).read_text(encoding="utf-8")


def defaults() -> dict[str, object]:
    """The values every setting starts from, as the file writes them.

    Raw values, not a :class:`Config`: a path setting is kept exactly as it is
    written (``~/...`` rather than expanded), because these are written back into
    the file and expanding them there would turn a readable path absolute.
    """
    values = tomllib.loads(defaults_text())
    missing = [key for key in KNOWN_KEYS if key not in values]
    if missing:
        # Better a plain error than "Restore Defaults" doing only part of its job.
        raise ConfigError(f"{DEFAULTS_FILE_NAME} is missing settings: {', '.join(missing)}")
    return values


def raw_values(target: Path | None = None) -> dict[str, object]:
    """The file's own values, as written, with renamed keys applied.

    What :func:`read` returns has been resolved: paths are absolute and the
    detect settings are ``None``. That is what the tool needs to run, but not
    what it needs to *edit* -- writing those back would rewrite every path in
    the file -- so this is the unresolved view.
    """
    found = target or path()
    if found is None or not found.is_file():
        raise ConfigError(f"no configuration file at {found or 'any of the usual places'}")
    try:
        values = tomllib.loads(found.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"cannot read {found}: {error}") from None
    _apply_renames(found, values)
    return values


def render_value(value: object) -> str:
    """A Python value as a TOML literal, for writing a setting back.

    Only the shapes a setting can have, and deliberately not a general TOML
    writer: a value that is none of them is a bug in the caller, and failing
    loudly is better than writing something the parser will reject.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        items = sorted(value, key=str) if isinstance(value, (set, frozenset)) else value
        return "[" + ", ".join(render_value(item) for item in items) + "]"
    if isinstance(value, str):
        return json.dumps(value)
    raise TypeError(f"cannot write {value!r} as a setting")


# A setting's line: the name at the start, which is how the file is navigated.
_SETTING_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _comment_start(line: str) -> int:
    """Where a trailing comment begins, or -1. A '#' inside a string is text."""
    in_string = False
    escaped = False
    for index, char in enumerate(line):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "#":
            return index
    return -1


def update_file(changes: Mapping[str, object], target: Path | None = None) -> Path:
    """Rewrite the named settings in the file, leaving everything else alone.

    Line by line rather than by regenerating the file: the configuration explains
    every setting in a comment above it, and those comments are the documentation
    a user reads -- writing the parsed values back would throw all of it away.
    A trailing comment on a changed line is kept for the same reason. A setting
    the file does not name yet is appended, so a file written by an older version
    gains the settings added since.

    The file is left valid: nothing is written until every value has been
    rendered, and the write itself is atomic.
    """
    found = target or path()
    if found is None:
        raise ConfigError(_missing_message())
    if not found.is_file():
        raise ConfigError(f"no configuration file at {found}")

    pending = dict(changes)
    written: list[str] = []
    for line in found.read_text(encoding="utf-8").splitlines(keepends=True):
        match = _SETTING_LINE.match(line)
        key = match.group(1) if match else None
        if key is not None and key not in pending and key in RENAMED_KEYS:
            # The old name of something being written under its new one. Keeping
            # both would leave the file carrying two spellings of one setting.
            if RENAMED_KEYS[key] in changes:
                continue
        if key is None or key not in pending:
            written.append(line)
            continue
        comment_start = _comment_start(line)
        comment = ""
        if comment_start >= 0:
            # Everything from the gap before the comment onwards, so the spacing
            # the file already uses is kept rather than normalized.
            before = line[:comment_start]
            comment = before[len(before.rstrip()) :] + line[comment_start:].rstrip()
        written.append(f"{key} = {render_value(pending.pop(key))}{comment}\n")

    if pending:
        if written and not written[-1].endswith("\n"):
            written[-1] += "\n"
        written.extend(f"{key} = {render_value(value)}\n" for key, value in pending.items())

    atomic_write_text(found, "".join(written))
    # One cache serves the whole process, and this process just changed the file
    # it describes.
    forget()
    return found
