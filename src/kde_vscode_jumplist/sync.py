"""Synchronization: read histories, merge pinned entries, regenerate desktop files."""

from __future__ import annotations

import errno
import logging
import os
from pathlib import Path

from . import desktop_entry
from .discovery import Installation, discover_installations
from .entry_store import EntryStore
from .pinned import Pinned
from .models import MenuEntry
from .paths import lock_path
from .vscode_history import read_recent_entries

log = logging.getLogger(__name__)


class _FileLock:
    """Simple non-blocking lock file to prevent overlapping syncs."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: int | None = None

    def acquire(self) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            # Stale lock detection: remove if the owning PID is gone.
            try:
                pid = int(self._path.read_text().strip() or "0")
                os.kill(pid, 0)
                return False  # live process holds the lock
            except (OSError, ValueError) as error:
                if isinstance(error, OSError) and error.errno not in (errno.ESRCH, errno.EPERM):
                    return False
                try:
                    self._path.unlink()
                except OSError:
                    return False
                return self.acquire()
        except OSError as error:
            log.warning("cannot acquire lock %s: %s", self._path, error)
            return False
        os.write(self._fd, str(os.getpid()).encode())
        return True

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self._path.unlink()
        except OSError:
            pass


def sync_once(
    installations: list[Installation] | None = None,
    pinned: Pinned | None = None,
    store: EntryStore | None = None,
) -> bool:
    """Run one synchronization pass. Returns True if any desktop file changed."""
    installations = installations if installations is not None else discover_installations()
    pinned = pinned if pinned is not None else Pinned()
    store = store if store is not None else EntryStore()

    all_entries: list[MenuEntry] = []
    changed = False

    for installation in installations:
        # Newer VS Code versions keep the recent list in a shared storage DB;
        # prefer it and fall back to (or merge with) the profile-level DB. Either
        # may be absent: discovery returns an installation with no profile
        # database when only the shared one exists.
        dbs = [db for db in (installation.shared_state_db, installation.state_db) if db]
        if not dbs or installation.desktop_path is None:
            log.warning("nothing to read for %s; skipping", installation.variant)
            continue
        entries: list[MenuEntry] = []
        seen: set[str] = set()
        for db in dbs:
            for entry in read_recent_entries(db, installation.variant):
                if entry.entry_id in seen:
                    continue
                seen.add(entry.entry_id)
                entries.append(entry)
        all_entries.extend(entries)

        # Single MRU-ordered recents list (folders, workspaces and files
        # interleaved as VS Code stores them); pinned entries come first and
        # are not repeated in the recents.
        recents = [e for e in entries if not pinned.contains(e.entry_id)]
        pinned_entries = [
            e for e in pinned.all() if e.source == installation.variant
        ]

        written = desktop_entry.write_user_desktop_entry(
            installation.desktop_path, installation.variant, pinned_entries, recents
        )
        changed = changed or written is not None

    # Pinned may reference entries no longer recent; keep them resolvable.
    all_entries.extend(pinned.all())
    store.update(all_entries)

    if changed:
        desktop_entry.refresh_service_cache()
    return changed


def run_locked_sync() -> bool:
    """``sync_once`` guarded by a lock file (used by the timer/daemon)."""
    lock = _FileLock(lock_path())
    if not lock.acquire():
        log.info("another sync is running; skipping")
        return False
    try:
        return sync_once()
    finally:
        lock.release()
