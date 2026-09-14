"""Per-user pinned entries, persisted as JSON.

Pinned entries survive removal from VS Code's recent list and are never
written back into VS Code's database.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .models import MenuEntry
from .paths import pinned_path
from .util import atomic_write_text

log = logging.getLogger(__name__)

# What this file, and the key inside it, were called before "favorites" became
# "pinned". An upgrade must not look like having pinned nothing, so both are
# still read: the old file name, and the old key inside either file.
#
# Reading is deliberately *all* that happens to them. A read must never move or
# delete a file the user owns -- two processes loading at once, or one that has
# already loaded and then writes, is enough to turn a rename into lost entries,
# and the list is hand-curated. The new name is written by the next change
# (pin, unpin or reorder), and the old file is then simply left alone; it is
# only ever consulted when the current file does not exist.
PINNED_JSON_KEY = "pinned"
LEGACY_FILE_NAME = "favorites.json"
LEGACY_JSON_KEY = "favorites"


class Pinned:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or pinned_path()
        self._by_id: dict[str, MenuEntry] = {}
        self._order: list[str] = []
        self.load()

    def _source_path(self) -> Path | None:
        """The file to read, or ``None`` when there is nothing to read.

        A file left under the pre-rename name is read where it lies. It is
        never renamed into place: this runs on every watcher pass, and moving
        the user's data as a side effect of *reading* it is how a list ends up
        overwritten by whichever process wrote last.
        """
        if self._path.is_file():
            return self._path
        legacy = self._path.with_name(LEGACY_FILE_NAME)
        if legacy.is_file():
            log.warning(
                "reading %s, which was called %s before the rename; it is left "
                "where it is and the next change writes %s",
                legacy,
                LEGACY_FILE_NAME,
                self._path.name,
            )
            return legacy
        return None

    def load(self) -> None:
        self._by_id.clear()
        self._order.clear()
        path = self._source_path()
        if path is None:
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            log.warning("cannot read pinned %s: %s", path, error)
            return
        if not isinstance(data, dict):
            return
        items = data.get(PINNED_JSON_KEY)
        if items is None:
            # Written before the rename, which stored them under the old key.
            items = data.get(LEGACY_JSON_KEY, [])
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                entry = MenuEntry.from_dict(item)
            except (KeyError, TypeError) as error:
                log.warning("skipping malformed pinned: %s", error)
                continue
            self._by_id[entry.entry_id] = entry
            self._order.append(entry.entry_id)

    def save(self) -> None:
        payload = {
            "version": 1,
            PINNED_JSON_KEY: [
                self._by_id[eid].to_dict() for eid in self._order if eid in self._by_id
            ],
        }
        atomic_write_text(self._path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    def all(self) -> list[MenuEntry]:
        return [self._by_id[eid] for eid in self._order if eid in self._by_id]

    def contains(self, entry_id: str) -> bool:
        return entry_id in self._by_id

    def get(self, entry_id: str) -> MenuEntry | None:
        return self._by_id.get(entry_id)

    def pin(self, entry: MenuEntry) -> bool:
        if entry.entry_id in self._by_id:
            return False
        self._by_id[entry.entry_id] = entry
        self._order.append(entry.entry_id)
        self.save()
        return True

    def unpin(self, entry_id: str) -> bool:
        if entry_id not in self._by_id:
            return False
        del self._by_id[entry_id]
        self._order.remove(entry_id)
        self.save()
        return True

    def move(self, entry_id: str, offset: int) -> bool:
        """Move a pinned entry ``offset`` places; negative is towards the top.

        The stored order is the order the jump list lists the pinned entries in,
        so this is what the manager dialog's up/down buttons change. Returns
        False when the entry is not pinned or cannot move any further in that
        direction, so a caller can tell a no-op from a real change.
        """
        if entry_id not in self._by_id or not offset:
            return False
        try:
            index = self._order.index(entry_id)
        except ValueError:  # pragma: no cover - _by_id and _order move together
            return False
        target = index + offset
        if not 0 <= target < len(self._order):
            return False
        self._order.pop(index)
        self._order.insert(target, entry_id)
        self.save()
        return True
