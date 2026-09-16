# kde-vscode-jumplist

Dynamic KDE Plasma task manager jump lists for VS Code — recent files, folders
and remote targets, with pinnable entries.

![The VS Code icon menu in KDE Plasma's task manager, listing pinned entries
and recent entries](screenshots/context_menu.png)

Right-click the VS Code icon in the task manager and pick an entry; it opens in
VS Code. A per-user systemd service keeps the list in step with VS Code's
history.

![The Manage Pinned Files dialog: the recent entries in the left pane, the pinned
entries in the right, and the four buttons between them](screenshots/manage_pinned_files.png)

The **Pinned Files: ▹** heading is itself the way into that window — click it to
pin, unpin and reorder — so there is no separate entry to go hunting for further
down the menu. Pinned entries carry a star, which is what marks them out from
the recents at a glance.

The menu holds up to twelve entries in all: the recents newest-first, then the
pinned entries in the order you arranged them. Workspaces are left out of the
recents (a `.code-workspace` usually repeats a folder already listed), though a
pinned one still shows.

## Install

```bash
make install    # builds the executable, then installs it and the service
```

Needs Python ≥ 3.11, KDE Plasma, systemd user units, and — for the dialog —
Qt 6 through PyQt6 (`python3-pyqt6`). Without PyQt6 the CLI says so and every
other command keeps working.

## Use

```bash
kde-vscode-jumplist recent        # refresh, and list entries with their IDs
kde-vscode-jumplist recent --uri  # ... also showing each URI
kde-vscode-jumplist pin <entry-id>
kde-vscode-jumplist unpin <entry-id>
kde-vscode-jumplist pinned        # list pinned entries
kde-vscode-jumplist manage        # pin and reorder in a dialog
kde-vscode-jumplist update        # regenerate the menu
```

`recent` is the one to start with: it prints the IDs that `pin` and `unpin`
expect. By default its list matches what the menu shows, so kinds the menu
leaves out can be revealed with `--all`.

Everything else is fixed: the tool keeps its files under
`~/.config/kde-vscode-jumplist`, writes the generated menu to
`~/.local/share/applications`, and finds VS Code itself. There is no
configuration file and nothing to set up.

One thing is chosen rather than found: which family of editors to aim at. The
`FORK` environment variable does that — `VSCODE` by default (VS Code,
Insiders, OSS and VSCodium), `BUDDY` for Tencent CodeBuddy CN:

```bash
FORK=BUDDY kde-vscode-jumplist recent   # list CodeBuddy's recents instead
FORK=BUDDY make install                 # and make the service watch CodeBuddy
```

Set it when installing, too: the service runs where a shell's exports do not
reach, so `install` bakes the fork into the unit it writes.

## Details

Install layout, how the menu is built, the dialog's behaviour, the Makefile and
the test suite: **[DETAILS.md](DETAILS.md)**.
