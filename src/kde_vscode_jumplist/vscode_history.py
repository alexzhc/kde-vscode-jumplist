"""Read VS Code's recent entries from its SQLite state database.

The database is opened read-only in URI mode so WAL files and a running
VS Code instance do not cause problems. Only the JSON value of
``history.recentlyOpenedPathsList`` is consumed; the database is never
modified.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from .models import ENTRY_FILE, ENTRY_FOLDER, ENTRY_WORKSPACE, MenuEntry
from .uris import uri_display_name, uri_from_stored

log = logging.getLogger(__name__)

STORAGE_KEY = "history.recentlyOpenedPathsList"

# Files VS Code itself excludes from dock/taskbar jump lists.
COMMON_FILES_FILTER = {"COMMIT_EDITMSG", "MERGE_MSG", "git-rebase-todo"}

# vscode-remote for VS Code itself; forks derive their own scheme the same way
# from their product.json urlProtocol (vscodium-remote, codebuddycn-remote...).
REMOTE_SCHEMES = {"vscode-remote", "vscode-vfs", "codebuddycn-remote"}


def _read_raw_json(db_path: Path) -> dict | None:
    uri = f"file:{db_path}?mode=ro&immutable=0"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=1.0)
    except sqlite3.Error as error:
        log.warning("cannot open %s: %s", db_path, error)
        return None
    try:
        row = connection.execute(
            "SELECT value FROM ItemTable WHERE key = ?", (STORAGE_KEY,)
        ).fetchone()
    except sqlite3.Error as error:
        log.warning("cannot query %s: %s", db_path, error)
        return None
    finally:
        connection.close()
    if not row or not isinstance(row[0], (str, bytes)):
        return None
    try:
        data = json.loads(row[0])
    except (ValueError, UnicodeDecodeError) as error:
        log.warning("malformed JSON in %s: %s", db_path, error)
        return None
    return data if isinstance(data, dict) else None


def _is_remote(uri: str) -> bool:
    return uri.split(":", 1)[0] in REMOTE_SCHEMES


def _entry_from_item(item: dict, kind: str, uri_key: str, source: str) -> MenuEntry | None:
    uri = uri_from_stored(item.get(uri_key))
    if not uri:
        return None
    label = item.get("label")
    if not isinstance(label, str) or not label:
        label = uri_display_name(uri)
    return MenuEntry(kind=kind, uri=uri, label=label, source=source, remote=_is_remote(uri))


def parse_recently_opened(data: dict, source: str) -> list[MenuEntry]:
    """Parse the stored JSON into normalized entries, preserving MRU order.

    Malformed records are skipped. The current schema keeps folders,
    workspaces and files interleaved in one ``entries`` list ordered by most
    recent use; the legacy schema lists workspaces first, then files.
    """
    entries: list[MenuEntry] = []
    seen: set[str] = set()

    def add(entry: MenuEntry | None) -> None:
        if entry is None:
            return
        key = f"{entry.kind}|{entry.uri}"
        if key in seen:
            return
        seen.add(key)
        entries.append(entry)

    for item in data.get("entries", []) or []:
        if not isinstance(item, dict):
            continue
        if "workspace" in item:
            workspace = item["workspace"]
            if isinstance(workspace, dict):
                add(_entry_from_item(workspace, ENTRY_WORKSPACE, "configPath", source))
        elif "folderUri" in item:
            add(_entry_from_item(item, ENTRY_FOLDER, "folderUri", source))
        elif "fileUri" in item:
            entry = _entry_from_item(item, ENTRY_FILE, "fileUri", source)
            if entry is not None:
                name = entry.uri.rstrip("/").rsplit("/", 1)[-1]
                if name in COMMON_FILES_FILTER:
                    continue
            add(entry)

    # Legacy schema: separate "workspaces" and "files" lists.
    for item in data.get("workspaces", []) or []:
        if isinstance(item, dict):
            if "workspace" in item and isinstance(item["workspace"], dict):
                add(_entry_from_item(item["workspace"], ENTRY_WORKSPACE, "configPath", source))
            elif "configPath" in item:
                add(_entry_from_item(item, ENTRY_WORKSPACE, "configPath", source))
            elif "folderUri" in item:
                add(_entry_from_item(item, ENTRY_FOLDER, "folderUri", source))
    for item in data.get("files", []) or []:
        if isinstance(item, dict):
            add(_entry_from_item(item, ENTRY_FILE, "fileUri", source))

    return entries


def read_recent_entries(db_path: Path, source: str) -> list[MenuEntry]:
    """Read and parse recent entries from one VS Code state database."""
    data = _read_raw_json(db_path)
    if data is None:
        return []
    return parse_recently_opened(data, source)
