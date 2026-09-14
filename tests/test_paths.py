"""Tests for the path settings (data dir, apps dir and the VS Code data dir)."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import make_state_db

from kde_vscode_jumplist import cli, config, discovery
from kde_vscode_jumplist import paths
from kde_vscode_jumplist.pinned import Pinned
from kde_vscode_jumplist.paths import (
    data_dir,
    pinned_path,
    user_applications_dir,
)

WriteConfig = Callable[..., Path]


def _fake_installation(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> Path:
    """Give discovery a desktop file and executable to match the data dir."""
    data_dir.mkdir(parents=True, exist_ok=True)
    desktop = data_dir / "code.desktop"
    desktop.write_text("[Desktop Entry]\nName=x\nExec=x\n", encoding="utf-8")
    monkeypatch.setattr(discovery, "_find_desktop_file", lambda candidates: desktop)
    monkeypatch.setattr(discovery.shutil, "which", lambda name: "/usr/bin/code")
    return make_state_db(data_dir / "User" / "globalStorage" / "state.vscdb", {})


# --- data directory -------------------------------------------------------


def test_data_dir_holds_every_file(
    write_config: WriteConfig, tmp_path: Path
) -> None:
    """Pinned, recents and the lock share one directory."""
    data = tmp_path / "elsewhere" / "data"
    write_config(data_dir=str(data))

    assert data_dir() == data
    assert pinned_path() == data / "pinned.json"
    assert paths.entries_path() == data / "entries.json"
    assert paths.lock_path() == data / "sync.lock"


def test_data_dir_setting_expands_user(
    write_config: WriteConfig, _fake_home: Path
) -> None:
    write_config(data_dir="~/dotfiles/jumplist")

    assert data_dir() == _fake_home / "dotfiles" / "jumplist"


def test_relative_settings_resolve_against_the_config_file(
    write_config: WriteConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not against the working directory: that would make the file ambivalent.

    A checkout can say ``data_dir = ".tmp"`` and stay self-contained, and the
    same text copied to ~/.config/kde-vscode-jumplist by `install` keeps
    pointing inside that directory rather than at wherever a command ran.
    """
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    path = write_config(data_dir="relative/data")

    assert data_dir() == path.parent / "relative" / "data"
    assert data_dir().is_absolute()


def test_blank_data_dir_is_refused(write_config: WriteConfig) -> None:
    """A blank value is a mistake here, not "work it out yourself".

    The two directory settings decide where the tool writes, so they have to
    say so; only the settings that describe a VS Code installation accept an
    empty value, where it means "detect it".
    """
    write_config(data_dir="")

    with pytest.raises(config.ConfigError, match="data_dir must be a path"):
        data_dir()


# --- applications directory ---------------------------------------------


def test_apps_dir_comes_from_the_configuration(
    write_config: WriteConfig, tmp_path: Path
) -> None:
    """There is no XDG fallback: the file states where the .desktop goes.

    A test of the error a blank one raises lives in test_config.py; this is the
    other half -- the configured value is the one that is used.
    """
    apps = tmp_path / "custom-applications"
    write_config(apps_dir=str(apps))

    assert user_applications_dir() == apps


# --- VS Code data directory -----------------------------------------------


@pytest.fixture(autouse=True)
def _fake_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect HOME so discovery cannot find the developer's real VS Code.

    ``_shared_state_db_for`` looks under HOME, so without this the assertions
    below would depend on the machine running the suite.
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    return home


def _fake_shared_db(home: Path) -> Path:
    return make_state_db(home / ".vscode-shared" / "sharedStorage" / "state.vscdb", {})


def test_vscode_dir_setting_replaces_auto_detection(
    write_config: WriteConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data_dir = tmp_path / "custom-code"
    db = _fake_installation(monkeypatch, data_dir)
    write_config(vscode_dir=str(data_dir))

    found = discovery.discover_installations()

    assert len(found) == 1  # exactly the pointed-at directory, nothing merged
    assert found[0].state_db == db
    assert found[0].variant == "code"
    assert found[0].desktop_path == data_dir / "code.desktop"


def test_vscode_dir_setting_keeps_reading_the_shared_db(
    write_config: WriteConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _fake_home: Path,
) -> None:
    """The recent list often lives in the shared DB, outside the profile dir.

    Ignoring it because a data directory was named would empty the menu on an
    install whose profile DB holds no history -- which is the common case.
    """
    data_dir = tmp_path / "custom-code"
    _fake_installation(monkeypatch, data_dir)
    shared = _fake_shared_db(_fake_home)
    write_config(vscode_dir=str(data_dir))

    found = discovery.discover_installations()

    assert len(found) == 1
    assert found[0].shared_state_db == shared


def test_vscode_dir_without_profile_db_uses_shared_only(
    write_config: WriteConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _fake_home: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A profile-less setting still yields the entries it can find."""
    desktop = tmp_path / "code.desktop"
    desktop.write_text("[Desktop Entry]\nName=x\nExec=x\n", encoding="utf-8")
    monkeypatch.setattr(discovery, "_find_desktop_file", lambda candidates: desktop)
    shared = _fake_shared_db(_fake_home)
    write_config(vscode_dir=str(tmp_path / "typo"))

    with caplog.at_level(logging.WARNING):
        found = discovery.discover_installations()

    assert found[0].state_db is None
    assert found[0].shared_state_db == shared
    assert "shared one only" in caplog.text


def test_unusable_setting_falls_back_to_detection(
    write_config: WriteConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    _fake_home: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A configured path must not be able to empty the menu.

    Regression: the fallback used to be advertised and then re-read the same
    setting during detection, so it could never find anything -- the menu went
    empty on any machine whose VS Code data dir is not the default while the
    log claimed it had recovered.
    """
    # A perfectly good installation, discoverable by the normal rules. The
    # desktop file goes in XDG_DATA_DIRS (set below), since the fixture already
    # redirects XDG_DATA_HOME elsewhere.
    data_dir = _fake_home / ".config" / "Code"
    db = make_state_db(data_dir / "User" / "globalStorage" / "state.vscdb", {})
    desktop = _fake_home / "share" / "applications" / "code.desktop"
    desktop.parent.mkdir(parents=True)
    desktop.write_text("[Desktop Entry]\nName=x\nExec=x\n", encoding="utf-8")
    monkeypatch.setenv("XDG_DATA_DIRS", str(_fake_home / "share"))
    monkeypatch.setattr(discovery.shutil, "which", lambda name: "/usr/bin/code")
    write_config(vscode_dir=str(tmp_path / "typo"))

    with caplog.at_level(logging.WARNING):
        found = discovery.discover_installations()

    assert [i.state_db for i in found] == [db]
    assert "does not exist" in caplog.text
    assert "detecting instead" in caplog.text


def test_detection_ignores_the_vscode_dir_setting(
    write_config: WriteConfig, _fake_home: Path
) -> None:
    """_state_db_for is the detection path, so it must not consult the setting.

    If it did, detection would look only where the setting points -- which is
    exactly why the "fall back to detection" retry used to find nothing.
    """
    import kde_vscode_jumplist.discovery as disc

    standard = make_state_db(
        _fake_home / ".config" / "Code" / "User" / "globalStorage" / "state.vscdb", {}
    )
    write_config(vscode_dir="/nonexistent/from/the/setting")

    assert disc._state_db_for("code") == standard


def test_exact_state_db_still_wins_over_the_directory(
    write_config: WriteConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The file settings are the more specific request, so they take priority."""
    data_dir = tmp_path / "custom-code"
    _fake_installation(monkeypatch, data_dir)
    elsewhere = make_state_db(tmp_path / "picked" / "state.vscdb", {})
    write_config(vscode_dir=str(data_dir), state_db=str(elsewhere))

    found = discovery.discover_installations()

    assert len(found) == 1
    assert found[0].state_db == elsewhere


def test_state_db_setting_works_without_a_desktop_setting(
    write_config: WriteConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Previously a desktop override was also required; detection fills the gap."""
    db = make_state_db(tmp_path / "db" / "state.vscdb", {})
    desktop = tmp_path / "code.desktop"
    desktop.write_text("[Desktop Entry]\nName=x\nExec=x\n", encoding="utf-8")
    monkeypatch.setattr(discovery, "_find_desktop_file", lambda candidates: desktop)
    write_config(state_db=str(db))

    found = discovery.discover_installations()

    assert [i.state_db for i in found] == [db]


def test_shared_db_setting_is_used(
    write_config: WriteConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data_dir = tmp_path / "custom-code"
    _fake_installation(monkeypatch, data_dir)
    shared = make_state_db(tmp_path / "shared" / "state.vscdb", {})
    write_config(vscode_dir=str(data_dir), shared_db=str(shared))

    assert discovery.discover_installations()[0].shared_state_db == shared


# --- end to end -----------------------------------------------------------


def test_pinned_and_entries_share_the_data_dir(
    write_config: WriteConfig,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Pin writes to (and `pinned` reads from) the configured directory."""
    data = tmp_path / "custom-data"
    write_config(data_dir=str(data))
    monkeypatch.setattr(cli, "run_locked_sync", lambda: False)

    uri = "file:///home/user/proj"
    data.mkdir(parents=True)
    (data / "entries.json").write_text(
        json.dumps(
            {
                "version": 1,
                "entries": [{"kind": "folder", "uri": uri, "label": "proj", "source": "code"}],
            }
        ),
        encoding="utf-8",
    )

    assert cli.main(["pin", cli.EntryStore().all()[0].entry_id]) == 0

    # Same directory as the entry store, not a separate config/state split.
    written = data / "pinned.json"
    assert written.is_file()
    stored = json.loads(written.read_text(encoding="utf-8"))["pinned"]
    assert [item["uri"] for item in stored] == [uri]
    # And the reader agrees.
    assert [entry.uri for entry in Pinned().all()] == [uri]
    assert cli.main(["pinned"]) == 0
    assert "proj" in capsys.readouterr().out


def test_sqlite_db_setting_is_readable(
    write_config: WriteConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Sanity check that the configured path really is a usable database."""
    db = _fake_installation(monkeypatch, tmp_path / "custom-code")
    connection = sqlite3.connect(db)
    try:
        assert connection.execute("SELECT COUNT(*) FROM ItemTable").fetchone()
    finally:
        connection.close()
