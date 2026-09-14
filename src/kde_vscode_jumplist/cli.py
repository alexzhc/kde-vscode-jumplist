"""Command line interface.

Commands:
  recent    refresh from VS Code and list the recent entries
  update    regenerate the right-click context menu (--watch to keep running)
  pin       pin an entry
  unpin     unpin an entry
  pinned    list pinned entries
  manage    open the pin dialog (also the menu's "Manage Pinned Files…")
  open      resolve an entry ID and launch it (used by the menu actions)
  install / uninstall  set up or remove the systemd user units
  install-config  apply this configuration to the one the service reads
  reset     restore the generated desktop entry from the vendor file

The `--config` option names the configuration file, and it is the one thing
every command needs: the settings live in ``config.toml`` (see
:mod:`kde_vscode_jumplist.config`), and a missing file is an error rather than
a run against built-in defaults. Note that ``install`` copies a checkout's file
to the user's own and then never overwrites it, so an edit made in a checkout
reaches the service through ``install-config``.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import APP_NAME, __version__
from . import config
from . import desktop_entry
from . import manage as manage_panel
from .discovery import discover_installations
from .entry_store import EntryStore
from .pinned import Pinned
from .launcher import open_entry
from .paths import data_dir, user_bin_dir
from .sync import run_locked_sync
from .util import atomic_write_text

log = logging.getLogger("kde_vscode_jumplist")

# Same string as the package's APP_NAME, defined once there.
PROG = APP_NAME

# Narrowest a table column may be squeezed to when fitting the terminal.
MIN_COLUMN_WIDTH = 24

# Seconds between passes for `update --watch`. A bare `--watch` uses this; the
# CLI accepts an override after it.
DEFAULT_WATCH_INTERVAL = 5.0

SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"
SERVICE_NAME = "kde-vscode-jumplist.service"
# Unit names from earlier versions. They are removed on install as well as
# uninstall, so an upgrade cannot leave a second timer running against the same
# desktop file: "-sync" was dropped when the CLI command was renamed, the
# project was once kde-vscode-menu, and the timer itself was replaced by the
# service running `update --watch`.
LEGACY_UNIT_NAMES = (
    "kde-vscode-jumplist.timer",
    "kde-vscode-jumplist-sync.service",
    "kde-vscode-jumplist-sync.timer",
    "kde-vscode-menu-sync.service",
    "kde-vscode-menu-sync.timer",
)

# Type=simple because this is a long-running watcher, not a single pass: it syncs
# and then sleeps, for as long as the service is up (see cli._watch). Restart
# covers an unexpected exit; a clean stop via `systemctl stop` is not a failure
# and is not restarted.
#
# The configuration file is part of ExecStart rather than a set of Environment=
# lines, so the service reads exactly the settings `install` read -- including
# any of them changed afterwards, which the unit then needs no rewriting for.
SERVICE_TEMPLATE = """\
[Unit]
Description=Keep KDE Plasma jump lists in step with VS Code's recent entries

[Service]
Type=simple
ExecStart={exec_start}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


def _systemctl(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argv list, no shell
        ["systemctl", "--user", *arguments], capture_output=True, text=True
    )


def _remove_legacy_units() -> list[str]:
    """Delete units left behind by earlier versions; returns their names."""
    removed: list[str] = []
    for name in LEGACY_UNIT_NAMES:
        path = SYSTEMD_USER_DIR / name
        if not path.exists():
            continue
        # Disable first: a lingering timer would keep firing the old unit.
        _systemctl("disable", "--now", name)
        path.unlink(missing_ok=True)
        removed.append(name)
    return removed


def install_executable() -> Path | None:
    """Copy the built executable into the user's bin directory.

    Returns the installed path, or ``None`` when there is nothing to install --
    a pipx/pip install has no ``bin/`` artifact, and its console script already
    lives somewhere appropriate.
    """
    built = desktop_entry.packaged_cli_path()
    if built is None:
        return None
    target = user_bin_dir() / desktop_entry.PROG
    target.parent.mkdir(parents=True, exist_ok=True)
    # Skip the copy when the content already matches, but never skip the
    # executable bit: a plain copy would leave a 0644 file that Plasma cannot
    # run, and the menu would look clickable while doing nothing.
    if not (target.is_file() and target.read_bytes() == built.read_bytes()):
        shutil.copyfile(built, target)
    if not target.stat().st_mode & 0o111:
        target.chmod(0o755)
    return target


def _remove_installed_executable() -> Path | None:
    """Delete the executable ``install`` placed in the user's bin directory.

    A symlink is left alone: that is how pipx exposes the program, and it is
    not ours to delete.
    """
    target = user_bin_dir() / desktop_entry.PROG
    if not target.is_file() or target.is_symlink():
        return None
    target.unlink()
    return target


def _watch_interval(text: str) -> float:
    """Parse the optional value after ``--watch``: seconds, as a float.

    Rejected rather than silently defaulted: ``update --watch 5m`` would
    otherwise run at the built-in interval and look like it had been accepted.
    """
    try:
        interval = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"not a number of seconds: {text!r} (try --watch 5)"
        ) from None
    if interval <= 0:
        raise argparse.ArgumentTypeError(f"interval must be greater than zero: {text!r}")
    return interval


def _seconds_text(interval: float) -> str:
    """An interval for a message: 5.0 -> "5s", 2.5 -> "2.5s"."""
    return f"{interval:g}s"


def _watch(interval: float, verbose: bool) -> int:
    """Sync, wait ``interval`` seconds, and repeat until interrupted.

    Used instead of a systemd timer where a long-running process is simpler:
    there is no unit to install, no boot ordering, and the interval can be
    changed by restarting the one command. SIGINT is how this loop is meant to
    end, so Ctrl-C is reported as a normal stop rather than a failure.

    Each pass goes through :func:`run_locked_sync`, so a second watcher or the
    ``manage`` dialog cannot interleave with it.

    Output is flushed line by line: stdout is block buffered when it is not a
    terminal, and this runs as a service whose output is a pipe to the journal,
    where an unflushed line may not appear until the buffer fills -- which, for
    a loop that mostly sleeps, can be never.
    """
    print(
        f"watching: syncing every {_seconds_text(interval)} (Ctrl-C to stop)",
        flush=True,
    )
    try:
        while True:
            changed = run_locked_sync()
            if verbose:
                print("updated (changed)" if changed else "updated (no changes)", flush=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("stopped", flush=True)
        return 0


def cmd_update(args: argparse.Namespace) -> int:
    """Sync once, or repeatedly when ``--watch`` was given."""
    if getattr(args, "watch", None) is not None:
        return _watch(args.watch, args.verbose)
    changed = run_locked_sync()
    if args.verbose:
        print("updated (changed)" if changed else "updated (no changes)")
    return 0


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    """Print ``rows`` as a table that fits the terminal.

    Imported lazily: ``tabulate`` is only needed for the listing commands, so a
    missing install degrades to tab-separated columns instead of breaking the
    ``open`` path that the menu actions call.
    """
    try:
        from tabulate import tabulate
    except ImportError:
        print("\t".join(headers))
        for row in rows:
            print("\t".join(row))
        return
    if not rows:
        return
    print(tabulate(rows, headers=headers, tablefmt="simple", maxcolwidths=_column_caps(headers, rows)))


def _column_caps(headers: list[str], rows: list[list[str]]) -> list[int] | None:
    """Per-column width caps, or ``None`` when the table already fits.

    Long values (remote labels, URIs) would otherwise push the table past the
    terminal width and make the whole thing wrap; the widest columns are
    narrowed until it fits, so a long cell wraps in place instead.
    """
    natural = [max(len(h), max(len(row[i]) for row in rows)) for i, h in enumerate(headers)]
    # Reserve a few columns: tabulate pads the drawn rule slightly wider than
    # the sum of the cell widths.
    overflow = sum(natural) + 2 * (len(headers) - 1) - (shutil.get_terminal_size().columns - 6)
    if overflow <= 0:
        return None
    caps = list(natural)
    while overflow > 0:
        widest = caps.index(max(caps))
        if caps[widest] <= MIN_COLUMN_WIDTH:
            break  # nothing left worth shrinking
        shrink = min(overflow, caps[widest] - MIN_COLUMN_WIDTH)
        caps[widest] -= shrink
        overflow -= shrink
    return caps


def cmd_recent(args: argparse.Namespace) -> int:
    """Refresh from VS Code, then list entries with their IDs.

    The IDs are what ``pin``/``unpin`` expect, so this is where you look them
    up. URIs are omitted by default because they dominate the width of the
    table; pass ``--uri`` to show them.

    By default the table matches the menu, hiding kinds the menu excludes
    (workspaces); ``--all`` lists them too, so nothing is unpinnable.
    """
    run_locked_sync()
    stored = EntryStore().all()
    if not stored:
        print("no recent entries (run with -v to see why)", file=sys.stderr)
        return 0
    entries = stored if args.all else desktop_entry.keep_recent(stored)
    pinned = Pinned()
    headers = ["Entry ID", "Kind", "Label", "Pinned"]
    rows = [
        [
            entry.entry_id,
            entry.kind,
            entry.label,
            "yes" if pinned.contains(entry.entry_id) else "",
        ]
        for entry in entries
    ]
    if args.uri:
        headers.append("URI")
        for row, entry in zip(rows, entries):
            row.append(entry.uri)
    _print_table(headers, rows)
    noun = "entry" if len(entries) == 1 else "entries"
    print(f"\n{len(entries)} {noun} - pin one with: {PROG} pin <entry-id>")
    if len(stored) > len(entries):
        hidden = ", ".join(sorted(desktop_entry.excluded_kinds()))
        print(
            f"({len(stored) - len(entries)} hidden: {hidden} - set exclude_kinds in "
            f"{config.current().path} to change, or pass --all)"
        )
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    entry = EntryStore().get(args.entry_id)
    if entry is None:
        # Fall back to the pinned entries, which stay resolvable even after VS
        # Code has forgotten them.
        entry = Pinned().get(args.entry_id)
    if entry is None:
        log.error("unknown entry id: %s", args.entry_id)
        return 2
    return open_entry(entry)


def cmd_pin(args: argparse.Namespace) -> int:
    entry = EntryStore().get(args.entry_id)
    if entry is None:
        log.error("unknown entry id: %s (run `%s recent` first)", args.entry_id, PROG)
        return 2
    Pinned().pin(entry)
    run_locked_sync()
    return 0


def cmd_unpin(args: argparse.Namespace) -> int:
    if not Pinned().unpin(args.entry_id):
        log.error("not pinned: %s (see `%s pinned`)", args.entry_id, PROG)
        return 2
    run_locked_sync()
    return 0


def cmd_pinned(_args: argparse.Namespace) -> int:
    pinned = Pinned().all()
    if not pinned:
        print(f"no pinned entries yet - add one with: {PROG} pin <entry-id>")
        return 0
    _print_table(
        ["Entry ID", "Kind", "Label"],
        [[entry.entry_id, entry.kind, entry.label] for entry in pinned],
    )
    return 0


def cmd_manage(args: argparse.Namespace) -> int:
    """Open the pin manager: the menu's "Manage Pinned Files…" action.

    The recents are refreshed first, so the left pane lists what VS Code has
    open now rather than whatever the last timer tick left behind. The menu is
    regenerated afterwards only if the dialog actually changed something.
    """
    run_locked_sync()
    model = manage_panel.ManageModel(EntryStore().all(), Pinned())
    try:
        changed = manage_panel.run_dialog(model)
    except manage_panel.DialogUnavailable as error:
        log.error("cannot open the manager dialog: %s", error)
        return 2
    if changed:
        run_locked_sync()
    return 0


def install_configuration() -> Path:
    """The file the installed service runs against.

    ``install`` sets up a service that outlives the checkout it was installed
    from, so the unit always names the user's own file: the configuration in
    use is copied there the first time, and left alone afterwards, so a later
    ``install`` cannot overwrite settings the service has been running with.
    From here on this run resolves against that file too.

    There is nothing to invent when no file was found: the settings have no
    built-in defaults, and `config.current()` has already reported where it
    looked.
    """
    source = config.current()  # raises when there is no configuration to read
    target = config.installed_path()
    if source.path != target:
        if target.is_file():
            print(f"using the existing configuration at {target}")
            _note_if_diverged(source.path, target)
        else:
            atomic_write_text(target, source.path.read_text(encoding="utf-8"))
            print(f"wrote {target} from {source.path}")
        config.use(target)
    return target


def _note_if_diverged(source: Path, target: Path) -> None:
    """Point out that the file just read is not the one the service uses.

    Two files called config.toml is the one genuinely confusing thing about
    this setup: editing the checkout's copy changes nothing, because the service
    reads the user's own, and the next watcher pass overwrites whatever a
    foreground run wrote. ``install`` is where the two are both in hand, so it
    is where the difference is worth saying out loud.
    """
    try:
        same = source.read_text(encoding="utf-8") == target.read_text(encoding="utf-8")
    except OSError:  # unreadable either way; not this note's business
        return
    if same:
        return
    print(
        f"note: {source} differs from it, and the service reads {target}\n"
        f"      run `{PROG} install-config` to apply the settings in {source}"
    )


def cmd_install_config(_args: argparse.Namespace) -> int:
    """Copy the configuration in use over the installed one.

    Deliberately separate from ``install``: that one never overwrites an
    installed configuration, so a reinstall cannot discard settings a running
    service was started with. The cost is that the checkout's config.toml and
    the user's copy drift apart, with the service reading the second -- so
    applying an edit made in the checkout is its own command rather than a side
    effect of installing.
    """
    source = config.current()
    target = config.installed_path()
    if source.path == target:
        print(f"already using {target}")
        return 0
    atomic_write_text(target, source.path.read_text(encoding="utf-8"))
    print(f"wrote {target} from {source.path}")
    # The running watcher re-reads on change, so nothing has to be restarted.
    print("the service picks it up on its next pass; no restart needed")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    # First, so a missing or broken configuration stops the command before it
    # has touched the unit directory or removed anything.
    install_configuration()

    SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)

    # Drop units from earlier versions first: two timers writing the same
    # desktop file would fight each other.
    for name in _remove_legacy_units():
        print(f"removed obsolete unit {name}")

    installed = install_executable()
    if installed is not None:
        launcher = [str(installed)]
        print(f"installed {installed}")
        if str(user_bin_dir()) not in os.environ.get("PATH", "").split(os.pathsep):
            print(f"note: {user_bin_dir()} is not on PATH")
    else:
        # No built executable (e.g. a pipx install): reuse whatever verified
        # launcher the desktop entries use, so the service keeps tracking it.
        launcher = desktop_entry.resolve_cli_argv()
        log.info(
            "no built executable to install; the service will run %s",
            desktop_entry.format_exec(launcher),
        )

    # The unit names the configuration file, so it reads the settings this run
    # read however it is started -- systemd exports nothing either.
    exec_start = desktop_entry.format_exec(
        [*launcher, *desktop_entry.config_arguments(), "update", "--watch"]
    )
    (SYSTEMD_USER_DIR / SERVICE_NAME).write_text(
        SERVICE_TEMPLATE.format(exec_start=exec_start), encoding="utf-8"
    )
    # enable then restart, rather than enable --now. The latter starts the
    # service but deliberately leaves an *already* running one alone -- wrong
    # here, because `install` has just replaced the executable it runs. The old
    # process keeps its old code in memory, and since it owns the menu it keeps
    # regenerating it from that code, so an install looks like it did nothing
    # until something restarts it by hand. `restart` also starts a stopped
    # service, so the two calls cover both cases.
    for command in (("daemon-reload",), ("enable", SERVICE_NAME), ("restart", SERVICE_NAME)):
        result = _systemctl(*command)
        if result.returncode != 0:
            log.error("systemctl %s failed: %s", " ".join(command), result.stderr.strip())
            return result.returncode
    print(
        f"installed {SERVICE_NAME} (watching every {_seconds_text(DEFAULT_WATCH_INTERVAL)})\n"
        f"configuration:  {config.current().path}\n"
        f"data directory: {data_dir()}\n"
        f"menu file:      {desktop_entry.paths.user_applications_dir()}\n"
        f"follow it with: systemctl --user status {SERVICE_NAME}"
    )
    return 0


def cmd_uninstall(_args: argparse.Namespace) -> int:
    for name in (SERVICE_NAME, *LEGACY_UNIT_NAMES):
        _systemctl("disable", "--now", name)
    for name in (SERVICE_NAME, *LEGACY_UNIT_NAMES):
        (SYSTEMD_USER_DIR / name).unlink(missing_ok=True)
    _systemctl("daemon-reload")
    print(f"uninstalled {SERVICE_NAME}")

    removed = _remove_installed_executable()
    if removed is not None:
        print(f"removed {removed}")
    return 0


def cmd_reset(_args: argparse.Namespace) -> int:
    """Restore the generated desktop entries from the vendor files.

    Our menu file is the vendor entry with ``Actions=`` groups appended, so a
    generated file that has drifted -- hand-edited, truncated, or left behind
    by an older version -- can be rebuilt rather than diagnosed. The generated
    copies are deleted and written again from the vendor text.

    Nothing else is touched. In particular no sync is run: the pinned entries,
    ``entries.json`` and the systemd units are left exactly as they are, and
    the entry cache is not rewritten. The list is rebuilt from the entries
    already stored, so this repairs the menu file without altering what the
    menu contains.
    """
    apps_dir = desktop_entry.paths.user_applications_dir()
    removed: list[Path] = []
    if apps_dir.is_dir():
        for path in sorted(apps_dir.glob("*.desktop")):
            # Only ours: the vendor file is discovered later and must survive,
            # and an unrelated application's entry is none of our business.
            if desktop_entry.is_generated(path):
                path.unlink()
                removed.append(path)
    for path in removed:
        print(f"removed {path}")

    store = EntryStore()
    pinned = Pinned()
    restored: list[Path] = []
    for installation in discover_installations():
        if installation.desktop_path is None:
            log.warning("no desktop file found for %s", installation.variant)
            continue
        # Mirrors what a sync would pass, but from the stored entries, so the
        # cache is read and never written here.
        pinned_entries = [
            entry for entry in pinned.all() if entry.source == installation.variant
        ]
        recents = [
            entry
            for entry in store.all()
            if entry.source == installation.variant
            and not pinned.contains(entry.entry_id)
        ]
        written = desktop_entry.write_user_desktop_entry(
            installation.desktop_path, installation.variant, pinned_entries, recents
        )
        if written is not None:
            restored.append(written)

    for path in restored:
        print(f"restored {path}")
    if not restored:
        if removed:
            log.warning("removed %d generated file(s) but wrote none back", len(removed))
        else:
            print("no generated desktop file to restore")
        return 0
    desktop_entry.refresh_service_cache()
    return 0


def _entry_id_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("entry_id", help="entry ID (see `recent` / `pinned`)")


def _config_option(parser: argparse.ArgumentParser) -> None:
    """Add ``--config``; the same option, on the main parser and subcommands.

    Everything that starts this CLI without an inherited environment writes the
    option *before* the subcommand (the generated menu actions and the systemd
    unit), while a person typing it by hand tends to put it last, so both are
    accepted. ``SUPPRESS`` is what makes that work: without it the subcommand's
    default would overwrite the value given before it.
    """
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help=(
            "configuration file to use (default: ./config.toml, then "
            f"{config.installed_path()})"
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    # Shared with every subcommand so --config is accepted on either side of it;
    # add_help stays with the owning parser so there is one -h per command.
    common = argparse.ArgumentParser(add_help=False)
    _config_option(common)

    parser = argparse.ArgumentParser(prog=PROG, description=__doc__, parents=[common])
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    recent = sub.add_parser(
        "recent", parents=[common], help="refresh from VS Code and list recent entries"
    )
    recent.add_argument("--uri", action="store_true", help="also show each entry's URI")
    recent.add_argument(
        "--all", action="store_true", help="also list kinds the menu excludes (workspaces)"
    )
    recent.set_defaults(func=cmd_recent)
    update = sub.add_parser(
        "update", parents=[common], help="regenerate the icon context menu"
    )
    update.add_argument(
        "--watch",
        nargs="?",
        const=DEFAULT_WATCH_INTERVAL,
        default=None,
        type=_watch_interval,
        metavar="SECONDS",
        help=(
            "keep running, syncing every SECONDS; "
            f"a bare --watch uses {_seconds_text(DEFAULT_WATCH_INTERVAL)}"
        ),
    )
    update.set_defaults(func=cmd_update)

    pin = sub.add_parser("pin", parents=[common], help="pin an entry")
    _entry_id_arg(pin)
    pin.set_defaults(func=cmd_pin)

    unpin = sub.add_parser("unpin", parents=[common], help="unpin an entry")
    _entry_id_arg(unpin)
    unpin.set_defaults(func=cmd_unpin)

    sub.add_parser(
        "pinned", parents=[common], help="list pinned entries"
    ).set_defaults(func=cmd_pinned)

    sub.add_parser(
        "manage", parents=[common], help="pin and reorder pinned entries in a dialog"
    ).set_defaults(func=cmd_manage)

    # Internal: the generated menu actions invoke this to launch an entry.
    open_cmd = sub.add_parser(
        "open", parents=[common], help="open an entry by ID in VS Code"
    )
    _entry_id_arg(open_cmd)
    open_cmd.set_defaults(func=cmd_open)

    install = sub.add_parser(
        "install",
        parents=[common],
        help=(
            f"install the executable to {user_bin_dir()} and a {SERVICE_NAME} "
            f"watching the menu to {SYSTEMD_USER_DIR}"
        ),
    )
    install.set_defaults(func=cmd_install)

    sub.add_parser(
        "install-config",
        parents=[common],
        help=(
            "copy the configuration in use over the installed one, which is what "
            "the service reads"
        ),
    ).set_defaults(func=cmd_install_config)

    sub.add_parser(
        "uninstall", parents=[common], help="remove the systemd user units"
    ).set_defaults(func=cmd_uninstall)

    sub.add_parser(
        "reset",
        parents=[common],
        help="restore the generated desktop entry from the vendor file",
    ).set_defaults(func=cmd_reset)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    # Set before dispatch: every command but `uninstall` reads a setting, and
    # `install` needs to know which file to seed the user's own copy from.
    config.use(getattr(args, "config", None))
    try:
        return int(args.func(args) or 0)
    except config.ConfigError as error:
        # A configuration problem is the user's to fix, so it is reported as a
        # message naming the file rather than as a traceback.
        print(f"{PROG}: {error}", file=sys.stderr)
        return 2
    except Exception:  # noqa: BLE001 - top-level guard for a user daemon
        log.exception("unhandled error")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
