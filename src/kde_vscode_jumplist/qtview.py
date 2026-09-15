"""The Qt plumbing the pinned-entries manager is built from.

The manager is a Qt window that has to look like the rest of the desktop, and
it is optional: the CLI imports and every other command works on a machine
without PyQt6, so Qt is imported lazily and a missing binding is reported rather
than raised. That much is common, and it lives here instead of in the dialog
module.

Nothing here knows what the dialog is for -- no entries, no layout beyond what a
window needs to exist.
"""

from __future__ import annotations

import os

# The Qt platform theme, which picks both the widget style and -- the part that
# matters more here -- the icon theme. The value is the name of the plugin
# ("kde" is KDEPlasmaPlatformTheme6), not the name of a style.
#
# It is set by the callers rather than left to the session because without it Qt
# falls back to its built-in Fusion style and to an icon theme it cannot resolve
# *at all*: every icon in either dialog, headings, rows and buttons alike, comes
# out blank. That is a silent failure, so it is worth being explicit about.
PLATFORM_THEME = "kde"

# The QApplication, kept at module scope on purpose: Qt owns the object, but the
# Python wrapper has to stay referenced or it is collected and takes the running
# application down with it.
_APPLICATION = None


class DialogUnavailable(RuntimeError):
    """A dialog cannot be shown: no Qt bindings, or no display to draw on.

    Raised instead of an ImportError so the caller has one thing to catch
    whatever the reason was, and can report it as a plain sentence.
    """


def load_qt():
    """Import the PyQt6 modules a dialog is built from.

    Returns ``(QtCore, QtGui, QtWidgets)``: this module has no module-scope
    binding for them, so the caller has to be handed them.

    Imported inside the call rather than at module scope so that importing this
    module, and the CLI with it, still works on a system without PyQt6.
    """
    try:
        from PyQt6 import QtCore, QtGui, QtWidgets
    except ImportError as error:  # pragma: no cover - depends on the system
        raise DialogUnavailable(
            "Qt 6 bindings are not installed (on Debian/Ubuntu: "
            "apt install python3-pyqt6)"
        ) from error
    return QtCore, QtGui, QtWidgets


def require_display() -> None:
    """Raise :class:`DialogUnavailable` when there is nowhere to draw.

    Checked *before* a QApplication exists, because Qt does not fail gracefully
    here: with no platform plugin it calls qFatal, which aborts the process. That
    would take the whole CLI down -- and the `open` command the menu actions
    call, since they share this entry point -- so the check has to come first
    and be a normal exception.
    """
    if os.environ.get("QT_QPA_PLATFORM"):
        return  # explicitly chosen, offscreen included: not our business
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"):
        return
    raise DialogUnavailable("no graphical display available")


def use_platform_theme() -> None:
    """Ask Qt for the desktop's own style and icons, unless told otherwise.

    Set before the QApplication exists, because the platform theme is chosen
    once, when it is created. ``setdefault`` rather than assignment, so a user
    who has configured Qt differently keeps their choice -- the fallback, not a
    decision.
    """
    os.environ.setdefault("QT_QPA_PLATFORMTHEME", PLATFORM_THEME)


def application(QtWidgets):
    """The QApplication, created once per process.

    Qt allows exactly one, and constructing a second is fatal, so an existing one
    is reused. Both halves matter: the suite opens a window per test in one
    process, and the reference is held here so the application outlives the call
    that made it.
    """
    global _APPLICATION
    if _APPLICATION is None:
        _APPLICATION = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return _APPLICATION


def themed_icon(name: str, size: int, QtGui):
    """The theme's icon ``name``, ready to draw at ``size`` pixels.

    A null QIcon when the theme has no such name, which is what the callers test
    to fall back to their label text.
    """
    icon = QtGui.QIcon.fromTheme(name)
    if size and not icon.isNull():
        # Ask for the size up front: a themed icon is a vector, and letting the
        # widget rescale a default-size pixmap would blur it.
        icon = QtGui.QIcon(icon.pixmap(size, size))
    return icon


def exec_dialog(dialog) -> None:
    """Show ``dialog`` and block until it is closed.

    A one-line function so each dialog can expose it as its own seam: the suite
    replaces the dialog module's name for this with one that inspects the window
    and closes it, which is both simpler and less fragile than hunting for the
    window by hand.
    """
    dialog.exec()


def popup_menu(menu, global_position) -> None:
    """Show ``menu`` at ``global_position``, blocking until a row is picked.

    A seam for the same reason :func:`exec_dialog` is one: it lets the suite be
    handed the menu that was built -- to read its rows, and to pick one --
    instead of an event loop having to open it and a click having to be forged.
    """
    menu.exec(global_position)


def copy_to_clipboard(text: str) -> None:
    """Put ``text`` on the clipboard, importing Qt on first use.

    No Qt modules are handed in, unlike the dialog builders: there is nothing to
    be given here that the caller already has, so the signature stays a plain
    string in and nothing out -- which is also what lets the dialog keep its
    copy rows free of toolkit code.

    The clipboard belongs to the running application and a Wayland or X11
    clipboard is *served* by the process that filled it, so this is one more
    reason the QApplication has to outlive the window: see the module-scope
    _APPLICATION above.
    """
    _QtCore, _QtGui, QtWidgets = load_qt()
    QtWidgets.QApplication.clipboard().setText(text)
