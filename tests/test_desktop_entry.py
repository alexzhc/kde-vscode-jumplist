"""Tests for desktop-entry generation (golden-style structural checks)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import VENDOR_DESKTOP, parse_raw

from kde_vscode_jumplist.desktop_entry import (
    ACTION_PREFIX,
    DEFAULT_ICON,
    PINNED_ICON,
    PINNED_CAPTION_ACTION_ID,
    PINNED_CAPTION_BASE,
    PINNED_CAPTION_ICON,
    PINNED_CAPTION_MARKER,
    PINNED_CAPTION_PAD_FREE,
    PINNED_CAPTION_PAD_RATIO,
    RECENT_CAPTION_ACTION_ID,
    RECENT_CAPTION_ICON,
    RECENT_CAPTION_TEXT,
    TOTAL_ENTRIES,
    build_desktop_content,
    desktop_launcher_command,
    excluded_kinds,
    pinned_caption_text,
    format_exec,
    keep_recent,
)
from kde_vscode_jumplist.models import ENTRY_FILE, ENTRY_FOLDER, ENTRY_WORKSPACE, MenuEntry


# Shared parser from conftest: same pitfalls as the unit files.
_parse = parse_raw


def _expected_heading(pinned: list[MenuEntry], recents: list[MenuEntry]) -> str:
    """The heading the generator will write for these entries.

    Its padding is sized off the longest label that gets listed, so it cannot be
    a module constant; this mirrors the same arithmetic to compare against.
    """
    listed_pinned = pinned[:TOTAL_ENTRIES]
    listed_recents = keep_recent(recents)[: max(0, TOTAL_ENTRIES - len(listed_pinned))]
    longest = max((len(e.label) for e in (*listed_pinned, *listed_recents)), default=0)
    return pinned_caption_text(longest)


# The two section headings. Neither is a per-entry action, so they are filtered
# out wherever the entry actions themselves are being counted.
CAPTION_IDS = (PINNED_CAPTION_ACTION_ID, RECENT_CAPTION_ACTION_ID)
NON_ENTRY_IDS = CAPTION_IDS

# The action id an earlier version wrote for its dedicated "Manage Pinned
# Files…" entry. It is no longer generated, but files carrying it still have to
# be cleaned up, so it is named here rather than imported.
LEGACY_MANAGE_ACTION_ID = ACTION_PREFIX + "Manage"


def _entries() -> tuple[list[MenuEntry], list[MenuEntry]]:
    """One pinned folder and a mixed recents list.

    The recents deliberately include a workspace: that is the kind the menu
    excludes by default, so the structural tests exercise the filter too.
    """
    pinned = [MenuEntry(ENTRY_FOLDER, "file:///pinned", "pinned-proj", "code")]
    recents = [
        MenuEntry(ENTRY_FOLDER, "file:///proj", "proj", "code"),
        MenuEntry(ENTRY_WORKSPACE, "file:///w.code-workspace", "w (Workspace)", "code"),
        MenuEntry(ENTRY_FILE, "file:///notes.md", "notes.md", "code"),
        MenuEntry(ENTRY_FILE, "vscode-remote://ssh-remote+box/srv/f.py", "f.py", "code", remote=True),
    ]
    return pinned, recents


def test_generated_actions_structure() -> None:
    pinned, recents = _entries()
    parser = _parse(build_desktop_content(VENDOR_DESKTOP, pinned, recents))

    actions = parser.get("Desktop Entry", "Actions").split(";")
    actions = [a for a in actions if a]

    # Vendor action preserved, ours appended after it.
    assert actions[0] == "new-empty-window"
    generated = [
        a for a in actions if a.startswith(ACTION_PREFIX) and a not in NON_ENTRY_IDS
    ]
    assert len(generated) == 4  # 1 pinned + 3 recents (the workspace is excluded)
    workspace = next(e for e in recents if e.kind == ENTRY_WORKSPACE)
    assert f"KdeVsCodeJumpList-Recent-{workspace.entry_id}" not in actions

    # Pinned last: the pinned block is listed after the recents (PINNED_BELOW).
    pinned = [a for a in generated if "Pinned" in a]
    assert len(pinned) == 1 and generated[-1] == pinned[0]

    for action in generated:
        # KService only recognizes "[Desktop Action <id>]" groups and skips
        # actions with NoDisplay=true, so neither may be present.
        section = f"Desktop Action {action}"
        assert parser.has_section(section)
        assert not parser.has_option(section, "NoDisplay")
        assert not parser.has_option(section, "Type")
        assert parser.has_option(section, "Name")
    for action in generated:
        section = f"Desktop Action {action}"
        assert parser.get(section, "Exec").endswith(f"open {action.rsplit('-', 1)[-1]}")
    # The list ends with a separator, so Plasma's own task-manager entries
    # follow a line rather than butting up against our last entry.
    assert actions[-1] == "_SEPARATOR_"
    assert actions[-2] == pinned[0]


def test_exec_uses_entry_ids_not_uris() -> None:
    pinned, recents = _entries()
    content = build_desktop_content(VENDOR_DESKTOP, pinned, recents)
    parser = _parse(content)
    listed = [e for e in recents if e.kind != ENTRY_WORKSPACE]
    for entry in pinned + listed:
        assert entry.uri not in content
        found = False
        for section in parser.sections():
            if parser.has_option(section, "Exec") and entry.entry_id in parser.get(section, "Exec"):
                found = True
        assert found, f"no Exec references {entry.entry_id}"

    # The excluded workspace must not be referenced at all.
    workspace = next(e for e in recents if e.kind == ENTRY_WORKSPACE)
    assert workspace.entry_id not in content
    assert workspace.uri not in content


def test_vendor_identity_preserved() -> None:
    pinned, recents = _entries()
    parser = _parse(build_desktop_content(VENDOR_DESKTOP, pinned, recents))
    assert parser.get("Desktop Entry", "Exec") == "/usr/share/code/code --unity-launch %F"
    assert parser.get("Desktop Entry", "StartupWMClass") == "Code"
    assert parser.get("Desktop Entry", "Icon") == "vscode"
    assert "inode/directory" in parser.get("Desktop Entry", "MimeType")


def test_regeneration_removes_stale_actions() -> None:
    pinned, recents = _entries()
    first = build_desktop_content(VENDOR_DESKTOP, pinned, recents)
    # Second pass with fewer entries must not leave stale groups behind.
    second = build_desktop_content(first, [], recents[:1])
    parser = _parse(second)
    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]
    generated = [
        a for a in actions if a.startswith(ACTION_PREFIX) and a not in NON_ENTRY_IDS
    ]
    assert len(generated) == 1
    for section in parser.sections():
        if section.startswith(f"Desktop Action {ACTION_PREFIX}") and not any(
            section == f"Desktop Action {name}" for name in NON_ENTRY_IDS
        ):
            assert section == f"Desktop Action {generated[0]}"


def test_an_older_files_manage_action_is_dropped() -> None:
    """The separate Manage entry is gone, replaced by the pinned heading.

    A menu an earlier version generated still names it in Actions= and defines
    its group, so regeneration has to take both away rather than leave two ways
    into the dialog.
    """
    from kde_vscode_jumplist.desktop_entry import GENERATED_MARKER

    old = GENERATED_MARKER + "\n" + VENDOR_DESKTOP.replace(
        "Actions=new-empty-window;",
        f"Actions=new-empty-window;{LEGACY_MANAGE_ACTION_ID};",
    ) + (
        f"\n[Desktop Action {LEGACY_MANAGE_ACTION_ID}]\n"
        "Name=Manage Pinned Files…\nIcon=bookmark-new\n"
        "Exec=/usr/bin/python3 -m kde_vscode_jumplist manage\n"
    )

    _, recents = _entries()
    content = build_desktop_content(old, [], recents)
    parser = _parse(content)
    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]

    assert LEGACY_MANAGE_ACTION_ID not in actions
    assert not parser.has_section(f"Desktop Action {LEGACY_MANAGE_ACTION_ID}")
    # The way in is the pinned heading now, and it carries the verified launcher
    # the other actions use rather than a stale hard-coded interpreter.
    heading = f"Desktop Action {PINNED_CAPTION_ACTION_ID}"
    assert parser.get(heading, "Exec") == f"{desktop_launcher_command()} manage"
    assert "/usr/bin/python3 -m" not in content


def test_a_fork_menu_bakes_the_fork_into_every_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FORK=BUDDY prefixes every Exec line with ``env FORK=BUDDY``.

    Plasma launches menu actions with no environment of ours, so a CodeBuddy
    menu action would otherwise resolve the click against the VS Code family's
    entry cache and pinned list.
    """
    import kde_vscode_jumplist.desktop_entry as de

    monkeypatch.setenv("FORK", "BUDDY")
    monkeypatch.setattr(
        de, "resolve_cli_argv", lambda python=None: ["/usr/bin/kde-vscode-jumplist"]
    )
    monkeypatch.setattr(de.shutil, "which", lambda name: "/usr/bin/env")

    pinned, recents = _entries()
    parser = _parse(de.build_desktop_content(VENDOR_DESKTOP, pinned, recents))

    prefix = "/usr/bin/env FORK=BUDDY /usr/bin/kde-vscode-jumplist"
    assert de.desktop_launcher_command() == prefix
    # Both headings open the manager, every entry action opens its entry, and
    # none of them may launch without the fork.
    heading = f"Desktop Action {PINNED_CAPTION_ACTION_ID}"
    assert parser.get(heading, "Exec") == f"{prefix} manage"
    for section in parser.sections():
        if not section.startswith("Desktop Action KdeVsCodeJumpList-"):
            continue
        exec_line = parser.get(section, "Exec")
        assert exec_line.startswith(prefix)
        assert exec_line.endswith(" manage") or " open " in exec_line


def test_legacy_actions_are_replaced() -> None:
    """Pre-0.2 files used '[KdeVsCodeMenu Recent <id>]' groups with spaces.

    KService ignores those; regeneration must drop both the stale ids from
    Actions= and the legacy groups.
    """
    legacy = VENDOR_DESKTOP.replace(
        "Actions=new-empty-window;",
        "Actions=new-empty-window;KdeVsCodeMenu Recent abc123;",
    ) + "\n[KdeVsCodeMenu Recent abc123]\nName=old\nExec=kde-vscode-menu open abc123\n"
    _, recents = _entries()
    content = build_desktop_content(legacy, [], recents)
    parser = _parse(content)
    assert not any(s.startswith("KdeVsCodeMenu ") for s in parser.sections())
    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]
    assert not any(a.startswith("KdeVsCodeMenu ") for a in actions)
    assert "new-empty-window" in actions


def test_pre_rename_actions_are_replaced(tmp_path: Path) -> None:
    """Files generated while the project was 'kde-vscode-menu' are migrated.

    The old marker and the old ``KdeVsCodeMenu-`` prefix must both be
    recognized as ours, and regeneration must not leave the stale ids behind.
    """
    from kde_vscode_jumplist.desktop_entry import (
        ACTION_PREFIX,
        LEGACY_ACTION_PREFIXES,
        LEGACY_GENERATED_MARKER,
        is_generated,
    )

    assert "KdeVsCodeMenu-" in LEGACY_ACTION_PREFIXES
    assert ACTION_PREFIX == "KdeVsCodeJumpList-"

    old = LEGACY_GENERATED_MARKER + "\n" + VENDOR_DESKTOP.replace(
        "Actions=new-empty-window;",
        "Actions=new-empty-window;KdeVsCodeMenu-Recent-abc123;KdeVsCodeMenu-Manage;",
    ) + (
        "\n[Desktop Action KdeVsCodeMenu-Recent-abc123]\n"
        "Name=old-proj\nExec=kde-vscode-menu open abc123\n"
        "\n[Desktop Action KdeVsCodeMenu-Manage]\n"
        "Name=Manage VS Code Pinned…\nExec=kde-vscode-menu manage\n"
    )

    # Still detected as generated, so discovery never treats it as a vendor file.
    stale = tmp_path / "code.desktop"
    stale.write_text(old, encoding="utf-8")
    assert is_generated(stale)

    _, recents = _entries()
    content = build_desktop_content(old, [], recents)
    parser = _parse(content)
    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]

    assert not any(a.startswith("KdeVsCodeMenu") for a in actions)
    assert not any(s.startswith("Desktop Action KdeVsCodeMenu") for s in parser.sections())
    assert content.startswith("# Generated by kde-vscode-jumplist")
    # Neither the pre-rename Manage group nor any current one remains: there is
    # no dedicated manager entry at all any more.
    assert not [s for s in parser.sections() if "Manage" in s]
    assert "new-empty-window" in actions


def test_labels_with_special_chars_sanitized() -> None:
    entry = MenuEntry(ENTRY_FILE, "file:///weird.txt", "weird \\ multiline\nname", "code")
    content = build_desktop_content(VENDOR_DESKTOP, [entry], [])
    parser = _parse(content)  # must still parse
    name = next(
        parser.get(s, "Name")
        for s in parser.sections()
        if s.startswith("Desktop Action KdeVsCodeJumpList-Pinned")
    )
    assert "\n" not in name
    # The label is used as-is: no "[pinned]" marker appended. The backslash is
    # still escaped and the newline still folded to a space.
    assert name == "weird \\\\ multiline name"


def test_pinned_entries_get_the_star_icon() -> None:
    """Pin state is shown by the icon, not by text appended to the label."""
    pinned, recents = _entries()
    parser = _parse(build_desktop_content(VENDOR_DESKTOP, pinned, recents))

    for action in parser.get("Desktop Entry", "Actions").split(";"):
        if not action.startswith("KdeVsCodeJumpList-Pinned"):
            continue
        section = f"Desktop Action {action}"
        # The filled star, not the outline one the manager dialog uses on its own
        # pinned rows (manage.PINNED_ROW_ICON) -- the two are separate names.
        assert parser.get(section, "Icon") == PINNED_ICON == "starred"
        assert not parser.get(section, "Name").endswith(" [pinned]")

    # Recents keep their per-kind icons, so the star reads as "pinned".
    recent_icons = {
        parser.get(f"Desktop Action {action}", "Icon")
        for action in parser.get("Desktop Entry", "Actions").split(";")
        if action.startswith("KdeVsCodeJumpList-Recent")
    }
    assert PINNED_ICON not in recent_icons
    assert recent_icons == {"folder", "text-x-generic"}  # workspace excluded by default


def test_remote_entries_get_icons() -> None:
    _, recents = _entries()
    parser = _parse(build_desktop_content(VENDOR_DESKTOP, [], recents))
    icons = {
        parser.get(s, "Icon")
        for s in parser.sections()
        if s.startswith("Desktop Action KdeVsCodeJumpList-Recent")
    }
    # Folder and file icons only: the workspace entry is not in this block.
    assert icons == {"folder", "text-x-generic"}


def test_missing_desktop_entry_section_raises() -> None:
    with pytest.raises(ValueError):
        build_desktop_content("[Not Desktop Entry]\nName=x\n", [], [])


def _many_entries(n: int) -> list[MenuEntry]:
    """MRU-ordered mix of folders and files, newest first."""
    entries: list[MenuEntry] = []
    for i in range(n):
        if i % 2 == 0:
            entries.append(MenuEntry(ENTRY_FOLDER, f"file:///proj{i}", f"proj{i}", "code"))
        else:
            entries.append(MenuEntry(ENTRY_FILE, f"file:///f{i}.md", f"f{i}.md", "code"))
    return entries


def _recent_sections(content: str) -> list[str]:
    parser = _parse(content)
    return [s for s in parser.sections() if s.startswith("Desktop Action KdeVsCodeJumpList-Recent")]


def test_total_entries_is_12() -> None:
    content = build_desktop_content(VENDOR_DESKTOP, [], _many_entries(25))
    assert len(_recent_sections(content)) == 12


def test_recents_share_one_budget_across_kinds() -> None:
    """Folders and files share one budget, taken in MRU order."""
    recents = _many_entries(25)
    content = build_desktop_content(VENDOR_DESKTOP, [], recents)
    parser = _parse(content)

    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]
    recent_ids = [a for a in actions if a.startswith("KdeVsCodeJumpList-Recent")]
    assert len(recent_ids) == 12

    # The first 12 MRU entries are kept, in order, regardless of kind.
    assert [a.rsplit("-", 1)[-1] for a in recent_ids] == [e.entry_id for e in recents[:12]]

    # Both kinds are represented in the combined list.
    icons = {parser.get(f"Desktop Action {a}", "Icon") for a in recent_ids}
    assert "folder" in icons and "text-x-generic" in icons


def test_the_budget_is_shared_with_the_pinned_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pinned entries take their share of the budget first, recents get the rest."""
    monkeypatch.setattr("kde_vscode_jumplist.desktop_entry.TOTAL_ENTRIES", 5)
    pinned = _many_entries(3)
    content = build_desktop_content(VENDOR_DESKTOP, pinned, _many_entries(25))
    actions = [a for a in _parse(content).get("Desktop Entry", "Actions").split(";") if a]

    assert len([a for a in actions if a.startswith("KdeVsCodeJumpList-Pinned")]) == 3
    assert len([a for a in actions if a.startswith("KdeVsCodeJumpList-Recent")]) == 2


def test_pinned_entries_alone_can_fill_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """More pins than the budget leaves no room for recents at all."""
    monkeypatch.setattr("kde_vscode_jumplist.desktop_entry.TOTAL_ENTRIES", 2)
    content = build_desktop_content(VENDOR_DESKTOP, _many_entries(3), _many_entries(25))
    actions = [a for a in _parse(content).get("Desktop Entry", "Actions").split(";") if a]

    assert not _recent_sections(content)
    assert len([a for a in actions if a.startswith("KdeVsCodeJumpList-Pinned")]) == 2


def test_total_entries_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("kde_vscode_jumplist.desktop_entry.TOTAL_ENTRIES", 3)
    content = build_desktop_content(VENDOR_DESKTOP, [], _many_entries(25))
    assert len(_recent_sections(content)) == 3


def test_a_zero_budget_lists_no_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pinned block is capped by the same budget, so 0 lists no entries.

    The pinned heading survives even then, because it is the way into the
    dialog and the menu would otherwise have no way back in.
    """
    monkeypatch.setattr("kde_vscode_jumplist.desktop_entry.TOTAL_ENTRIES", 0)
    pinned = [MenuEntry(ENTRY_FOLDER, "file:///pinned", "pinned", "code")]
    content = build_desktop_content(VENDOR_DESKTOP, pinned, _many_entries(5))
    actions = [a for a in _parse(content).get("Desktop Entry", "Actions").split(";") if a]

    assert actions == [
        "new-empty-window",
        "_SEPARATOR_",
        PINNED_CAPTION_ACTION_ID,
        "_SEPARATOR_",
    ]
    assert not _recent_sections(content)


# --- excluded kinds -------------------------------------------------------


def test_workspaces_excluded_from_recents_by_default() -> None:
    """A .code-workspace is not listed in the recents block."""
    workspace = MenuEntry(ENTRY_WORKSPACE, "file:///w.code-workspace", "w", "code")
    folder = MenuEntry(ENTRY_FOLDER, "file:///proj", "proj", "code")

    parser = _parse(build_desktop_content(VENDOR_DESKTOP, [], [workspace, folder]))
    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]

    assert f"KdeVsCodeJumpList-Recent-{folder.entry_id}" in actions
    assert f"KdeVsCodeJumpList-Recent-{workspace.entry_id}" not in actions
    assert not parser.has_section(
        f"Desktop Action KdeVsCodeJumpList-Recent-{workspace.entry_id}"
    )


def test_workspace_only_recents_leave_no_empty_block() -> None:
    """Excluding every recent must not leave a bare recents caption behind."""
    workspace = MenuEntry(ENTRY_WORKSPACE, "file:///w.code-workspace", "w", "code")
    parser = _parse(build_desktop_content(VENDOR_DESKTOP, [], [workspace]))
    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]

    assert actions == [
        "new-empty-window",
        "_SEPARATOR_",
        PINNED_CAPTION_ACTION_ID,
        "_SEPARATOR_",
    ]
    assert not parser.has_section(f"Desktop Action {RECENT_CAPTION_ACTION_ID}")


def test_pinned_workspace_is_still_listed() -> None:
    """Filtering applies to recents only: pinning is an explicit request."""
    workspace = MenuEntry(ENTRY_WORKSPACE, "file:///w.code-workspace", "w", "code")
    parser = _parse(build_desktop_content(VENDOR_DESKTOP, [workspace], [workspace]))
    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]

    pinned_section = f"Desktop Action KdeVsCodeJumpList-Pinned-{workspace.entry_id}"
    # The star replaces the workspace icon, so the block reads as pinned.
    assert parser.get(pinned_section, "Icon") == PINNED_ICON
    assert f"KdeVsCodeJumpList-Pinned-{workspace.entry_id}" in actions
    assert f"KdeVsCodeJumpList-Recent-{workspace.entry_id}" not in actions
    # Nothing is left in the recents block, so it disappears entirely: one
    # separator opening the pinned block, one closing the list.
    assert actions.count("_SEPARATOR_") == 2
    assert not parser.has_section(f"Desktop Action {RECENT_CAPTION_ACTION_ID}")


def test_excluding_more_kinds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "kde_vscode_jumplist.desktop_entry.EXCLUDED_KINDS",
        frozenset({ENTRY_WORKSPACE, ENTRY_FOLDER}),
    )
    _, recents = _entries()
    parser = _parse(build_desktop_content(VENDOR_DESKTOP, [], recents))
    icons = {
        parser.get(s, "Icon")
        for s in parser.sections()
        if s.startswith("Desktop Action KdeVsCodeJumpList-Recent")
    }
    assert icons == {"text-x-generic"}  # only the two files survive


def test_excluding_nothing_lists_every_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty set opts back in to the excluded kind."""
    monkeypatch.setattr("kde_vscode_jumplist.desktop_entry.EXCLUDED_KINDS", frozenset())
    assert excluded_kinds() == frozenset()

    _, recents = _entries()
    parser = _parse(build_desktop_content(VENDOR_DESKTOP, [], recents))
    icons = {
        parser.get(s, "Icon")
        for s in parser.sections()
        if s.startswith("Desktop Action KdeVsCodeJumpList-Recent")
    }
    # The workspace is listed again, using the generic fallback icon (there is
    # no dedicated workspace icon in the usual themes).
    workspace = next(e for e in recents if e.kind == ENTRY_WORKSPACE)
    section = f"Desktop Action KdeVsCodeJumpList-Recent-{workspace.entry_id}"
    assert parser.get(section, "Icon") == DEFAULT_ICON
    assert icons == {"folder", DEFAULT_ICON}


def test_excluded_kinds_are_not_counted_by_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The limit applies to listed entries, not to filtered-out ones."""
    monkeypatch.setattr("kde_vscode_jumplist.desktop_entry.TOTAL_ENTRIES", 3)
    entries = [
        MenuEntry(ENTRY_WORKSPACE, "file:///w0.code-workspace", "w0", "code"),
        *_many_entries(4),
        MenuEntry(ENTRY_WORKSPACE, "file:///w1.code-workspace", "w1", "code"),
    ]
    sections = _recent_sections(build_desktop_content(VENDOR_DESKTOP, [], entries))

    assert len(sections) == 3
    for entry in _many_entries(4)[:3]:
        assert f"Desktop Action KdeVsCodeJumpList-Recent-{entry.entry_id}" in sections


def test_keep_recent_preserves_mru_order() -> None:
    entries = [
        MenuEntry(ENTRY_WORKSPACE, "file:///w.code-workspace", "w", "code"),
        MenuEntry(ENTRY_FOLDER, "file:///a", "a", "code"),
        MenuEntry(ENTRY_FILE, "file:///b", "b", "code"),
    ]
    assert keep_recent(entries) == [entries[1], entries[2]]


def test_separators_delimit_both_blocks() -> None:
    """Native "_SEPARATOR_" actions open the recents and pinned blocks."""
    pinned, recents = _entries()
    parser = _parse(build_desktop_content(VENDOR_DESKTOP, pinned, recents))
    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]
    listed = [e for e in recents if e.kind != ENTRY_WORKSPACE]

    assert actions == [
        "new-empty-window",
        "_SEPARATOR_",
        RECENT_CAPTION_ACTION_ID,
        *[f"KdeVsCodeJumpList-Recent-{e.entry_id}" for e in listed],
        "_SEPARATOR_",
        PINNED_CAPTION_ACTION_ID,
        f"KdeVsCodeJumpList-Pinned-{pinned[0].entry_id}",
        "_SEPARATOR_",
    ]
    # A separator is an Actions= entry only; it must not define a group.
    assert not parser.has_section("Desktop Action _SEPARATOR_")


def test_pinned_above_recents_swaps_the_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """PINNED_BELOW decides which block comes first."""
    monkeypatch.setattr("kde_vscode_jumplist.desktop_entry.PINNED_BELOW", False)
    pinned, recents = _entries()
    listed = [e for e in recents if e.kind != ENTRY_WORKSPACE]

    parser = _parse(build_desktop_content(VENDOR_DESKTOP, pinned, recents))
    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]

    assert actions == [
        "new-empty-window",
        "_SEPARATOR_",
        PINNED_CAPTION_ACTION_ID,
        f"KdeVsCodeJumpList-Pinned-{pinned[0].entry_id}",
        "_SEPARATOR_",
        RECENT_CAPTION_ACTION_ID,
        *[f"KdeVsCodeJumpList-Recent-{e.entry_id}" for e in listed],
        "_SEPARATOR_",
    ]
    # Still closed by exactly one separator, whichever order is used.
    assert actions.count("_SEPARATOR_") == 3


def test_the_pinned_heading_is_always_there() -> None:
    """With no recents the pinned heading is the whole menu.

    It is the way into the dialog, so it is not part of a block that can be
    omitted; only the recents block comes and goes.
    """
    _, recents = _entries()
    content = build_desktop_content(VENDOR_DESKTOP, [], recents)

    assert set(_captions(content)) == {RECENT_CAPTION_ACTION_ID, PINNED_CAPTION_ACTION_ID}
    actions = [a for a in _parse(content).get("Desktop Entry", "Actions").split(";") if a]
    assert actions[-1] == "_SEPARATOR_"
    assert actions[-2] == PINNED_CAPTION_ACTION_ID


def _captions(content: str) -> dict[str, tuple[str, str, str]]:
    """Caption action id -> (Name, Icon, Exec) for the captions present."""
    parser = _parse(content)
    return {
        caption: (
            parser.get(f"Desktop Action {caption}", "Name"),
            parser.get(f"Desktop Action {caption}", "Icon"),
            parser.get(f"Desktop Action {caption}", "Exec"),
        )
        for caption in CAPTION_IDS
        if parser.has_section(f"Desktop Action {caption}")
    }


def test_captions_head_their_blocks() -> None:
    """Each heading sits directly under the separator opening its block.

    A separator cannot carry text, so a heading is a real action: it has to
    have real text (Kickoff skips empty-text actions) and an Exec, and both of
    ours launch the manager -- see
    test_the_pinned_heading_opens_the_manager and
    test_the_recents_heading_also_opens_the_manager.
    """
    pinned, recents = _entries()
    content = build_desktop_content(VENDOR_DESKTOP, pinned, recents)
    parser = _parse(content)
    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]

    assert _captions(content) == {
        PINNED_CAPTION_ACTION_ID: (
            _expected_heading(pinned, recents),
            PINNED_CAPTION_ICON,
            f"{desktop_launcher_command()} manage",
        ),
        RECENT_CAPTION_ACTION_ID: (
            RECENT_CAPTION_TEXT,
            RECENT_CAPTION_ICON,
            f"{desktop_launcher_command()} manage",
        ),
    }
    # The pinned heading says what it does. The marker is pushed to the right
    # edge by padding the text out, so the heading is the widest item in the
    # menu and its marker lands where a submenu arrow would -- rather than
    # sitting in the shortcut column 7px further in.
    #
    # This fixture's labels are all shorter than the heading, so there is no
    # padding to add here; test_heading_padding_appears_only_when_needed
    # covers the other case.
    heading = _expected_heading(pinned, recents)
    assert heading.startswith(PINNED_CAPTION_BASE)
    assert heading.endswith(PINNED_CAPTION_MARKER)
    pad = heading[len(PINNED_CAPTION_BASE) : -len(PINNED_CAPTION_MARKER)]
    assert set(pad) <= {" "}  # only spaces, and possibly none
    assert heading == heading.rstrip()  # nothing trailing to be trimmed off
    assert "\t" not in heading  # a tab would go to the shortcut column instead
    # The recents heading is a plain heading: no marker, no padding. It opens
    # the manager like the other one, but the pinned heading is the one that
    # has to be the widest item for its marker to reach the right edge, so a
    # chevron here would only widen the menu.
    assert RECENT_CAPTION_TEXT == "Recent Files:"
    assert PINNED_CAPTION_MARKER not in RECENT_CAPTION_TEXT
    assert "\t" not in RECENT_CAPTION_TEXT
    # The pinned heading carries the manager's own icon, because that is what
    # clicking it does. The recents heading keeps its clock: the two have to
    # stay tellable apart.
    assert PINNED_CAPTION_ICON == "bookmark-new"
    assert RECENT_CAPTION_ICON == "clock"
    assert PINNED_CAPTION_ICON != RECENT_CAPTION_ICON

    # Pinned heading: after its separator, before the pinned block.
    pinned_index = actions.index(PINNED_CAPTION_ACTION_ID)
    assert actions[pinned_index - 1] == "_SEPARATOR_"
    assert actions[pinned_index + 1] == (
        f"KdeVsCodeJumpList-Pinned-{pinned[0].entry_id}"
    )

    # Recents heading: after the second separator, before the recents.
    recent_index = actions.index(RECENT_CAPTION_ACTION_ID)
    assert actions[recent_index - 1] == "_SEPARATOR_"
    assert actions[recent_index + 1] == f"KdeVsCodeJumpList-Recent-{recents[0].entry_id}"

    # Pinned are listed below the recents (PINNED_BELOW).
    assert recent_index < pinned_index

    for caption in CAPTION_IDS:
        section = f"Desktop Action {caption}"
        # Plasma skips actions carrying NoDisplay, so captions stay visible.
        assert not parser.has_option(section, "NoDisplay")
        assert not parser.has_option(section, "Type")

    # Every generated action launches something: the captions included, which
    # is the point of there being no inert-caption constant any more.
    for name in actions:
        if name.startswith("KdeVsCodeJumpList-"):
            assert parser.get(f"Desktop Action {name}", "Exec").startswith(
                desktop_launcher_command() + " "
            )


def test_heading_padding_appears_only_when_it_is_needed() -> None:
    """The pad is sized off the longest label, and only off what it has to make up.

    Padding exists to make the heading the widest item so its marker reaches the
    right edge. Two ways to get that wrong, both covered here: padding a heading
    that is already wider than every label does nothing but widen the menu, and
    charging the heading again for the width its own text already supplies does
    the same. The second was a real bug -- it added roughly 82px of bare menu.
    """
    long_enough = "x" * (PINNED_CAPTION_PAD_FREE + 10)
    short = "x" * (PINNED_CAPTION_PAD_FREE - 1)

    def heading_for(label: str) -> str:
        content = build_desktop_content(
            VENDOR_DESKTOP, [MenuEntry(ENTRY_FOLDER, "file:///p", label, "code")], []
        )
        return _parse(content).get(f"Desktop Action {PINNED_CAPTION_ACTION_ID}", "Name")

    def pad_of(heading: str) -> int:
        return len(heading) - len(PINNED_CAPTION_BASE) - len(PINNED_CAPTION_MARKER)

    # A label shorter than the heading needs no padding: the heading already
    # wins, so padding it would only widen the menu.
    assert pad_of(heading_for(short)) == 0

    # A longer one gets padding, and only for the overhang: the base text is
    # free, so 10 characters of overhang is not charged as the whole label.
    expected = round(10 * PINNED_CAPTION_PAD_RATIO)
    assert pad_of(heading_for(long_enough)) == expected
    assert expected < round(len(long_enough) * PINNED_CAPTION_PAD_RATIO)

    # The marker still ends up last, with nothing trailing.
    assert heading_for(long_enough).endswith(PINNED_CAPTION_MARKER)


def test_the_heading_padding_survives_regeneration() -> None:
    """The next pass reads the file the last pass wrote, padding included.

    The interior spaces are what push the marker to the right edge, so losing
    them on the way through the file would quietly put the glyph back beside the
    label. Worth checking because they are written into a desktop file and read
    back out again, which is where trailing whitespace would be trimmed.
    """
    pinned, recents = _entries()
    expected = _expected_heading(pinned, recents)
    first = build_desktop_content(VENDOR_DESKTOP, pinned, recents)
    section = f"Desktop Action {PINNED_CAPTION_ACTION_ID}"

    assert _parse(first).get(section, "Name") == expected
    assert f"Name={expected}" in first  # real spaces in the text

    second = build_desktop_content(first, pinned, recents)
    assert _parse(second).get(section, "Name") == expected


def test_empty_lists_leave_only_the_way_in() -> None:
    """With nothing to list, the pinned heading is the whole menu.

    It is what opens the dialog, so unlike the recents block it is not omitted
    when it has no entries behind it.
    """
    content = build_desktop_content(VENDOR_DESKTOP, [], [])
    parser = _parse(content)
    assert set(_captions(content)) == {PINNED_CAPTION_ACTION_ID}
    assert not parser.has_section(f"Desktop Action {RECENT_CAPTION_ACTION_ID}")
    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]
    assert actions == [
        "new-empty-window",
        "_SEPARATOR_",
        PINNED_CAPTION_ACTION_ID,
        "_SEPARATOR_",
    ]


def test_the_pinned_heading_is_always_present() -> None:
    """It is emitted with nothing to list, because it is the only way in.

    Hiding it when the lists happen to be empty would leave no way to pin
    anything from the menu at all.
    """
    parser = _parse(build_desktop_content(VENDOR_DESKTOP, [], []))
    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]
    assert PINNED_CAPTION_ACTION_ID in actions
    section = f"Desktop Action {PINNED_CAPTION_ACTION_ID}"
    # Nothing to line up with, so no padding: the marker sits just after the
    # text rather than a line of spaces away from it.
    assert parser.get(section, "Name") == pinned_caption_text(0)
    assert parser.get(section, "Name") == f"{PINNED_CAPTION_BASE}{PINNED_CAPTION_MARKER}"
    assert parser.get(section, "Exec").endswith(" manage")


def test_the_menu_ends_with_a_separator() -> None:
    """So Plasma's own task-manager entries follow a line, not an entry.

    Plasma appends "Pin to Task Manager" / "Unpin from Task Manager" after the
    whole Actions= list, so what we leave last is what they sit under.
    """
    pinned, recents = _entries()
    parser = _parse(build_desktop_content(VENDOR_DESKTOP, pinned, recents))
    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]

    assert actions[-1] == "_SEPARATOR_"
    # The closing line comes straight after the last entry row, and is the only
    # thing after it -- nothing of ours trails past it.
    assert actions[-2].startswith(f"{ACTION_PREFIX}Pinned-")
    assert len(actions) - 1 == actions.index(actions[-2]) + 1


def test_the_pinned_heading_opens_the_manager() -> None:
    """Clicking "Pinned Files:" runs the CLI's ``manage``."""
    pinned, recents = _entries()
    parser = _parse(build_desktop_content(VENDOR_DESKTOP, pinned, recents))
    section = f"Desktop Action {PINNED_CAPTION_ACTION_ID}"

    assert parser.get(section, "Name") == _expected_heading(pinned, recents)
    assert parser.get(section, "Exec") == f"{desktop_launcher_command()} manage"
    # Plasma skips actions carrying NoDisplay, so it stays visible.
    assert not parser.has_option(section, "NoDisplay")
    # And its icon is the manager's, which is what says what a click does.
    assert parser.get(section, "Icon") == PINNED_CAPTION_ICON


def test_the_recents_heading_also_opens_the_manager() -> None:
    """Both headings are clickable: "Recent Files:" opens the dialog too."""
    pinned, recents = _entries()
    parser = _parse(build_desktop_content(VENDOR_DESKTOP, pinned, recents))
    section = f"Desktop Action {RECENT_CAPTION_ACTION_ID}"

    assert parser.get(section, "Name") == RECENT_CAPTION_TEXT
    assert parser.get(section, "Exec") == f"{desktop_launcher_command()} manage"
    # Plasma skips actions carrying NoDisplay, so it stays visible.
    assert not parser.has_option(section, "NoDisplay")
    # It keeps the clock rather than taking the manager's bookmark icon: the
    # headings have to stay distinguishable from each other.
    assert parser.get(section, "Icon") == RECENT_CAPTION_ICON


def test_recent_caption_absent_without_recents() -> None:
    """No recents => no recents heading; the pinned block is still titled."""
    pinned = [MenuEntry(ENTRY_FOLDER, "file:///pinned", "pinned", "code")]
    content = build_desktop_content(VENDOR_DESKTOP, pinned, [])
    parser = _parse(content)
    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]

    assert not parser.has_section(f"Desktop Action {RECENT_CAPTION_ACTION_ID}")
    # Compared against the parsed ids: the recents caption id is a prefix of
    # the pinned one, so a substring check would match the wrong caption.
    assert RECENT_CAPTION_ACTION_ID not in actions
    assert set(_captions(content)) == {PINNED_CAPTION_ACTION_ID}


def test_pinned_heading_kept_when_nothing_is_pinned() -> None:
    """No pins => the heading is still there, and is still the way in.

    The realistic empty case: recents to list, nothing pinned yet. The heading
    is what opens the dialog, so it has to survive with no entries under it --
    and still carry the launcher, or it would read as clickable and do nothing.
    """
    _, recents = _entries()
    content = build_desktop_content(VENDOR_DESKTOP, [], recents)
    parser = _parse(content)
    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]

    assert parser.has_section(f"Desktop Action {PINNED_CAPTION_ACTION_ID}")
    assert set(_captions(content)) == {RECENT_CAPTION_ACTION_ID, PINNED_CAPTION_ACTION_ID}
    section = f"Desktop Action {PINNED_CAPTION_ACTION_ID}"
    assert parser.get(section, "Name") == _expected_heading([], recents)
    assert parser.get(section, "Exec") == f"{desktop_launcher_command()} manage"
    # Nothing pinned, so no entry rows between the heading and the closing line.
    index = actions.index(PINNED_CAPTION_ACTION_ID)
    assert actions[index + 1] == "_SEPARATOR_"
    assert actions[index - 1] == "_SEPARATOR_"


def test_captions_not_duplicated_on_regeneration() -> None:
    """Feeding the generator its own output keeps exactly one of each caption."""
    pinned, recents = _entries()
    first = build_desktop_content(VENDOR_DESKTOP, pinned, recents)
    second = build_desktop_content(first, pinned, recents)
    assert second == first
    actions = [a for a in _parse(second).get("Desktop Entry", "Actions").split(";") if a]
    for caption in CAPTION_IDS:
        assert actions.count(caption) == 1


def test_vendor_separator_is_preserved() -> None:
    """A separator the vendor placed for its own purposes is left alone.

    Ours follows it, so a vendor file that already ends its Actions= with a
    separator does end up with two in a row -- the vendor's line is theirs to
    place, and dropping it would be editing their file.
    """
    vendor = VENDOR_DESKTOP.replace(
        "Actions=new-empty-window;", "Actions=new-empty-window;_SEPARATOR_;"
    )
    parser = _parse(build_desktop_content(vendor, [], []))
    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]
    assert actions == [
        "new-empty-window",
        "_SEPARATOR_",  # the vendor's, kept
        "_SEPARATOR_",  # ours, opening the pinned block
        PINNED_CAPTION_ACTION_ID,
        "_SEPARATOR_",
    ]


def test_write_user_desktop_entry_atomic_and_idempotent(
    vendor_desktop: Path,
) -> None:
    import kde_vscode_jumplist.desktop_entry as de

    pinned, recents = _entries()

    first = de.write_user_desktop_entry(vendor_desktop, "code", pinned, recents)
    assert first is not None and first.is_file()
    # It goes where Plasma reads: $XDG_DATA_HOME/applications.
    assert first.parent == de.paths.user_applications_dir()
    # Identical content => no rewrite.
    second = de.write_user_desktop_entry(vendor_desktop, "code", pinned, recents)
    assert second is None


def test_generated_file_marked_and_detected(
    vendor_desktop: Path,
) -> None:
    import kde_vscode_jumplist.desktop_entry as de

    pinned, recents = _entries()

    written = de.write_user_desktop_entry(vendor_desktop, "code", pinned, recents)
    assert written is not None
    assert de.is_generated(written)
    assert not de.is_generated(vendor_desktop)


def test_generated_file_without_marker_detected(tmp_path: Path) -> None:
    """Files written before the marker comment existed are still recognized."""
    from kde_vscode_jumplist.desktop_entry import is_generated

    legacy = tmp_path / "code.desktop"
    legacy.write_text(
        VENDOR_DESKTOP + "\n[KdeVsCodeMenu Recent abc123]\nName=x\n",
        encoding="utf-8",
    )
    assert is_generated(legacy)


# --- launcher resolution (click-ability) --------------------------------


def test_format_exec_quotes_reserved_characters() -> None:
    """Exec quoting follows the spec's double-quote rules, not shell rules."""
    assert format_exec(["/usr/bin/python3", "-m", "kde_vscode_jumplist"]) == (
        "/usr/bin/python3 -m kde_vscode_jumplist"
    )
    assert format_exec(["/opt/my tools/python"]) == '"/opt/my tools/python"'
    # A dollar sign must be escaped inside the quotes.
    assert format_exec(["/a$b"]) == '"/a\\$b"'
    # Single quotes are literal in Exec values, so they force quoting too.
    assert format_exec(["/a'b"]) == '"/a\'b"'


def test_resolve_cli_argv_launches_cli_without_pythonpath() -> None:
    """Regression: clicking a recent entry used to do nothing.

    ``make update`` from a source checkout imports the package through
    PYTHONPATH, which Plasma does not inherit. The baked command therefore has
    to start the CLI with PYTHONPATH removed.
    """
    import kde_vscode_jumplist.desktop_entry as de

    argv = de.resolve_cli_argv()
    result = subprocess.run(  # noqa: S603 - argv list, no shell
        [*argv, "--version"],
        env=de.launcher_environment(),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    # The production launcher is the same argv, formatted for Exec=.
    assert desktop_launcher_command() == format_exec(argv)


def test_resolve_cli_skips_interpreter_that_cannot_import_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare ``python -m`` fallback is rejected when it needs PYTHONPATH.

    The fake interpreter only succeeds when PYTHONPATH points at the source
    root, reproducing the source-checkout case that produced dead menu items.
    """
    import kde_vscode_jumplist.desktop_entry as de

    # A built or installed artifact would be chosen before the fallbacks,
    # hiding the behaviour under test on a machine where `make build`/install
    # has been run.
    monkeypatch.setattr(de, "installed_cli_path", lambda: None)
    monkeypatch.setattr(de, "packaged_cli_path", lambda: None)

    source_root = de.module_source_root()
    assert source_root is not None
    real_python = sys.executable

    fake_python = tmp_path / "python"
    fake_python.write_text(
        "#!/bin/sh\n"
        'case ":$PYTHONPATH:" in\n'
        f'  *":{source_root}:"*) exec "{real_python}" "$@" ;;\n'
        "esac\n"
        "exit 1\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    # Pretend the console script is not installed, but keep `env` findable.
    real_which = shutil.which
    monkeypatch.setattr(
        de.shutil,
        "which",
        lambda name: None if name == "kde-vscode-jumplist" else real_which(name),
    )

    command = de.resolve_cli_argv(python=str(fake_python))

    assert Path(command[0]).name == "env"
    assert f"PYTHONPATH={source_root}" in command
    assert command[-1] == de.MODULE
    assert de._command_launches_cli(command)


def test_resolve_cli_prefers_console_script(monkeypatch: pytest.MonkeyPatch) -> None:
    import kde_vscode_jumplist.desktop_entry as de

    # Order is what this asserts, so neutralise the candidates that depend on
    # this machine: the running program (pytest), the installed executable and
    # a `make build` artifact.
    monkeypatch.setattr(de, "_running_program", lambda: None)
    monkeypatch.setattr(de, "installed_cli_path", lambda: None)
    monkeypatch.setattr(de, "packaged_cli_path", lambda: None)
    monkeypatch.setattr(
        de.shutil,
        "which",
        lambda name: (
            "/opt/venv/bin/kde-vscode-jumplist" if name == "kde-vscode-jumplist" else None
        ),
    )
    probed: list[list[str]] = []

    def always_works(command: list[str]) -> bool:
        probed.append(list(command))
        return True

    monkeypatch.setattr(de, "_command_launches_cli", always_works)

    assert de.resolve_cli_argv() == ["/opt/venv/bin/kde-vscode-jumplist"]
    assert len(probed) == 1  # stopped at the first working candidate


def test_resolve_cli_prefers_the_installed_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`make install` output wins: it survives the checkout being moved."""
    import kde_vscode_jumplist.desktop_entry as de

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    installed = bin_dir / de.APP_NAME
    installed.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("XDG_BIN_HOME", str(bin_dir))
    monkeypatch.setattr(de, "_running_program", lambda: None)
    monkeypatch.setattr(de.shutil, "which", lambda name: None)
    monkeypatch.setattr(de, "packaged_cli_path", lambda: tmp_path / "other")

    probed: list[list[str]] = []

    def always_works(command: list[str]) -> bool:
        probed.append(list(command))
        return True

    monkeypatch.setattr(de, "_command_launches_cli", always_works)

    assert de.installed_cli_path() == installed
    assert de.resolve_cli_argv() == [str(installed)]
    assert len(probed) == 1


def test_resolve_cli_prefers_the_built_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With nothing installed, `make build` output is the next best thing."""
    import kde_vscode_jumplist.desktop_entry as de

    source_root = tmp_path / "src"
    (source_root / "kde_vscode_jumplist").mkdir(parents=True)
    built = tmp_path / "bin" / "kde-vscode-jumplist"
    built.parent.mkdir()
    built.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(de, "module_source_root", lambda: source_root)
    monkeypatch.setattr(de, "_running_program", lambda: None)
    monkeypatch.setattr(de.shutil, "which", lambda name: None)
    probed: list[list[str]] = []

    def always_works(command: list[str]) -> bool:
        probed.append(list(command))
        return True

    monkeypatch.setattr(de, "_command_launches_cli", always_works)

    assert de.packaged_cli_path() == built
    assert de.resolve_cli_argv() == [str(built)]
    assert len(probed) == 1


def test_candidate_commands_are_deduplicated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The installed copy is usually also what ``which`` finds; probe it once."""
    import kde_vscode_jumplist.desktop_entry as de

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    installed = bin_dir / de.APP_NAME
    installed.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("XDG_BIN_HOME", str(bin_dir))
    monkeypatch.setattr(de, "_running_program", lambda: None)
    monkeypatch.setattr(de.shutil, "which", lambda name: str(installed) if name == de.APP_NAME else None)
    monkeypatch.setattr(de, "packaged_cli_path", lambda: None)
    monkeypatch.setattr(de, "module_source_root", lambda: None)

    firsts = [command[0] for command in de._candidate_cli_commands()]
    assert firsts.count(str(installed)) == 1


def test_packaged_cli_path_absent_without_a_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import kde_vscode_jumplist.desktop_entry as de

    source_root = tmp_path / "src"
    (source_root / "kde_vscode_jumplist").mkdir(parents=True)
    monkeypatch.setattr(de, "module_source_root", lambda: source_root)

    assert de.packaged_cli_path() is None


def test_resolver_reuses_the_running_program(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Running the built binary directly makes the menu point back at it."""
    import kde_vscode_jumplist.desktop_entry as de

    fake_cli = tmp_path / "kde-vscode-jumplist"
    fake_cli.write_text(f'#!/bin/sh\necho "{de.__version__}"\n', encoding="utf-8")
    fake_cli.chmod(0o755)

    monkeypatch.setattr(de.sys, "argv", [str(fake_cli)])
    monkeypatch.setattr(de.shutil, "which", lambda name: None)
    monkeypatch.setattr(de, "module_source_root", lambda: None)

    assert de.resolve_cli_argv() == [str(fake_cli)]


def test_running_program_ignored_when_not_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import kde_vscode_jumplist.desktop_entry as de

    script = tmp_path / "__main__.py"
    script.write_text("print('x')\n", encoding="utf-8")  # not chmod +x
    monkeypatch.setattr(de.sys, "argv", [str(script)])

    assert de._running_program() is None


def test_resolver_rejects_a_foreign_program_with_matching_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--version` success alone is not proof it is our CLI.

    ``python --version`` and ``pytest --version`` both exit 0, so a naive probe
    would happily bake the host program into the menu.
    """
    import kde_vscode_jumplist.desktop_entry as de

    fake = tmp_path / "python"
    fake.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "3.14.4"; exit 0; fi\nexit 1\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)

    assert de._command_launches_cli([str(fake)]) is False

    real = tmp_path / "kde-vscode-jumplist"
    real.write_text(f'#!/bin/sh\necho "{de.__version__}"\n', encoding="utf-8")
    real.chmod(0o755)
    assert de._command_launches_cli([str(real)]) is True

    # An artifact from a different release is rejected too, so a stale build
    # cannot be baked in after a version bump.
    stale = tmp_path / "stale"
    stale.write_text('#!/bin/sh\necho "0.0.1"\n', encoding="utf-8")
    stale.chmod(0o755)
    assert de._command_launches_cli([str(stale)]) is False


def test_generated_actions_use_verified_launcher() -> None:
    """Every clickable action carries the same verified command prefix."""
    _, recents = _entries()
    parser = _parse(build_desktop_content(VENDOR_DESKTOP, [recents[0]], recents))
    prefix = desktop_launcher_command()
    checked = 0
    for section in parser.sections():
        if not section.startswith("Desktop Action KdeVsCodeJumpList-"):
            continue
        exec_value = parser.get(section, "Exec")
        assert exec_value.startswith(prefix + " "), exec_value
        checked += 1
    # 1 pinned + 3 recents (the workspace is excluded) + both headings, which
    # launch the manager rather than being inert.
    assert checked == 6



def test_discovery_skips_generated_user_desktop_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    vendor_desktop: Path,
    xdg_dirs: dict[str, Path],
) -> None:
    """The vendor file must be found even when a generated copy shadows it."""
    import kde_vscode_jumplist.desktop_entry as de
    import kde_vscode_jumplist.discovery as disc

    # The generated copy goes where Plasma reads it, shadowing the vendor file.
    apps_dir = de.paths.user_applications_dir()

    pinned, recents = _entries()
    written = de.write_user_desktop_entry(vendor_desktop, "code", pinned, recents)
    assert written is not None and written.parent == apps_dir

    # Vendor file lives in a system data dir; generated copy in apps_dir.
    system_dir = tmp_path / "system" / "applications"
    system_dir.mkdir(parents=True)
    system_vendor = system_dir / "code.desktop"
    system_vendor.write_text(VENDOR_DESKTOP, encoding="utf-8")
    monkeypatch.setattr(disc, "xdg_data_dirs", lambda: [tmp_path / "system"])

    found = disc._find_desktop_file(disc.DESKTOP_CANDIDATES["code"])
    assert found == system_vendor
