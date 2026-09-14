"""Tests for the Settings window and the configuration writing behind it.

Two halves, tested separately. The file-writing half needs no display at all --
what a setting may be, and how a change is written back, is the parser's business
-- and the window half is about the *view*: which control each setting gets, what
the form shows, and what happens to a value that will not parse.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from kde_vscode_jumplist import config, settings
from kde_vscode_jumplist.settings import (
    BROWSE_LABEL,
    CLOSE_LABEL,
    DETECTED_PLACEHOLDER,
    FIELDS,
    GROUPS,
    KIND_LABELS,
    POSITION_CHOICES,
    REQUIRED_PLACEHOLDER,
    RESTORE_LABEL,
    SETTING_KEY_PROPERTY,
    SETTINGS_ICON,
    SETTINGS_LABEL,
    editor_in,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_CONFIG = REPO_ROOT / "config.toml"
# The short README, and the reference it links to; the example being checked
# against the defaults is in the second.
DETAILS = REPO_ROOT / "DETAILS.md"

WriteConfig = Callable[..., Path]


# --- the settings the form has to cover -----------------------------------


def test_every_setting_has_exactly_one_field() -> None:
    """A setting added to the parser cannot be left out of the window.

    The two lists have to agree, and this is what keeps them agreeing: a setting
    with no control would be one the window silently could not change.
    """
    keys = [field.key for field in FIELDS]
    assert len(keys) == len(set(keys)), "a setting has two fields"
    assert set(keys) == set(config.KNOWN_KEYS)


def test_every_field_explains_itself() -> None:
    """Labels and help text, since a bare setting name is not documentation."""
    for field in FIELDS:
        assert field.label and field.label != field.key, field.key
        assert field.help, field.key
        assert field.editor in (
            settings.DIRECTORY,
            settings.FILE,
            settings.COMMAND,
            settings.COUNT,
            settings.KINDS,
            settings.CHOICE,
        ), field.key


def test_only_paths_offer_a_browser() -> None:
    """A Browse button beside a number or a drop-down would be a lie."""
    for field in FIELDS:
        is_path = field.editor in (settings.DIRECTORY, settings.FILE, settings.COMMAND)
        assert field.browse is is_path, field.key


def test_the_form_is_grouped_by_subject() -> None:
    """Every field belongs to a heading, and each heading has fields."""
    grouped = [field.key for group in GROUPS for field in group.fields]
    assert grouped == [field.key for field in FIELDS]
    assert all(group.title and group.fields for group in GROUPS)
    # The two directories this tool writes to come before what it reads.
    assert GROUPS[0].fields[0].key == config.REQUIRED_PATH_KEYS[0]


def test_the_kind_boxes_cover_the_kinds() -> None:
    assert {kind for kind, _label in KIND_LABELS} == set(config.ENTRY_KINDS)
    assert all(label for _kind, label in KIND_LABELS)


def test_the_position_choices_cover_what_the_parser_accepts() -> None:
    """Both spellings the parser normalizes to, and nothing else."""
    assert [value for value, _label in POSITION_CHOICES] == ["above", "below"]
    assert all(label for _value, label in POSITION_CHOICES)


# --- the shipped defaults -------------------------------------------------


def test_defaults_name_every_setting() -> None:
    """Restore Defaults must restore all of them, not the ones it happens to know.

    ``config.defaults()`` raises for a missing one, so this covers the shipped
    file being complete as well as the values being the ones a first run uses.
    """
    shipped = config.defaults()
    assert set(shipped) >= set(config.KNOWN_KEYS)
    assert shipped["data_dir"] == "~/.config/kde-vscode-jumplist"
    assert shipped["max_recents"] == 10
    assert shipped["exclude_kinds"] == ["workspace"]
    assert shipped["pinned_position"] in ("above", "below")


def test_the_defaults_are_readable_as_a_configuration(write_config: WriteConfig) -> None:
    """They are values the parser accepts, so restoring cannot break the file."""
    path = write_config()

    config.update_file(config.defaults(), path)

    assert config.read(path).max_recents == config.defaults()["max_recents"]


def test_defaults_are_the_values_the_reference_shows() -> None:
    """The documented example is what the project starts from, and vice versa.

    One of the two would otherwise drift, and the example is what a new user
    copies while Restore Defaults is what they get back.
    """
    shipped = config.defaults()
    example = re.search(r"```toml\n(.*?)```", DETAILS.read_text(encoding="utf-8"), re.DOTALL)
    assert example, "DETAILS.md has no TOML example"

    shown = {}
    for line in example.group(1).splitlines():
        key, separator, value = line.partition(" = ")
        if separator:
            shown[key.strip()] = value.strip()
    assert set(shown) == set(config.KNOWN_KEYS)
    for key, value in shipped.items():
        assert config.render_value(value) == shown[key], key


def test_the_checkouts_config_is_a_complete_file() -> None:
    """What the repository ships still parses, whatever the defaults say."""
    raw = config.raw_values(REPO_CONFIG)
    assert set(raw) >= set(config.KNOWN_KEYS)
    assert config.read(REPO_CONFIG)


# --- writing the file back ------------------------------------------------


def test_update_file_changes_only_what_was_asked(write_config: WriteConfig) -> None:
    path = write_config(max_recents=10, pinned_position="below")
    before = path.read_text(encoding="utf-8")

    config.update_file({"max_recents": 25}, path)
    after = path.read_text(encoding="utf-8")

    assert "max_recents = 25" in after
    assert "pinned_position = \"below\"" in after
    # Everything else is byte-identical, comments included.
    assert after.replace("max_recents = 25", "max_recents = 10") == before


def test_update_file_keeps_the_documentation() -> None:
    """The comments are why the file is worth reading; writing must not erase them."""
    comments_before = REPO_CONFIG.read_text(encoding="utf-8").count("#")

    path = REPO_CONFIG.parent / ".pytest-config-check.toml"
    path.write_text(REPO_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    try:
        config.update_file({"max_recents": 33}, path)
        text = path.read_text(encoding="utf-8")
        assert text.count("#") == comments_before
        # Including an inline comment on a changed line.
        config.update_file({"state_db": "/tmp/x.vscdb"}, path)
        line = next(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("state_db =")
        )
        assert line == 'state_db = "/tmp/x.vscdb"   # the profile database itself'
    finally:
        path.unlink(missing_ok=True)


def test_update_file_keeps_a_path_exactly_as_written(write_config: WriteConfig) -> None:
    """``~`` stays ``~``: the file is not the place to expand it."""
    path = write_config(data_dir="~/.config/kde-vscode-jumplist")

    config.update_file({"max_recents": 4}, path)

    data_dir = next(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("data_dir =")
    )
    assert data_dir == 'data_dir = "~/.config/kde-vscode-jumplist"'
    assert str(Path.home()) not in data_dir


def test_update_file_appends_a_setting_the_file_lacks(write_config: WriteConfig) -> None:
    """A file written by an older version gains what it is missing."""
    path = write_config()
    path.write_text(
        "\n".join(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.startswith("pinned_position")
        )
        + "\n",
        encoding="utf-8",
    )
    config.forget()

    config.update_file({"pinned_position": "above"}, path)

    assert config.read(path).pinned_position == "above"


def test_update_file_replaces_a_renamed_setting_in_place(
    write_config: WriteConfig,
) -> None:
    """The old spelling is not left behind beside the new one."""
    path = write_config()
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            'pinned_position = "above"', 'favorites_position = "above"'
        ),
        encoding="utf-8",
    )
    config.forget()

    config.update_file({"pinned_position": "below"}, path)
    text = path.read_text(encoding="utf-8")

    assert "pinned_position = \"below\"" in text
    assert "favorites_position" not in text


def test_update_file_leaves_the_result_readable(write_config: WriteConfig) -> None:
    path = write_config()

    config.update_file({"exclude_kinds": ["folder", "file"], "max_recents": 0}, path)

    resolved = config.read(path)
    assert resolved.exclude_kinds == frozenset({"folder", "file"})
    assert resolved.max_recents == 0


def test_update_file_forgets_the_cached_parse(write_config: WriteConfig) -> None:
    """The process that wrote the file must not keep serving the old values."""
    path = write_config(max_recents=10)
    assert config.current().max_recents == 10

    config.update_file({"max_recents": 42}, path)

    assert config.current().max_recents == 42


@pytest.mark.parametrize(
    "value, expected",
    [
        (5, "5"),
        ("x", '"x"'),
        (["b", "a"], '["b", "a"]'),
        (frozenset({"b", "a"}), '["a", "b"]'),  # a set has no order to preserve
        (True, "true"),
    ],
)
def test_render_value(value: object, expected: str) -> None:
    assert config.render_value(value) == expected


def test_render_value_refuses_what_it_cannot_write() -> None:
    """Better a crash in the caller than a file the parser will reject."""
    with pytest.raises(TypeError):
        config.render_value({"nested": 1})


def test_raw_values_are_the_file_not_the_resolution(write_config: WriteConfig) -> None:
    """Exactly what the window needs: what is written, not what it resolves to."""
    write_config(data_dir="~/.config/kde-vscode-jumplist", max_recents=7)

    raw = config.raw_values()
    assert raw["data_dir"] == "~/.config/kde-vscode-jumplist"
    assert raw["max_recents"] == 7
    # The detect settings stay empty strings rather than becoming None.
    assert raw["vscode_dir"] == ""


# --- the window -----------------------------------------------------------


@pytest.fixture()
def window(
    qt_modules: SimpleNamespace, write_config: WriteConfig
) -> settings.SettingsWindow:
    """A settings window over this test's configuration file."""
    from kde_vscode_jumplist import qtview

    qtview.application(qt_modules.QtWidgets)
    write_config()  # the standard values, so the form has something to show
    return settings.build_settings_dialog()


def _row(window: settings.SettingsWindow, key: str):
    """The form row for one setting."""
    QtWidgets = settings.load_qt()[2]
    for widget in window.dialog.findChildren(QtWidgets.QWidget):
        if widget.property(SETTING_KEY_PROPERTY) == key:
            return widget
    raise AssertionError(f"no field for {key!r}")


def _editor(window: settings.SettingsWindow, key: str):
    return editor_in(_row(window, key))


def _boxes(window: settings.SettingsWindow, key: str) -> dict[str, object]:
    """The check boxes of a kinds field, by kind."""
    QtWidgets = settings.load_qt()[2]
    found = {}
    for box in _row(window, key).findChildren(QtWidgets.QCheckBox):
        found[box.text()] = box
    return found


def test_each_setting_gets_the_control_that_fits_it(
    window: settings.SettingsWindow, qt_modules: SimpleNamespace
) -> None:
    """The point of the window: a path is typed, a count is counted, a kind is
    ticked, and the one setting with two spellings is picked."""
    expected = {
        settings.DIRECTORY: qt_modules.QtWidgets.QLineEdit,
        settings.FILE: qt_modules.QtWidgets.QLineEdit,
        settings.COMMAND: qt_modules.QtWidgets.QLineEdit,
        settings.COUNT: qt_modules.QtWidgets.QSpinBox,
        settings.CHOICE: qt_modules.QtWidgets.QComboBox,
    }
    for field in FIELDS:
        editor = _editor(window, field.key)
        if field.editor == settings.KINDS:
            boxes = _boxes(window, field.key)
            assert len(boxes) == len(config.ENTRY_KINDS), field.key
            continue
        assert isinstance(editor, expected[field.editor]), (
            f"{field.key}: {type(editor).__name__}"
        )


def test_paths_have_a_browse_button_and_numbers_do_not(
    window: settings.SettingsWindow,
) -> None:
    """A chooser belongs where the value is a location."""
    for field in FIELDS:
        has_browse = any(
            button.text() == BROWSE_LABEL
            for button in _row(window, field.key).findChildren(
                settings.load_qt()[2].QPushButton
            )
        )
        assert has_browse is field.browse, field.key


def test_the_count_cannot_go_negative(window: settings.SettingsWindow) -> None:
    """A spin box is what makes that impossible, rather than a rule to enforce."""
    editor = _editor(window, "max_recents")
    assert editor.minimum() == 0
    assert editor.maximum() >= config.defaults()["max_recents"]


def test_the_form_shows_the_file_as_written(
    qt_modules: SimpleNamespace, write_config: WriteConfig
) -> None:
    """Including ``~``: the form edits the file's text, not the resolved paths."""
    from kde_vscode_jumplist import qtview

    qtview.application(qt_modules.QtWidgets)
    write_config(data_dir="~/.config/kde-vscode-jumplist", max_recents=7)

    window = settings.build_settings_dialog()

    assert _editor(window, "data_dir").text() == "~/.config/kde-vscode-jumplist"
    assert _editor(window, "max_recents").value() == 7


def test_the_window_says_which_file_it_edits(window: settings.SettingsWindow) -> None:
    """Two files can be called config.toml, and only one of them is in use."""
    QtWidgets = settings.load_qt()[2]
    shown = [label.text() for label in window.dialog.findChildren(QtWidgets.QLabel)]
    assert any(str(window.target) in text for text in shown)


def test_the_window_edits_the_file_in_use(window: settings.SettingsWindow) -> None:
    assert window.target == config.path()
    assert window.changed() is False  # nothing shown has been edited


def test_restore_defaults_is_leftmost_and_close_rightmost(
    window: settings.SettingsWindow, qt_modules: SimpleNamespace
) -> None:
    """The two footer buttons hold the two corners, as asked for."""
    dialog = window.dialog
    dialog.show()
    qt_modules.QtWidgets.QApplication.processEvents()
    try:
        buttons = {
            button.text(): button
            for button in dialog.findChildren(qt_modules.QtWidgets.QPushButton)
            if button.text() in (RESTORE_LABEL, CLOSE_LABEL)
        }
        assert set(buttons) == {RESTORE_LABEL, CLOSE_LABEL}

        restore = buttons[RESTORE_LABEL].mapTo(dialog, qt_modules.QtCore.QPoint(0, 0))
        close = buttons[CLOSE_LABEL].mapTo(dialog, qt_modules.QtCore.QPoint(0, 0))
        assert restore.x() < close.x()
        assert restore.y() == close.y()
        # Nothing is to the left of Restore Defaults, nor to the right of Close.
        for button in dialog.findChildren(qt_modules.QtWidgets.QPushButton):
            spot = button.mapTo(dialog, qt_modules.QtCore.QPoint(0, 0))
            if button.text() not in (RESTORE_LABEL, CLOSE_LABEL) and spot.y() == restore.y():
                assert restore.x() <= spot.x() <= close.x(), button.text()
    finally:
        dialog.hide()


def test_changing_a_setting_writes_it(
    window: settings.SettingsWindow, write_config: WriteConfig
) -> None:
    """A count goes up, and the file says so -- nothing else moves."""
    before = window.target.read_text(encoding="utf-8")

    _editor(window, "max_recents").setValue(25)

    after = window.target.read_text(encoding="utf-8")
    assert "max_recents = 25" in after
    assert after.replace("max_recents = 25", "max_recents = 10") == before
    assert window.changed() is True
    assert config.read(window.target).max_recents == 25


def test_a_text_field_saves_when_it_is_left(
    window: settings.SettingsWindow,
) -> None:
    """Not on every keystroke: editingFinished is when the value is meant."""
    editor = _editor(window, "vscode_dir")
    editor.setText("/srv/vscode")
    assert "vscode_dir = " in window.target.read_text(encoding="utf-8")
    assert 'vscode_dir = ""' in window.target.read_text(encoding="utf-8")  # not yet

    editor.editingFinished.emit()

    assert 'vscode_dir = "/srv/vscode"' in window.target.read_text(encoding="utf-8")


def test_ticking_a_kind_lists_it_in_the_file(window: settings.SettingsWindow) -> None:
    boxes = _boxes(window, "exclude_kinds")
    boxes["Folders"].setChecked(True)

    text = window.target.read_text(encoding="utf-8")
    assert 'exclude_kinds = ["folder", "workspace"]' in text
    assert config.read(window.target).exclude_kinds == frozenset({"folder", "workspace"})

    boxes["Workspaces (.code-workspace)"].setChecked(False)
    assert config.read(window.target).exclude_kinds == frozenset({"folder"})


def test_an_empty_kind_list_survives_the_round_trip(window: settings.SettingsWindow) -> None:
    """``[]`` means "exclude nothing", which is not the same as "unset"."""
    boxes = _boxes(window, "exclude_kinds")
    for box in boxes.values():
        box.setChecked(False)

    assert "exclude_kinds = []" in window.target.read_text(encoding="utf-8")
    assert config.read(window.target).exclude_kinds == frozenset()


def test_the_position_dropdown_writes_the_spelling_the_parser_wants(
    window: settings.SettingsWindow,
) -> None:
    editor = _editor(window, "pinned_position")
    assert editor.currentData() == config.raw_values(window.target)["pinned_position"]

    editor.setCurrentIndex(0)  # "above"

    assert 'pinned_position = "above"' in window.target.read_text(encoding="utf-8")


def test_a_value_that_will_not_parse_is_refused(
    window: settings.SettingsWindow,
) -> None:
    """The file is put back, and the reason is shown.

    The parser is what decides, so the window cannot accept something the tool
    would then refuse to start with.
    """
    before = window.target.read_text(encoding="utf-8")

    editor = _editor(window, "data_dir")
    editor.setText("")  # the data directory has to point somewhere
    editor.editingFinished.emit()

    assert window.target.read_text(encoding="utf-8") == before
    status = _status(window)
    assert "data_dir" in status.text()
    # And the file is still one the tool would read.
    assert config.read(window.target)


def _status(window: settings.SettingsWindow):
    """The line the window reports on."""
    QtWidgets = settings.load_qt()[2]
    for label in window.dialog.findChildren(QtWidgets.QLabel):
        if label.property(settings.STATUS_PROPERTY):
            return label
    raise AssertionError("no status line in the window")


def test_a_refused_value_leaves_the_earlier_edits_alone(
    window: settings.SettingsWindow,
) -> None:
    """A rejected change must not undo the ones already written."""
    _editor(window, "max_recents").setValue(12)
    assert "max_recents = 12" in window.target.read_text(encoding="utf-8")

    editor = _editor(window, "data_dir")
    editor.setText("")
    editor.editingFinished.emit()

    assert "max_recents = 12" in window.target.read_text(encoding="utf-8")


def test_restore_defaults_writes_the_shipped_values(
    window: settings.SettingsWindow, qt_modules: SimpleNamespace
) -> None:
    """Every setting, not just the ones that had been changed."""
    _editor(window, "max_recents").setValue(99)
    _editor(window, "vscode_dir").setText("/srv/vscode")
    _editor(window, "vscode_dir").editingFinished.emit()

    button = next(
        button
        for button in window.dialog.findChildren(qt_modules.QtWidgets.QPushButton)
        if button.text() == RESTORE_LABEL
    )
    button.click()

    shipped = config.defaults()
    raw = config.raw_values(window.target)
    for key, value in shipped.items():
        assert config.render_value(raw[key]) == config.render_value(value), key
    # And the values are shown as well as written.
    assert _editor(window, "max_recents").value() == shipped["max_recents"]
    assert _editor(window, "vscode_dir").text() == ""


def test_close_saves_a_value_that_was_still_being_typed(
    window: settings.SettingsWindow, qt_modules: SimpleNamespace
) -> None:
    """Typing then reaching straight for Close must not drop the edit.

    A field saves when it is left, and a button click does not always leave it --
    so the window writes once more as it closes.
    """
    _editor(window, "vscode_dir").setText("/srv/vscode")
    assert 'vscode_dir = ""' in window.target.read_text(encoding="utf-8")

    next(
        button
        for button in window.dialog.findChildren(qt_modules.QtWidgets.QPushButton)
        if button.text() == CLOSE_LABEL
    ).click()

    assert 'vscode_dir = "/srv/vscode"' in window.target.read_text(encoding="utf-8")


def test_close_is_refused_rather_than_discarding_a_bad_value(
    window: settings.SettingsWindow, qt_modules: SimpleNamespace
) -> None:
    """The window stays open with the reason instead of closing over the edit."""
    _editor(window, "data_dir").setText("")

    next(
        button
        for button in window.dialog.findChildren(qt_modules.QtWidgets.QPushButton)
        if button.text() == CLOSE_LABEL
    ).click()

    assert window.dialog.result() != qt_modules.QtWidgets.QDialog.DialogCode.Accepted
    assert "data_dir" in _status(window).text()


def test_close_closes_when_there_is_nothing_to_fix(
    window: settings.SettingsWindow, qt_modules: SimpleNamespace
) -> None:
    next(
        button
        for button in window.dialog.findChildren(qt_modules.QtWidgets.QPushButton)
        if button.text() == CLOSE_LABEL
    ).click()

    assert window.dialog.result() == qt_modules.QtWidgets.QDialog.DialogCode.Accepted


def test_building_the_window_does_not_write_anything(
    write_config: WriteConfig, qt_modules: SimpleNamespace
) -> None:
    """Showing the form is not an edit, whatever the file says."""
    from kde_vscode_jumplist import qtview

    qtview.application(qt_modules.QtWidgets)
    path = write_config(max_recents=3, pinned_position="below")
    before = path.read_text(encoding="utf-8")

    window = settings.build_settings_dialog()

    assert path.read_text(encoding="utf-8") == before
    assert window.changed() is False


def test_the_window_needs_a_configuration_to_edit(
    qt_modules: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no file there is nothing to show, and that is said plainly."""
    from kde_vscode_jumplist import qtview

    qtview.application(qt_modules.QtWidgets)
    config.installed_path().unlink()
    config.forget()

    with pytest.raises(settings.DialogUnavailable, match="no configuration file"):
        settings.build_settings_dialog()


def test_settings_are_reachable_from_the_other_window() -> None:
    """The button exists, is labelled, and has an icon to fall back from."""
    assert SETTINGS_LABEL == "Settings"
    assert SETTINGS_ICON


def test_no_window_is_opened_by_importing_the_module() -> None:
    """Qt stays out of the import path, or the CLI would need it to run at all."""
    import subprocess
    import sys

    result = subprocess.run(  # noqa: S603 - argv list, no shell
        [sys.executable, "-c", "from kde_vscode_jumplist import settings, manage"],
        env={"PATH": "/usr/bin", "PYTHONPATH": str(REPO_ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    # With no DISPLAY and no WAYLAND_DISPLAY: importing a module must not need
    # either, or every command would.
    assert result.returncode == 0, result.stderr
