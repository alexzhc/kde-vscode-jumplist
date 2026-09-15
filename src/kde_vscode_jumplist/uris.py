"""URI helpers for values stored by VS Code."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

# The scheme a URI uses when it names something on this machine.
LOCAL_SCHEME = "file"
# Authorities that still mean "this machine". ``file://localhost/x`` is the one
# legal spelling of a local file URI that carries an authority at all; anything
# else (``file://buildhost/x``) names another machine's filesystem.
LOCAL_AUTHORITIES = frozenset({"", "localhost"})


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


def local_path(uri: str) -> Path | None:
    """The file on this machine a URI names, or ``None`` when it names none.

    Only a ``file:`` URI has one. A remote entry's path is a path on *another*
    machine, so it is deliberately not returned: writing ``/srv/app`` on the
    clipboard for something that lives on a build host would look like a local
    path and be wrong, and there is nothing on this machine to show in a file
    manager either. Callers use ``None`` to grey out the rows that would need
    one rather than to guess.

    The percent-encoding is decoded, so the result is a path that can be handed
    to a file manager or pasted into a shell as it stands.
    """
    try:
        parts = urlsplit(uri)
    except ValueError:
        return None
    if parts.scheme != LOCAL_SCHEME or parts.netloc not in LOCAL_AUTHORITIES:
        return None
    path = unquote(parts.path or "")
    # A file URI is absolute by definition (RFC 8089); a relative one is not
    # something to resolve against whatever directory this happens to run in.
    if not path.startswith("/"):
        return None
    return Path(path)
