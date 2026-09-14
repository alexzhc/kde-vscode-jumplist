"""Normalized data model for menu entries."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .uris import uri_display_name

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
            label=data.get("label") or uri_display_name(uri),
            source=data.get("source", "code"),
            remote=bool(data.get("remote", False)),
        )
