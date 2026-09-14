# kde-vscode-jumplist

Dynamic KDE Plasma task manager jump lists for VS Code — recent files, folders
and remote targets, with pinnable entries.

![The VS Code icon menu in KDE Plasma's task manager, listing pinned entries
and recent entries](screenshots/context_menu.png)

Right-click the VS Code icon in the task manager and pick an entry; it opens in
VS Code. A per-user systemd service keeps the list in step with VS Code's
history.

![The Manage Pinned Files dialog: the pinned entries in one pane, the recent
entries in the other, and the four buttons between them](screenshots/manage_pinned_files.png)

The menu's last entry opens that window: pin an entry, drag its order up or
down, and search either list. The **Settings** button in its footer edits the
configuration.

## Install

```bash
make install    # builds the executable, then installs it and the service
```

Needs Python ≥ 3.11, KDE Plasma, systemd user units, and — for the dialog —
Qt 6 through PyQt6 (`python3-pyqt6`). Without PyQt6 the CLI says so and every
other command keeps working.

## Use

```bash
kde-vscode-jumplist recent    # refresh, and list entries with their IDs
kde-vscode-jumplist pin <entry-id>
kde-vscode-jumplist pinned    # list pinned entries
kde-vscode-jumplist manage    # pin and reorder in a dialog
kde-vscode-jumplist update    # regenerate the menu
```

Everything else is configured in one file, `~/.config/kde-vscode-jumplist/config.toml`,
which `install` writes the first time and the dialog's **Settings** button edits.

## Details

Install layout, the full configuration reference, how it works, both dialogs,
the Makefile and the test suite: **[DETAILS.md](DETAILS.md)**.
