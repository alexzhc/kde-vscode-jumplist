"""Persisted registry mapping stable entry IDs to full entries.

Generated desktop actions reference entries only by ID; the launcher
resolves the URI from this store. This keeps desktop files short and
avoids embedding URIs in shell-visible Exec lines.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .models import MenuEntry
from .paths import entries_path
from .util import atomic_write_text

log = logging.getLogger(__name__)


class EntryStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or entries_path()
        self._entries: dict[str, MenuEntry] = {}

    def update(self, entries: list[MenuEntry]) -> None:
        """Replace the store with ``entries``, keyed by entry id.

        Serialized from the mapping rather than from the argument: a list may
        name the same entry twice (a pinned entry is also a recent one), and
        writing those duplicates verbatim is pure waste -- each one is a second
        copy of the same record, and nothing can ever read the difference.

        The write is skipped when the content has not changed. This runs every
        few seconds under `update --watch`, so an unconditional write would
        rewrite an identical file all day for nothing.
        """
        self._entries = {entry.entry_id: entry for entry in entries}
        payload = {
            "version": 1,
            "entries": [entry.to_dict() for entry in self._entries.values()],
        }
        text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        if self._path.is_file() and self._path.read_text(encoding="utf-8") == text:
            return  # identical: leave the file, and its mtime, alone
        atomic_write_text(self._path, text)

    def load(self) -> None:
        self._entries.clear()
        if not self._path.is_file():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            log.warning("cannot read entry store %s: %s", self._path, error)
            return
        for item in data.get("entries", []) if isinstance(data, dict) else []:
            if not isinstance(item, dict):
                continue
            try:
                entry = MenuEntry.from_dict(item)
            except (KeyError, TypeError):
                continue
            self._entries[entry.entry_id] = entry

    def get(self, entry_id: str) -> MenuEntry | None:
        if not self._entries:
            self.load()
        return self._entries.get(entry_id)

    def all(self) -> list[MenuEntry]:
        """Every stored entry, in store order (recents first, then pinned)."""
        if not self._entries:
            self.load()
        return list(self._entries.values())
