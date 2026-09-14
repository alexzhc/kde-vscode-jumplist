"""The requirements files must list what pyproject declares, no more, no less.

pyproject.toml is the source of truth: pip installs from it, and
tools/build_zipapp.py bundles its ``dependencies`` into the shipped executable
(tests/test_build.py asserts that half). The requirements files are a second
restatement of the same list for tooling that expects them, so they get the
same treatment here -- a dependency added to pyproject without being added to
requirements.txt fails the suite instead of silently diverging.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = REPO_ROOT / "requirements.txt"
REQUIREMENTS_DEV = REPO_ROOT / "requirements-dev.txt"


def _normalize(requirement: str) -> str:
    """``tabulate>=0.9`` -> ``tabulate``; PEP 503 style name normalization."""
    name = requirement.split(";")[0]
    for separator in ("<", ">", "=", "!", "~", "[", " "):
        name = name.split(separator)[0]
    return name.strip().lower().replace("_", "-")


def _declared(path: Path, seen: frozenset[Path] = frozenset()) -> set[str]:
    """Package names in a requirements file, following ``-r`` includes.

    Includes are expanded so the comparison is against what pip would actually
    install: requirements-dev.txt extends the runtime file rather than copying
    it, and that is deliberate (a copy would be another place to update).
    """
    names: set[str] = set()
    resolved = path.resolve()
    if resolved in seen:
        return names  # a cycle; pip would not get here either
    seen |= {resolved}

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-r "):
            include = path.parent / line[3:].strip()
            names |= _declared(include, seen)
        elif not line.startswith("-"):
            names.add(_normalize(line))
    return names


def _pyproject() -> dict:
    tomllib = pytest.importorskip("tomllib", reason="needs Python 3.11+")
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_requirements_mirrors_pyproject_runtime_dependencies() -> None:
    project = _pyproject()["project"]
    assert _declared(REQUIREMENTS) == {
        _normalize(requirement) for requirement in project["dependencies"]
    }


def test_requirements_dev_adds_every_extra_from_pyproject() -> None:
    """The dev file is the runtime list plus every extras group -- nothing else."""
    optional = _pyproject()["project"]["optional-dependencies"]
    expected = {
        _normalize(requirement)
        for requirements in optional.values()
        for requirement in requirements
    }
    expected |= _declared(REQUIREMENTS)

    assert _declared(REQUIREMENTS_DEV) == expected


def test_requirements_dev_includes_the_runtime_file() -> None:
    """It must pull in requirements.txt rather than repeat those packages.

    A copy would be one more place to update, and the point of this file is to
    extend the runtime list, not restate it.
    """
    text = REQUIREMENTS_DEV.read_text(encoding="utf-8")
    assert re.search(r"^-r\s+requirements\.txt\s*$", text, re.MULTILINE)


def test_every_extras_group_is_covered() -> None:
    """A new extras group in pyproject must reach the dev requirements.

    Guards the case the two tests above cannot see: ``optional-dependencies``
    gaining a third group that no requirements file mentions.
    """
    optional = _pyproject()["project"]["optional-dependencies"]
    covered = _declared(REQUIREMENTS_DEV) | _declared(REQUIREMENTS)
    missing: dict[str, list[str]] = {}
    for group, requirements in optional.items():
        absent = [_normalize(r) for r in requirements if _normalize(r) not in covered]
        if absent:
            missing[group] = absent
    assert not missing, f"extras absent from requirements-dev.txt: {missing}"
