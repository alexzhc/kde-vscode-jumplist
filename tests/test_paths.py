"""Tests for the tool's own directories and for VS Code discovery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import make_state_db

from kde_vscode_jumplist import APP_NAME, cli, discovery, paths
from kde_vscode_jumplist.pinned import Pinned
from kde_vscode_jumplist.paths import data_dir, pinned_path, user_applications_dir


# --- data directory -------------------------------------------------------


def test_data_dir_follows_xdg_config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_home = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    assert data_dir() == config_home / APP_NAME


def test_data_dir_defaults_to_dot_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    assert data_dir() == home / ".config" / APP_NAME


def test_data_dir_holds_every_file() -> None:
    """Pinned, recents and the lock share one directory."""
    data = data_dir()

    assert pinned_path() == data / "pinned.json"
    assert paths.entries_path() == data / "entries.json"
    assert paths.lock_path() == data / "sync.lock"


def test_data_dir_does_not_depend_on_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Where a command is run from never decides where the tool writes."""
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    assert data_dir() == tmp_path / "home" / ".config" / APP_NAME
    assert data_dir().is_absolute()


def test_a_relative_xdg_config_home_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """XDG requires an absolute path, so a relative one falls back to ~/.config.

    Resolving it instead is what put a data directory inside a checkout: the
    value would be read relative to whatever directory the command ran from.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative-config")

    assert data_dir() == tmp_path / "home" / ".config" / APP_NAME
    assert not (tmp_path / "relative-config").exists()


def test_ensure_data_dir_creates_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The directory is made on demand, and making it twice is harmless."""
    config_home = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    assert not config_home.exists()

    assert paths.ensure_data_dir() == config_home / APP_NAME
    assert (config_home / APP_NAME).is_dir()
    assert paths.ensure_data_dir() == config_home / APP_NAME


# --- applications directory ----------------------------------------------


def test_applications_dir_follows_xdg_data_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_home = tmp_path / "xdg-data"
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))

    assert user_applications_dir() == data_home / "applications"


def test_applications_dir_defaults_to_local_share(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)

    assert user_applications_dir() == home / ".local" / "share" / "applications"


# --- VS Code discovery ----------------------------------------------------


@pytest.fixture()
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect HOME so discovery cannot find the developer's real VS Code.

    ``_shared_state_db_for`` looks under HOME, so without this the assertions
    below would depend on the machine running the suite.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


def _install_fake_code(home: Path) -> tuple[Path, Path]:
    """A detectable VS Code: profile database, desktop file and executable."""
    db = make_state_db(home / ".config" / "Code" / "User" / "globalStorage" / "state.vscdb", {})
    desktop = home / "share" / "applications" / "code.desktop"
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text("[Desktop Entry]\nName=x\nExec=x\n", encoding="utf-8")
    return db, desktop


def test_detection_finds_a_standard_installation(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, _desktop = _install_fake_code(fake_home)
    monkeypatch.setenv("XDG_DATA_DIRS", str(fake_home / "share"))
    monkeypatch.setattr(discovery.shutil, "which", lambda name: "/usr/bin/code")

    found = discovery.discover_installations()

    assert [(i.variant, i.state_db) for i in found] == [("code", db)]


def test_detection_reports_nothing_without_a_database(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An executable and a desktop file alone are not an installation."""
    desktop = fake_home / "share" / "applications" / "code.desktop"
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text("[Desktop Entry]\nName=x\nExec=x\n", encoding="utf-8")
    monkeypatch.setenv("XDG_DATA_DIRS", str(fake_home / "share"))
    monkeypatch.setattr(discovery.shutil, "which", lambda name: "/usr/bin/code")

    assert discovery.discover_installations() == []


def test_the_shared_database_is_found_when_present(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Newer VS Code versions keep the recent list outside the profile dir."""
    _install_fake_code(fake_home)
    shared = make_state_db(fake_home / ".vscode-shared" / "sharedStorage" / "state.vscdb", {})
    monkeypatch.setenv("XDG_DATA_DIRS", str(fake_home / "share"))
    monkeypatch.setattr(discovery.shutil, "which", lambda name: "/usr/bin/code")

    found = discovery.discover_installations()

    assert [i.shared_state_db for i in found] == [shared]


# --- end to end -----------------------------------------------------------


def test_pinned_and_entries_share_the_data_dir(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Pin writes to (and `pinned` reads from) the one data directory."""
    monkeypatch.setattr(cli, "run_locked_sync", lambda: False)

    uri = "file:///home/user/proj"
    data = data_dir()
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

    written = data / "pinned.json"
    assert written.is_file()
    stored = json.loads(written.read_text(encoding="utf-8"))["pinned"]
    assert [item["uri"] for item in stored] == [uri]
    # And the reader agrees.
    assert [entry.uri for entry in Pinned().all()] == [uri]
    assert cli.main(["pinned"]) == 0
    assert "proj" in capsys.readouterr().out
