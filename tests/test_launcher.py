"""Tests for launcher command construction (argv safety, remote URIs)."""

from __future__ import annotations

import pytest

from kde_vscode_jumplist import launcher
from kde_vscode_jumplist.discovery import Installation
from kde_vscode_jumplist.launcher import build_command
from kde_vscode_jumplist.models import ENTRY_FILE, ENTRY_FOLDER, ENTRY_WORKSPACE, MenuEntry


def _install(**kwargs: object) -> Installation:
    defaults: dict = {"variant": "code", "executable": "/usr/bin/code"}
    defaults.update(kwargs)
    return Installation(**defaults)  # type: ignore[arg-type]


def test_folder_uses_folder_uri() -> None:
    entry = MenuEntry(ENTRY_FOLDER, "file:///home/user/proj", "proj", "code")
    assert build_command(_install(), entry) == ["/usr/bin/code", "--folder-uri", "file:///home/user/proj"]


def test_file_uses_file_uri() -> None:
    entry = MenuEntry(ENTRY_FILE, "file:///home/user/notes.md", "notes.md", "code")
    assert build_command(_install(), entry) == ["/usr/bin/code", "--file-uri", "file:///home/user/notes.md"]


def test_workspace_uses_file_uri() -> None:
    entry = MenuEntry(ENTRY_WORKSPACE, "file:///w.code-workspace", "w", "code")
    assert build_command(_install(), entry) == ["/usr/bin/code", "--file-uri", "file:///w.code-workspace"]


def test_remote_ssh_uri_passed_verbatim() -> None:
    uri = "vscode-remote://ssh-remote+devbox/srv/code/proj"
    entry = MenuEntry(ENTRY_FOLDER, uri, "proj [SSH]", "code", remote=True)
    command = build_command(_install(), entry)
    assert command == ["/usr/bin/code", "--folder-uri", uri]


def test_flatpak_local_path_forwarding() -> None:
    installation = _install(executable=None, flatpak_app="com.visualstudio.code")
    entry = MenuEntry(ENTRY_FILE, "file:///home/user/notes.md", "notes.md", "code")
    command = build_command(installation, entry)
    assert command == [
        "flatpak", "run", "--file-forwarding", "com.visualstudio.code",
        "@@", "/home/user/notes.md", "@@",
    ]


def test_flatpak_remote_keeps_uri_flag() -> None:
    installation = _install(executable=None, flatpak_app="com.visualstudio.code")
    uri = "vscode-remote://ssh-remote+box/srv/proj"
    entry = MenuEntry(ENTRY_FOLDER, uri, "proj", "code", remote=True)
    command = build_command(installation, entry)
    assert command[-2:] == ["--folder-uri", uri]


def test_unknown_kind_raises() -> None:
    entry = MenuEntry("bogus", "file:///x", "x", "code")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        build_command(_install(), entry)


def test_no_shell_metacharacters_in_argv() -> None:
    """URIs with spaces/quotes stay single argv elements (no shell involved)."""
    uri = "file:///home/user/My Projects/odd'name & stuff"
    entry = MenuEntry(ENTRY_FOLDER, uri, "odd", "code")
    command = build_command(_install(), entry)
    assert command[-1] == uri  # one element, unescaped


def test_open_resolves_the_entrys_own_editor_across_forks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A click opens the editor the entry came from, fork selection or not.

    Plasma launches a menu action with no FORK of its own, so resolving an
    entry cannot be limited to the selected fork: a CodeBuddy entry must open
    in CodeBuddy even when nothing selected that fork.
    """
    buddy = _install(variant="codebuddycn", executable="/usr/bin/buddycn")
    code = _install(variant="code")
    entry = MenuEntry(ENTRY_FOLDER, "file:///home/user/proj", "proj", "codebuddycn")
    monkeypatch.setattr(launcher, "discover_all_installations", lambda: [code, buddy])
    launched: list[list[str]] = []

    def record(command: list[str]) -> int:
        launched.append(command)
        return 0

    monkeypatch.setattr(launcher, "_spawn", record)

    assert launcher.open_entry(entry) == 0

    assert launched == [["/usr/bin/buddycn", "--folder-uri", "file:///home/user/proj"]]
