"""Tests for desktop-entry generation (golden-style structural checks)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import VENDOR_DESKTOP, parse_raw

from kde_vscode_jumplist import config
from kde_vscode_jumplist.desktop_entry import (
    ACTION_PREFIX,
    DEFAULT_ICON,
    PINNED_ICON,
    PINNED_CAPTION_ACTION_ID,
    PINNED_CAPTION_ICON,
    PINNED_CAPTION_TEXT,
    MANAGE_ACTION_ID,
    MANAGE_ICON,
    MANAGE_TEXT,
    NOOP_EXEC,
    RECENT_CAPTION_ACTION_ID,
    RECENT_CAPTION_ICON,
    RECENT_CAPTION_TEXT,
    build_desktop_content,
    config_arguments,
    desktop_launcher_command,
    excluded_kinds,
    pinned_position,
    format_exec,
    keep_recent,
    resolve_cli_command,
)
from kde_vscode_jumplist.models import ENTRY_FILE, ENTRY_FOLDER, ENTRY_WORKSPACE, MenuEntry

WriteConfig = Callable[..., Path]


# Shared parser from conftest: same pitfalls as the unit files.
_parse = parse_raw


# Both inert section headings; they are never per-entry actions.
CAPTION_IDS = (PINNED_CAPTION_ACTION_ID, RECENT_CAPTION_ACTION_ID)
# Generated actions that are not a recent/pinned entry: the two headings and
# the manager. Filters Actions= down to the entries themselves.
NON_ENTRY_IDS = (*CAPTION_IDS, MANAGE_ACTION_ID)


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

    # Vendor action preserved, ours appended, and the manager closes the list.
    assert actions[0] == "new-empty-window"
    generated = [
        a for a in actions if a.startswith(ACTION_PREFIX) and a not in NON_ENTRY_IDS
    ]
    assert len(generated) == 4  # 1 pinned + 3 recents (the workspace is excluded)
    workspace = next(e for e in recents if e.kind == ENTRY_WORKSPACE)
    assert f"KdeVsCodeJumpList-Recent-{workspace.entry_id}" not in actions

    # Pinned first.
    pinned = [a for a in generated if "Pinned" in a]
    assert len(pinned) == 1 and generated[0] == pinned[0]

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
    # The blocks are closed by a separator, and the manager sits below it -- the
    # position where Plasma's own task-manager entries follow.
    assert actions[-2] == "_SEPARATOR_"
    assert actions[-1] == MANAGE_ACTION_ID


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


def test_manage_action_is_rewritten_from_older_files() -> None:
    """An older menu's Manage action is replaced in place, not duplicated.

    The id is the same one an earlier version wrote, so regeneration must
    update its caption and launcher rather than appending a second copy.
    """
    from kde_vscode_jumplist.desktop_entry import GENERATED_MARKER

    old = GENERATED_MARKER + "\n" + VENDOR_DESKTOP.replace(
        "Actions=new-empty-window;", f"Actions=new-empty-window;{MANAGE_ACTION_ID};"
    ) + (
        f"\n[Desktop Action {MANAGE_ACTION_ID}]\n"
        "Name=Manage VS Code Pinned…\nIcon=bookmark-new\n"
        "Exec=/usr/bin/python3 -m kde_vscode_jumplist manage\n"
    )

    _, recents = _entries()
    content = build_desktop_content(old, [], recents)
    parser = _parse(content)
    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]
    section = f"Desktop Action {MANAGE_ACTION_ID}"

    assert actions.count(MANAGE_ACTION_ID) == 1
    assert parser.get(section, "Name") == MANAGE_TEXT
    assert parser.get(section, "Exec").endswith(" manage")
    # The stale hard-coded interpreter is gone, replaced by the verified 
    # launcher the other actions use.
    assert "/usr/bin/python3 -m" not in content


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
    # The pre-rename Manage group is dropped, and only the current one remains.
    assert [s for s in parser.sections() if "Manage" in s] == [
        f"Desktop Action {MANAGE_ACTION_ID}"
    ]
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


def test_max_recents_default_is_10() -> None:
    content = build_desktop_content(VENDOR_DESKTOP, [], _many_entries(25))
    assert len(_recent_sections(content)) == 10


def test_max_recents_caps_combined_not_per_kind() -> None:
    """Folders/workspaces/files share one budget, taken in MRU order."""
    recents = _many_entries(25)
    content = build_desktop_content(VENDOR_DESKTOP, [], recents)
    parser = _parse(content)

    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]
    recent_ids = [a for a in actions if a.startswith("KdeVsCodeJumpList-Recent")]
    assert len(recent_ids) == 10

    # The first 10 MRU entries are kept, in order, regardless of kind.
    kept = {e.entry_id for e in recents[:10]}
    assert {a.rsplit("-", 1)[-1] for a in recent_ids} == kept
    assert [a.rsplit("-", 1)[-1] for a in recent_ids] == [e.entry_id for e in recents[:10]]

    # Both kinds are represented in the combined list.
    icons = {parser.get(f"Desktop Action {a}", "Icon") for a in recent_ids}
    assert "folder" in icons and "text-x-generic" in icons


def test_max_recents_setting(write_config: WriteConfig) -> None:
    write_config(max_recents=3)
    content = build_desktop_content(VENDOR_DESKTOP, [], _many_entries(25))
    assert len(_recent_sections(content)) == 3


def test_max_recents_zero_hides_recents(write_config: WriteConfig) -> None:
    write_config(max_recents=0)
    pinned = [MenuEntry(ENTRY_FOLDER, "file:///pinned", "pinned", "code")]
    content = build_desktop_content(VENDOR_DESKTOP, pinned, _many_entries(5))
    parser = _parse(content)
    assert not _recent_sections(content)
    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]
    # The whole recents block is gone, caption and separators included; the
    # pinned block keeps its own pair plus the closing separator.
    assert actions == [
        "new-empty-window",
        "_SEPARATOR_",
        PINNED_CAPTION_ACTION_ID,
        f"KdeVsCodeJumpList-Pinned-{pinned[0].entry_id}",
        "_SEPARATOR_",
        MANAGE_ACTION_ID,
    ]
    assert not parser.has_section(f"Desktop Action {RECENT_CAPTION_ACTION_ID}")


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
    """Excluding every recent must not leave a bare caption and separators."""
    workspace = MenuEntry(ENTRY_WORKSPACE, "file:///w.code-workspace", "w", "code")
    parser = _parse(build_desktop_content(VENDOR_DESKTOP, [], [workspace]))
    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]

    # Nothing to list, so no separator either -- but the manager is always there.
    assert actions == ["new-empty-window", MANAGE_ACTION_ID]
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


def test_exclude_kinds_setting_can_exclude_more(write_config: WriteConfig) -> None:
    write_config(exclude_kinds=["workspace", "folder"])
    _, recents = _entries()
    parser = _parse(build_desktop_content(VENDOR_DESKTOP, [], recents))
    icons = {
        parser.get(s, "Icon")
        for s in parser.sections()
        if s.startswith("Desktop Action KdeVsCodeJumpList-Recent")
    }
    assert icons == {"text-x-generic"}  # only the two files survive


def test_exclude_kinds_empty_lists_every_kind(write_config: WriteConfig) -> None:
    """An empty list opts back in to the excluded kind."""
    write_config(exclude_kinds=[])
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


def test_excluded_kinds_are_not_counted_by_the_limit(write_config: WriteConfig) -> None:
    """The limit applies to listed entries, not to filtered-out ones."""
    write_config(max_recents=3)
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
    """Native "_SEPARATOR_" actions open the pinned and recents blocks."""
    pinned, recents = _entries()
    parser = _parse(build_desktop_content(VENDOR_DESKTOP, pinned, recents))
    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]
    listed = [e for e in recents if e.kind != ENTRY_WORKSPACE]

    assert actions == [
        "new-empty-window",
        "_SEPARATOR_",
        PINNED_CAPTION_ACTION_ID,
        f"KdeVsCodeJumpList-Pinned-{pinned[0].entry_id}",
        "_SEPARATOR_",
        RECENT_CAPTION_ACTION_ID,
        *[f"KdeVsCodeJumpList-Recent-{e.entry_id}" for e in listed],
        "_SEPARATOR_",
        MANAGE_ACTION_ID,
    ]
    # A separator is an Actions= entry only; it must not define a group.
    assert not parser.has_section("Desktop Action _SEPARATOR_")


def test_pinned_below_recents_swaps_the_blocks(write_config: WriteConfig) -> None:
    """pinned_position decides which block comes first."""
    pinned, recents = _entries()
    listed = [e for e in recents if e.kind != ENTRY_WORKSPACE]
    write_config(pinned_position="below")

    parser = _parse(build_desktop_content(VENDOR_DESKTOP, pinned, recents))
    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]

    assert actions == [
        "new-empty-window",
        "_SEPARATOR_",
        RECENT_CAPTION_ACTION_ID,
        *[f"KdeVsCodeJumpList-Recent-{e.entry_id}" for e in listed],
        "_SEPARATOR_",
        PINNED_CAPTION_ACTION_ID,
        f"KdeVsCodeJumpList-Pinned-{pinned[0].entry_id}",
        "_SEPARATOR_",
        MANAGE_ACTION_ID,
    ]
    # Still closed by exactly one separator, whichever order is used.
    assert actions.count("_SEPARATOR_") == 3


def test_pinned_below_with_only_recents(write_config: WriteConfig) -> None:
    """With nothing pinned, the order setting makes no difference to the menu.

    The two files are not byte-identical: the launcher carries the path of the
    configuration file, which is what stops a click from reading another one.
    What must not change is the order the entries are listed in.
    """
    _, recents = _entries()

    def listed(content: str) -> list[str]:
        parser = _parse(content)
        return [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]

    above = listed(build_desktop_content(VENDOR_DESKTOP, [], recents))
    write_config(pinned_position="below")

    assert listed(build_desktop_content(VENDOR_DESKTOP, [], recents)) == above


def test_pinned_position_value_is_normalized(write_config: WriteConfig) -> None:
    """Synonyms and capitals are accepted and reduced to "above"/"below".

    Values that make no sense are rejected rather than silently defaulted; the
    parsing tests in test_config.py cover that.
    """
    assert pinned_position() == "above"  # the standard test configuration

    write_config(pinned_position="  TOP ")
    assert pinned_position() == "above"

    write_config(pinned_position="Bottom")
    assert pinned_position() == "below"


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

    A separator cannot carry text, so a heading is an inert action: it must
    have real text (Kickoff skips empty-text actions) and a harmless Exec.
    """
    pinned, recents = _entries()
    content = build_desktop_content(VENDOR_DESKTOP, pinned, recents)
    parser = _parse(content)
    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]

    assert _captions(content) == {
        PINNED_CAPTION_ACTION_ID: (
            PINNED_CAPTION_TEXT,
            PINNED_CAPTION_ICON,
            NOOP_EXEC,
        ),
        RECENT_CAPTION_ACTION_ID: (RECENT_CAPTION_TEXT, RECENT_CAPTION_ICON, NOOP_EXEC),
    }
    assert PINNED_CAPTION_TEXT == "Pinned Files:"
    # The two headings are worded consistently (both plural) and use icons that
    # exist in the usual themes, so neither is mistaken for the other.
    assert RECENT_CAPTION_TEXT == "Recent Files:"
    assert PINNED_CAPTION_ICON == "bookmarks"
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

    # Pinned are listed above the recents.
    assert pinned_index < recent_index

    for caption in CAPTION_IDS:
        section = f"Desktop Action {caption}"
        # Plasma skips actions carrying NoDisplay, so captions stay visible.
        assert not parser.has_option(section, "NoDisplay")
        assert not parser.has_option(section, "Type")

    # Only the captions are inert; every other generated action launches.
    for name in actions:
        if name.startswith("KdeVsCodeJumpList-") and name not in CAPTION_IDS:
            assert parser.get(f"Desktop Action {name}", "Exec") != NOOP_EXEC


def test_all_captions_absent_without_entries() -> None:
    """Empty lists leave no captions and therefore no separators behind."""
    content = build_desktop_content(VENDOR_DESKTOP, [], [])
    parser = _parse(content)
    assert _captions(content) == {}
    for caption in CAPTION_IDS:
        assert not parser.has_section(f"Desktop Action {caption}")
    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]
    assert actions == ["new-empty-window", MANAGE_ACTION_ID]


def test_manage_action_is_always_present() -> None:
    """The manager is emitted even with nothing to list.

    It is the only way to pin something from the menu, so hiding it when the
    lists happen to be empty would leave no way back in.
    """
    parser = _parse(build_desktop_content(VENDOR_DESKTOP, [], []))
    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]
    assert actions == ["new-empty-window", MANAGE_ACTION_ID]
    assert parser.get(f"Desktop Action {MANAGE_ACTION_ID}", "Name") == MANAGE_TEXT


def test_manage_action_sits_above_the_task_manager_entries() -> None:
    """Last of all our actions, so Plasma's own entries follow it.

    Plasma appends "Pin to Task Manager" / "Unpin from Task Manager" after the
    whole Actions= list, so being last is what places this directly above them
    rather than between the pinned and the recents blocks.
    """
    pinned, recents = _entries()
    parser = _parse(build_desktop_content(VENDOR_DESKTOP, pinned, recents))
    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]

    assert actions[-1] == MANAGE_ACTION_ID
    assert actions[-2] == "_SEPARATOR_"
    # Nothing of ours follows it.
    assert not any(a.startswith(ACTION_PREFIX) for a in actions[actions.index(MANAGE_ACTION_ID) + 1 :])


def test_manage_action_launches_the_dialog() -> None:
    """It is a real action: it runs the CLI's ``manage``, and is not inert."""
    pinned, recents = _entries()
    parser = _parse(build_desktop_content(VENDOR_DESKTOP, pinned, recents))
    section = f"Desktop Action {MANAGE_ACTION_ID}"

    assert MANAGE_TEXT == "Manage Pinned Files…"
    assert parser.get(section, "Name") == MANAGE_TEXT
    assert parser.get(section, "Exec") == f"{desktop_launcher_command()} manage"
    assert parser.get(section, "Exec") != NOOP_EXEC
    # Plasma skips actions carrying NoDisplay, so it stays visible.
    assert not parser.has_option(section, "NoDisplay")
    # Its icon is distinct from both headings, so it cannot read as one.
    assert MANAGE_ICON not in (PINNED_CAPTION_ICON, RECENT_CAPTION_ICON)


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


def test_pinned_caption_absent_without_pinned() -> None:
    """No pins => no pinned heading, and no separator left dangling above it."""
    _, recents = _entries()
    content = build_desktop_content(VENDOR_DESKTOP, [], recents)
    parser = _parse(content)
    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]

    assert not parser.has_section(f"Desktop Action {PINNED_CAPTION_ACTION_ID}")
    assert set(_captions(content)) == {RECENT_CAPTION_ACTION_ID}
    # One separator opening the recents block, one closing it, then the manager.
    assert actions.count("_SEPARATOR_") == 2
    assert actions[-2] == "_SEPARATOR_"
    assert actions[-1] == MANAGE_ACTION_ID
    assert actions[actions.index(RECENT_CAPTION_ACTION_ID) - 1] == "_SEPARATOR_"


def test_captions_not_duplicated_on_regeneration() -> None:
    """Feeding the generator its own output keeps exactly one of each caption."""
    pinned, recents = _entries()
    first = build_desktop_content(VENDOR_DESKTOP, pinned, recents)
    second = build_desktop_content(first, pinned, recents)
    assert second == first
    actions = [a for a in _parse(second).get("Desktop Entry", "Actions").split(";") if a]
    for caption in CAPTION_IDS:
        assert actions.count(caption) == 1


def test_desktop_launcher_names_the_configuration_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, write_config: WriteConfig
) -> None:
    """A click is a bare process launch: it must be told which file to read.

    Regression: the data directory used to be the only thing embedded, so a
    click resolved the rest from defaults and silently rewrote the menu with
    them. Naming the file carries every setting at once, including ones added
    later, and works from a working directory that is not this one.
    """
    import kde_vscode_jumplist.desktop_entry as de

    monkeypatch.setattr(de, "_command_launches_cli", lambda command: True)
    monkeypatch.setattr(de, "_running_program", lambda: None)
    monkeypatch.setattr(de, "installed_cli_path", lambda: None)
    monkeypatch.setattr(de, "packaged_cli_path", lambda: None)
    monkeypatch.setattr(de.shutil, "which", lambda name: None)
    path = write_config(
        data_dir=str(tmp_path / "data"),
        pinned_position="below",
        max_recents=25,
    )

    command = desktop_launcher_command()

    assert config_arguments() == ["--config", str(path)]
    # The option has to precede the subcommand, which is where argparse wants
    # it -- the generated actions append "open <id>" to this prefix.
    assert command == f"{resolve_cli_command()} {format_exec(config_arguments())}"
    assert f"--config {path}" in command


def test_desktop_launcher_names_the_configuration_with_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even a configuration that sets nothing is named as the file to read.

    "Unset means the default applies everywhere" is exactly the assumption that
    broke: a click is not the process that generated the menu, so it has to be
    told where the settings are rather than left to find them again.
    """
    import kde_vscode_jumplist.desktop_entry as de

    monkeypatch.setattr(de, "_command_launches_cli", lambda command: True)
    monkeypatch.setattr(de, "_running_program", lambda: None)
    monkeypatch.setattr(de.shutil, "which", lambda name: None)

    assert config.current().path == config.installed_path()
    assert config_arguments() == ["--config", str(config.installed_path())]


def test_vendor_separator_is_preserved() -> None:
    """A separator the vendor placed for its own purposes is left alone."""
    vendor = VENDOR_DESKTOP.replace(
        "Actions=new-empty-window;", "Actions=new-empty-window;_SEPARATOR_;"
    )
    parser = _parse(build_desktop_content(vendor, [], []))
    actions = [a for a in parser.get("Desktop Entry", "Actions").split(";") if a]
    assert actions == ["new-empty-window", "_SEPARATOR_", MANAGE_ACTION_ID]


def test_write_user_desktop_entry_atomic_and_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    vendor_desktop: Path,
    write_config: WriteConfig,
) -> None:
    import kde_vscode_jumplist.desktop_entry as de

    apps_dir = tmp_path / "applications"
    write_config(apps_dir=str(apps_dir))
    pinned, recents = _entries()

    first = de.write_user_desktop_entry(vendor_desktop, "code", pinned, recents)
    assert first is not None and first.is_file()
    # Identical content => no rewrite.
    second = de.write_user_desktop_entry(vendor_desktop, "code", pinned, recents)
    assert second is None


def test_generated_file_marked_and_detected(
    tmp_path: Path,
    vendor_desktop: Path,
    write_config: WriteConfig,
) -> None:
    import kde_vscode_jumplist.desktop_entry as de

    apps_dir = tmp_path / "applications"
    write_config(apps_dir=str(apps_dir))
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
    assert resolve_cli_command() == format_exec(argv)


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
    installed = bin_dir / de.PROG
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
    installed = bin_dir / de.PROG
    installed.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("XDG_BIN_HOME", str(bin_dir))
    monkeypatch.setattr(de, "_running_program", lambda: None)
    monkeypatch.setattr(de.shutil, "which", lambda name: str(installed) if name == de.PROG else None)
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
        if exec_value == NOOP_EXEC:
            continue  # the inert caption is intentionally not a launcher
        assert exec_value.startswith(prefix + " "), exec_value
        checked += 1
    assert checked == 5  # 1 pinned + 3 recents (the workspace is excluded) + manager



def test_discovery_skips_generated_user_desktop_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    vendor_desktop: Path,
    xdg_dirs: dict[str, Path],
    write_config: WriteConfig,
) -> None:
    """The vendor file must be found even when a generated copy shadows it."""
    import kde_vscode_jumplist.desktop_entry as de
    import kde_vscode_jumplist.discovery as disc

    # _find_desktop_file searches ~/.local/share/applications first (HOME is
    # redirected to tmp_path by the xdg_dirs fixture); put the generated copy
    # there so it shadows the system vendor file.
    apps_dir = tmp_path / ".local" / "share" / "applications"
    write_config(apps_dir=str(apps_dir))

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
