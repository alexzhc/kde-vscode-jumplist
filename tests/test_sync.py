"""End-to-end sync tests with a fake VS Code installation."""

from __future__ import annotations

import configparser
from pathlib import Path

import pytest
from conftest import CURRENT_SCHEMA_PAYLOAD, make_state_db, parse_raw

from kde_vscode_jumplist.desktop_entry import PINNED_CAPTION_ACTION_ID
from kde_vscode_jumplist.discovery import Installation
from kde_vscode_jumplist.entry_store import EntryStore
from kde_vscode_jumplist.pinned import Pinned
from kde_vscode_jumplist.models import ENTRY_FOLDER, MenuEntry
from kde_vscode_jumplist import sync as sync_module


@pytest.fixture()
def fake_installation(tmp_path: Path, vendor_desktop: Path) -> Installation:
    db = make_state_db(tmp_path / "vscode" / "state.vscdb", CURRENT_SCHEMA_PAYLOAD)
    return Installation(variant="code", executable="/usr/bin/code", desktop_path=vendor_desktop, state_db=db)


def _parse(path: Path) -> configparser.RawConfigParser:
    return parse_raw(path.read_text(encoding="utf-8"))


def test_sync_generates_desktop_and_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_installation: Installation,
    xdg_dirs: dict[str, Path],
) -> None:
    import kde_vscode_jumplist.desktop_entry as de

    apps_dir = xdg_dirs["data"] / "applications"
    monkeypatch.setattr(de, "refresh_service_cache", lambda: None)

    pinned = Pinned(xdg_dirs["config"] / "pinned.json")
    store = EntryStore(xdg_dirs["data"] / "entries.json")

    changed = sync_module.sync_once([fake_installation], pinned, store)
    assert changed is True

    written = apps_dir / "code.desktop"
    assert written.is_file()
    parser = _parse(written)
    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]
    recent = [a for a in actions if a.startswith("KdeVsCodeJumpList-Recent")]
    assert recent, "expected recent entries in the jump list"

    # Entry store resolves every generated action ID.
    store2 = EntryStore(xdg_dirs["data"] / "entries.json")
    store2.load()
    for action in recent:
        entry_id = parser.get(f"Desktop Action {action}", "Exec").split()[-1]
        assert store2.get(entry_id) is not None


def test_sync_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_installation: Installation,
    xdg_dirs: dict[str, Path],
) -> None:
    import kde_vscode_jumplist.desktop_entry as de

    apps_dir = xdg_dirs["data"] / "applications"
    refresh_calls: list[bool] = []
    monkeypatch.setattr(de, "refresh_service_cache", lambda: refresh_calls.append(True))

    pinned = Pinned(xdg_dirs["config"] / "pinned.json")
    store = EntryStore(xdg_dirs["data"] / "entries.json")

    assert sync_module.sync_once([fake_installation], pinned, store) is True
    assert sync_module.sync_once([fake_installation], pinned, store) is False
    assert len(refresh_calls) == 1  # cache refreshed only on actual change


def test_pinned_entries_appear_first_and_survive_history_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_installation: Installation,
    xdg_dirs: dict[str, Path],
) -> None:
    import kde_vscode_jumplist.desktop_entry as de

    apps_dir = xdg_dirs["data"] / "applications"
    monkeypatch.setattr(de, "refresh_service_cache", lambda: None)

    pinned = Pinned(xdg_dirs["config"] / "pinned.json")
    store = EntryStore(xdg_dirs["data"] / "entries.json")

    # Pin an entry that is NOT in the current history.
    gone = MenuEntry(ENTRY_FOLDER, "file:///home/user/old-project", "old-project", "code")
    pinned.pin(gone)

    sync_module.sync_once([fake_installation], pinned, store)
    parser = _parse(apps_dir / "code.desktop")
    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]
    generated = [a for a in actions if a.startswith("KdeVsCodeJumpList-")]
    # Match the per-entry prefix exactly: "KdeVsCodeJumpList-Pinned" is also how
    # the caption's id begins, so a substring test would count the heading too.
    pinned_actions = [a for a in generated if a.startswith("KdeVsCodeJumpList-Pinned-")]
    assert len(pinned_actions) == 1
    # The pinned block comes first, right after the "Pinned Files:" caption.
    assert generated.index(pinned_actions[0]) == generated.index(PINNED_CAPTION_ACTION_ID) + 1

    # The pinned entry is resolvable from the store even though it is not recent.
    store2 = EntryStore(xdg_dirs["data"] / "entries.json")
    store2.load()
    entry_id = parser.get(f"Desktop Action {pinned_actions[0]}", "Exec").split()[-1]
    assert store2.get(entry_id).uri == "file:///home/user/old-project"


def test_shared_state_db_preferred_and_merged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    vendor_desktop: Path,
    xdg_dirs: dict[str, Path],
) -> None:
    """Newer VS Code keeps recents in the shared DB; both sources are merged."""
    import kde_vscode_jumplist.desktop_entry as de

    apps_dir = xdg_dirs["data"] / "applications"
    monkeypatch.setattr(de, "refresh_service_cache", lambda: None)

    # Profile DB has one folder; shared DB has a different one plus a duplicate.
    profile_payload = {
        "entries": [
            {"folderUri": {"$mid": 1, "path": "/home/user/profile-only", "scheme": "file"}},
            {"folderUri": {"$mid": 1, "path": "/home/user/shared-too", "scheme": "file"}},
        ]
    }
    shared_payload = {
        "entries": [
            {"folderUri": {"$mid": 1, "path": "/home/user/shared-only", "scheme": "file"}},
            {"folderUri": {"$mid": 1, "path": "/home/user/shared-too", "scheme": "file"}},
        ]
    }
    profile_db = make_state_db(tmp_path / "profile" / "state.vscdb", profile_payload)
    shared_db = make_state_db(tmp_path / "shared" / "state.vscdb", shared_payload)
    installation = Installation(
        variant="code",
        executable="/usr/bin/code",
        desktop_path=vendor_desktop,
        state_db=profile_db,
        shared_state_db=shared_db,
    )

    store = EntryStore(xdg_dirs["data"] / "entries.json")
    sync_module.sync_once([installation], Pinned(xdg_dirs["config"] / "pinned.json"), store)

    store2 = EntryStore(xdg_dirs["data"] / "entries.json")
    store2.load()
    # Every generated desktop action must resolve in the store.
    parser = _parse(apps_dir / "code.desktop")
    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]
    recent = [a for a in actions if a.startswith("KdeVsCodeJumpList-Recent")]
    uris = [store2.get(parser.get(f"Desktop Action {a}", "Exec").split()[-1]).uri for a in recent]
    assert "file:///home/user/shared-only" in uris
    assert "file:///home/user/profile-only" in uris
    assert uris.count("file:///home/user/shared-too") == 1  # deduplicated


def test_shared_db_only_installation_is_synced(
    vendor_desktop: Path,
    monkeypatch: pytest.MonkeyPatch,
    xdg_dirs: dict[str, Path],
) -> None:
    """Regression: a shared-DB-only install used to crash the whole sync.

    discovery returns an Installation with state_db=None when only VS Code's
    shared database exists, and sync_once used to assert the opposite -- an
    AssertionError out of the timer, logged as "unhandled error".
    """
    import kde_vscode_jumplist.desktop_entry as de

    monkeypatch.setattr(de, "refresh_service_cache", lambda: None)
    shared_db = make_state_db(
        xdg_dirs["home"] / ".vscode-shared" / "sharedStorage" / "state.vscdb",
        CURRENT_SCHEMA_PAYLOAD,
    )
    installation = Installation(
        variant="code",
        executable="/usr/bin/code",
        desktop_path=vendor_desktop,
        state_db=None,
        shared_state_db=shared_db,
    )

    store = EntryStore(xdg_dirs["data"] / "entries.json")
    changed = sync_module.sync_once(
        [installation], Pinned(xdg_dirs["config"] / "pinned.json"), store
    )

    assert changed is True
    menu = (xdg_dirs["data"] / "applications" / "code.desktop").read_text(encoding="utf-8")
    assert "KdeVsCodeJumpList-Recent-" in menu
    # The entry store is populated, so the generated IDs resolve.
    store2 = EntryStore(xdg_dirs["data"] / "entries.json")
    store2.load()
    assert store2.all()


def test_installation_with_no_database_is_skipped(
    vendor_desktop: Path,
    monkeypatch: pytest.MonkeyPatch,
    xdg_dirs: dict[str, Path],
) -> None:
    """No databases at all: skip that installation rather than failing the run."""
    import kde_vscode_jumplist.desktop_entry as de

    monkeypatch.setattr(de, "refresh_service_cache", lambda: None)
    installation = Installation(
        variant="code",
        executable="/usr/bin/code",
        desktop_path=vendor_desktop,
        state_db=None,
        shared_state_db=None,
    )

    store = EntryStore(xdg_dirs["data"] / "entries.json")
    assert sync_module.sync_once([installation], Pinned(), store) is False
    assert not (xdg_dirs["data"] / "applications" / "code.desktop").exists()


def test_lock_prevents_concurrent_sync(
    monkeypatch: pytest.MonkeyPatch, xdg_dirs: dict[str, Path]
) -> None:
    from kde_vscode_jumplist.paths import lock_path

    lock_file = lock_path()
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    # Simulate a live process holding the lock.
    import os

    lock_file.write_text(str(os.getpid()), encoding="utf-8")
    assert sync_module.run_locked_sync() is False

    # Stale lock (dead PID) is reclaimed.
    lock_file.write_text("999999999", encoding="utf-8")
    monkeypatch.setattr(sync_module, "sync_once", lambda: False)
    assert sync_module.run_locked_sync() is False  # ran (returned False), lock cleaned
    assert not lock_file.exists()
