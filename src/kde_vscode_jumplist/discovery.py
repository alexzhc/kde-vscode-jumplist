"""Discover VS Code installations, desktop entries and state databases.

Each installation is kept separate: its own recent history feeds its own
generated user desktop entry.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

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
    # xdg_data_home() alongside the path helper: both point at the same place,
    # and the standard location is searched either way.
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
    """Profile database for a variant, by auto-detection.

    A database is only returned when it exists, so an installation is only
    reported when there is really something to read.
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


def discover_installations() -> list[Installation]:
    """Detect all usable VS Code installations for the current user.

    Every variant with an executable, a desktop file and a state database is
    reported, along with any Flatpak installation of it. Nothing is configured:
    what is found is what is read.
    """
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
