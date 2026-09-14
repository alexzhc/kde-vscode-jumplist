"""Tests for reading config.toml: what is found, what it means, what is refused.

The configuration file is the tool's only input -- there are no environment
variables any more -- so these tests are also what keep its restatements honest:
the shipped ``config.toml``, the packaged ``defaults.toml``, and the reference
in DETAILS.md all have to describe the settings the parser actually reads.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import settings_text

from kde_vscode_jumplist import cli, config
from kde_vscode_jumplist.xdg import xdg_config_home, xdg_data_home

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_CONFIG = REPO_ROOT / "config.toml"
# The docs are two files: a short README with the screenshots, and the reference
# it links to. What used to be guarded in README.md is guarded here.
README = REPO_ROOT / "README.md"
DETAILS = REPO_ROOT / "DETAILS.md"

# write_config, as the fixture in conftest hands it over.
WriteConfig = Callable[..., Path]


# --- finding the file -----------------------------------------------------


def test_installed_path_is_under_the_xdg_config_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    assert config.installed_path() == tmp_path / "xdg" / "kde-vscode-jumplist" / "config.toml"


def test_checkout_file_wins_over_the_installed_one() -> None:
    """`./config.toml` is the checkout's own file, so it is looked at first."""
    checkout = Path.cwd() / config.CONFIG_FILE_NAME
    checkout.write_text(settings_text(max_recents=5), encoding="utf-8")
    config.forget()

    assert config.path() == checkout
    assert config.current().max_recents == 5


def test_installed_file_is_used_without_a_checkout_file() -> None:
    assert not (Path.cwd() / config.CONFIG_FILE_NAME).exists()
    config.forget()

    assert config.path() == config.installed_path()
    assert config.current().path == config.installed_path()


def test_config_option_wins_over_both(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere.toml"
    elsewhere.write_text(settings_text(max_recents=3), encoding="utf-8")

    config.use(elsewhere)
    assert config.path() == elsewhere
    assert config.current().max_recents == 3

    config.use(None)  # goes back to searching
    assert config.path() == config.installed_path()


def test_config_option_expands_user_and_relative_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The value is typed by hand, and is also written into the unit file."""
    monkeypatch.setenv("HOME", str(tmp_path))
    config.use("~/elsewhere.toml")
    assert config.path() == tmp_path / "elsewhere.toml"

    monkeypatch.chdir(tmp_path)
    config.use("relative.toml")
    assert config.path() == tmp_path / "relative.toml"


def test_missing_file_is_an_error_naming_both_locations() -> None:
    config.installed_path().unlink()
    config.forget()

    assert config.path() is None
    with pytest.raises(config.ConfigError) as caught:
        config.current()

    message = str(caught.value)
    for candidate in config.search_paths():
        assert str(candidate) in message
    assert "--config" in message


def test_missing_file_given_explicitly_is_an_error(tmp_path: Path) -> None:
    missing = tmp_path / "nowhere.toml"
    config.use(missing)

    with pytest.raises(config.ConfigError, match=str(missing)):
        config.current()


def test_the_cli_reports_a_missing_configuration_instead_of_crashing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No file is a message and a non-zero exit, not a traceback."""
    config.installed_path().unlink()
    config.forget()

    assert cli.main(["pinned"]) == 2

    captured = capsys.readouterr()
    assert f"no {config.CONFIG_FILE_NAME} found" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("position", ["before", "after"])
def test_the_config_option_is_accepted_on_either_side(
    tmp_path: Path, position: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """A person types it last; the generated unit writes it first.

    The menu actions and ExecStart put ``--config`` before the subcommand, since
    that is where argparse wants an option that also exists on every
    subcommand. Both orders have to reach the same place.
    """
    missing = tmp_path / "nowhere.toml"
    argv = (
        ["--config", str(missing), "pinned"]
        if position == "before"
        else ["pinned", "--config", str(missing)]
    )

    assert cli.main(argv) == 2
    assert str(missing) in capsys.readouterr().err


def test_the_config_option_is_optional() -> None:
    """Without it the search runs, so `no file` is the error rather than a
    missing argument."""
    assert not hasattr(cli.build_parser().parse_args(["pinned"]), "config")
    assert cli.build_parser().parse_args(["pinned", "--config", "/x"]).config == "/x"


# --- reading the settings -------------------------------------------------


def test_the_standard_configuration_is_read() -> None:
    """The file the tests are given yields exactly the values it names."""
    settings = config.current()

    assert settings.path == config.installed_path()
    assert settings.data_dir == xdg_config_home() / config.APP_NAME
    assert settings.apps_dir == xdg_data_home() / "applications"
    assert settings.vscode_dir is None
    assert settings.state_db is None
    assert settings.shared_db is None
    assert settings.desktop is None
    assert settings.exec_path is None
    assert settings.max_recents == 10
    assert settings.exclude_kinds == frozenset({config.ENTRY_WORKSPACE})
    assert settings.pinned_position == "above"


def test_a_missing_setting_is_an_error(write_config: WriteConfig) -> None:
    """Every key is required: a file that omits one is refused, naming it.

    This is the point of the file being the source of truth -- the values it
    omits are the ones that decide where the tool writes, so completing them
    silently is how a run ends up somewhere the user never chose.
    """
    path = write_config()
    text = path.read_text(encoding="utf-8")
    path.write_text(
        "".join(
            line
            for line in text.splitlines(keepends=True)
            if not line.startswith("max_recents")
        ),
        encoding="utf-8",
    )
    config.forget()

    with pytest.raises(config.ConfigError, match="missing settings: max_recents"):
        config.current()


def test_every_missing_setting_is_named_at_once(write_config: WriteConfig) -> None:
    """One message for all of them, so the file is not fixed a key per run."""
    path = write_config()
    path.write_text('data_dir = "/tmp/d"\n', encoding="utf-8")
    config.forget()

    with pytest.raises(config.ConfigError) as caught:
        config.current()

    message = str(caught.value)
    assert "missing settings" in message
    for key in ("apps_dir", "max_recents", "exclude_kinds", "pinned_position"):
        assert key in message


def test_every_path_setting_is_read(write_config: WriteConfig, tmp_path: Path) -> None:
    names = {
        "data_dir": "data",
        "apps_dir": "apps",
        "vscode_dir": "code",
        "state_db": "state.vscdb",
        "shared_db": "shared.vscdb",
        "desktop": "code.desktop",
        "exec": "code",
    }
    write_config(**{key: str(tmp_path / value) for key, value in names.items()})

    settings = config.current()
    for key, value in names.items():
        # `exec` is the one attribute whose name differs (it is a command).
        assert getattr(settings, "exec_path" if key == "exec" else key) == tmp_path / value


def test_blank_detect_path_means_detect(write_config: WriteConfig) -> None:
    """These say "find it yourself", so they are written empty rather than left out."""
    write_config(vscode_dir="", state_db="   ", shared_db="", desktop="", exec="")

    settings = config.current()
    assert settings.vscode_dir is None
    assert settings.state_db is None
    assert settings.shared_db is None
    assert settings.desktop is None
    assert settings.exec_path is None


@pytest.mark.parametrize("key", ["data_dir", "apps_dir"])
def test_blank_required_path_is_an_error(write_config: WriteConfig, key: str) -> None:
    """The tool's own directories must be stated: they have nowhere to fall back to."""
    write_config(**{key: ""})

    with pytest.raises(config.ConfigError, match=f"{key} must be a path"):
        config.current()


def test_tilde_is_expanded(
    write_config: WriteConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    write_config(data_dir="~/dotfiles")

    assert config.current().data_dir == tmp_path / "dotfiles"


def test_relative_paths_are_resolved_against_the_file(
    write_config: WriteConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not against the working directory, so the file can be copied around."""
    monkeypatch.chdir(tmp_path)
    path = write_config(data_dir=".tmp")

    assert config.current().data_dir == path.parent / ".tmp"


def test_the_file_is_reread_when_it_changes(write_config: WriteConfig) -> None:
    """The watcher runs for days: an edit must reach it without a restart."""
    write_config(max_recents=5)
    assert config.current().max_recents == 5

    write_config(max_recents=12)
    assert config.current().max_recents == 12


def test_an_unknown_setting_only_warns(
    write_config: WriteConfig, caplog: pytest.LogCaptureFixture
) -> None:
    """A file from a newer version, or a typo, must not stop a running service."""
    write_config(max_recents=5, max_recent=7)

    with caplog.at_level("WARNING"):
        assert config.current().max_recents == 5
    assert "max_recent" in caplog.text


@pytest.mark.parametrize("value", ["ten", True, 1.5])
def test_max_recents_must_be_a_whole_number(write_config: WriteConfig, value: object) -> None:
    # Rendered by type, so these become "ten", true and 1.5 in the file.
    write_config(max_recents=value)

    with pytest.raises(config.ConfigError, match="max_recents must be a whole number"):
        config.current()


def test_max_recents_must_not_be_negative(write_config: WriteConfig) -> None:
    write_config(max_recents=-1)

    with pytest.raises(config.ConfigError, match="must not be negative"):
        config.current()


def test_max_recents_zero_is_allowed(write_config: WriteConfig) -> None:
    """0 hides the recents block, so it is a real setting rather than an error."""
    write_config(max_recents=0)

    assert config.current().max_recents == 0


def test_exclude_kinds_accepts_the_known_kinds(write_config: WriteConfig) -> None:
    write_config(exclude_kinds=["folder", "file"])

    assert config.current().exclude_kinds == frozenset({"folder", "file"})


def test_exclude_kinds_empty_keeps_every_kind(write_config: WriteConfig) -> None:
    """An empty list is a deliberate "exclude nothing", not a missing value."""
    write_config(exclude_kinds=[])

    assert config.current().exclude_kinds == frozenset()


def test_exclude_kinds_rejects_an_unknown_kind(write_config: WriteConfig) -> None:
    write_config(exclude_kinds=["folders"])

    with pytest.raises(config.ConfigError, match="unknown kind 'folders'"):
        config.current()


def test_exclude_kinds_must_be_a_list(write_config: WriteConfig) -> None:
    write_config(exclude_kinds="folder")

    with pytest.raises(config.ConfigError, match="must be a list"):
        config.current()


def test_pinned_position_rejects_nonsense(write_config: WriteConfig) -> None:
    """A typo is refused rather than silently defaulted.

    The value decides the menu's layout, so a wrong one is visible; a warning in
    the journal would not be.
    """
    write_config(pinned_position="sideways")

    with pytest.raises(config.ConfigError, match='must be "above" or "below"'):
        config.current()


# --- a configuration written before the "favorites" rename -----------------
#
# `install` copies the configuration once and then never overwrites it -- that
# is what keeps it from clobbering the settings a running service uses. So a
# renamed key would otherwise leave an installed file permanently unusable, with
# an error that does not say why; it cost a `make install` that failed on
# "missing settings: pinned_position".


def test_a_key_under_its_old_name_is_still_read(
    write_config: WriteConfig, caplog: pytest.LogCaptureFixture
) -> None:
    """The upgrade must not need the file edited by hand."""
    path = write_config()
    text = path.read_text(encoding="utf-8").replace(
        'pinned_position = "above"', 'favorites_position = "below"'
    )
    path.write_text(text, encoding="utf-8")
    config.forget()

    with caplog.at_level("WARNING"):
        settings = config.current()

    assert settings.pinned_position == "below"  # the old value, not a default
    # ...and the warning says what to change it to, since the file is not ours
    # to rewrite.
    assert "favorites_position" in caplog.text
    assert "pinned_position" in caplog.text


def test_a_renamed_key_leaves_nothing_missing(
    write_config: WriteConfig, caplog: pytest.LogCaptureFixture
) -> None:
    """It is renamed before the missing-key check, not after."""
    path = write_config()
    text = path.read_text(encoding="utf-8").replace(
        "pinned_position = ", "favorites_position = "
    )
    path.write_text(text, encoding="utf-8")
    config.forget()

    with caplog.at_level("WARNING"):
        assert config.current().pinned_position == "above"


def test_the_current_name_wins_when_both_are_present(
    write_config: WriteConfig, caplog: pytest.LogCaptureFixture
) -> None:
    """A half-edited file is not an error; the current key is the one that counts."""
    path = write_config(pinned_position="below")
    path.write_text(
        path.read_text(encoding="utf-8") + 'favorites_position = "above"\n',
        encoding="utf-8",
    )
    config.forget()

    with caplog.at_level("WARNING"):
        assert config.current().pinned_position == "below"
    assert "favorites_position" in caplog.text  # the leftover is named


def test_a_broken_file_names_itself_and_the_reason(write_config: WriteConfig) -> None:
    path = write_config()
    path.write_text(path.read_text(encoding="utf-8") + "this is not toml\n", encoding="utf-8")
    config.forget()

    with pytest.raises(config.ConfigError) as caught:
        config.current()

    message = str(caught.value)
    assert str(path) in message
    assert "cannot parse" in message


def test_read_does_not_cache(tmp_path: Path) -> None:
    """`read` is the uncached entry point: the parse tests above depend on it."""
    path = tmp_path / "other.toml"
    path.write_text(settings_text(max_recents=4), encoding="utf-8")

    assert config.read(path).max_recents == 4
    path.write_text(settings_text(max_recents=44), encoding="utf-8")
    assert config.read(path).max_recents == 44


# --- the documentation has to match the parser ----------------------------


def test_the_shipped_config_sets_every_setting() -> None:
    """The checked-in file is a complete configuration, not an example.

    It is what a checkout runs against and what `install` copies to
    ~/.config/kde-vscode-jumplist, so it has to be usable as written: every key
    present, and the two paths pointing where an installed copy expects.

    The *other* values are the ones this checkout is run with, which is the
    point of the file -- so they are checked for being valid rather than for
    equalling a particular choice. Asserting `pinned_position == "above"` here
    made the suite fail when the preference was legitimately changed to
    "below", which is a setting doing its job, not a regression.
    """
    raw = config.read(REPO_CONFIG)

    assert raw.data_dir == Path("~/.config/kde-vscode-jumplist").expanduser()
    assert raw.apps_dir == Path("~/.local/share/applications").expanduser()
    assert raw.max_recents >= 0
    assert raw.exclude_kinds <= frozenset(config.ENTRY_KINDS)
    assert raw.pinned_position in ("above", "below")
    # The VS Code settings are left to detection, written empty rather than
    # omitted so the file still describes the whole run.
    assert raw.vscode_dir is None
    assert raw.state_db is None
    assert raw.shared_db is None
    assert raw.desktop is None
    assert raw.exec_path is None


def test_the_shipped_config_explains_every_setting() -> None:
    """Each key is introduced by a comment saying what it is for.

    Several keys may share one explanation, so it is the comment block directly
    above a key -- not the line immediately above -- that has to be there.
    """
    lines = REPO_CONFIG.read_text(encoding="utf-8").splitlines()
    unexplained: list[str] = []
    for index, line in enumerate(lines):
        key, separator, _ = line.partition(" = ")
        if not separator or key not in config.KNOWN_KEYS:
            continue
        block: list[str] = []
        for above in reversed(lines[:index]):
            if not above.strip():
                break
            block.append(above)
        if not any(entry.lstrip().startswith("#") for entry in block):
            unexplained.append(key)
    assert not unexplained, f"unexplained in config.toml: {', '.join(unexplained)}"


def test_the_documented_config_is_the_shipped_file() -> None:
    """The reference example must be the file, or it documents a fiction.

    A reader copies one or the other; if they disagreed, whichever they did not
    read would be the one that was wrong.
    """
    example = re.search(r"```toml\n(.*?)```", DETAILS.read_text(encoding="utf-8"), re.DOTALL)
    assert example, "DETAILS.md has no TOML example"

    documented = set(re.findall(r"^(\w+) = ", example.group(1), re.MULTILINE))
    assert documented == set(config.KNOWN_KEYS)


def test_the_docs_do_not_document_environment_variables() -> None:
    """The settings moved into the file, so no KDE_VSCODE_JUMPLIST_* is left."""
    for path in (README, DETAILS):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"KDE_VSCODE_JUMPLIST_[A-Z_]+", text), (
            f"{path.name} still documents an environment variable"
        )


def test_the_readme_is_the_short_one_and_links_to_the_details() -> None:
    """One page with the screenshots, and the reference behind a link.

    A README that quietly grew back into the reference would leave the two
    describing the same things, which is how they drift apart.
    """
    text = README.read_text(encoding="utf-8")

    assert "DETAILS.md" in text
    assert len(text.splitlines()) < 80, "README.md is growing into the reference"
    # Both screenshots, since showing the thing is the point of the short page.
    assert text.count("screenshots/") == 2
    assert "screenshots/context_menu.png" in text
    assert "screenshots/manage_pinned_files.png" in text
    for section in ("## Install", "## Use"):
        assert section in text
