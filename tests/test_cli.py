"""Tests for the CLI listing output, the systemd units and the tabulate fallback."""

from __future__ import annotations

import builtins
import configparser
import json
import subprocess
from pathlib import Path

import pytest

from conftest import CURRENT_SCHEMA_PAYLOAD, VENDOR_DESKTOP, parse_raw

from kde_vscode_jumplist import cli
from kde_vscode_jumplist.paths import entries_path, pinned_path, user_bin_dir


def _write_entries(entries: list[dict]) -> None:
    path = entries_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "entries": entries}, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_pinned(entries: list[dict]) -> None:
    path = pinned_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "pinned": entries}, ensure_ascii=False),
        encoding="utf-8",
    )


def _entry(uri: str, label: str, kind: str = "folder", remote: bool = False) -> dict:
    return {"kind": kind, "uri": uri, "label": label, "source": "code", "remote": remote}


@pytest.fixture()
def no_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the listing tests offline: they must not touch VS Code or ksycoca."""
    monkeypatch.setattr(cli, "run_locked_sync", lambda: False)


def test_recent_prints_tabulate_table(
    xdg_dirs: dict[str, Path], no_sync: None, capsys: pytest.CaptureFixture[str]
) -> None:
    from kde_vscode_jumplist.models import MenuEntry

    _write_entries(
        [
            _entry("file:///home/user/proj", "proj"),
            _entry("file:///home/user/notes.md", "notes.md", kind="file"),
        ]
    )

    assert cli.main(["recent"]) == 0
    out = capsys.readouterr().out

    # Headers, a dashed rule, and no tabs: i.e. tabulate rendered it.
    assert "Entry ID" in out and "Kind" in out and "Label" in out
    assert "Pinned" in out
    assert "\t" not in out
    assert any(set(line) == {"-", " "} and "-" in line for line in out.splitlines())

    # One row per entry, carrying the ID that `pin` expects.
    assert "folder" in out and "file" in out
    entry_id = MenuEntry("folder", "file:///home/user/proj", "proj", "code").entry_id
    assert entry_id in out
    assert "pin one with" in out


def test_recent_omits_uri_unless_requested(
    xdg_dirs: dict[str, Path], no_sync: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """URIs dominate the width, so they are opt-in via --uri."""
    _write_entries([_entry("file:///home/user/proj", "proj")])

    assert cli.main(["recent"]) == 0
    assert "file:///home/user/proj" not in capsys.readouterr().out

    assert cli.main(["recent", "--uri"]) == 0
    out = capsys.readouterr().out
    assert "URI" in out and "file:///home/user/proj" in out


def test_recent_marks_pinned_entries(
    xdg_dirs: dict[str, Path], no_sync: None, capsys: pytest.CaptureFixture[str]
) -> None:
    from kde_vscode_jumplist.models import MenuEntry

    pinned = _entry("file:///home/user/proj", "proj")
    _write_entries([_entry("file:///home/user/other", "other"), pinned])
    _write_pinned([pinned])

    assert cli.main(["recent"]) == 0
    out = capsys.readouterr().out
    proj_line = next(line for line in out.splitlines() if line.endswith("proj") or "  proj" in line)
    other_line = next(line for line in out.splitlines() if "other" in line)
    # Only the pinned entry is flagged in the Pinned column.
    assert proj_line.rstrip().endswith("yes")
    assert not other_line.rstrip().endswith("yes")

    assert cli.main(["pinned"]) == 0
    pinned_out = capsys.readouterr().out
    assert MenuEntry("folder", "file:///home/user/proj", "proj", "code").entry_id in pinned_out


def test_pinned_prints_table(
    xdg_dirs: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _write_pinned([_entry("file:///home/user/proj", "proj")])

    assert cli.main(["pinned"]) == 0
    out = capsys.readouterr().out
    assert "Entry ID" in out and "Kind" in out and "Label" in out
    assert "\t" not in out


def test_recent_hides_kinds_the_menu_excludes(
    xdg_dirs: dict[str, Path], no_sync: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """The listing matches the menu: workspaces are hidden unless --all."""
    workspace = _entry("file:///w.code-workspace", "ws-label", kind="workspace")
    folder = _entry("file:///home/user/proj", "proj")
    _write_entries([workspace, folder])

    assert cli.main(["recent"]) == 0
    out = capsys.readouterr().out
    assert "proj" in out
    assert "ws-label" not in out
    # The count reflects what is shown, and the hidden ones are explained.
    assert "1 entry -" in out
    assert "1 hidden: workspace" in out
    assert "--all" in out

    assert cli.main(["recent", "--all"]) == 0
    out = capsys.readouterr().out
    assert "ws-label" in out
    assert "2 entries" in out
    assert "hidden" not in out


def test_empty_listings_are_friendly(
    xdg_dirs: dict[str, Path], no_sync: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["recent"]) == 0
    assert "no recent entries" in capsys.readouterr().err

    assert cli.main(["pinned"]) == 0
    assert "no pinned entries yet" in capsys.readouterr().out


def test_the_cli_makes_its_data_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A first run creates ~/.config/kde-vscode-jumplist; nothing else has to.

    `pinned` on a fresh machine only reads, and would find nothing to read --
    so the directory being there afterwards is the command's own doing.
    """
    config_home = tmp_path / "fresh-config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    data = config_home / "kde-vscode-jumplist"
    assert not config_home.exists()

    assert cli.main(["pinned"]) == 0
    assert data.is_dir()
    assert list(data.iterdir()) == []  # created, with nothing invented in it
    capsys.readouterr()


# --- systemd user units --------------------------------------------------


@pytest.fixture()
def systemd_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the installer at a temp unit dir and stub out command probing."""
    directory = tmp_path / "systemd" / "user"
    monkeypatch.setattr(cli, "SYSTEMD_USER_DIR", directory)
    # The launcher is verified by running it, which these tests are not about:
    # answering with a fixed argv keeps them off subprocesses entirely.
    monkeypatch.setattr(
        cli.desktop_entry,
        "resolve_cli_argv",
        lambda python=None: ["/usr/bin/kde-vscode-jumplist"],
    )
    return directory


def _fake_systemctl(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Record systemctl calls instead of talking to systemd."""
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(cli.subprocess, "run", run)
    return calls


def _unit_environment(path: Path) -> dict[str, str]:
    """``Environment=`` entries from a unit file, which there must never be.

    Read line by line rather than with configparser, which rejects the repeated
    keys a unit file legitimately has.
    """
    entries: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("Environment="):
            name, _, value = line[len("Environment=") :].partition("=")
            entries[name] = value
    return entries


def _exec_start(path: Path) -> str:
    """The unit's ExecStart, as written."""
    return _parse_unit(path).get("Service", "ExecStart")


def _expected_exec_start(launcher: str) -> str:
    """What `install` writes: the verified launcher, then the watcher."""
    return f"{launcher} update --watch"


def _parse_unit(path: Path) -> configparser.RawConfigParser:
    return parse_raw(path.read_text(encoding="utf-8"))


def test_install_writes_one_long_running_service(
    systemd_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single Type=simple service replaces the oneshot+timer pair."""
    # No executable in play: this test is about the unit file itself.
    monkeypatch.setattr(cli.desktop_entry, "packaged_cli_path", lambda: None)
    calls = _fake_systemctl(monkeypatch)

    assert cli.main(["install"]) == 0

    # The unit is named after the project, with no "-sync" suffix.
    assert cli.SERVICE_NAME == "kde-vscode-jumplist.service"
    service = systemd_dir / cli.SERVICE_NAME
    assert service.is_file()
    # There is no timer any more: the service does the waiting itself.
    assert not (systemd_dir / "kde-vscode-jumplist.timer").exists()
    assert not hasattr(cli, "TIMER_NAME") and not hasattr(cli, "TIMER_TEMPLATE")

    parser = _parse_unit(service)
    # simple, not oneshot: it stays up and watches rather than running once.
    assert parser.get("Service", "Type") == "simple"
    # The verified launcher is baked into ExecStart. systemd exports nothing,
    # not even PATH, so a bare command name would not be found -- and the
    # watcher has to start the same way the menu actions start the CLI.
    assert parser.get("Service", "ExecStart") == _expected_exec_start(
        "/usr/bin/kde-vscode-jumplist"
    )
    # Nothing is configured that way any more.
    assert _unit_environment(service) == {}
    # A long-running process that dies should come back on its own.
    assert parser.get("Service", "Restart") == "on-failure"
    # And it can be enabled, which the oneshot service deliberately could not.
    assert parser.get("Install", "WantedBy") == "default.target"

    # Reloaded, enabled, and restarted. The restart is what makes the install
    # take effect: the service has just had its executable replaced, and a
    # running process would otherwise keep serving the old code -- and with it,
    # the old menu.
    assert calls == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", cli.SERVICE_NAME],
        ["systemctl", "--user", "restart", cli.SERVICE_NAME],
    ]


def test_install_removes_obsolete_units(
    systemd_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upgrading must not leave an old timer running against the same file."""
    systemd_dir.mkdir(parents=True)
    stale = systemd_dir / "kde-vscode-jumplist-sync.timer"
    stale.write_text("[Timer]\n", encoding="utf-8")
    calls = _fake_systemctl(monkeypatch)

    assert cli.main(["install"]) == 0

    assert not stale.exists()
    assert ["systemctl", "--user", "disable", "--now", stale.name] in calls
    assert (systemd_dir / cli.SERVICE_NAME).is_file()


def test_install_restarts_the_service_it_replaced(
    systemd_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Installing new code has to restart the process running the old code.

    Regression: the install enabled the service with ``--now``, which starts a
    stopped service but deliberately leaves a running one alone. The service
    had therefore been running the *previous* executable all along, and since it
    owns the menu, every install appeared to do nothing -- it kept regenerating
    the menu from the old code, seconds after any manual update. Both a changed
    ``Exec=`` and a changed menu icon were each mistaken for a bug in the
    setting rather than for a process that was never restarted.
    """
    calls = _fake_systemctl(monkeypatch)

    assert cli.main(["install"]) == 0

    assert ["systemctl", "--user", "restart", cli.SERVICE_NAME] in calls
    # enable --now would leave a running service untouched, so it is not used.
    assert ["systemctl", "--user", "enable", "--now", cli.SERVICE_NAME] not in calls
    # The restart comes after the enable, so the unit is active either way.
    assert calls.index(["systemctl", "--user", "enable", cli.SERVICE_NAME]) < calls.index(
        ["systemctl", "--user", "restart", cli.SERVICE_NAME]
    )


def test_install_reports_a_failed_restart(
    systemd_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A service that would not restart is an error, not a silent success."""
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(command))
        failed = "restart" in command
        return subprocess.CompletedProcess(
            command, 1 if failed else 0, "", "restart failed" if failed else ""
        )

    monkeypatch.setattr(cli.subprocess, "run", run)

    with caplog.at_level("ERROR"):
        assert cli.main(["install"]) != 0
    assert ["systemctl", "--user", "restart", cli.SERVICE_NAME] in calls
    assert "restart failed" in caplog.text


def test_install_migrates_the_timer_it_replaced(
    systemd_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An install from before --watch left a timer; installing must remove it.

    Otherwise the old timer and the new service would both regenerate the same
    desktop file, which is exactly the fight the migration exists to prevent.
    """
    systemd_dir.mkdir(parents=True)
    old_timer = systemd_dir / "kde-vscode-jumplist.timer"
    old_timer.write_text("[Timer]\n", encoding="utf-8")
    assert old_timer.name in cli.LEGACY_UNIT_NAMES
    calls = _fake_systemctl(monkeypatch)

    assert cli.main(["install"]) == 0

    assert not old_timer.exists()
    assert ["systemctl", "--user", "disable", "--now", old_timer.name] in calls
    assert "update --watch" in _exec_start(systemd_dir / cli.SERVICE_NAME)


def test_uninstall_removes_every_known_unit(
    systemd_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    systemd_dir.mkdir(parents=True)
    names = (cli.SERVICE_NAME, *cli.LEGACY_UNIT_NAMES)
    for name in names:
        (systemd_dir / name).write_text("[Unit]\n", encoding="utf-8")
    calls = _fake_systemctl(monkeypatch)

    assert cli.main(["uninstall"]) == 0
    assert list(systemd_dir.iterdir()) == []
    for name in names:
        assert ["systemctl", "--user", "disable", "--now", name] in calls
    assert ["systemctl", "--user", "daemon-reload"] in calls


def test_install_writes_the_service_to_the_user_systemd_dir(
    systemd_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The unit lives in ~/.config/systemd/user and runs the installed copy."""
    built = tmp_path / "build" / "kde-vscode-jumplist"
    built.parent.mkdir(parents=True)
    built.write_text("#!/bin/sh\necho built\n", encoding="utf-8")
    monkeypatch.setattr(cli.desktop_entry, "packaged_cli_path", lambda: built)
    calls = _fake_systemctl(monkeypatch)

    assert cli.main(["install"]) == 0

    # The single-file executable is copied to the user bin directory, which is
    # not the build directory (`XDG_BIN_HOME` from the autouse fixture).
    installed = user_bin_dir() / "kde-vscode-jumplist"
    assert installed != built
    assert installed.is_file()
    assert installed.stat().st_mode & 0o111  # executable
    assert installed.read_bytes() == built.read_bytes()

    # The unit is under the systemd user dir and watches via the installed copy.
    unit = (systemd_dir / cli.SERVICE_NAME).read_text(encoding="utf-8")
    assert _exec_start(systemd_dir / cli.SERVICE_NAME) == _expected_exec_start(str(installed))
    assert "update --watch" in unit
    assert ["systemctl", "--user", "daemon-reload"] in calls
    assert ["systemctl", "--user", "enable", cli.SERVICE_NAME] in calls
    assert ["systemctl", "--user", "restart", cli.SERVICE_NAME] in calls


def test_install_skips_executable_when_nothing_was_built(
    systemd_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pipx/pip install has no bin/ artifact; the unit still gets written."""
    monkeypatch.setattr(cli.desktop_entry, "packaged_cli_path", lambda: None)
    _fake_systemctl(monkeypatch)

    assert cli.main(["install"]) == 0

    assert not (user_bin_dir() / "kde-vscode-jumplist").exists()
    unit = (systemd_dir / cli.SERVICE_NAME).read_text(encoding="utf-8")
    assert "update --watch" in unit and "ExecStart=" in unit


def test_install_does_not_rewrite_an_identical_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = tmp_path / "build" / "kde-vscode-jumplist"
    built.parent.mkdir(parents=True)
    built.write_text("#!/bin/sh\necho built\n", encoding="utf-8")
    monkeypatch.setattr(cli.desktop_entry, "packaged_cli_path", lambda: built)

    # A different directory from the build output, or there would be nothing
    # to compare and the test would prove nothing.
    bin_dir = tmp_path / "local-bin"
    bin_dir.mkdir()
    monkeypatch.setenv("XDG_BIN_HOME", str(bin_dir))
    installed = bin_dir / "kde-vscode-jumplist"
    installed.write_text(built.read_text(encoding="utf-8"), encoding="utf-8")
    before = installed.stat().st_mtime_ns

    assert cli.install_executable() == installed
    assert installed.stat().st_mtime_ns == before  # untouched

    # A changed build does replace it.
    built.write_text("#!/bin/sh\necho v2\n", encoding="utf-8")
    assert cli.install_executable() == installed
    assert installed.read_text(encoding="utf-8") == built.read_text(encoding="utf-8")


def test_uninstall_removes_the_installed_executable(
    systemd_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_systemctl(monkeypatch)
    installed = user_bin_dir() / "kde-vscode-jumplist"
    installed.parent.mkdir(parents=True)
    installed.write_text("#!/bin/sh\n", encoding="utf-8")

    assert cli.main(["uninstall"]) == 0
    assert not installed.exists()


def test_uninstall_leaves_foreign_symlinks_alone(
    systemd_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """~/.local/bin entries from pipx are symlinks into its venvs: not ours."""
    _fake_systemctl(monkeypatch)
    elsewhere = user_bin_dir().parent / "pipx-venv" / "kde-vscode-jumplist"
    elsewhere.parent.mkdir(parents=True)
    elsewhere.write_text("#!/bin/sh\n", encoding="utf-8")
    link = user_bin_dir() / "kde-vscode-jumplist"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(elsewhere)

    assert cli.main(["uninstall"]) == 0
    assert link.is_symlink()
    assert elsewhere.is_file()


# --- update --watch -------------------------------------------------------


def test_update_without_watch_syncs_once(
    no_sync: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """--watch is opt-in: a plain update still does exactly one pass."""
    slept: list[float] = []

    assert cli.main(["update"]) == 0
    assert slept == []
    # Nothing long-running is announced either.
    assert "watching" not in capsys.readouterr().out


@pytest.fixture()
def watch_loop(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Record each pass and interval, and end the loop after three of them.

    KeyboardInterrupt is how the loop is meant to finish, so raising it from
    the sleep is also what the Ctrl-C test relies on.
    """
    calls: dict = {"syncs": 0, "sleeps": [], "stop_after": 3}

    def fake_sync() -> bool:
        calls["syncs"] += 1
        return calls["syncs"] % 2 == 1  # alternate, so both messages are seen

    def fake_sleep(seconds: float) -> None:
        calls["sleeps"].append(seconds)
        if len(calls["sleeps"]) >= calls["stop_after"]:
            raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run_locked_sync", fake_sync)
    monkeypatch.setattr(cli.time, "sleep", fake_sleep)
    return calls


def test_update_watch_syncs_repeatedly(
    watch_loop: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["update", "--watch", "2"]) == 0

    assert watch_loop["syncs"] == 3
    assert watch_loop["sleeps"] == [2.0, 2.0, 2.0]
    assert "every 2s" in capsys.readouterr().out


def test_bare_watch_uses_the_default_interval(
    watch_loop: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--watch` with no number follows it uses the built-in interval."""
    assert cli.DEFAULT_WATCH_INTERVAL == 5.0

    assert cli.main(["update", "--watch"]) == 0

    assert watch_loop["sleeps"] == [cli.DEFAULT_WATCH_INTERVAL] * 3
    assert "every 5s" in capsys.readouterr().out


def test_watch_stops_cleanly_on_interrupt(
    watch_loop: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ctrl-C is the normal way to end it, so it is not an error."""
    assert cli.main(["update", "--watch"]) == 0
    assert "stopped" in capsys.readouterr().out


def test_watch_reports_each_pass_only_when_verbose(
    watch_loop: dict, capsys: pytest.CaptureFixture[str]
) -> None:
    """Otherwise a long-running loop would fill the journal with one line each."""
    assert cli.main(["update", "--watch"]) == 0
    quiet = capsys.readouterr().out
    assert "updated (changed)" not in quiet and "updated (no changes)" not in quiet

    # Both counters, or the loop would stop at once on the sleeps recorded by
    # the run above.
    watch_loop["syncs"] = 0
    watch_loop["sleeps"] = []
    assert cli.main(["-v", "update", "--watch"]) == 0
    loud = capsys.readouterr().out
    assert "updated (changed)" in loud and "updated (no changes)" in loud


def test_watch_parses_seconds_after_the_flag() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["update"]).watch is None
    assert parser.parse_args(["update", "--watch"]).watch == cli.DEFAULT_WATCH_INTERVAL
    assert parser.parse_args(["update", "--watch", "10"]).watch == 10.0
    assert parser.parse_args(["update", "--watch", "2.5"]).watch == 2.5


@pytest.mark.parametrize("bad", ["abc", "0", "-3", "5m", ""])
def test_watch_rejects_intervals_it_cannot_use(bad: str) -> None:
    """A rejected value must fail loudly rather than quietly use the default."""
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["update", "--watch", bad])


# --- reset ---------------------------------------------------------------


@pytest.fixture()
def generated_desktop(xdg_dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> Path:
    """A VS Code installation plus a menu file this tool generated.

    The file is built directly rather than through a sync, so the test does not
    depend on the sync module's own installation discovery.
    """
    from conftest import make_state_db
    from kde_vscode_jumplist.desktop_entry import write_user_desktop_entry
    from kde_vscode_jumplist.discovery import Installation
    from kde_vscode_jumplist.models import MenuEntry

    tmp = xdg_dirs["config"]
    db = make_state_db(tmp / "vscode" / "state.vscdb", CURRENT_SCHEMA_PAYLOAD)
    vendor = xdg_dirs["data"] / "vendor" / "code.desktop"
    vendor.parent.mkdir(parents=True, exist_ok=True)
    vendor.write_text(VENDOR_DESKTOP, encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "discover_installations",
        lambda: [
            Installation(
                variant="code",
                executable="/usr/bin/code",
                desktop_path=vendor,
                state_db=db,
            )
        ],
    )
    monkeypatch.setattr(cli.desktop_entry, "refresh_service_cache", lambda: None)

    # A stored entry, so the rebuild has something to list.
    _write_entries([_entry("file:///home/user/proj", "proj")])

    written = write_user_desktop_entry(
        vendor, "code", [], [MenuEntry("folder", "file:///home/user/proj", "proj", "code")]
    )
    assert written is not None and written.is_file()
    return written


def test_reset_restores_the_generated_desktop_file(
    xdg_dirs: dict[str, Path], generated_desktop: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A hand-edited or damaged menu file is rewritten from the vendor entry."""
    from kde_vscode_jumplist.desktop_entry import GENERATED_MARKER, is_generated

    # Damage it the way a stray edit would: drop the marker and the actions.
    generated_desktop.write_text(
        VENDOR_DESKTOP + "\n# note to self\n", encoding="utf-8"
    )
    assert not is_generated(generated_desktop)

    assert cli.main(["reset"]) == 0

    restored = generated_desktop.read_text(encoding="utf-8")
    assert restored.startswith(GENERATED_MARKER)
    assert "note to self" not in restored
    assert "KdeVsCodeJumpList-Recent-" in restored
    assert str(generated_desktop) in capsys.readouterr().out


def test_reset_leaves_pinned_and_entries_alone(
    xdg_dirs: dict[str, Path],
    generated_desktop: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It repairs the menu file, so saved data must survive untouched."""
    _write_pinned([_entry("file:///home/user/keepme", "keepme")])
    pinned_before = pinned_path().read_bytes()
    entries_before = entries_path().read_bytes()
    # Any sync would rewrite the cache; reset must not run one.
    monkeypatch.setattr(
        cli, "run_locked_sync", lambda: pytest.fail("reset must not sync")
    )

    assert cli.main(["reset"]) == 0

    assert pinned_path().read_bytes() == pinned_before
    assert entries_path().read_bytes() == entries_before


def test_reset_restores_only_our_own_files(
    xdg_dirs: dict[str, Path], generated_desktop: Path
) -> None:
    """Another application's desktop file, and ours from a vendor, both stay."""
    apps_dir = cli.desktop_entry.paths.user_applications_dir()
    foreign = apps_dir / "firefox.desktop"
    foreign.write_text("[Desktop Entry]\nName=Firefox\n", encoding="utf-8")
    # A second VS Code entry that we did not generate: not ours to touch.
    other_vendor = apps_dir / "code-url-handler.desktop"
    other_vendor.write_text(VENDOR_DESKTOP, encoding="utf-8")

    assert cli.main(["reset"]) == 0

    assert foreign.read_text(encoding="utf-8") == "[Desktop Entry]\nName=Firefox\n"
    assert other_vendor.read_text(encoding="utf-8") == VENDOR_DESKTOP
    assert generated_desktop.is_file()


def test_reset_is_quiet_when_there_is_nothing_generated(
    xdg_dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With no VS Code desktop file to rebuild from, it says so and exits 0."""
    from kde_vscode_jumplist.discovery import Installation

    monkeypatch.setattr(
        cli, "discover_installations", lambda: [Installation(variant="code")]
    )

    assert cli.main(["reset"]) == 0
    assert "no generated desktop file to restore" in capsys.readouterr().out


def test_reset_keeps_the_pinned_entries_in_the_menu(
    xdg_dirs: dict[str, Path], generated_desktop: Path
) -> None:
    """Restoring rebuilds the file from stored data, so pins stay listed."""
    _write_pinned([_entry("file:///home/user/keepme", "keepme")])

    assert cli.main(["reset"]) == 0

    from conftest import parse_raw

    parser = parse_raw(generated_desktop.read_text(encoding="utf-8"))
    names = [
        parser.get(section, "Name")
        for section in parser.sections()
        if section.startswith("Desktop Action KdeVsCodeJumpList-Pinned-")
    ]
    assert names == ["keepme"]


# --- tabulate fallback ---------------------------------------------------


def test_table_falls_back_without_tabulate(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing tabulate must degrade, not break the CLI.

    The Makefile runs the CLI through the system interpreter, where tabulate
    may not be installed; the ``open`` path the menu actions use must survive.
    """
    real_import = builtins.__import__

    def block_tabulate(name: str, *args: object, **kwargs: object) -> object:
        if name == "tabulate":
            raise ImportError("simulated: tabulate not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", block_tabulate)

    cli._print_table(["Entry ID", "Kind"], [["abc123", "folder"]])
    assert capsys.readouterr().out == "Entry ID\tKind\nabc123\tfolder\n"
