"""Tests for vscode_history parsing (current + legacy schema, remotes, junk)."""

from __future__ import annotations

from pathlib import Path

from conftest import CURRENT_SCHEMA_PAYLOAD, LEGACY_SCHEMA_PAYLOAD, make_state_db

from kde_vscode_jumplist.models import ENTRY_FILE, ENTRY_FOLDER, ENTRY_WORKSPACE
from kde_vscode_jumplist.vscode_history import parse_recently_opened, read_recent_entries


def test_current_schema_entries() -> None:
    entries = parse_recently_opened(CURRENT_SCHEMA_PAYLOAD, "code")
    kinds = [(e.kind, e.uri) for e in entries]
    assert (ENTRY_FOLDER, "file:///home/user/project") in kinds
    assert (ENTRY_WORKSPACE, "file:///home/user/proj/app.code-workspace") in kinds
    assert (ENTRY_FILE, "file:///home/user/notes.md") in kinds
    # COMMIT_EDITMSG is filtered like VS Code does.
    assert not any("COMMIT_EDITMSG" in uri for _, uri in kinds)


def test_remote_folder_uri_preserved() -> None:
    entries = parse_recently_opened(CURRENT_SCHEMA_PAYLOAD, "code")
    remote = next(e for e in entries if e.remote)
    assert remote.uri == "vscode-remote://ssh-remote+devbox/srv/code/remote-proj"
    assert remote.kind == ENTRY_FOLDER
    assert remote.label == "remote-proj [SSH: devbox]"


def test_legacy_schema() -> None:
    entries = parse_recently_opened(LEGACY_SCHEMA_PAYLOAD, "code")
    uris = {e.uri for e in entries}
    assert "file:///home/user/legacy-folder" in uris
    assert "file:///home/user/legacy.code-workspace" in uris
    assert "file:///home/user/legacy.txt" in uris


def test_malformed_records_skipped() -> None:
    payload = {
        "entries": [
            None,
            42,
            {"folderUri": None},
            {"fileUri": {}},
            {"workspace": "not-a-dict"},
            {"fileUri": "file:///ok.txt"},
        ]
    }
    entries = parse_recently_opened(payload, "code")
    assert [e.uri for e in entries] == ["file:///ok.txt"]


def test_duplicates_deduplicated_preserving_order() -> None:
    payload = {
        "entries": [
            {"fileUri": "file:///a.txt"},
            {"fileUri": "file:///b.txt"},
            {"fileUri": "file:///a.txt"},
        ]
    }
    entries = parse_recently_opened(payload, "code")
    assert [e.uri for e in entries] == ["file:///a.txt", "file:///b.txt"]


def test_read_from_sqlite_db(tmp_path: Path) -> None:
    db = make_state_db(tmp_path / "state.vscdb", CURRENT_SCHEMA_PAYLOAD)
    entries = read_recent_entries(db, "code")
    assert entries and entries[0].source == "code"


def test_missing_db_returns_empty(tmp_path: Path) -> None:
    assert read_recent_entries(tmp_path / "nope.vscdb", "code") == []


def test_corrupt_db_returns_empty(tmp_path: Path) -> None:
    db = tmp_path / "corrupt.vscdb"
    db.write_bytes(b"this is not sqlite")
    assert read_recent_entries(db, "code") == []
