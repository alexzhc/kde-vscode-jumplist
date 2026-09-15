"""Tests for pinned persistence and the entry store."""

from __future__ import annotations

import json
from pathlib import Path

from kde_vscode_jumplist.entry_store import EntryStore
from kde_vscode_jumplist.pinned import Pinned
from kde_vscode_jumplist.models import ENTRY_FILE, ENTRY_FOLDER, MenuEntry


def _entry(uri: str = "file:///home/user/proj", kind: str = ENTRY_FOLDER) -> MenuEntry:
    return MenuEntry(kind=kind, uri=uri, label="proj", source="code")


def test_pin_unpin_persists(tmp_path: Path) -> None:
    path = tmp_path / "pinned.json"
    pinned = Pinned(path)
    entry = _entry()
    assert pinned.pin(entry) is True
    assert pinned.pin(entry) is False  # idempotent

    reloaded = Pinned(path)
    assert reloaded.contains(entry.entry_id)
    assert reloaded.all() == [entry]

    assert reloaded.unpin(entry.entry_id) is True
    assert Pinned(path).all() == []


def test_pinned_survive_history_removal(tmp_path: Path) -> None:
    path = tmp_path / "pinned.json"
    pinned = Pinned(path)
    pinned.pin(_entry("file:///gone/forever"))
    # Even with no recent entries at all, pinned remain listed.
    assert [e.uri for e in Pinned(path).all()] == ["file:///gone/forever"]


def test_corrupt_pinned_file_ignored(tmp_path: Path) -> None:
    path = tmp_path / "pinned.json"
    path.write_text("{not json", encoding="utf-8")
    assert Pinned(path).all() == []


def test_a_file_under_another_name_is_not_read(tmp_path: Path) -> None:
    """Only ``pinned.json`` is the list; a similarly named file is not ours.

    The name is the whole of the agreement about where the list lives, so a
    file called something else -- whatever it contains -- is left alone.
    """
    entry = _entry("file:///home/user/other")
    (tmp_path / "favorites.json").write_text(
        json.dumps({"version": 1, "favorites": [entry.to_dict()]}), encoding="utf-8"
    )

    assert Pinned(tmp_path / "pinned.json").all() == []


def test_entry_store_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "entries.json"
    entries = [_entry(), _entry("file:///f.txt", ENTRY_FILE)]
    store = EntryStore(path)
    store.update(entries)

    reloaded = EntryStore(path)
    reloaded.load()
    assert reloaded.get(entries[0].entry_id) == entries[0]
    assert reloaded.get(entries[1].entry_id) == entries[1]
    assert reloaded.get("deadbeef") is None


def test_entry_ids_are_stable_and_unique() -> None:
    a = _entry("file:///a")
    b = _entry("file:///b")
    assert a.entry_id == _entry("file:///a").entry_id
    assert a.entry_id != b.entry_id
    assert len(a.entry_id) == 16


def test_entry_store_writes_each_entry_once(tmp_path: Path) -> None:
    """A repeated entry must not be stored twice.

    sync passes the recents and then appends the pinned, so a pinned entry
    appears twice in that list. Writing both copies put a duplicate record in
    entries.json for every pin -- harmless to read, but pure waste, and the one
    thing that made the file grow with pinning.
    """
    path = tmp_path / "entries.json"
    pinned = _entry("file:///pinned")
    entries = [_entry("file:///a"), pinned, _entry("file:///b"), pinned]

    store = EntryStore(path)
    store.update(entries)

    written = json.loads(path.read_text(encoding="utf-8"))["entries"]
    ids = [MenuEntry.from_dict(item).entry_id for item in written]
    assert len(written) == 3  # not 4
    assert len(set(ids)) == len(ids), "duplicate records in the store"
    assert ids.count(pinned.entry_id) == 1

    # And the in-memory view agrees with the file.
    assert [e.entry_id for e in EntryStore(path).all()].count(pinned.entry_id) == 1


def test_entry_store_skips_an_identical_write(tmp_path: Path) -> None:
    """Unchanged content must not rewrite the file.

    `update --watch` calls this every few seconds, so an unconditional write
    would rewrite an identical file (and bump its mtime) all day.
    """
    path = tmp_path / "entries.json"
    entries = [_entry("file:///a"), _entry("file:///b")]

    store = EntryStore(path)
    store.update(entries)
    first = path.stat().st_mtime_ns
    assert json.loads(path.read_text())["entries"]

    store.update([_entry("file:///a"), _entry("file:///b")])
    assert path.stat().st_mtime_ns == first, "rewrote an unchanged store"

    # A real change still writes.
    store.update([*entries, _entry("file:///c")])
    assert path.stat().st_mtime_ns != first
    assert len(json.loads(path.read_text())["entries"]) == 3


def _pinned(tmp_path: Path, count: int) -> tuple[Pinned, list[MenuEntry]]:
    """A pinned file with ``count`` entries, in pin order."""
    pinned = Pinned(tmp_path / "pinned.json")
    entries = [_entry(f"file:///p{index}") for index in range(count)]
    for entry in entries:
        pinned.pin(entry)
    return pinned, entries


def test_move_reorders_and_persists(tmp_path: Path) -> None:
    """Up/down in the manager changes the stored order the menu lists."""
    path = tmp_path / "pinned.json"
    pinned, entries = _pinned(tmp_path, 3)
    first, second, third = (e.entry_id for e in entries)

    assert pinned.move(third, -1) is True
    assert [e.entry_id for e in pinned.all()] == [first, third, second]

    # The order is persisted, not just held in memory.
    assert [e.entry_id for e in Pinned(path).all()] == [first, third, second]


def test_move_refuses_to_wrap_around(tmp_path: Path) -> None:
    """The ends are hard stops, so a held-down button cannot reorder wildly."""
    pinned, entries = _pinned(tmp_path, 3)
    order = [e.entry_id for e in pinned.all()]

    assert pinned.move(entries[0].entry_id, -1) is False
    assert pinned.move(entries[-1].entry_id, 1) is False
    # An offset larger than the list is refused rather than clamped.
    assert pinned.move(entries[0].entry_id, 99) is False
    assert [e.entry_id for e in pinned.all()] == order


def test_move_ignores_unknown_and_noop_offsets(tmp_path: Path) -> None:
    pinned, entries = _pinned(tmp_path, 2)
    assert pinned.move("deadbeef", -1) is False
    assert pinned.move(entries[0].entry_id, 0) is False


def test_move_is_a_single_step_for_larger_offsets(tmp_path: Path) -> None:
    """An offset of 2 is still one move of two places, not two writes."""
    pinned, entries = _pinned(tmp_path, 3)
    assert pinned.move(entries[0].entry_id, 2) is True
    assert [e.entry_id for e in pinned.all()] == [
        entries[1].entry_id,
        entries[2].entry_id,
        entries[0].entry_id,
    ]
