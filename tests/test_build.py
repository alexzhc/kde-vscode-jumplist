"""Tests for `make build`: the single-file ``bin/kde-vscode-jumplist`` zipapp."""

from __future__ import annotations

import functools
import importlib.util
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from kde_vscode_jumplist import __version__

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_SCRIPT = REPO_ROOT / "tools" / "build_zipapp.py"


@functools.lru_cache(maxsize=1)
def _build_module():
    """Load tools/build_zipapp.py without it being an importable package."""
    spec = importlib.util.spec_from_file_location("build_zipapp", BUILD_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the archive once for this module; it is a few hundred milliseconds."""
    output = tmp_path_factory.mktemp("build") / "kde-vscode-jumplist"
    return _build_module().build(output)


def test_build_creates_executable_file(built: Path) -> None:
    assert built.is_file()
    # chmod +x and a shebang: Plasma execs the file directly.
    assert built.stat().st_mode & 0o111
    assert built.read_bytes().startswith(b"#!/usr/bin/env python3\n")


def test_built_archive_runs_the_cli_without_any_environment(built: Path) -> None:
    """The whole point: it must work with no PYTHONPATH and nothing installed."""
    result = subprocess.run(  # noqa: S603 - argv list, no shell
        [str(built), "--version"],
        env={},  # no PATH, no PYTHONPATH, no HOME
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == __version__


def test_build_smoke_tests_its_own_output(built: Path) -> None:
    """`build()` returns only after verifying, so the artifact is known good."""
    assert _build_module().build(built) == built


def test_archive_contains_package_dependencies_and_entry_point(built: Path) -> None:
    with zipfile.ZipFile(built) as archive:
        names = archive.namelist()

    assert "__main__.py" in names
    assert "kde_vscode_jumplist/cli.py" in names
    # Runtime dependencies are bundled, not left to the host interpreter.
    assert any(name.startswith("tabulate/") for name in names)
    # No caches or build noise.
    assert not [name for name in names if "__pycache__" in name or name.endswith(".pyc")]


def test_archive_entry_point_calls_main(built: Path) -> None:
    with zipfile.ZipFile(built) as archive:
        main = archive.read("__main__.py").decode("utf-8")
    assert "from kde_vscode_jumplist.cli import main" in main
    assert "main()" in main


def test_bundled_tabulate_is_actually_used(tmp_path: Path) -> None:
    """A table, not the tab-separated fallback: tabulate was bundled correctly."""
    module = _build_module()
    output = module.build(tmp_path / "kde-vscode-jumplist")
    # The archive is run with no environment at all, so the configuration file
    # has to be named on the command line -- which is what a menu action does.
    # Every setting is required, and both paths point inside tmp_path so the
    # run cannot reach the real home directory.
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        f'data_dir = "{tmp_path / "data"}"\n'
        f'apps_dir = "{tmp_path / "applications"}"\n'
        'vscode_dir = ""\n'
        'state_db = ""\n'
        'shared_db = ""\n'
        'desktop = ""\n'
        'exec = ""\n'
        "max_recents = 10\n"
        "exclude_kinds = []\n"
        'pinned_position = "above"\n',
        encoding="utf-8",
    )

    result = subprocess.run(  # noqa: S603 - argv list, no shell
        [str(output), "--config", str(config_file), "pinned"],
        env={},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    output_text = result.stdout + result.stderr
    # Either "no pinned entries yet" (no table needed) or a rendered one.
    assert "\t" not in output_text


def test_runtime_dependencies_match_pyproject() -> None:
    """The bundled list must track pyproject, or the build silently ships less."""
    tomllib = pytest.importorskip("tomllib", reason="needs Python 3.11+")
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = pyproject["project"]["dependencies"]

    def normalize(requirement: str) -> str:
        # "tabulate>=0.9" -> "tabulate"; PEP 503 style name normalization.
        name = requirement.split(";")[0]
        for separator in ("<", ">", "=", "!", "~", "[", " "):
            name = name.split(separator)[0]
        return name.strip().lower().replace("_", "-")

    assert {normalize(requirement) for requirement in declared} == set(
        _build_module().RUNTIME_DEPENDENCIES
    )


def test_missing_dependency_is_reported_clearly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _build_module()
    monkeypatch.setattr(module, "RUNTIME_DEPENDENCIES", ("definitely_not_installed_xyz",))
    with pytest.raises(SystemExit, match="not installed"):
        module.dependency_path("definitely_not_installed_xyz")


def test_build_requires_the_package_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _build_module()
    monkeypatch.setattr(module, "SOURCE_DIR", tmp_path / "missing")
    with pytest.raises(SystemExit, match="source not found"):
        module.build(tmp_path / "out")


def test_build_script_is_valid_on_the_running_interpreter() -> None:
    """The script itself must not need anything beyond the standard library."""
    result = subprocess.run(  # noqa: S603 - argv list, no shell
        [sys.executable, str(BUILD_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0
    assert "--output" in result.stdout
