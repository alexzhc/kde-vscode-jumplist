"""URI helpers for values stored by VS Code."""

from __future__ import annotations

from urllib.parse import quote, unquote, urlsplit


def uri_from_stored(value: object) -> str | None:
    """Convert a value from VS Code storage into a URI string.

    VS Code stores URIs either as plain strings or as serialized URI objects
    (``{"$mid": 1, "scheme": ..., "authority": ..., "path": ...}``). Remote
    URIs such as ``vscode-remote://ssh-remote+host/path`` keep their authority.
    """
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        scheme = value.get("scheme")
        if not isinstance(scheme, str) or not scheme:
            return None
        authority = value.get("authority")
        authority = authority if isinstance(authority, str) else ""
        path = value.get("path")
        path = path if isinstance(path, str) else ""
        if path and not path.startswith("/"):
            path = "/" + path
        uri = f"{scheme}://{authority}{quote(path, safe='/')}"
        query = value.get("query")
        if isinstance(query, str) and query:
            uri += "?" + query
        fragment = value.get("fragment")
        if isinstance(fragment, str) and fragment:
            uri += "#" + fragment
        return uri
    return None


def uri_display_name(uri: str) -> str:
    """Human readable name for a URI: last path segment, else authority."""
    try:
        parts = urlsplit(uri)
    except ValueError:
        return uri
    path = unquote(parts.path or "")
    name = path.rstrip("/").rsplit("/", 1)[-1]
    if name:
        return name
    if parts.netloc:
        return parts.netloc
    return uri
