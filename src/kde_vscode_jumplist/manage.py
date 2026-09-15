"""Interactive editor for the pinned entries.

This is what the jump list's "Pinned Files:" and "Recent Files:" headings open.
The layout is the two-pane box fcitx5's input-method configuration uses: every
recent VS Code entry in the left pane, the pinned subset the menu lists in the
right, and the four buttons between them. The recents are on the left and the
pinned entries on the right, the order the menu itself lists them in. The two
transfer arrows point at the pane they send entries to, so `>` pins the selected
recents and `<` unpins the selected pinned entries.

The two panes are independent views rather than a transfer box. `>` adds the
selected recents to the pinned entries and `<` removes the selected pinned
entries, but neither touches the recents, so an entry that is pinned is listed
in both panes and the recents pane never changes when something is pinned or
unpinned. `^` and `v` reorder the pinned entries. Only the pinned list is ever
written.

Everything that decides *what* the dialog does lives in :class:`ManageModel`,
which imports nothing graphical, so the pinning and the reordering stay testable
in a headless suite; :func:`run_dialog` is the Qt view over it.

The icons are mostly the menu's own -- the pane headings take the heading icons
and each row the icon of the entry behind it -- so the dialog and the menu do
not drift apart. They are read from :mod:`kde_vscode_jumplist.desktop_entry`
for that reason rather than repeated. The one exception is the star on a pinned
row, which this window draws hollow and the menu draws filled: see
:data:`PINNED_ROW_ICON`.

Right-clicking a row offers what to do with that one entry -- open it in VS
Code, show its folder, copy its path -- which is the same set on either side,
since what can be done with an entry does not depend on which list it is in.

The window is drawn with Qt 6, through PyQt6. Two things come from that choice
rather than from styling. Qt's ``ExtendedSelection`` *is* the usual click
behaviour -- plain click replaces, Ctrl toggles one row, Shift selects the range
from the last click, Ctrl+Shift extends it -- so none of that is implemented
here; and Qt 6 has a real Wayland backend, so the window gets a native Wayland
surface rather than reaching the session through XWayland. On top of that, the
KDE platform theme gives the window the desktop's own Breeze style and, more to
the point, resolves the theme's icon names -- which are the same names the jump
list uses.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import NamedTuple

from . import APP_NAME, __author__, __url__, __version__
from .desktop_entry import (
    DEFAULT_ICON,
    PINNED_CAPTION_ICON,
    ICON_FOR_KIND,
    RECENT_CAPTION_ICON,
)
from .launcher import open_entry, open_folder
from .models import MenuEntry
from .pinned import Pinned
from .qtview import (
    # Re-exported: callers catch the dialog's own module attribute rather than
    # reaching past it into qtview, since this is the dialog it belongs to.
    DialogUnavailable,
    exec_dialog,
    themed_icon as _themed_icon,
)
from .qtview import application as _application
from .qtview import copy_to_clipboard as _copy_to_clipboard
from .qtview import load_qt as _load_qt
from .qtview import popup_menu as _popup
from .qtview import require_display as _require_display
from .qtview import use_platform_theme as _use_platform_theme

log = logging.getLogger(__name__)

WINDOW_TITLE = "Manage Pinned Files"

# Icon for the dialog window. The vendor desktop entry ships ``Icon=vscode``
# -- the same name the task manager draws its own icon from -- so the dialog is
# recognisably the one belonging to the application whose menu opened it. A
# different VS Code variant ships its own name here (VSCodium is ``vscodium``).
WINDOW_ICON = "vscode"

# The Wayland app id, which is *not* the icon name: a Wayland client cannot put
# an icon on its own window, so the compositor looks up ``<app id>.desktop``
# (here code.desktop, generated with ``Icon=vscode``) and takes the icon from
# there. Using the icon name as the app id would leave the window with no icon
# at all, because no ``vscode.desktop`` exists to read it from. Set as the
# desktop file name before the window is created; see run_dialog.
WINDOW_APP_ID = "code"

# Roles carried by every row, relative to Qt's first free one (UserRole). The
# entry ID is what a selection is mapped back through, so a row is never
# identified by its displayed text -- two entries can read identically. The icon
# name is recorded for the same reason: it is the thing that has to match the
# jump list, and a QIcon cannot be asked what it was built from.
ENTRY_ID_ROLE = 0
ICON_NAME_ROLE = 1

# Dynamic properties, set on a widget so that what it is showing can be read back
# (the tests do). A QIcon has no name, and a pixmap is not worth comparing.
ICON_NAME_PROPERTY = "iconName"
PANE_TITLE_PROPERTY = "paneTitle"
# Which of the two lists a pane holds, and which transfer a button performs.
# Both are recorded rather than inferred from position, so the enabling code
# and the tests can ask for "the pin button" or "the pinned pane" by name.
PANE_ROLE_PROPERTY = "paneRole"
BUTTON_ACTION_PROPERTY = "buttonAction"
RECENT_ROLE = "recent"
PINNED_ROLE = "pinned"

# The transfer buttons, top to bottom between the two panes. The horizontal
# pair sends the selection to the pane its arrow points at -- "<" to the recents
# on the left, ">" to the pinned entries on the right; the vertical pair
# reorders the pinned entries, which is also the order the menu lists them in.
#
# They are drawn with the theme's own arrows rather than as text glyphs, so they
# match the rest of the desktop the way fcitx5's list boxes do. The labels are
# the fallback for an icon theme that has no such icon: a button with nothing
# visible on it would be unusable.
LEFT_ICON = "go-previous"
RIGHT_ICON = "go-next"
UP_ICON = "go-up"
DOWN_ICON = "go-down"
LEFT_LABEL = "<"
RIGHT_LABEL = ">"
UP_LABEL = "^"
DOWN_LABEL = "v"

# The two things a transfer button can be for, recorded on the button so it can
# be found by what it does rather than by where it sits -- the tests do that, and
# so does the button-enabling code.
PIN_ACTION = "pin"
UNPIN_ACTION = "unpin"

# Icon-only buttons do not say what they do, and the arrows are a convention
# rather than a statement, so each one carries its meaning as a tooltip. The two
# transfer tooltips name their destination list, which is what makes them
# readable whichever side that list is on.
UNPIN_TOOLTIP = "Remove from Pinned Files"
PIN_TOOLTIP = "Add to Pinned Files"
UP_TOOLTIP = "Move up"
DOWN_TOOLTIP = "Move down"
TOOLTIP_FOR_ACTION = {PIN_ACTION: PIN_TOOLTIP, UNPIN_ACTION: UNPIN_TOOLTIP}

# What the About window reports, as data rather than as dialog lines: the four
# facts are checked directly by the tests, and the dialog is built from them.
ABOUT_FIELDS: tuple[tuple[str, str], ...] = (
    ("Project", APP_NAME),
    ("Version", __version__),
    ("Author", __author__),
    ("Github", __url__),
)
ABOUT_BUTTON_LABEL = "About"
CLOSE_BUTTON_LABEL = "Close"

# Window size, and the side of the square the four middle buttons are fixed to.
# A button left to size itself would be a wide rectangle, which does not line the
# column up; a square does.
WINDOW_SIZE = (900, 480)
BUTTON_SIZE = 34

# Pane headings, matching the jump list's two captions.
RECENT_PANE_TITLE = "Recent Files"
PINNED_PANE_TITLE = "Pinned Files"
RECENT_PANE_ICON = RECENT_CAPTION_ICON
PINNED_PANE_ICON = PINNED_CAPTION_ICON

# The size the menu draws these icons at, and the size they are drawn here.
ROW_ICON_SIZE = 16

# The star this window marks a pinned row with. Breeze ships both stars at
# 16/22/24px: this is the outline one, and the menu's filled one is
# ``desktop_entry.PINNED_ICON``. Its own name rather than that constant on
# purpose -- the two are changed independently, so neither can drag the other
# along.
PINNED_ROW_ICON = "non-starred"

# Shown in an empty search box. A placeholder rather than a label, so the box
# does not need one of its own and the two panes cannot drift.
SEARCH_PLACEHOLDER = "Search"


def _row_text(entry: MenuEntry) -> str:
    """How an entry reads in a list: its label, exactly as the menu shows it.

    No kind prefix and no ID, so the panes show what the jump list will show.
    Two entries can therefore read identically, which is why selections are
    tracked by entry ID rather than by the displayed text.
    """
    return entry.label


def entry_icon(entry: MenuEntry, pinned: bool) -> str:
    """The icon for one entry: the star for a pinned one, else its kind's.

    A recent gets the icon for its kind (a folder for a folder, a document for
    a file), read from the menu's own table. A pinned entry gets
    :data:`PINNED_ROW_ICON` whatever its kind, so the right pane reads as pinned
    at a glance -- a star of this window's own rather than the menu's, which is
    filled; see the constant. Pure naming, so it is testable without a display.
    """
    if pinned:
        return PINNED_ROW_ICON
    return ICON_FOR_KIND.get(entry.kind, DEFAULT_ICON)


# The entry context menu: what a right-click on a row of either pane offers.
#
# Described as data -- one ContextItem per row -- rather than built straight
# into a QMenu, so what the menu says, and when a row can act, is checkable
# without a toolkit. The same reason the panes' behaviour lives in ManageModel.
#
# "Open Folder" and "Copy Path" need a path on *this* machine, so they are
# greyed out for an entry that lives on another one. That is not a rare case
# here: a remote folder is one of the things a jump list is most useful for.
OPEN_IN_CODE_ACTION = "open-in-code"
OPEN_FOLDER_ACTION = "open-folder"
COPY_PATH_ACTION = "copy-path"
OPEN_IN_CODE_LABEL = "Open in Code"
OPEN_FOLDER_LABEL = "Open Folder"
COPY_PATH_LABEL = "Copy Path"
# The application's own icon for "open it in VS Code" -- the name the window
# itself uses, since it is the same thing being opened -- the desktop's file
# manager for "show the folder", and the usual copy glyph.
OPEN_IN_CODE_ICON = WINDOW_ICON
OPEN_FOLDER_ICON = "system-file-manager"
COPY_PATH_ICON = "edit-copy"


class ContextItem(NamedTuple):
    """One row of the entry context menu.

    ``enabled`` rather than an omission: a row that cannot act on this entry is
    greyed out, so the menu keeps its shape from row to row, and *why* a row does
    nothing is visible instead of the row simply not being there.
    """

    action: str
    label: str
    icon: str
    enabled: bool


def context_items(entry: MenuEntry) -> tuple[ContextItem, ...]:
    """The right-click menu for one entry, top to bottom.

    The same three rows on either side: what can be done with an entry does not
    depend on which of the two lists it happens to be listed in.
    """
    return (
        ContextItem(OPEN_IN_CODE_ACTION, OPEN_IN_CODE_LABEL, OPEN_IN_CODE_ICON, True),
        ContextItem(
            OPEN_FOLDER_ACTION,
            OPEN_FOLDER_LABEL,
            OPEN_FOLDER_ICON,
            entry.local_folder is not None,
        ),
        ContextItem(
            COPY_PATH_ACTION,
            COPY_PATH_LABEL,
            COPY_PATH_ICON,
            entry.local_path is not None,
        ),
    )


def run_context_action(action: str, entry: MenuEntry) -> int:
    """Carry out one context-menu row for ``entry``; 0 when it was done.

    Nothing here writes anything, so no row can make the dialog dirty: the
    entries and the pinned list are exactly as they were afterwards.
    """
    if action == OPEN_IN_CODE_ACTION:
        return open_entry(entry)
    if action == OPEN_FOLDER_ACTION:
        folder = entry.local_folder
        if folder is None:
            log.error("no local folder to show for %s", entry.uri)
            return 2
        return open_folder(folder)
    if action == COPY_PATH_ACTION:
        path = entry.local_path
        if path is None:
            log.error("no local path to copy for %s", entry.uri)
            return 2
        # The path itself, decoded: what a file manager takes and what can be
        # pasted into a shell, rather than the URI it was derived from.
        _copy_to_clipboard(str(path))
        return 0
    raise ValueError(f"unknown context action: {action!r}")


class ManageModel:
    """The dialog's state and behaviour, with no GUI toolkit involved.

    The view reads :meth:`visible_recents` / :meth:`visible_pinned` and calls
    the mutators; keeping the decisions here is what lets the suite cover the
    search, the pinning and the reordering without a display.
    """

    def __init__(self, recents: Iterable[MenuEntry], pinned: Pinned) -> None:
        self._recents = list(recents)
        self.pinned = pinned
        self._recent_query = ""
        self._pinned_query = ""
        # Set once something was pinned, unpinned or reordered, so the caller
        # knows whether the menu still has to be regenerated.
        self.dirty = False

    # -- what the two panes show -------------------------------------------

    @staticmethod
    def _matches(entry: MenuEntry, query: str) -> bool:
        """Case-insensitive substring match on the label, then on the URI.

        The URI is searched as well so a remote entry can be found by its host
        ("ssh-remote+box"), which the label does not always spell out.
        """
        if not query:
            return True
        needle = query.casefold()
        return needle in entry.label.casefold() or needle in entry.uri.casefold()

    def set_recent_query(self, text: str) -> None:
        self._recent_query = text.strip()

    def set_pinned_query(self, text: str) -> None:
        self._pinned_query = text.strip()

    def visible_recents(self) -> list[MenuEntry]:
        """Every recent entry, narrowed by the left search.

        Pinned entries stay listed. The panes are two independent views -- all
        recents on the left, the pinned subset on the right -- rather than a
        transfer box, so an entry being pinned shows up in both and nothing
        ever leaves the left pane.
        """
        return [
            entry for entry in self._recents if self._matches(entry, self._recent_query)
        ]

    def visible_pinned(self) -> list[MenuEntry]:
        """Pinned entries, in menu order, narrowed by the right search."""
        return [
            entry
            for entry in self.pinned.all()
            if self._matches(entry, self._pinned_query)
        ]

    # -- the four buttons ---------------------------------------------------

    def pin(self, entries: Iterable[MenuEntry]) -> int:
        """Pin every entry given; returns how many were newly pinned."""
        pinned = sum(1 for entry in entries if self.pinned.pin(entry))
        self.dirty = self.dirty or pinned > 0
        return pinned

    def unpin(self, entries: Iterable[MenuEntry]) -> int:
        """Unpin every entry given; returns how many were removed."""
        removed = sum(1 for entry in entries if self.pinned.unpin(entry.entry_id))
        self.dirty = self.dirty or removed > 0
        return removed

    def move(self, entry: MenuEntry, offset: int) -> bool:
        """Move one pinned up (negative) or down; True when it moved."""
        moved = self.pinned.move(entry.entry_id, offset)
        self.dirty = self.dirty or moved
        return moved

    def can_move(self, entry: MenuEntry | None, offset: int) -> bool:
        """Whether ``entry`` has room to move, so the buttons can be greyed out."""
        if entry is None:
            return False
        order = [item.entry_id for item in self.pinned.all()]
        if entry.entry_id not in order:
            return False
        return 0 <= order.index(entry.entry_id) + offset < len(order)



def _middle_button(icon_name: str, label: str, tooltip: str, QtGui, QtWidgets):
    """A square button showing the theme's ``icon_name``.

    Falls back to ``label`` when the theme has no such icon, so the button is
    never blank and unidentifiable. The icon name is recorded on the button
    either way, because a QIcon cannot be asked what it was built from.
    """
    button = QtWidgets.QPushButton()
    button.setProperty(ICON_NAME_PROPERTY, icon_name)
    button.setToolTip(tooltip)
    icon = _themed_icon(icon_name, ROW_ICON_SIZE, QtGui)
    if icon.isNull():
        button.setText(label)
    else:
        button.setIcon(icon)
    button.setFixedSize(BUTTON_SIZE, BUTTON_SIZE)
    return button


def _build_pane(title: str, icon_name: str, role: str, QtCore, QtGui, QtWidgets):
    """One pane: its heading, its search box and its list.

    Returns ``(pane, listing, query)``. The heading is built by hand rather than
    handed to a QGroupBox, whose title frame carries text only -- this heading
    has to show the same icon the jump list's caption does. ``role`` is recorded
    on the pane so it can be found without knowing which side it is on.
    """
    pane = QtWidgets.QWidget()
    pane.setProperty(PANE_ROLE_PROPERTY, role)
    column = QtWidgets.QVBoxLayout(pane)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(6)

    header = QtWidgets.QWidget()
    # Recorded on the header so the tests can read back what it was given; see
    # ICON_NAME_PROPERTY.
    header.setProperty(PANE_TITLE_PROPERTY, title)
    header.setProperty(ICON_NAME_PROPERTY, icon_name)
    heading = QtWidgets.QHBoxLayout(header)
    heading.setContentsMargins(0, 0, 0, 0)
    heading.setSpacing(6)
    # Skipped rather than drawn blank when the theme cannot resolve the name:
    # an empty badge would leave a gap in front of the title. This is reachable
    # -- a Qt without the desktop's platform theme has no icon theme at all --
    # and the heading still says which list it is either way.
    icon = _themed_icon(icon_name, ROW_ICON_SIZE, QtGui)
    if not icon.isNull():
        badge = QtWidgets.QLabel()
        badge.setPixmap(icon.pixmap(ROW_ICON_SIZE, ROW_ICON_SIZE))
        heading.addWidget(badge)
    caption = QtWidgets.QLabel(title)
    font = caption.font()
    font.setBold(True)
    caption.setFont(font)
    heading.addWidget(caption)
    heading.addStretch(1)
    column.addWidget(header)

    query = QtWidgets.QLineEdit()
    query.setPlaceholderText(SEARCH_PLACEHOLDER)
    # A search box that cannot be emptied without selecting the text is worse
    # than one that can.
    query.setClearButtonEnabled(True)
    column.addWidget(query)

    listing = _entry_list_class(QtCore, QtWidgets)()
    # The usual click/Ctrl/Shift behaviour, from the toolkit -- with one part
    # kept here; see the class docstring.
    listing.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
    listing.setIconSize(QtCore.QSize(ROW_ICON_SIZE, ROW_ICON_SIZE))
    listing.setTextElideMode(QtCore.Qt.TextElideMode.ElideRight)
    listing.setUniformItemSizes(True)
    # Right-clicking a row opens that entry's own menu (built in _build_dialog).
    #
    # The policy goes on the list and not on its viewport: the platform delivers
    # the context-menu event to the viewport, which ignores it under its default
    # policy, and Qt then hands it to the scroll area -- where the policy is
    # consulted. Setting it on the viewport instead means it is never asked, and
    # the menu simply never opens. The position that arrives is in the viewport's
    # coordinates, the ones itemAt() takes. (Measured, not assumed.)
    listing.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
    column.addWidget(listing, 1)
    return pane, listing, query


# The list widget the two panes use, built on first use. A factory rather than a
# module-scope definition because PyQt6 is imported lazily: this module has to
# import on a system without it, so it cannot subclass QtWidgets at module scope.
_ENTRY_LIST_CLASS = None


def _entry_list_class(QtCore, QtWidgets):
    """The QListWidget both panes are built from, defined once."""
    global _ENTRY_LIST_CLASS
    if _ENTRY_LIST_CLASS is not None:
        return _ENTRY_LIST_CLASS

    SelectionFlag = QtCore.QItemSelectionModel.SelectionFlag

    class EntryList(QtWidgets.QListWidget):
        """A list that forgets where a Shift range started when it is cleared.

        Qt handles all of this itself -- a plain click replaces the selection,
        Ctrl toggles one row, Shift selects the range from the last click,
        Ctrl+Shift extends it, and a click past the last row clears it -- except
        for one thing. The row a Shift range is measured from is kept by Qt
        privately, and is *not* forgotten when the selection is cleared.

        The panes clear each other, so without this a Shift click after a click
        in the other pane would extend from a row that is not selected any more:
        a range appearing out of nowhere, which is exactly what the panes being
        mutually exclusive is meant to prevent. So the anchor is kept here,
        beside the selection it belongs to, and only the Shift case is handled
        in full; everything else is Qt's.
        """

        def __init__(self) -> None:
            super().__init__()
            self._anchor: int | None = None

        def forget_selection(self) -> None:
            """Drop the selection *and* the row a Shift range is measured from."""
            self.clearSelection()
            self._anchor = None

        def mousePressEvent(self, event) -> None:
            row = self.indexAt(event.position().toPoint()).row()
            shift = bool(event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier)
            if shift and row >= 0:
                self._select_range(row, bool(event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier))
                return
            super().mousePressEvent(event)
            if row >= 0:
                # A plain or Ctrl click is what a range is measured from.
                self._anchor = row
            elif not event.modifiers():
                # The click was past the last row, which cleared the selection,
                # so there is nothing left for a range to be measured from. With
                # a modifier held that click is a no-op, and the anchor stands.
                self._anchor = None

        def _select_range(self, row: int, control: bool) -> None:
            """Select from the anchor to ``row``, keeping the anchor itself.

            With no anchor there is nothing to measure from -- the selection was
            cleared from the other pane -- so this row is selected alone rather
            than reaching back to the row Qt still remembers.
            """
            if not control:
                self.clearSelection()
            start, end = (
                (self._anchor, row) if self._anchor is not None else (row, row)
            )
            for position in range(min(start, end), max(start, end) + 1):
                self.item(position).setSelected(True)
            self._anchor = row if self._anchor is None else self._anchor
            # Move the cursor with the selection, so keyboard navigation carries
            # on from where the click landed -- without letting the selection
            # model touch the selection, which has just been set.
            self.selectionModel().setCurrentIndex(
                self.model().index(row, 0), SelectionFlag.NoUpdate
            )

    _ENTRY_LIST_CLASS = EntryList
    return _ENTRY_LIST_CLASS


def build_about_dialog(parent) -> object:
    """The About window: project, version, author and repository on one page.

    Hand-built rather than using a richer About box: the four facts are the whole
    content, so they are laid out as a label/value grid with a Close button and
    nothing else -- no logo, and no second page of credits.

    Returned rather than shown, so the window can be inspected without a display
    (see the tests).
    """
    QtCore, _QtGui, QtWidgets = _load_qt()

    about = QtWidgets.QDialog(parent)
    about.setWindowTitle(f"About {APP_NAME}")
    about.setModal(True)

    outer = QtWidgets.QVBoxLayout(about)
    grid = QtWidgets.QGridLayout()
    grid.setColumnStretch(1, 1)
    outer.addLayout(grid)
    for row, (label, value) in enumerate(ABOUT_FIELDS):
        heading = QtWidgets.QLabel(f"{label}:")
        heading.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        # Dimmed so the values, not the field names, are what stands out.
        heading.setEnabled(False)
        grid.addWidget(heading, row, 0)

        if value.startswith(("http://", "https://")):
            # A URL is worth clicking, and the text shown is the URL itself
            # rather than a name for it, so the dialog still states it plainly.
            cell = QtWidgets.QLabel(f'<a href="{value}">{value}</a>')
            cell.setOpenExternalLinks(True)
        else:
            # Selectable so the version can be copied out of the window.
            cell = QtWidgets.QLabel(value)
            cell.setTextInteractionFlags(
                QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
            )
        grid.addWidget(cell, row, 1)

    footer = QtWidgets.QHBoxLayout()
    close = QtWidgets.QPushButton(CLOSE_BUTTON_LABEL)
    close.clicked.connect(about.accept)
    footer.addStretch(1)
    footer.addWidget(close)
    outer.addLayout(footer)
    return about


def _build_dialog(model: ManageModel):
    """Build the manager window, wired to ``model``.

    Built separately from :func:`run_dialog` so the window can be built, and
    inspected, without an event loop.
    """
    QtCore, QtGui, QtWidgets = _load_qt()

    dialog = QtWidgets.QDialog()
    dialog.setWindowTitle(WINDOW_TITLE)
    dialog.setWindowIcon(_themed_icon(WINDOW_ICON, 0, QtGui))
    dialog.resize(*WINDOW_SIZE)

    # Vertical stack: the panes row, then the footer. Putting the footer in the
    # horizontal box instead would place it as a column beside the panes.
    outer = QtWidgets.QVBoxLayout(dialog)
    body = QtWidgets.QHBoxLayout()
    outer.addLayout(body, 1)

    # The entries each list is showing, so a selection can be mapped back to the
    # entries behind it rather than to row numbers.
    shown: dict[str, list[MenuEntry]] = {RECENT_ROLE: [], PINNED_ROLE: []}
    panes: dict[str, object] = {}
    # The transfer buttons by the action they carry, filled in once they exist.
    # update_buttons reads it, and is only ever called after that.
    button_for: dict[str, object] = {}

    recent_pane, recent_list, recent_query = _build_pane(
        RECENT_PANE_TITLE, RECENT_PANE_ICON, RECENT_ROLE, QtCore, QtGui, QtWidgets
    )
    pinned_pane, pinned_list, pinned_query = _build_pane(
        PINNED_PANE_TITLE, PINNED_PANE_ICON, PINNED_ROLE, QtCore, QtGui, QtWidgets
    )
    panes.update({RECENT_ROLE: recent_list, PINNED_ROLE: pinned_list})

    def selected(key: str) -> list[MenuEntry]:
        """The entries selected in one pane, in the order the pane lists them."""
        chosen = {
            item.data(int(QtCore.Qt.ItemDataRole.UserRole) + ENTRY_ID_ROLE)
            for item in panes[key].selectedItems()
        }
        return [entry for entry in shown[key] if entry.entry_id in chosen]

    def entry_at(key: str, item) -> MenuEntry | None:
        """The entry behind one row, looked up by ID and not by its text.

        Two entries can read identically, which is why a row carries the ID of
        the entry it was built from. The pane's own list is searched rather than
        the store, so the row acts on what the user is looking at.
        """
        entry_id = item.data(int(QtCore.Qt.ItemDataRole.UserRole) + ENTRY_ID_ROLE)
        return next(
            (entry for entry in shown[key] if entry.entry_id == entry_id), None
        )

    def show_context_menu(key: str, position) -> None:
        """The entry menu for the row under ``position``, if there is one.

        ``position`` is the viewport's, which is what Qt hands over (see
        _build_pane) and what both ``itemAt`` and the popup below want.

        Right-clicking past the last row opens nothing: there is no entry to act
        on, and a menu whose every row was greyed out would be a worse answer
        than no menu at all. One entry is acted on -- the row that was clicked,
        which Qt has already selected if it was not part of the selection.
        """
        listing = panes[key]
        item = listing.itemAt(position)
        entry = entry_at(key, item) if item is not None else None
        if entry is None:
            return
        menu = QtWidgets.QMenu(listing)
        for row in context_items(entry):
            action = menu.addAction(
                _themed_icon(row.icon, ROW_ICON_SIZE, QtGui), row.label
            )
            action.setEnabled(row.enabled)
            action.triggered.connect(
                # `triggered` carries a checked flag that means nothing here, so
                # it is swallowed and this row's own action id is bound instead.
                lambda _checked=False, action_id=row.action: run_context_action(
                    action_id, entry
                )
            )
        _popup(menu, listing.viewport().mapToGlobal(position))

    def update_buttons() -> None:
        """Grey out whatever cannot act on the current selection."""
        button_for[PIN_ACTION].setEnabled(bool(selected(RECENT_ROLE)))
        pinned_selected = selected(PINNED_ROLE)
        button_for[UNPIN_ACTION].setEnabled(bool(pinned_selected))
        # Reordering is a one-at-a-time operation: with several rows selected
        # there is no single "up" that means anything.
        single = pinned_selected[0] if len(pinned_selected) == 1 else None
        for button, offset in ((up_button, -1), (down_button, 1)):
            button.setEnabled(model.can_move(single, offset))

    def refresh(preserve: bool = True) -> None:
        """Repopulate both panes from the model, keeping the selection if asked."""
        previous = {key: selected(key) if preserve else [] for key in panes}
        shown[RECENT_ROLE] = model.visible_recents()
        shown[PINNED_ROLE] = model.visible_pinned()
        for key, listing in panes.items():
            pinned = key == PINNED_ROLE
            # Rebuilding clears the selection, which would otherwise fire
            # selectionChanged and drop the *other* pane's selection while it was
            # being restored.
            listing.blockSignals(True)
            listing.clear()
            for entry in shown[key]:
                icon_name = entry_icon(entry, pinned)
                item = QtWidgets.QListWidgetItem(_themed_icon(icon_name, ROW_ICON_SIZE, QtGui), _row_text(entry))
                item.setData(int(QtCore.Qt.ItemDataRole.UserRole) + ENTRY_ID_ROLE, entry.entry_id)
                item.setData(int(QtCore.Qt.ItemDataRole.UserRole) + ICON_NAME_ROLE, icon_name)
                # The label alone does not say where an entry points.
                item.setToolTip(entry.uri)
                listing.addItem(item)
            keep = {entry.entry_id for entry in previous[key]}
            for row, entry in enumerate(shown[key]):
                if entry.entry_id in keep:
                    listing.item(row).setSelected(True)
            listing.blockSignals(False)
        update_buttons()

    def clear_other_pane(key: str) -> None:
        """Drop the other pane's selection, so only one list is ever active.

        Without this it would be ambiguous which list the four buttons act on.
        Only done when *this* pane gained a selection: an empty selection is
        what clearing a pane produces, and reacting to that would make the two
        panes clear each other.
        """
        if not selected(key):
            return
        other = panes[PINNED_ROLE if key == RECENT_ROLE else RECENT_ROLE]
        if not other.selectedItems():
            return
        # forget_selection rather than clearSelection: it drops the row a Shift
        # range would be measured from as well. See _entry_list_class.
        other.blockSignals(True)
        other.forget_selection()
        other.blockSignals(False)

    def on_selection_changed(key: str) -> None:
        clear_other_pane(key)
        update_buttons()

    def transfer_pin() -> None:
        if model.pin(selected(RECENT_ROLE)):
            # Only the pinned entries changed: the recents pane still lists
            # everything, so both panes keep their selection.
            refresh()

    def transfer_unpin() -> None:
        if model.unpin(selected(PINNED_ROLE)):
            refresh()

    def reorder(offset: int) -> None:
        chosen = selected(PINNED_ROLE)
        if len(chosen) == 1 and model.move(chosen[0], offset):
            refresh()  # the entry keeps its selection on its new row

    middle = QtWidgets.QVBoxLayout()
    middle.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

    # The arrows keep pointing the way their entries travel: "<" back to the
    # recents on the left, ">" over to the pinned entries on the right. The
    # action is recorded on the button so the enabling code and the tests can
    # ask for "the pin button" rather than for the right-hand one.
    def transfer_button(is_left: bool) -> object:
        action = UNPIN_ACTION if is_left else PIN_ACTION
        button = _middle_button(
            LEFT_ICON if is_left else RIGHT_ICON,
            LEFT_LABEL if is_left else RIGHT_LABEL,
            TOOLTIP_FOR_ACTION[action],
            QtGui,
            QtWidgets,
        )
        button.setProperty(BUTTON_ACTION_PROPERTY, action)
        button.clicked.connect(transfer_unpin if is_left else transfer_pin)
        return button

    left_button, right_button = transfer_button(True), transfer_button(False)
    button_for = {UNPIN_ACTION: left_button, PIN_ACTION: right_button}
    up_button = _middle_button(UP_ICON, UP_LABEL, UP_TOOLTIP, QtGui, QtWidgets)
    down_button = _middle_button(DOWN_ICON, DOWN_LABEL, DOWN_TOOLTIP, QtGui, QtWidgets)
    up_button.clicked.connect(lambda: reorder(-1))
    down_button.clicked.connect(lambda: reorder(1))
    for button in (left_button, right_button, up_button, down_button):
        middle.addWidget(button)

    # Recents on the left, pinned on the right: the order the menu lists its two
    # blocks in, and the direction the transfer arrows point.
    body.addWidget(recent_pane, 1)
    body.addLayout(middle)
    body.addWidget(pinned_pane, 1)

    # One row spanning the window: About at the left edge, Close at the right.
    footer = QtWidgets.QHBoxLayout()
    about_button = QtWidgets.QPushButton(ABOUT_BUTTON_LABEL)
    about_button.clicked.connect(lambda: build_about_dialog(dialog).exec())
    close_button = QtWidgets.QPushButton(CLOSE_BUTTON_LABEL)
    close_button.clicked.connect(dialog.accept)
    footer.addWidget(about_button)
    footer.addStretch(1)
    footer.addWidget(close_button)
    outer.addLayout(footer)

    for key, listing in panes.items():
        listing.itemSelectionChanged.connect(lambda key=key: on_selection_changed(key))
        listing.customContextMenuRequested.connect(
            lambda position, key=key: show_context_menu(key, position)
        )

    recent_query.textChanged.connect(
        lambda text: (model.set_recent_query(text), refresh())
    )
    pinned_query.textChanged.connect(
        lambda text: (model.set_pinned_query(text), refresh())
    )

    refresh()
    return dialog


# The runner the suite replaces to drive the window without an event loop; see
# qtview.exec_dialog.
_run = exec_dialog


def run_dialog(model: ManageModel) -> bool:
    """Show the manager in a Qt window, blocking until it is closed.

    Returns True when the pinned entries changed, so the caller can regenerate
    the menu. There is deliberately no Cancel button: edits are written as they
    are made -- the file is tiny and written atomically -- so closing never
    discards work, and nothing implies otherwise.
    """
    _require_display()
    # Both of these have to happen before a window exists: the platform theme is
    # read when the application is created, and the app id decides which
    # <app id>.desktop file the compositor draws the window icon from.
    _use_platform_theme()
    QtCore, QtGui, QtWidgets = _load_qt()
    QtGui.QGuiApplication.setDesktopFileName(WINDOW_APP_ID)
    _application(QtWidgets)

    dialog = _build_dialog(model)
    _run(dialog)
    return model.dirty
