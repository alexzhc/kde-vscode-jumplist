"""Tests for kde_vscode_jumplist.buildinfo: the commit the About window shows.

The two sources are covered separately, and so is the case where neither has an
answer -- the whole point of the fallback being that the window still opens.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import types

import pytest

from kde_vscode_jumplist import buildinfo

HEX = set("0123456789abcdef")


@pytest.fixture(autouse=True)
def _uncached():
    """Each test asks git afresh: the id is cached, and tests change the answers."""
    buildinfo.git_id.cache_clear()
    yield
    buildinfo.git_id.cache_clear()


def _no_git(monkeypatch: pytest.MonkeyPatch, result: object = None) -> None:
    """Make the git call fail the way a machine without git would."""

    def run(*_args, **_kwargs):
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(buildinfo.subprocess, "run", run)


def test_git_id_is_the_checkouts_own_short_id() -> None:
    """The suite runs from the repository, so git answers with a short id."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed here")
    value = buildinfo.git_id()
    assert value != buildinfo.GIT_ID_UNKNOWN
    # git only guarantees *at least* the length asked for: it grows one past it
    # should that much be ambiguous, which a repository this size never needs.
    assert buildinfo.GIT_ID_LENGTH <= len(value) <= 40
    assert set(value) <= HEX


def test_git_id_is_read_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Asked for every time the window is opened, but only looked up once."""
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "abc1234\n", "")

    monkeypatch.setattr(buildinfo.subprocess, "run", run)
    assert buildinfo.git_id() == "abc1234"
    assert buildinfo.git_id() == "abc1234"
    assert len(calls) == 1


def test_missing_git_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """No git on the machine: the window says so rather than failing to open."""
    _no_git(monkeypatch, OSError("git: not found"))
    assert buildinfo.git_id() == buildinfo.GIT_ID_UNKNOWN


def test_git_failure_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Outside a repository, and a timeout on a hung git: same fallback."""
    _no_git(monkeypatch, subprocess.CompletedProcess(["git"], 128, "", "not a repository"))
    assert buildinfo.git_id() == buildinfo.GIT_ID_UNKNOWN

    buildinfo.git_id.cache_clear()
    _no_git(monkeypatch, subprocess.TimeoutExpired(["git"], buildinfo.GIT_COMMAND_TIMEOUT))
    assert buildinfo.git_id() == buildinfo.GIT_ID_UNKNOWN


def test_bundled_id_is_used_where_the_build_wrote_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """An installed copy has no .git, so the build stamps the id into it.

    It also wins over whatever repository the copy happens to sit in afterwards,
    which is not the one it was built from.
    """
    _no_git(monkeypatch, subprocess.CompletedProcess(["git"], 0, "zzz9999\n", ""))
    stamped = types.ModuleType(f"{buildinfo.__package__}.{buildinfo.BUILD_ID_MODULE}")
    stamped.GIT_ID = "abc1234"
    monkeypatch.setitem(sys.modules, stamped.__name__, stamped)

    assert buildinfo.git_id() == "abc1234"


def test_a_stamp_without_an_id_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stamp that says nothing falls through to git rather than showing 'None'."""
    _no_git(monkeypatch, subprocess.CompletedProcess(["git"], 0, "abc1234\n", ""))
    stamped = types.ModuleType(f"{buildinfo.__package__}.{buildinfo.BUILD_ID_MODULE}")
    monkeypatch.setitem(sys.modules, stamped.__name__, stamped)

    assert buildinfo.git_id() == "abc1234"
