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

# The key the list is stored under inside the file.
PINNED_JSON_KEY = "pinned"


class Pinned:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or pinned_path()
        self._by_id: dict[str, MenuEntry] = {}
        self._order: list[str] = []
        self.load()

    def load(self) -> None:
        """Read the list from disk, replacing whatever is in memory.

        A read never writes. This runs on every watcher pass, so moving or
        deleting the user's own file as a side effect of reading it is how a
        list ends up replaced by whichever process wrote last.
        """
        self._by_id.clear()
        self._order.clear()
        if not self._path.is_file():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            log.warning("cannot read pinned %s: %s", self._path, error)
            return
        if not isinstance(data, dict):
            return
        for item in data.get(PINNED_JSON_KEY, []):
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
