"""Tests for the "Manage Pinned Files…" dialog.

Everything that decides what the window shows and writes lives in
``ManageModel``, and is exercised headlessly: no toolkit, no display. The view is
driven separately, by building the real window and inspecting it -- which is what
notices a mistyped property or a pane that never got packed, and what the model
tests cannot see.

The window tests skip themselves when PyQt6 or a display is missing, so the suite
still runs on a machine that cannot show the dialog. "Display" includes Qt's
offscreen platform, so they do run in a headless checkout -- but note that the
*system* PyQt6 is what has the KDE platform theme (see PLATFORM_THEME): the
bundled Qt in a PyPI PyQt6 wheel carries its own plugin directory, cannot load
the system's KDE plugin, and so resolves no theme icons at all. Assertions about
the icon *names* therefore hold everywhere, while the ones about icons actually
being drawn -- and about the style -- skip when the theme cannot be reached.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from kde_vscode_jumplist import cli
from kde_vscode_jumplist.pinned import Pinned
from kde_vscode_jumplist.desktop_entry import (
    DEFAULT_ICON,
    PINNED_CAPTION_ICON,
    PINNED_ICON,
    ICON_FOR_KIND,
    RECENT_CAPTION_ICON,
)
from kde_vscode_jumplist.manage import (
    ABOUT_BUTTON_LABEL,
    ABOUT_FIELDS,
    BUTTON_ACTION_PROPERTY,
    BUTTON_SIZE,
    CLOSE_BUTTON_LABEL,
    DOWN_ICON,
    DOWN_LABEL,
    DOWN_TOOLTIP,
    ICON_NAME_PROPERTY,
    ICON_NAME_ROLE,
    LEFT_ICON,
    LEFT_LABEL,
    PANE_ROLE_PROPERTY,
    PANE_TITLE_PROPERTY,
    PINNED_PANE_ICON,
    PINNED_ROLE,
    PIN_ACTION,
    PIN_TOOLTIP,
    RECENT_PANE_ICON,
    RECENT_ROLE,
    RIGHT_ICON,
    RIGHT_LABEL,
    UNPIN_ACTION,
    UNPIN_TOOLTIP,
    UP_ICON,
    UP_LABEL,
    UP_TOOLTIP,
    WINDOW_APP_ID,
    WINDOW_ICON,
    DialogUnavailable,
    ManageModel,
    build_about_dialog,
    entry_icon,
)
from kde_vscode_jumplist.models import (
    ENTRY_FILE,
    ENTRY_FOLDER,
    ENTRY_WORKSPACE,
    MenuEntry,
)
from kde_vscode_jumplist.qtview import PLATFORM_THEME
from kde_vscode_jumplist import APP_NAME


def _entry(
    label: str,
    uri: str | None = None,
    kind: str = ENTRY_FOLDER,
    remote: bool = False,
) -> MenuEntry:
    return MenuEntry(
        kind=kind, uri=uri or f"file:///{label}", label=label, source="code", remote=remote
    )


@pytest.fixture()
def model(tmp_path: Path) -> ManageModel:
    """A model over three recents and an empty pinned file."""
    recents = [
        _entry("alpha"),
        _entry("beta"),
        _entry("gamma", kind=ENTRY_FILE),
    ]
    return ManageModel(recents, Pinned(tmp_path / "pinned.json"))


# --- the two panes --------------------------------------------------------


def test_pinning_leaves_the_recent_pane_alone(model: ManageModel) -> None:
    """Pinning only changes what is pinned, so the recents view is untouched.

    The panes are independent lists, not a transfer box: the pinned entry stays
    in the left pane and also appears in the right one.
    """
    assert model.pin(model.visible_recents()[:1]) == 1

    assert [e.label for e in model.visible_recents()] == ["alpha", "beta", "gamma"]
    assert [e.label for e in model.visible_pinned()] == ["alpha"]


def test_unpinning_leaves_the_recent_pane_alone(model: ManageModel) -> None:
    model.pin(model.visible_recents()[:1])
    assert model.unpin(model.visible_pinned()) == 1

    assert [e.label for e in model.visible_recents()] == ["alpha", "beta", "gamma"]
    assert model.visible_pinned() == []


def test_recents_keep_their_mru_order(model: ManageModel) -> None:
    """The left pane is the order VS Code reported, not sorted."""
    assert [e.label for e in model.visible_recents()] == ["alpha", "beta", "gamma"]


def test_pinned_are_listed_in_stored_order(model: ManageModel) -> None:
    """Which is the order the jump list uses, so the panes agree with the menu."""
    model.pin(model.visible_recents())
    model.move(model.visible_pinned()[-1], -1)
    assert [e.label for e in model.visible_pinned()] == ["alpha", "gamma", "beta"]


# --- the two searches -----------------------------------------------------


def test_search_narrows_each_pane_independently(model: ManageModel) -> None:
    model.pin(model.visible_recents()[:1])  # alpha

    model.set_recent_query("BE")
    assert [e.label for e in model.visible_recents()] == ["beta"]
    # The pinned pane is unaffected by the other pane's query.
    assert [e.label for e in model.visible_pinned()] == ["alpha"]

    model.set_pinned_query("zzz")
    assert model.visible_pinned() == []


def test_search_is_trimmed_and_case_insensitive(model: ManageModel) -> None:
    model.set_recent_query("  GaMmA  ")
    assert [e.label for e in model.visible_recents()] == ["gamma"]


def test_search_matches_the_uri_so_remotes_are_findable(tmp_path: Path) -> None:
    """A remote entry is reachable by host, which its label does not spell out."""
    remote = _entry(
        "app",
        uri="vscode-remote://ssh-remote+box/srv/app",
        remote=True,
    )
    model = ManageModel([remote], Pinned(tmp_path / "pinned.json"))

    model.set_recent_query("box")
    assert model.visible_recents() == [remote]

    model.set_recent_query("nothing-like-this")
    assert model.visible_recents() == []


def test_empty_search_shows_everything(model: ManageModel) -> None:
    model.set_recent_query("beta")
    model.set_recent_query("")
    assert len(model.visible_recents()) == 3


# --- the four buttons -----------------------------------------------------


def test_move_reorders_and_marks_dirty(model: ManageModel) -> None:
    model.pin(model.visible_recents())  # alpha, beta, gamma
    last = model.visible_pinned()[-1]

    assert model.move(last, -1) is True
    assert [e.label for e in model.visible_pinned()] == ["alpha", "gamma", "beta"]
    assert model.dirty is True


def test_can_move_reports_the_ends(model: ManageModel) -> None:
    """The buttons are greyed out at the ends, so can_move must agree with move."""
    model.pin(model.visible_recents())
    top, middle, bottom = model.visible_pinned()

    assert model.can_move(top, -1) is False
    assert model.can_move(top, 1) is True
    assert model.can_move(middle, -1) is True
    assert model.can_move(middle, 1) is True
    assert model.can_move(bottom, 1) is False
    assert model.can_move(bottom, -1) is True

    # Reordering needs exactly one row, so "nothing selected" cannot move.
    assert model.can_move(None, -1) is False


def test_can_move_is_false_for_something_that_is_not_a_pinned(model: ManageModel) -> None:
    assert model.can_move(model.visible_recents()[0], 1) is False


def test_move_works_through_a_filtered_view(model: ManageModel) -> None:
    """Reordering under a search still moves the entry in the real order.

    Its neighbours are hidden by the query, but it still has somewhere to go --
    the buttons answer for the list, not for what happens to be on screen.
    """
    model.pin(model.visible_recents())
    model.set_pinned_query("gamma")
    shown = model.visible_pinned()
    assert [e.label for e in shown] == ["gamma"]

    assert model.can_move(shown[0], -1) is True
    assert model.move(shown[0], -1) is True

    model.set_pinned_query("")
    assert [e.label for e in model.visible_pinned()] == ["alpha", "gamma", "beta"]


def test_multi_selection_pins_and_unpins_together(model: ManageModel) -> None:
    assert model.pin(model.visible_recents()) == 3
    assert [e.label for e in model.visible_pinned()] == ["alpha", "beta", "gamma"]

    assert model.unpin(model.visible_pinned()) == 3
    assert model.visible_pinned() == []
    # Through all of it the recents view never moved.
    assert [e.label for e in model.visible_recents()] == ["alpha", "beta", "gamma"]


def test_pinning_twice_counts_nothing(model: ManageModel) -> None:
    """The count is what actually changed, which is what dirty is built on."""
    entry = model.visible_recents()[0]
    assert model.pin([entry]) == 1
    assert model.pin([entry]) == 0


# --- the recents are read-only --------------------------------------------


def test_no_button_changes_the_recents(tmp_path: Path) -> None:
    """The load-bearing invariant: this dialog never modifies the recents.

    `>`/`<` change which entries are pinned and `^`/`v` reorder them, but the
    recents list is the same before and after all four -- in the data, in the
    view, and on disk. VS Code's history is never written back to either.
    """
    pinned = Pinned(tmp_path / "pinned.json")
    recents = [_entry("alpha"), _entry("beta"), _entry("gamma")]
    model = ManageModel(recents, pinned)
    visible_before = [e.label for e in model.visible_recents()]

    model.pin(model.visible_recents()[:2])  # ">" twice
    model.move(model.visible_pinned()[0], 1)  # "v"
    model.unpin(model.visible_pinned()[:1])  # "<"

    # The same entries, in the same MRU order, still listed.
    assert model.visible_recents() == recents
    assert [e.label for e in model.visible_recents()] == visible_before

    # And nothing but pinned was written: no recents cache, no history.
    written = json.loads((tmp_path / "pinned.json").read_text(encoding="utf-8"))
    assert set(written) == {"version", "pinned"}
    uris = {item["uri"] for item in written["pinned"]}
    assert uris <= {entry.uri for entry in recents}


def test_search_never_hides_a_pinned_recent(model: ManageModel) -> None:
    """A pinned entry is still findable on the left, by either search."""
    model.pin(model.visible_recents()[:1])  # alpha

    assert [e.label for e in model.visible_recents()] == ["alpha", "beta", "gamma"]
    model.set_recent_query("alpha")
    assert [e.label for e in model.visible_recents()] == ["alpha"]
    model.set_pinned_query("alpha")
    assert [e.label for e in model.visible_pinned()] == ["alpha"]


# --- dirty tracking -------------------------------------------------------


def test_dirty_stays_false_for_read_only_interaction(model: ManageModel) -> None:
    model.set_recent_query("alpha")
    model.set_pinned_query("beta")
    model.visible_recents()
    model.visible_pinned()
    assert model.dirty is False


def test_dirty_tracks_a_refused_unpin(model: ManageModel) -> None:
    """Nothing was removed, so there is nothing to regenerate the menu for."""
    assert model.unpin(model.visible_recents()) == 0
    assert model.dirty is False


def test_dirty_tracks_a_refused_move(model: ManageModel) -> None:
    model.pin(model.visible_recents()[:1])
    assert model.move(model.visible_pinned()[0], -1) is False
    model.dirty = False  # ignore the pin itself; only the move is under test
    assert model.move(model.visible_pinned()[0], -1) is False
    assert model.dirty is False


def test_row_icon_follows_the_menu() -> None:
    """Rows take the menu's icon for the entry, chosen the same way.

    A pinned entry is starred whatever its kind; a recent takes the icon for
    its kind. Reading the menu's own tables is what keeps the two agreeing.
    """
    folder = _entry("proj")
    note = _entry("notes.md", kind=ENTRY_FILE)
    workspace = _entry("w", kind=ENTRY_WORKSPACE)

    assert entry_icon(folder, pinned=False) == ICON_FOR_KIND[ENTRY_FOLDER]
    assert entry_icon(note, pinned=False) == ICON_FOR_KIND[ENTRY_FILE]
    # A kind the menu has no icon for falls back rather than inventing one.
    assert entry_icon(workspace, pinned=False) == DEFAULT_ICON
    # Pinned replaces the kind icon, which is what marks it out.
    assert entry_icon(folder, pinned=True) == PINNED_ICON
    assert entry_icon(note, pinned=True) == PINNED_ICON
    assert PINNED_ICON != ICON_FOR_KIND[ENTRY_FOLDER]


def test_pane_icons_are_the_menu_headings() -> None:
    """The headings reuse the captions' icons rather than a parallel choice."""
    assert RECENT_PANE_ICON == RECENT_CAPTION_ICON
    assert PINNED_PANE_ICON == PINNED_CAPTION_ICON
    assert RECENT_PANE_ICON != PINNED_PANE_ICON  # never mistaken for each other


def _button_key(button) -> str:
    """How a button identifies itself: its icon name, or failing that its label.

    The four middle buttons are icon-only, so they are keyed by the icon name
    they were built with -- recorded on the button, since a QIcon cannot be
    asked what it was built from. About and Close carry text and no icon.
    """
    return button.property(ICON_NAME_PROPERTY) or button.text()


def _buttons(dialog) -> dict:
    """Every button in the window, keyed by :func:`_button_key`."""
    return {
        _button_key(w): w
        for w in dialog.findChildren(_qt_or_skip().QtWidgets.QPushButton)
    }


def test_middle_button_arrows_come_from_the_theme() -> None:
    """The four middle buttons use the theme's arrows, not text glyphs.

    Icon names rather than characters, so they match the rest of the desktop;
    the ASCII labels remain only as a fallback for a theme without them.
    """
    assert (LEFT_ICON, RIGHT_ICON, UP_ICON, DOWN_ICON) == (
        "go-previous",
        "go-next",
        "go-up",
        "go-down",
    )
    # The fallbacks are still the plain characters requested earlier.
    assert (LEFT_LABEL, RIGHT_LABEL, UP_LABEL, DOWN_LABEL) == ("<", ">", "^", "v")
    # Every tooltip says what its arrow does, since an arrow only implies it.
    assert len({UNPIN_TOOLTIP, PIN_TOOLTIP, UP_TOOLTIP, DOWN_TOOLTIP}) == 4
    assert all(
        text
        for text in (UNPIN_TOOLTIP, PIN_TOOLTIP, UP_TOOLTIP, DOWN_TOOLTIP)
    )


# --- the window itself ----------------------------------------------------

# Bound by _qt_or_skip() so the helpers below can use Qt without threading it
# through every call. None until a window test runs, and kept out of the module
# scope proper so this file still imports without PyQt6.
_QT: SimpleNamespace | None = None


def _qt_or_skip() -> SimpleNamespace:
    """The Qt modules the window is built from, or a skip.

    PyQt6 is needed to build the window at all. A headless run is given Qt's own
    offscreen platform rather than skipped, so the window is still built and laid
    out -- which is everything these tests need -- in a CI container as much as
    on a desktop. It has to be set here because Qt reads it when the QApplication
    is created, which run_dialog does.
    """
    global _QT
    if _QT is not None:
        return _QT

    QtCore = pytest.importorskip("PyQt6.QtCore", reason="PyQt6 is not installed")
    QtGui = pytest.importorskip("PyQt6.QtGui")
    QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
    QtTest = pytest.importorskip("PyQt6.QtTest")
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _QT = SimpleNamespace(
        QtCore=QtCore, QtGui=QtGui, QtWidgets=QtWidgets, QtTest=QtTest
    )
    return _QT


def _rows(listing) -> list[str]:
    """The text of each row of a list, top to bottom."""
    return [listing.item(index).text() for index in range(listing.count())]


def _row_icons(listing) -> list[str]:
    """The icon name chosen for each row, top to bottom.

    The name rather than the QIcon: it is the thing that has to match the jump
    list's icon, and a QIcon cannot be asked what it was built from. Whether it
    was *drawn* is a separate question -- see :func:`_icons_are_drawn`.
    """
    role = int(_qt_or_skip().QtCore.Qt.ItemDataRole.UserRole) + ICON_NAME_ROLE
    return [listing.item(index).data(role) for index in range(listing.count())]


def _pane_widget(dialog, role: str):
    """The pane holding one of the two roles, wherever it sits."""
    for pane in dialog.findChildren(_qt_or_skip().QtWidgets.QWidget):
        if pane.property(PANE_ROLE_PROPERTY) == role:
            return pane
    raise AssertionError(f"no {role} pane in the dialog")


def _pane(dialog, role: str):
    """The entry list inside one pane.

    By role rather than by position: each pane carries its role from the model
    that filled it, so the test asks for "the pinned pane" rather than for the
    right-hand one, and the panes stay recognisable whatever the layout does.
    """
    return _pane_widget(dialog, role).findChild(_qt_or_skip().QtWidgets.QListWidget)


def _search(dialog, role: str):
    """The search box inside one pane."""
    return _pane_widget(dialog, role).findChild(_qt_or_skip().QtWidgets.QLineEdit)


def _button(dialog, action: str):
    """The transfer button carrying one action, whichever side it is on."""
    for button in dialog.findChildren(_qt_or_skip().QtWidgets.QPushButton):
        if button.property(BUTTON_ACTION_PROPERTY) == action:
            return button
    raise AssertionError(f"no button for {action!r} in the dialog")


def _pane_x(dialog) -> dict[str, int]:
    """Role -> the x of its pane, so left and right can be compared."""
    QtCore = _qt_or_skip().QtCore
    return {
        role: _pane_widget(dialog, role).mapTo(dialog, QtCore.QPoint(0, 0)).x()
        for role in (RECENT_ROLE, PINNED_ROLE)
    }


def _pane_heading(dialog, role: str) -> dict[str, object]:
    """The heading above one pane: what it is titled, and with which icon."""
    heading = next(
        widget
        for widget in _pane_widget(dialog, role).findChildren(
            _qt_or_skip().QtWidgets.QWidget
        )
        if widget.property(PANE_TITLE_PROPERTY) is not None
    )
    return {
        "title": heading.property(PANE_TITLE_PROPERTY),
        "icon": heading.property(ICON_NAME_PROPERTY),
    }


def _pane_headings(dialog) -> dict[str, dict[str, object]]:
    """Role -> its heading, titled and iconned."""
    return {
        role: _pane_heading(dialog, role) for role in (RECENT_ROLE, PINNED_ROLE)
    }


def _row_geometry(listing) -> list[bool]:
    """Whether each row has been laid out in the viewport."""
    return [
        not listing.visualItemRect(listing.item(index)).isEmpty()
        for index in range(listing.count())
    ]


def _all_rows_drawn(listing) -> bool:
    """Whether every row is actually on screen.

    Counting rows and reading their text is not enough: items can be in the view
    while it renders nothing. A row with a real rectangle has been through the
    view's layout, which is what "drawn" means here.
    """
    return bool(listing.count()) and all(_row_geometry(listing))


def _icons_are_drawn(listing) -> bool:
    """Whether the theme's icons are actually resolving into pixmaps.

    False when Qt has no platform theme -- notably a PyPI PyQt6, whose bundled Qt
    carries its own plugin directory and cannot load the system's KDE plugin. The
    dialog is built for that case (icons are optional and fall back to text), so
    the assertions that care about rendering skip instead of failing.
    """
    if _qt_or_skip().QtGui.QIcon.fromTheme(PINNED_ICON).isNull():
        return False
    return bool(listing.count()) and not listing.item(0).icon().isNull()


def _click(listing, index, modifiers=None) -> None:
    """Click row ``index`` -- or past the last row -- with ``modifiers`` held.

    A real synthesized click with a real modifier mask, so what is exercised is
    Qt's own selection handling rather than a stand-in for it.
    """
    QtCore, QtWidgets, QtTest = (
        _qt_or_skip().QtCore,
        _qt_or_skip().QtWidgets,
        _qt_or_skip().QtTest,
    )
    if modifiers is None:
        modifiers = QtCore.Qt.KeyboardModifier.NoModifier
    if index is None:
        last = listing.visualItemRect(listing.item(listing.count() - 1))
        point = QtCore.QPoint(listing.viewport().width() // 2, last.bottom() + 30)
    else:
        point = listing.visualItemRect(listing.item(index)).center()
    QtTest.QTest.mouseClick(
        listing.viewport(), QtCore.Qt.MouseButton.LeftButton, modifiers, point
    )


def _selected(listing) -> list[int]:
    """The selected row numbers, in order."""
    return sorted(listing.row(item) for item in listing.selectedItems())


def _open_dialog(model, monkeypatch, inspect) -> bool:
    """Build the real window, hand it to ``inspect``, then close it.

    ``run_dialog`` is entered through its ``_run`` seam, so the test is given the
    dialog itself rather than having to find the window afterwards -- and the
    window is really shown and laid out first, so geometry means something.
    Returns what run_dialog reported.
    """
    QtGui = _qt_or_skip().QtGui

    def run(dialog) -> None:
        dialog.show()
        QtGui.QGuiApplication.processEvents()
        try:
            inspect(dialog)
        finally:
            dialog.reject()
            QtGui.QGuiApplication.processEvents()

    monkeypatch.setattr(cli.manage_panel, "_run", run)
    return cli.manage_panel.run_dialog(model)


def _require_theme_icons(drawn: bool) -> None:
    """Skip an icon-rendering assertion when the theme cannot be reached."""
    if not drawn:
        pytest.skip("the platform theme's icons cannot be resolved here")


def test_button_fallbacks_are_plain_ascii() -> None:
    """The labels kept for a theme without the icons stay font-independent."""
    assert (LEFT_LABEL, RIGHT_LABEL, UP_LABEL, DOWN_LABEL) == ("<", ">", "^", "v")
    assert all(label.isascii() for label in (LEFT_LABEL, RIGHT_LABEL, UP_LABEL, DOWN_LABEL))



def _ensure_app() -> None:
    """Make sure a QApplication exists: widgets cannot be built without one.

    The window tests get theirs from run_dialog; the About tests build a dialog
    directly, and Qt refuses to construct widgets before an application exists.
    """
    QtWidgets = _qt_or_skip().QtWidgets
    if QtWidgets.QApplication.instance() is None:
        cli.manage_panel._application(QtWidgets)


def _select_only(listing, *rows: int) -> None:
    """Select exactly ``rows``, the way a plain click would.

    Qt's ExtendedSelection adds to the selection, so anything already chosen has
    to be dropped first for this to stand in for one click.
    """
    listing.clearSelection()
    for row in rows:
        listing.item(row).setSelected(True)


def test_middle_button_icons_resolve_in_the_icon_theme() -> None:
    """The arrow icons must exist, or the buttons fall back to their labels.

    Skipped rather than failed: an icon theme is a configuration choice, and
    the fallback is what makes a missing icon harmless.
    """
    QtGui = _qt_or_skip().QtGui

    missing = [
        name
        for name in (LEFT_ICON, RIGHT_ICON, UP_ICON, DOWN_ICON)
        if QtGui.QIcon.fromTheme(name).isNull()
    ]
    if missing:
        pytest.skip(f"icon theme lacks {', '.join(missing)}")

    for name in (LEFT_ICON, RIGHT_ICON, UP_ICON, DOWN_ICON):
        assert not QtGui.QIcon.fromTheme(name).pixmap(16, 16).isNull()


def test_dialog_builds_its_widgets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Open the real window, check it, and close it again.

    The model tests cover behaviour; this is what notices the *view* being
    wrong -- a mistyped property, a pane that never got added, the search not
    being wired to the lists.
    """
    QtCore, QtWidgets = _qt_or_skip().QtCore, _qt_or_skip().QtWidgets

    pinned = Pinned(tmp_path / "pinned.json")
    pinned.pin(_entry("pinned"))
    model = ManageModel(
        [_entry("alpha"), _entry("beta"), _entry("gamma", kind=ENTRY_FILE)], pinned
    )
    seen: dict[str, object] = {}

    def inspect(dialog) -> None:
        buttons = _buttons(dialog)
        recents, pinned_list = _pane(dialog, RECENT_ROLE), _pane(dialog, PINNED_ROLE)
        seen["title"] = dialog.windowTitle()
        seen["app_id"] = _qt_or_skip().QtGui.QGuiApplication.desktopFileName()
        seen["buttons"] = [
            _button_key(w) for w in dialog.findChildren(QtWidgets.QPushButton)
        ]
        seen["tooltips"] = {key: button.toolTip() for key, button in buttons.items()}
        seen["lists"] = len(dialog.findChildren(QtWidgets.QListWidget))
        seen["searches"] = len(dialog.findChildren(QtWidgets.QLineEdit))
        seen["recent"] = _rows(recents)
        seen["pinned"] = _rows(pinned_list)
        seen["recent_icons"] = _row_icons(recents)
        seen["pinned_icons"] = _row_icons(pinned_list)
        seen["headings"] = _pane_headings(dialog)
        seen["drawn"] = _row_geometry(recents) + _row_geometry(pinned_list)
        # The four middle buttons are squares, not label-sized rectangles.
        seen["sizes"] = {
            key: (buttons[key].width(), buttons[key].height())
            for key in (LEFT_ICON, RIGHT_ICON, UP_ICON, DOWN_ICON)
        }

        # The footer is a row of its own under the panes: About at the left edge,
        # Close at the right. Positions rather than layout flags, since the
        # result is the point.
        about = buttons[ABOUT_BUTTON_LABEL].mapTo(dialog, QtCore.QPoint(0, 0))
        close = buttons[CLOSE_BUTTON_LABEL].mapTo(dialog, QtCore.QPoint(0, 0))
        panes = recents.mapTo(dialog, QtCore.QPoint(0, 0))
        seen["layout"] = {
            "about_x": about.x(),
            "close_x": close.x(),
            "close_right": close.x() + buttons[CLOSE_BUTTON_LABEL].width(),
            "width": dialog.width(),
            "same_row": about.y() == close.y(),
            "below_the_panes": about.y() > panes.y(),
        }

        # Typing in the recents search must filter that list through the model...
        _search(dialog, RECENT_ROLE).setText("beta")
        seen["filtered"] = _rows(recents)
        # ...and the rows it rebuilds must still be laid out.
        seen["filtered_drawn"] = _all_rows_drawn(recents)

    changed = _open_dialog(model, monkeypatch, inspect)

    assert changed is False  # looking around changes nothing
    assert seen["title"] == "Manage Pinned Files"
    assert seen["app_id"] == WINDOW_APP_ID
    assert seen["lists"] == 2
    assert seen["searches"] == 2
    assert seen["recent"] == ["alpha", "beta", "gamma"]
    assert seen["pinned"] == ["pinned"]
    # Both headings are iconned, and each with its own.
    assert seen["headings"] == {
        RECENT_ROLE: {"title": "Recent Files", "icon": RECENT_PANE_ICON},
        PINNED_ROLE: {"title": "Pinned Files", "icon": PINNED_PANE_ICON},
    }
    # Every row is iconned: the recents by kind, the pinned with the star.
    assert seen["recent_icons"] == [ICON_FOR_KIND[ENTRY_FOLDER]] * 2 + [
        ICON_FOR_KIND[ENTRY_FILE]
    ]
    assert seen["pinned_icons"] == [PINNED_ICON]
    # The four transfer buttons between the panes, then the footer row.
    assert seen["buttons"] == [
        LEFT_ICON,
        RIGHT_ICON,
        UP_ICON,
        DOWN_ICON,
        ABOUT_BUTTON_LABEL,
        CLOSE_BUTTON_LABEL,
    ]
    # Only the arrows carry tooltips: the footer buttons label themselves. Which
    # action each arrow carries depends on the layout, so the transfer pair is
    # checked as a set here and per-side in
    # test_transfer_buttons_point_at_their_destination.
    assert {
        seen["tooltips"][LEFT_ICON],
        seen["tooltips"][RIGHT_ICON],
    } == {PIN_TOOLTIP, UNPIN_TOOLTIP}
    assert seen["tooltips"][UP_ICON] == UP_TOOLTIP
    assert seen["tooltips"][DOWN_ICON] == DOWN_TOOLTIP
    assert seen["tooltips"][ABOUT_BUTTON_LABEL] == ""
    assert seen["tooltips"][CLOSE_BUTTON_LABEL] == ""
    assert seen["sizes"] == {
        key: (BUTTON_SIZE, BUTTON_SIZE)
        for key in (LEFT_ICON, RIGHT_ICON, UP_ICON, DOWN_ICON)
    }
    layout = seen["layout"]
    assert layout["same_row"] is True
    assert layout["below_the_panes"] is True
    # About sits at the left edge, Close at the right.
    assert layout["about_x"] < layout["close_x"]
    assert layout["close_right"] <= layout["width"]
    # The rows must be laid out, not merely present in the view.
    assert seen["drawn"] == [True] * 4
    assert seen["filtered"] == ["beta"]
    assert seen["filtered_drawn"] is True


def test_recents_pane_is_on_the_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recents are always on the left, the pinned entries always on the right.

    The same order the menu lists its two blocks in, so the dialog reads the
    way the menu it was opened from does.
    """
    pinned = Pinned(tmp_path / "pinned.json")
    pinned.pin(_entry("pinned"))
    model = ManageModel([_entry("alpha")], pinned)
    seen: dict[str, object] = {}

    def inspect(dialog) -> None:
        seen["x"] = _pane_x(dialog)

    _open_dialog(model, monkeypatch, inspect)

    assert seen["x"][RECENT_ROLE] < seen["x"][PINNED_ROLE]


def test_transfer_buttons_point_at_their_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each transfer arrow moves in the direction it points.

    The panes are fixed, so the arrows are too: ``<`` sends the selection back
    to the recents pane on the left (unpin) and ``>`` sends it to the pinned
    pane on the right (pin), each with a tooltip naming the list it reaches.
    """
    pinned = Pinned(tmp_path / "pinned.json")
    pinned.pin(_entry("pinned"))
    model = ManageModel([_entry("alpha"), _entry("beta")], pinned)
    seen: dict[str, object] = {}

    def inspect(dialog) -> None:
        buttons = _buttons(dialog)
        seen["left"] = buttons[LEFT_ICON]
        seen["right"] = buttons[RIGHT_ICON]

    _open_dialog(model, monkeypatch, inspect)

    left, right = seen["left"], seen["right"]
    assert left.property(BUTTON_ACTION_PROPERTY) == UNPIN_ACTION
    assert right.property(BUTTON_ACTION_PROPERTY) == PIN_ACTION
    assert left.toolTip() == UNPIN_TOOLTIP
    assert right.toolTip() == PIN_TOOLTIP
    # The arrow is drawn on the side it sends entries to.
    assert left.property(ICON_NAME_PROPERTY) == LEFT_ICON
    assert right.property(ICON_NAME_PROPERTY) == RIGHT_ICON


def test_pinning_moves_entries_from_left_to_right(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pin button takes the selected recents over to the pinned pane."""
    pinned = Pinned(tmp_path / "pinned.json")
    model = ManageModel([_entry("alpha")], pinned)
    seen: dict[str, object] = {}

    def inspect(dialog) -> None:
        _select_only(_pane(dialog, RECENT_ROLE), 0)
        _button(dialog, PIN_ACTION).click()
        seen["pinned"] = _rows(_pane(dialog, PINNED_ROLE))
        seen["recent"] = _rows(_pane(dialog, RECENT_ROLE))

    changed = _open_dialog(model, monkeypatch, inspect)

    assert seen["pinned"] == ["alpha"]
    assert seen["recent"] == ["alpha"]  # the recents pane never changes
    assert changed is True
    assert [e.label for e in pinned.all()] == ["alpha"]


def test_dialog_icons_are_drawn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The window, its rows and its headings all draw the theme's icons.

    Separate from the structural check above because it is a different claim, and
    because it is the one that cannot hold everywhere: see PLATFORM_THEME.
    """
    pinned = Pinned(tmp_path / "pinned.json")
    pinned.pin(_entry("pinned"))
    model = ManageModel([_entry("alpha")], pinned)
    seen: dict[str, object] = {}

    def inspect(dialog) -> None:
        recents = _pane(dialog, RECENT_ROLE)
        seen["rows"] = _icons_are_drawn(recents)
        seen["window"] = not dialog.windowIcon().isNull()
        seen["headings"] = all(
            not label.pixmap().isNull()
            for header in dialog.findChildren(_qt_or_skip().QtWidgets.QWidget)
            if header.property(PANE_TITLE_PROPERTY)
            for label in header.findChildren(_qt_or_skip().QtWidgets.QLabel)
            if not label.text()
        )

    _open_dialog(model, monkeypatch, inspect)
    _require_theme_icons(seen["rows"])

    assert seen["rows"] is True
    assert seen["headings"] is True
    # The window icon is the one name here that does not come from Breeze -- the
    # VS Code package installs it -- so it is checked only where it exists at
    # all. test_window_icon_resolves_in_the_icon_theme covers the lookup itself.
    if not _qt_or_skip().QtGui.QIcon.fromTheme(WINDOW_ICON).isNull():
        assert seen["window"] is True


def test_buttons_act_on_the_lists(tmp_path: Path, monkeypatch) -> None:
    """Clicking the pin arrow pins, and the entry moves panes; the buttons follow."""
    pinned = Pinned(tmp_path / "pinned.json")
    model = ManageModel([_entry("alpha"), _entry("beta")], pinned)
    seen: dict[str, object] = {}

    def inspect(dialog) -> None:
        recents, pinned_list = _pane(dialog, RECENT_ROLE), _pane(dialog, PINNED_ROLE)
        buttons = _buttons(dialog)
        pin_button = _button(dialog, PIN_ACTION)
        # Nothing is selected yet, so the buttons are inert.
        seen["pin_initial"] = pin_button.isEnabled()
        seen["up_initial"] = buttons[UP_ICON].isEnabled()

        # Pin the first recent. The recents pane keeps listing it -- the panes
        # are independent -- so the entry now appears in both.
        _select_only(recents, 0)
        pin_button.click()
        seen["recent_after_first"] = recents.count()
        seen["pinned_after_first"] = pinned_list.count()
        # A refresh rebuilds every row in both panes; all of them must be laid
        # out again, not just counted.
        seen["after_pin_drawn"] = _row_geometry(recents) + _row_geometry(pinned_list)

        # One pinned is selected, but it is the only one: it cannot move.
        _select_only(pinned_list, 0)
        seen["up_single"] = buttons[UP_ICON].isEnabled()
        seen["down_single"] = buttons[DOWN_ICON].isEnabled()

        # Pin a second one, giving the first somewhere to move.
        _select_only(recents, 1)
        pin_button.click()
        seen["recent"] = recents.count()
        seen["pinned"] = pinned_list.count()
        seen["after_second_drawn"] = _row_geometry(recents) + _row_geometry(pinned_list)

        # Two pinned: the top row cannot go up, the bottom one cannot go down, so
        # each button answers for the row that is actually selected.
        _select_only(pinned_list, 0)
        seen["top_up"] = buttons[UP_ICON].isEnabled()
        seen["top_down"] = buttons[DOWN_ICON].isEnabled()
        _select_only(pinned_list, 1)
        seen["bottom_up"] = buttons[UP_ICON].isEnabled()
        seen["bottom_down"] = buttons[DOWN_ICON].isEnabled()

        # Selecting two rows at once disables both: there is no single "up".
        _select_only(pinned_list, 0, 1)
        seen["two_up"] = buttons[UP_ICON].isEnabled()
        seen["two_down"] = buttons[DOWN_ICON].isEnabled()

    changed = _open_dialog(model, monkeypatch, inspect)

    assert seen["pin_initial"] is False
    assert seen["up_initial"] is False
    # Pin one: the recents pane is unchanged at 2 rows, the pinned one gains one.
    assert seen["recent_after_first"] == 2 and seen["pinned_after_first"] == 1
    assert seen["after_pin_drawn"] == [True, True, True]
    assert seen["up_single"] is False and seen["down_single"] is False
    # Pin the second: still 2 in the recents, now 2 pinned.
    assert seen["recent"] == 2 and seen["pinned"] == 2
    assert seen["after_second_drawn"] == [True, True, True, True]
    assert seen["top_up"] is False and seen["top_down"] is True
    assert seen["bottom_up"] is True and seen["bottom_down"] is False
    assert seen["two_up"] is False and seen["two_down"] is False
    assert changed is True
    assert [e.label for e in pinned.all()] == ["alpha", "beta"]


def test_window_icon_resolves_in_the_icon_theme() -> None:
    """The name has to be one the theme can actually look up.

    Skipped rather than failed when the icon is absent: another VS Code variant
    installs a differently named icon (VSCodium ships ``vscodium``), and that is
    a configuration difference, not a broken dialog.
    """
    QtGui = _qt_or_skip().QtGui

    icon = QtGui.QIcon.fromTheme(WINDOW_ICON)
    if icon.isNull():
        pytest.skip(f"no {WINDOW_ICON!r} icon in the current theme")
    assert not icon.pixmap(48, 48).isNull()


def test_app_id_is_not_capitalized() -> None:
    """The lookup a Wayland compositor does is exact.

    ``Code.desktop`` is not ``code.desktop``, so an app id derived from the
    application name -- which Qt capitalises the same way -- would leave the
    titlebar without an icon even though the file is right there. It is set
    explicitly for that reason; see run_dialog.
    """
    assert WINDOW_APP_ID == WINDOW_APP_ID.lower()
    assert WINDOW_APP_ID != WINDOW_APP_ID.capitalize()


def test_app_id_names_the_desktop_file_the_compositor_will_read() -> None:
    """A Wayland composer finds the icon through <app id>.desktop.

    Setting the app id to the icon name instead would leave the window with no
    icon at all, since no ``vscode.desktop`` exists to read ``Icon=`` from.
    """
    assert WINDOW_APP_ID != WINDOW_ICON
    assert WINDOW_APP_ID == Path("code.desktop").stem


def test_click_selection_uses_ctrl_and_shift(tmp_path: Path, monkeypatch) -> None:
    """The usual semantics: plain replaces, Ctrl toggles, Shift ranges.

    Qt's ExtendedSelection is what provides them, and this drives the real
    widgets through real synthesized clicks with real modifier masks, so what is
    checked is the behaviour the window actually has. The clicks with Shift also
    cross the one part the view keeps for itself -- the row a range is measured
    from -- which is why that is exercised here rather than assumed.
    """
    QtCore = _qt_or_skip().QtCore
    control = QtCore.Qt.KeyboardModifier.ControlModifier
    shift = QtCore.Qt.KeyboardModifier.ShiftModifier

    pinned = Pinned(tmp_path / "pinned.json")
    model = ManageModel([_entry(f"e{index}") for index in range(5)], pinned)
    seen: dict[str, object] = {}

    def inspect(dialog) -> None:
        listing = _pane(dialog, RECENT_ROLE)
        assert listing.count() == 5

        # A plain click replaces the selection.
        _click(listing, 0)
        seen["plain"] = _selected(listing)
        _click(listing, 2)
        seen["plain_replaces"] = _selected(listing)

        # Ctrl adds, and Ctrl again on a selected row removes just that one.
        _click(listing, 0, control)
        seen["ctrl_adds"] = _selected(listing)
        _click(listing, 2, control)
        seen["ctrl_toggles_off"] = _selected(listing)

        # Shift selects the range from the last plain/Ctrl click...
        _click(listing, 1)
        _click(listing, 3, shift)
        seen["shift_range"] = _selected(listing)
        # ...and backwards, still measured from the same row, so the range
        # shrinks to 0-1 rather than extending past it.
        _click(listing, 0, shift)
        seen["shift_range_back"] = _selected(listing)

        # Ctrl+Shift extends without dropping what is already selected.
        _click(listing, 0)
        _click(listing, 2, shift)
        _click(listing, 4, shift | control)
        seen["ctrl_shift_extends"] = _selected(listing)

        # A click past the last row clears the selection...
        _click(listing, None)
        seen["empty_space"] = _selected(listing)

        # ...but not when a modifier says the click meant to extend.
        _click(listing, 1)
        _click(listing, None, control)
        seen["empty_with_ctrl"] = _selected(listing)

    _open_dialog(model, monkeypatch, inspect)

    assert seen["plain"] == [0]
    assert seen["plain_replaces"] == [2]
    assert seen["ctrl_adds"] == [0, 2]
    assert seen["ctrl_toggles_off"] == [0]
    assert seen["shift_range"] == [1, 2, 3]
    assert seen["shift_range_back"] == [0, 1]
    assert seen["ctrl_shift_extends"] == [0, 1, 2, 3, 4]
    assert seen["empty_space"] == []
    assert seen["empty_with_ctrl"] == [1]


def test_selection_is_mutually_exclusive_between_panes(
    tmp_path: Path, monkeypatch
) -> None:
    """Selecting in one pane clears the other, whichever way it is done.

    Both sets being live at once would leave it unclear which list the four
    buttons act on, so a click claims the selection for its own pane.
    """
    QtCore = _qt_or_skip().QtCore
    control = QtCore.Qt.KeyboardModifier.ControlModifier

    pinned = Pinned(tmp_path / "pinned.json")
    pinned.pin(_entry("pinned-a"))
    pinned.pin(_entry("pinned-b"))
    model = ManageModel([_entry("r0"), _entry("r1")], pinned)
    seen: dict[str, object] = {}

    def inspect(dialog) -> None:
        recents, pinned_list = (
            _pane(dialog, RECENT_ROLE),
            _pane(dialog, PINNED_ROLE),
        )

        # Pinned selected, then a plain click on a recent.
        _click(pinned_list, 0)
        seen["pinned_first"] = _selected(pinned_list)
        _click(recents, 0)
        seen["pinned_cleared"] = _selected(pinned_list)
        seen["recents_kept"] = _selected(recents)

        # ...and the other way round.
        _click(pinned_list, 1)
        seen["recents_cleared"] = _selected(recents)
        seen["pinned_again"] = _selected(pinned_list)

        # A Ctrl click clearing the other pane works the same way.
        _click(recents, 0)
        _click(pinned_list, 0, control)
        seen["recents_cleared_by_ctrl"] = _selected(recents)
        seen["pinned_by_ctrl"] = _selected(pinned_list)

        # Multi-select within one pane is still possible: the exclusion is
        # between panes, not within one.
        _click(pinned_list, 0)
        _click(pinned_list, 1, control)
        seen["pinned_multi"] = _selected(pinned_list)
        seen["recents_still_empty"] = _selected(recents)

    _open_dialog(model, monkeypatch, inspect)

    assert seen["pinned_first"] == [0]
    assert seen["pinned_cleared"] == []
    assert seen["recents_kept"] == [0]
    assert seen["recents_cleared"] == []
    assert seen["pinned_again"] == [1]
    assert seen["recents_cleared_by_ctrl"] == []
    assert seen["pinned_by_ctrl"] == [0]
    assert seen["pinned_multi"] == [0, 1]
    assert seen["recents_still_empty"] == []


def test_shift_range_does_not_survive_a_click_in_the_other_pane(
    tmp_path: Path, monkeypatch
) -> None:
    """Clearing a pane forgets the row its Shift range was measured from.

    Otherwise a later Shift click there would extend from a row that is no
    longer selected, so a range would appear out of nowhere. Qt keeps that row
    privately and does not forget it when the selection is cleared, so the view
    keeps its own, beside the selection it belongs to; see the pane list class.
    """
    QtCore = _qt_or_skip().QtCore
    shift = QtCore.Qt.KeyboardModifier.ShiftModifier

    pinned = Pinned(tmp_path / "pinned.json")
    pinned.pin(_entry("pinned"))
    model = ManageModel([_entry(f"e{index}") for index in range(5)], pinned)
    seen: dict[str, object] = {}

    def inspect(dialog) -> None:
        recents, pinned_list = (
            _pane(dialog, RECENT_ROLE),
            _pane(dialog, PINNED_ROLE),
        )
        assert pinned_list.count() == 1  # the pane has to be clickable

        # Measure a range from row 1 of the recents pane.
        _click(recents, 1)
        _click(recents, 3, shift)
        seen["range"] = _selected(recents)

        # Clicking the other pane clears this one, its Shift row included.
        _click(pinned_list, 0)
        seen["cleared"] = _selected(recents)

        # So Shift now selects the clicked row alone rather than stretching back
        # to row 1, which is what a stale row would have produced.
        _click(recents, 4, shift)
        seen["no_stale_range"] = _selected(recents)

    _open_dialog(model, monkeypatch, inspect)

    assert seen["range"] == [1, 2, 3]
    assert seen["cleared"] == []
    # A stale row would have made this [1, 2, 3, 4].
    assert seen["no_stale_range"] == [4]


def test_about_fields_are_the_requested_four() -> None:
    """Project, Version, Author and Github, in that order and nothing else."""
    from kde_vscode_jumplist import __author__, __url__, __version__

    assert [label for label, _value in ABOUT_FIELDS] == [
        "Project",
        "Version",
        "Author",
        "Github",
    ]
    values = dict(ABOUT_FIELDS)
    assert values["Project"] == APP_NAME == "kde-vscode-jumplist"
    assert values["Version"] == __version__
    assert values["Author"] == __author__
    assert values["Github"] == __url__
    assert values["Github"].startswith("https://")
    # The dialog takes each fact straight from the package, so these cannot be
    # written out by hand and drift.
    assert values["Version"] != values["Author"]


def test_project_url_matches_pyproject() -> None:
    """__url__ must be the URL the package metadata declares.

    Otherwise the About dialog would advertise somewhere other than the project
    page, and nothing would notice.
    """
    from kde_vscode_jumplist import __url__

    tomllib = pytest.importorskip("tomllib", reason="needs Python 3.11+")
    root = Path(__file__).resolve().parent.parent
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["urls"]["Homepage"] == __url__


def test_about_dialog_is_one_page_with_every_field() -> None:
    """All four facts on one page, with no logo and no credits.

    That is why it is hand-built rather than a richer About box: those bring a
    large icon and a second page of credits, and neither is wanted.
    """
    QtWidgets = _qt_or_skip().QtWidgets
    _ensure_app()

    parent = QtWidgets.QWidget()
    about = build_about_dialog(parent)

    assert about.parent() is parent
    # One page: no image, and nothing that paginates.
    labels = about.findChildren(QtWidgets.QLabel)
    assert not any(not label.pixmap().isNull() for label in labels)
    assert not about.findChildren(QtWidgets.QTabWidget)
    assert not about.findChildren(QtWidgets.QStackedWidget)

    # Every field is present, as a dimmed heading and its value.
    shown = [label.text() for label in labels if label.text()]
    for heading, _value in ABOUT_FIELDS:
        assert f"{heading}:" in shown, heading
    # The URL is shown in full, and is the one clickable thing on the page.
    links = [label for label in labels if label.openExternalLinks()]
    assert len(links) == 1
    assert dict(ABOUT_FIELDS)["Github"] in links[0].text()

    # A single Close, and nothing resembling a credits button.
    buttons = {button.text() for button in about.findChildren(QtWidgets.QPushButton)}
    assert buttons == {CLOSE_BUTTON_LABEL}
    assert not any("credit" in text.lower() for text in buttons)

    about.destroy()
    parent.destroy()


def test_about_values_are_copied_from_the_package() -> None:
    """Each value comes from the package, not from a literal in the dialog."""
    QtWidgets = _qt_or_skip().QtWidgets
    _ensure_app()
    from kde_vscode_jumplist import __author__, __url__, __version__

    parent = QtWidgets.QWidget()
    about = build_about_dialog(parent)
    shown = {
        label.text()
        for label in about.findChildren(QtWidgets.QLabel)
        if label.text()
    }

    assert APP_NAME in shown
    assert __version__ in shown
    assert __author__ in shown
    # The URL is the link's href rather than its text, since the text is the URL.
    links = [label for label in about.findChildren(QtWidgets.QLabel) if label.openExternalLinks()]
    assert [label for label in links if __url__ in label.text()]

    about.destroy()
    parent.destroy()
    parent.destroy()


# --- the CLI wiring -------------------------------------------------------


def test_manage_command_is_registered() -> None:
    assert cli.build_parser().parse_args(["manage"]).func is cli.cmd_manage


def test_manage_reports_an_unavailable_dialog(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """No Qt bindings or no display is an error message, not a traceback."""
    monkeypatch.setattr(cli, "run_locked_sync", lambda: False)

    def unavailable(_model: ManageModel) -> bool:
        raise DialogUnavailable("Qt 6 bindings are not installed")

    monkeypatch.setattr(cli.manage_panel, "run_dialog", unavailable)

    with caplog.at_level("ERROR"):
        assert cli.cmd_manage(argparse.Namespace()) == 2
    assert "Qt 6 bindings are not installed" in caplog.text


def test_missing_qt_bindings_are_reported_not_raised() -> None:
    """A system without PyQt6 gets DialogUnavailable, not an ImportError.

    The CLI has to import, and every other command to work, on a machine that
    cannot show this dialog at all.
    """
    import builtins

    real_import = builtins.__import__

    def no_qt(name: str, *args: object, **kwargs: object):
        if name.startswith("PyQt6"):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(builtins, "__import__", no_qt)
        with pytest.raises(DialogUnavailable, match="python3-pyqt6"):
            cli.manage_panel._load_qt()


def test_platform_theme_is_asked_for_without_overriding_a_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Qt gets the desktop's style only when it has not been told otherwise.

    Without a platform theme Qt resolves *no* theme icons at all -- silently,
    which is why it is set at all -- but a session that configured Qt its own way
    keeps that choice.
    """
    monkeypatch.delenv("QT_QPA_PLATFORMTHEME", raising=False)
    cli.manage_panel._use_platform_theme()
    assert os.environ["QT_QPA_PLATFORMTHEME"] == PLATFORM_THEME

    monkeypatch.setenv("QT_QPA_PLATFORMTHEME", "gnome")
    cli.manage_panel._use_platform_theme()
    assert os.environ["QT_QPA_PLATFORMTHEME"] == "gnome"


def test_no_display_is_reported_before_qt_can_abort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A headless run must raise, not kill the process.

    Qt calls qFatal when it finds no platform plugin, which would take the whole
    CLI with it -- including the `open` command the menu actions call. So this is
    checked before any QApplication exists.
    """
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)

    with pytest.raises(DialogUnavailable, match="no graphical display"):
        cli.manage_panel._require_display()

    # Any of the three is enough to try.
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    cli.manage_panel._require_display()
    monkeypatch.delenv("QT_QPA_PLATFORM")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    cli.manage_panel._require_display()


def test_manage_regenerates_the_menu_only_when_something_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dialog writes as it goes, so the menu is rebuilt only on changes."""
    syncs: list[str] = []
    monkeypatch.setattr(cli, "run_locked_sync", lambda: syncs.append("sync") or False)

    changed = False
    monkeypatch.setattr(cli.manage_panel, "run_dialog", lambda _model: changed)

    # Refreshing the recents first, then no second pass: nothing changed.
    assert cli.cmd_manage(argparse.Namespace()) == 0
    assert syncs == ["sync"]

    changed = True
    assert cli.cmd_manage(argparse.Namespace()) == 0
    assert syncs == ["sync", "sync", "sync"]
