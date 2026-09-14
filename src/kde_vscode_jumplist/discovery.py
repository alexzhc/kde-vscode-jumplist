"""Discover VS Code installations, desktop entries and state databases.

Each installation is kept separate: its own recent history feeds its own
generated user desktop entry.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import config
from .desktop_entry import is_generated
from .paths import user_applications_dir
from .xdg import xdg_data_dirs, xdg_data_home

log = logging.getLogger(__name__)

# Known variants: (installation id, executable names, user-data dir name)
VARIANTS: list[tuple[str, tuple[str, ...], str]] = [
    ("code", ("code",), ".config/Code"),
    ("code-insiders", ("code-insiders",), ".config/Code - Insiders"),
    ("code-oss", ("code-oss",), ".config/Code - OSS"),
    ("vscodium", ("codium",), ".config/VSCodium"),
]

# variant -> the subdirectory of $HOME holding its user data. Derived from
# VARIANTS so the two cannot drift apart.
USER_DATA_DIRS: dict[str, str] = {variant: user_data for variant, _names, user_data in VARIANTS}

# Desktop entry file names per variant, in preference order.
DESKTOP_CANDIDATES: dict[str, tuple[str, ...]] = {
    "code": ("code.desktop", "code-url-handler.desktop"),
    "code-insiders": ("code-insiders.desktop",),
    "code-oss": ("code-oss.desktop",),
    "vscodium": ("vscodium.desktop", "codium.desktop"),
}

FLATPAK_APPS: dict[str, tuple[str, str, str]] = {
    # variant: (flatpak app id, user-data subdir, desktop file)
    "code": ("com.visualstudio.code", "data", "com.visualstudio.code.desktop"),
    "code-insiders": ("com.visualstudio.code-insiders", "data", "com.visualstudio.code-insiders.desktop"),
    "vscodium": ("com.vscodium.codium", "data", "com.vscodium.codium.desktop"),
}

# product.json "sharedDataFolderName" per variant: newer VS Code versions keep
# the recently-opened list in this shared database instead of the profile one.
SHARED_DATA_FOLDERS: dict[str, str] = {
    "code": ".vscode-shared",
    "code-insiders": ".vscode-insiders-shared",
    "code-oss": ".vscode-oss-shared",
    "vscodium": ".vscodium-shared",
}


@dataclass
class Installation:
    """A detected VS Code installation."""

    variant: str
    executable: str | None = None
    desktop_path: Path | None = None
    state_db: Path | None = None
    flatpak_app: str | None = None
    # VS Code >= 1.1xx keeps the recent list in a shared storage database
    # (~/.vscode-shared/sharedStorage/state.vscdb) instead of the profile one.
    # Either database may be absent; both are read when present.
    shared_state_db: Path | None = None


def _find_executable(names: tuple[str, ...]) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _find_desktop_file(candidates: tuple[str, ...]) -> Path | None:
    # The user's own applications directory first, then the system data dirs.
    # xdg_data_home() rather than the path helper alone: when APPS_DIR points
    # somewhere else, the standard location still holds the vendor file.
    user_dirs = [user_applications_dir()]
    xdg_user = xdg_data_home() / "applications"
    if xdg_user not in user_dirs:
        user_dirs.append(xdg_user)
    search_dirs: list[Path] = user_dirs + [d / "applications" for d in xdg_data_dirs()]
    for directory in search_dirs:
        for name in candidates:
            candidate = directory / name
            if not candidate.is_file():
                continue
            # Skip files we generated ourselves (they shadow the vendor
            # original in ~/.local/share/applications); regenerate from the
            # pristine vendor file instead.
            if is_generated(candidate):
                continue
            return candidate
    return None


def _state_db_for(variant: str) -> Path | None:
    """Profile database for a variant, by auto-detection only.

    Deliberately ignores the configured ``vscode_dir``: this is the detection
    path, and honouring the setting here is what would stop the "fall back to
    detection" retry from ever finding anything.
    """
    home = Path.home()
    user_data = USER_DATA_DIRS.get(variant)
    if not user_data:
        return None
    app_id = FLATPAK_APPS.get(variant, ("",))[0]
    for base in (home, home / ".var" / "app" / app_id):
        if not app_id and base != home:
            continue  # no flatpak location for this variant
        db = base / user_data / "User" / "globalStorage" / "state.vscdb"
        if db.is_file():
            return db
    return None


def _vscode_data_dir_override() -> Path | None:
    """The configured ``vscode_dir``: a VS Code user data directory.

    Set it when VS Code keeps its data somewhere non-standard. It replaces
    auto-detection for the main variant, so a pointed-at directory is used as
    written instead of being merged with the usual candidates.
    """
    return config.current().vscode_dir


def _shared_state_db_for(variant: str) -> Path | None:
    """Locate the shared storage DB newer VS Code versions use for recents."""
    folder = SHARED_DATA_FOLDERS.get(variant)
    if not folder:
        return None
    home = Path.home()
    bases = [home]
    app_id = FLATPAK_APPS.get(variant, ("",))[0]
    if app_id:
        bases.append(home / ".var" / "app" / app_id)
    for base in bases:
        db = base / folder / "sharedStorage" / "state.vscdb"
        if db.is_file():
            return db
    return None


def _flatpak_installation(variant: str) -> Installation | None:
    info = FLATPAK_APPS.get(variant)
    if not info:
        return None
    app_id, data_subdir, desktop_name = info
    flatpak_dir = Path.home() / ".var" / "app" / app_id
    if not flatpak_dir.is_dir():
        return None
    db = flatpak_dir / data_subdir / "User" / "globalStorage" / "state.vscdb"
    if not db.is_file():
        return None
    desktop = _find_desktop_file((desktop_name,))
    if desktop is None:
        return None
    return Installation(
        variant=variant,
        executable=f"flatpak run {app_id}",
        desktop_path=desktop,
        state_db=db,
        flatpak_app=app_id,
        shared_state_db=_shared_state_db_for(variant),
    )


def _override_installation() -> Installation | None:
    """An installation described by the configuration's VS Code settings.

    ``vscode_dir`` supplies the profile data directory (the one holding
    ``User/globalStorage/state.vscdb``); ``state_db`` and ``shared_db`` name the
    two databases outright and win over it.

    The shared database is still discovered normally: VS Code keeps it outside
    the profile directory (``~/.vscode-shared``), and on many installs -- this
    one included -- *that* is where the recent list actually lives. Ignoring it
    because a data directory was given would quietly empty the menu.
    """
    settings = config.current()
    data_dir = settings.vscode_dir
    state_db = settings.state_db
    if state_db is None and data_dir is not None:
        state_db = data_dir / "User" / "globalStorage" / "state.vscdb"
    if state_db is None:
        return None

    shared_db = settings.shared_db or _shared_state_db_for("code")
    if not state_db.is_file():
        if data_dir is not None and shared_db is not None:
            # The shared database alone can still be usable; keep going rather
            # than reporting nothing at all.
            log.warning("no state database at %s; using the shared one only", state_db)
            state_db = None
        else:
            # A typo here would otherwise quietly empty the menu.
            log.warning("configured state database does not exist: %s", state_db)
            return None

    desktop = settings.desktop or _find_desktop_file(DESKTOP_CANDIDATES["code"])
    if desktop is None:
        log.warning("no VS Code desktop file found for %s", state_db or shared_db)
        return None
    return Installation(
        variant="code",
        executable=settings.exec_path or shutil.which("code") or "code",
        desktop_path=desktop,
        state_db=state_db,
        shared_state_db=shared_db,
    )


def discover_installations() -> list[Installation]:
    """Detect all usable VS Code installations for the current user.

    A configured ``vscode_dir`` or ``state_db`` that resolves to a usable
    database means "use this one", so auto-detection is skipped. One that
    resolves to nothing usable is reported and then ignored, falling back to
    detection: it is a configuration value, so it must not be able to empty the
    menu on a machine laid out differently.
    """
    override = _override_installation()
    if override is not None:
        return [override]
    settings = config.current()
    if settings.vscode_dir is not None or settings.state_db is not None:
        log.warning("ignoring the configured VS Code path and detecting instead")

    found: list[Installation] = []
    for variant, names, _user_data in VARIANTS:
        executable = _find_executable(names)
        desktop = _find_desktop_file(DESKTOP_CANDIDATES.get(variant, (f"{variant}.desktop",)))
        db = _state_db_for(variant)
        if executable and desktop and db:
            found.append(
                Installation(
                    variant=variant,
                    executable=executable,
                    desktop_path=desktop,
                    state_db=db,
                    shared_state_db=_shared_state_db_for(variant),
                )
            )
            continue
        flatpak = _flatpak_installation(variant)
        if flatpak is not None:
            found.append(flatpak)
    return found
