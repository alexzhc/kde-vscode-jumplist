"""The Settings window: every setting in ``config.toml``, as a form.

Opened from the footer of the pinned-entries window, which is where the settings
are noticed in the first place: the menu is built from this file, so the window
that arranges it is the window that should also say how it is built.

Each setting gets the control that matches what it may hold -- a text field with
a Browse button for a path, a spin box for a count, a check box per entry kind, a
drop-down for the one setting with two spellings -- rather than every setting
being a line of TOML in a text area. The kinds are check boxes because "which of
these three" is a question about a set, and the count is a spin box because it
cannot be negative.

Two decisions are worth stating, because they are not obvious from the widgets:

**It edits the file that is in use** -- whatever ``--config`` named, or the one
that was found. That is the same file the service reads, so there is no second
copy to keep in step, and the window says which file it is editing along the top
for exactly that reason.

**It never writes a value the tool would then refuse to read.** A change is
written and then parsed again with the same reader the CLI uses; if that fails the
file is put back exactly as it was and the reason is shown. Validity is therefore
defined in one place -- the parser -- instead of being a second set of rules that
could disagree with it, which also means the window cannot leave behind a
configuration that stops the service from starting.

Closing after a change that will not parse keeps the window open so the problem
can be fixed, rather than closing over a discarded edit. There is no Cancel:
every valid change is already written, and the button that undoes a mistake is
Restore Defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import config
from .models import ENTRY_FILE, ENTRY_FOLDER, ENTRY_WORKSPACE
from .qtview import (
    DialogUnavailable,
    application,
    exec_dialog,
    load_qt,
    require_display,
    themed_icon,
    use_platform_theme,
)
from .util import atomic_write_text

WINDOW_TITLE = "Settings"
# How the button that opens this window reads, in the other window's footer.
SETTINGS_LABEL = "Settings"
# Icon for the footer button that opens this window, and for the window itself.
SETTINGS_ICON = "configure"

RESTORE_LABEL = "Restore Defaults"
CLOSE_LABEL = "Close"
BROWSE_LABEL = "Browse\u2026"

# The property each field's row carries, so a setting can be found by name rather
# than by its position in the form. The property is on the row; the control that
# holds the value is inside it (see editor_in).
SETTING_KEY_PROPERTY = "settingKey"
STATUS_PROPERTY = "settingsStatus"

# Shown in a field left empty. Which of the two depends on the setting: the two
# directories have to point somewhere, everything else is found by detection.
DETECTED_PLACEHOLDER = "detected automatically"
REQUIRED_PLACEHOLDER = "required"

# How a setting is edited. One per shape a setting can have; see Field.
DIRECTORY = "directory"
FILE = "file"
COMMAND = "command"
COUNT = "count"
KINDS = "kinds"
CHOICE = "choice"

# The spin box's range. The upper end is not a rule the parser enforces, just a
# control that has to stop somewhere.
MAX_RECENTS_LIMIT = 1000

# The controls a field's value lives in, most specific first. Used to find the
# editor inside a row.
_EDITOR_CLASSES = ("QSpinBox", "QComboBox", "QLineEdit")


@dataclass(frozen=True)
class Field:
    """One setting, as the form presents it."""

    key: str
    label: str
    help: str
    editor: str
    # Whether a Browse button belongs beside it. Only paths are worth choosing.
    browse: bool = True


@dataclass(frozen=True)
class Group:
    """A set of settings that belong together, under one heading."""

    title: str
    fields: tuple[Field, ...]


# Every setting, in the order the form shows them and grouped by what they are
# about rather than by their type: the two directories this tool writes to, then
# the VS Code installation it reads, then how the menu is built from both.
GROUPS: tuple[Group, ...] = (
    Group(
        "Where this tool keeps its files",
        (
            Field(
                "data_dir",
                "Data directory",
                "Holds pinned.json, entries.json and sync.lock.",
                DIRECTORY,
            ),
            Field(
                "apps_dir",
                "Menu file directory",
                "Where the generated .desktop file is written, for Plasma to read.",
                DIRECTORY,
            ),
        ),
    ),
    Group(
        "Which VS Code is read",
        (
            Field(
                "vscode_dir",
                "Profile data directory",
                "The one holding User/globalStorage/state.vscdb.",
                DIRECTORY,
            ),
            Field(
                "state_db",
                "Profile database",
                "That database itself, instead of the directory above.",
                FILE,
            ),
            Field(
                "shared_db",
                "Shared database",
                "Where newer versions keep the recent list.",
                FILE,
            ),
            Field(
                "desktop",
                "Desktop entry",
                "The vendor .desktop file to copy and extend.",
                FILE,
            ),
            Field(
                "exec",
                "VS Code executable",
                "What opens an entry when the menu is clicked.",
                COMMAND,
            ),
        ),
    ),
    Group(
        "How the menu is built",
        (
            Field(
                "max_recents",
                "Recent entries",
                "How many recent entries to list. 0 lists none of them.",
                COUNT,
                browse=False,
            ),
            Field(
                "exclude_kinds",
                "Leave out of the recents",
                "Kinds kept out of the recent list. Pinned entries are never "
                "filtered, so a pinned workspace still appears.",
                KINDS,
                browse=False,
            ),
            Field(
                "pinned_position",
                "Pinned entries",
                "Which block comes first in the menu.",
                CHOICE,
                browse=False,
            ),
        ),
    ),
)

# Every field, flat, in form order.
FIELDS: tuple[Field, ...] = tuple(field for group in GROUPS for field in group.fields)

# The kinds, with how each reads on its check box. The value is the kind, which
# is what goes in the file.
KIND_LABELS: tuple[tuple[str, str], ...] = (
    (ENTRY_FOLDER, "Folders"),
    (ENTRY_FILE, "Files"),
    (ENTRY_WORKSPACE, "Workspaces (.code-workspace)"),
)

# The two orders, with how each reads in the drop-down.
POSITION_CHOICES: tuple[tuple[str, str], ...] = (
    ("above", "First \u2014 pinned entries at the top"),
    ("below", "Last \u2014 recent entries at the top"),
)


@dataclass(frozen=True)
class SettingsWindow:
    """A built settings window: the dialog, and what it has done.

    The dialog is returned rather than only shown so the window can be inspected
    without an event loop, and the two questions a caller has -- what does the
    form say, and was anything written -- are answered here rather than by
    reaching into the widgets.
    """

    dialog: object
    target: Path
    values: object  # callable: () -> dict[str, object]
    changed: object  # callable: () -> bool


def editor_in(row):
    """The control holding a field's value, inside its row.

    A browsed path is a line edit and a Browse button in a row; everything else
    is the control itself. Callers want the value either way.

    The row is checked before its children because a spin box *contains* a line
    edit -- searching first would find the spin box's own text field and hand back
    something that cannot hold a number.
    """
    QtWidgets = load_qt()[2]
    classes = [getattr(QtWidgets, name) for name in _EDITOR_CLASSES]
    for control in classes:
        if isinstance(row, control):
            return row
    for control in classes:
        found = row.findChild(control)
        if found is not None:
            return found
    return row


def build_settings_dialog(parent=None) -> SettingsWindow:
    """Build the settings window: a control per setting, and a footer.

    Built separately from :func:`run_settings_dialog` so the window can be built,
    driven and inspected without an event loop.
    """
    QtCore, QtGui, QtWidgets = load_qt()

    target = config.path()
    if target is None or not target.is_file():
        looked_in = ", ".join(str(candidate) for candidate in config.search_paths())
        raise DialogUnavailable(f"no configuration file to edit (looked in {looked_in})")

    # The values as the file writes them, which is what the fields show and what a
    # change is compared against. Raw rather than parsed: a path stays ``~/...``
    # the way the file reads, instead of being shown expanded and written back
    # absolute.
    baseline = dict(config.raw_values(target))
    shipped = config.defaults()
    state = {"changed": False}

    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle(WINDOW_TITLE)
    dialog.setWindowIcon(themed_icon(SETTINGS_ICON, 0, QtGui))
    dialog.resize(720, 640)

    outer = QtWidgets.QVBoxLayout(dialog)

    # Which file is being edited, said plainly. Two files can be called
    # config.toml and only one is in use, so a window that edits settings without
    # saying which file it writes to would be the wrong kind of quiet.
    heading = QtWidgets.QLabel(f"Editing {target}")
    heading.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
    heading.setEnabled(False)  # dimmed: context, not content
    outer.addWidget(heading)

    # The form scrolls, so the window can be small on a small screen without the
    # buttons going off the bottom with it.
    body = QtWidgets.QScrollArea()
    body.setWidgetResizable(True)
    body.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
    form_host = QtWidgets.QWidget()
    form_column = QtWidgets.QVBoxLayout(form_host)
    body.setWidget(form_host)
    outer.addWidget(body, 1)

    rows: dict[str, object] = {}
    kind_boxes: dict[str, object] = {}

    def report(text: str, error: bool = False) -> None:
        status.setText(text)
        status.setStyleSheet("color: #c0392b;" if error else "color: #1e8449;")

    def widget_values() -> dict[str, object]:
        """What the form currently says, as values the file can hold."""
        values: dict[str, object] = {}
        for field in FIELDS:
            widget = editor_in(rows[field.key])
            if field.editor == KINDS:
                values[field.key] = sorted(
                    kind for kind, box in kind_boxes.items() if box.isChecked()
                )
            elif field.editor == COUNT:
                values[field.key] = widget.value()
            elif field.editor == CHOICE:
                values[field.key] = widget.currentData()
            else:
                values[field.key] = widget.text().strip()
        return values

    def save() -> bool:
        """Write whatever the form has changed; False when it cannot be written.

        Only the changed settings are written, so untouched lines stay exactly as
        they were -- including a path written with ``~``, which would otherwise
        come back expanded.
        """
        values = widget_values()
        edits = {
            key: value
            for key, value in values.items()
            if not _same(value, baseline.get(key, shipped[key]))
        }
        if not edits:
            return True

        previous = target.read_text(encoding="utf-8")
        try:
            config.update_file(edits, target)
            # Read it back with the reader the CLI uses: that is what makes this
            # window unable to leave behind a configuration that will not load.
            config.read(target)
        except config.ConfigError as error:
            atomic_write_text(target, previous)
            config.forget()
            report(str(error), error=True)
            return False

        baseline.update(config.raw_values(target))
        state["changed"] = True
        report(f"Saved {', '.join(sorted(edits))}.")
        return True

    def browse_row(editor, title: str, find_directory: bool):
        """A line edit with a Browse button beside it."""
        row = QtWidgets.QWidget()
        line = QtWidgets.QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.addWidget(editor, 1)
        button = QtWidgets.QPushButton(BROWSE_LABEL)
        line.addWidget(button)

        def choose() -> None:
            start = editor.text().strip()
            # A relative value belongs to the configuration file's directory, and
            # that is what the chooser should open at; an absolute one is its own
            # starting point.
            start_path = Path(start).expanduser() if start else Path.home()
            if not start_path.is_absolute():
                start_path = target.parent / start_path
            if find_directory:
                chosen = QtWidgets.QFileDialog.getExistingDirectory(
                    dialog, title, str(start_path)
                )
            else:
                chosen, _filters = QtWidgets.QFileDialog.getOpenFileName(
                    dialog, title, str(start_path)
                )
            if chosen:
                editor.setText(chosen)
                # Choosing a file ends the edit as much as leaving the field does.
                save()

        button.clicked.connect(choose)
        return row

    def editor_row(field: Field):
        """The row for one setting: its control, wired to save when it changes."""
        if field.editor == COUNT:
            widget = QtWidgets.QSpinBox()
            widget.setRange(0, MAX_RECENTS_LIMIT)
            widget.valueChanged.connect(lambda _value: save())
        elif field.editor == CHOICE:
            widget = QtWidgets.QComboBox()
            for value, text in POSITION_CHOICES:
                widget.addItem(text, value)
            widget.currentIndexChanged.connect(lambda _index: save())
        elif field.editor == KINDS:
            widget = QtWidgets.QWidget()
            column = QtWidgets.QVBoxLayout(widget)
            column.setContentsMargins(0, 0, 0, 0)
            for kind, label in KIND_LABELS:
                box = QtWidgets.QCheckBox(label)
                box.toggled.connect(lambda _checked: save())
                column.addWidget(box)
                kind_boxes[kind] = box
        else:
            widget = QtWidgets.QLineEdit()
            widget.setPlaceholderText(
                REQUIRED_PLACEHOLDER
                if field.key in config.REQUIRED_PATH_KEYS
                else DETECTED_PLACEHOLDER
            )
            widget.setToolTip(field.help)
            # editingFinished rather than textChanged: writing on every keystroke
            # would save a dozen intermediate paths, each of them briefly the
            # configuration in effect. Leaving the field -- or the Close button
            # taking focus -- is when the value is meant.
            widget.editingFinished.connect(save)
            widget = (
                browse_row(widget, field.label, field.editor == DIRECTORY)
                if field.browse
                else widget
            )
        widget.setProperty(SETTING_KEY_PROPERTY, field.key)
        rows[field.key] = widget
        return widget

    def show_values(values: dict[str, object]) -> None:
        """Put ``values`` into the form, without that counting as an edit.

        Signals are blocked throughout: otherwise displaying a value would save
        it, and "show me the defaults" would turn into a write on its own.
        """
        for field in FIELDS:
            value = values.get(field.key, shipped[field.key])
            if field.editor == KINDS:
                chosen = set(value or ())
                for kind, box in kind_boxes.items():
                    box.blockSignals(True)
                    box.setChecked(kind in chosen)
                    box.blockSignals(False)
                continue
            widget = editor_in(rows[field.key])
            widget.blockSignals(True)
            if field.editor == COUNT:
                widget.setValue(int(value))  # type: ignore[arg-type]
            elif field.editor == CHOICE:
                index = widget.findData(value)
                widget.setCurrentIndex(index if index >= 0 else 0)
            else:
                widget.setText(str(value or ""))
            widget.blockSignals(False)

    def restore_defaults() -> None:
        """Put every setting back to the values the project starts from."""
        show_values(shipped)
        save()

    def close() -> None:
        """Write anything still pending, and close unless it would be refused.

        A field is saved when it is left rather than as it is typed, so a value
        typed and followed straight by Close would otherwise be dropped. If the
        write is refused the window stays open with the reason: closing over a
        discarded edit would be the quiet kind of wrong.
        """
        if save():
            dialog.accept()

    for group in GROUPS:
        box = QtWidgets.QGroupBox(group.title)
        layout = QtWidgets.QFormLayout(box)
        layout.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        for field in group.fields:
            label = QtWidgets.QLabel(field.label)
            label.setToolTip(field.help)
            layout.addRow(label, editor_row(field))
        form_column.addWidget(box)
    form_column.addStretch(1)

    # The outcome of the last write: what went wrong, or what was saved. Its own
    # row above the buttons, so the two buttons keep the corners the footer gives
    # them.
    status = QtWidgets.QLabel("")
    status.setProperty(STATUS_PROPERTY, True)
    status.setWordWrap(True)
    outer.addWidget(status)

    footer = QtWidgets.QHBoxLayout()
    restore = QtWidgets.QPushButton(RESTORE_LABEL)
    restore.clicked.connect(restore_defaults)
    close_button = QtWidgets.QPushButton(CLOSE_LABEL)
    close_button.clicked.connect(close)
    footer.addWidget(restore)
    footer.addStretch(1)
    footer.addWidget(close_button)
    outer.addLayout(footer)

    show_values(baseline)
    report(f"Editing {target.name}")
    return SettingsWindow(
        dialog=dialog,
        target=target,
        values=widget_values,
        changed=lambda: state["changed"],
    )


def _same(left: object, right: object) -> bool:
    """Whether two setting values mean the same thing.

    Lists are compared as sets: the order the kinds are checked in is the order
    the form lists them, not something the user chose, and writing a different
    order back would be an edit with no meaning.
    """
    if isinstance(left, list) and isinstance(right, list):
        return sorted(left, key=str) == sorted(right, key=str)
    return left == right


# What the suite replaces to drive the window without an event loop; see
# qtview.exec_dialog.
_run = exec_dialog


def run_settings_dialog(parent=None) -> bool:
    """Show the settings window, blocking until it is closed.

    Returns True when the file was changed, so the caller can rebuild the menu
    from the new settings.
    """
    require_display()
    use_platform_theme()
    _QtCore, _QtGui, QtWidgets = load_qt()
    application(QtWidgets)

    window = build_settings_dialog(parent)
    _run(window.dialog)
    return bool(window.changed())
