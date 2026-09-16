"""Which commit this copy of the program was built from.

The About window shows it, and there are two ways of knowing. An installed copy
is a zipapp, which carries no ``.git`` of its own, so
``tools/build_zipapp.py`` writes the id into the copy of the package it bundles.
A checkout run from source has the repository around it and is asked directly.

Neither source is certain: a build made outside a repository, or a machine with
no git on it, has no answer at all. So the id falls back to
:data:`GIT_ID_UNKNOWN` rather than raising -- it labels a window, and a window
that opens without one is worth more than a start-up that fails over it.

Read lazily and then cached: the lookup shells out, and one process only ever
has one commit, so it is never asked twice.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from importlib import import_module
from pathlib import Path

# The length git's own short ids are usually shown at: long enough not to be
# ambiguous in a repository this size, short enough to read at a glance.
GIT_ID_LENGTH = 7

# Shown when neither source can answer.
GIT_ID_UNKNOWN = "unknown"

# The module ``tools/build_zipapp.py`` stamps into the copy it bundles, and the
# attribute it writes there. A module of its own so that a build made without
# one simply has none, rather than leaving a half-written file behind.
BUILD_ID_MODULE = "_build_id"
BUILD_ID_ATTRIBUTE = "GIT_ID"

# Long enough for git on a cold start, short enough not to hold a window open.
GIT_COMMAND_TIMEOUT = 5

# Asked of the package directory, which sits inside the repository in a source
# checkout: git walks up from there to the repository root itself.
_PACKAGE_DIR = Path(__file__).resolve().parent


def _bundled() -> str | None:
    """The id the build stamped into this copy, if it stamped one."""
    try:
        module = import_module(f"{__package__}.{BUILD_ID_MODULE}")
    except ImportError:
        return None
    value = getattr(module, BUILD_ID_ATTRIBUTE, None)
    return str(value) if value else None


def _from_git() -> str | None:
    """git's own short id for HEAD, or None where git cannot answer."""
    try:
        result = subprocess.run(  # noqa: S603 - argv list, no shell
            [
                "git",
                "-C",
                str(_PACKAGE_DIR),
                "rev-parse",
                f"--short={GIT_ID_LENGTH}",
                "HEAD",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=GIT_COMMAND_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


@lru_cache(maxsize=1)
def git_id() -> str:
    """The commit this copy was built from, abbreviated.

    The bundled id wins where there is one: a built copy can be unpacked inside
    some *other* repository, and that repository's HEAD is not the commit it was
    built from.
    """
    return _bundled() or _from_git() or GIT_ID_UNKNOWN
