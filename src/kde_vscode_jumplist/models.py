"""Normalized data model for menu entries."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import uris

ENTRY_FILE = "file"
ENTRY_FOLDER = "folder"
ENTRY_WORKSPACE = "workspace"


@dataclass(frozen=True)
class MenuEntry:
    """A single recent/pinned item shown in the KDE jump list."""

    kind: str  # ENTRY_FILE | ENTRY_FOLDER | ENTRY_WORKSPACE
    uri: str
    label: str
    source: str  # installation id, e.g. "code", "code-insiders"
    remote: bool = False

    @property
    def entry_id(self) -> str:
        """Stable hashed identifier safe for desktop-file action names."""
        digest = hashlib.sha256(f"{self.kind}|{self.uri}".encode("utf-8")).hexdigest()
        return digest[:16]

    @property
    def local_path(self) -> Path | None:
        """Where this entry lives on this machine, or ``None`` for a remote one."""
        return uris.local_path(self.uri)

    @property
    def local_folder(self) -> Path | None:
        """The directory to show for this entry, or ``None`` for a remote one.

        A folder entry is itself a directory; a file or a workspace config sits
        in one, so the directory holding it is the one to show. The distinction
        follows from the kind, which is why it lives here rather than in the
        dialog: every caller that opens a folder means the same thing by it.
        """
        path = self.local_path
        if path is None:
            return None
        return path if self.kind == ENTRY_FOLDER else path.parent

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "uri": self.uri,
            "label": self.label,
            "source": self.source,
            "remote": self.remote,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MenuEntry":
        uri = data["uri"]
        return cls(
            kind=data.get("kind", ENTRY_FILE),
            uri=uri,
            label=data.get("label") or uris.uri_display_name(uri),
            source=data.get("source", "code"),
            remote=bool(data.get("remote", False)),
        )
