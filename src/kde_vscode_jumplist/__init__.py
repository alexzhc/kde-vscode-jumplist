"""Dynamic KDE Plasma task manager jump lists for VS Code."""

__version__ = "0.1.0"
__author__ = "alexzhc"
# The project's home, shown in the About dialog. tests/test_manage.py checks it
# against the URL declared in pyproject.toml, so the two cannot drift.
__url__ = "https://github.com/alexzhc/kde-vscode-jumplist"

# Name of the program and of the directories it keeps under $XDG_CONFIG_HOME and
# $XDG_BIN_HOME. Defined here because both paths.py and config.py need it, and
# either importing the other would be a cycle.
APP_NAME = "kde-vscode-jumplist"
