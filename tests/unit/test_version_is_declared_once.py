"""`__version__` and `pyproject.toml` must agree, and the CHANGELOG must know.

The version is declared in two places that no tool compares:
`[project] version` in `pyproject.toml`, which decides the filename on the
index, and `redstring.__version__`, which is exported in `__all__` and is what
a caller reads at runtime. `release.yml`'s `validate` job checks the git tag
against `pyproject.toml` only.

**So the failure this catches is silent on both sides.** A release bumped in
`pyproject.toml` alone publishes a correctly-named artifact whose
`__version__` reports the previous release — nothing raises, the wheel smoke
test prints the wrong number without knowing it, and the first report comes
from a user comparing what they installed against what it says it is. Bumping
`__version__` alone is worse in the other direction: the tag check fails, but
only for the release that happens to come next.

This is `recurring-defects.md` §2 — one fact, two declaration sites, no
mechanism that fails when they disagree. The mechanism is cheap; not having it
is what let the alpha rehearsal nearly ship `0.1.0` inside a `0.1.0a1`
distribution.

`pyproject.toml` is read as the authority rather than `importlib.metadata`,
deliberately: the installed distribution is named `redstring-test` on the
TestPyPI path, so a metadata lookup keyed on `redstring` would raise there and
turn a prerelease rehearsal into a test failure about packaging. The file on
disk is the same on both paths.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import redstring

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"


def _project_version() -> str:
    return str(tomllib.loads(PYPROJECT.read_text())["project"]["version"])


def test_the_two_declaration_sites_agree():
    assert redstring.__version__ == _project_version(), (
        f"redstring.__version__ is {redstring.__version__!r} but pyproject.toml "
        f"says {_project_version()!r}. Both must move together."
    )


def test_the_changelog_has_a_section_for_the_current_version():
    """`release.yml` enforces this at tag time; enforcing it here is earlier.

    Failing at the tag means re-tagging, and an annotated tag that has been
    pushed is awkward to move. Failing on the commit that bumps the version
    costs nothing.
    """
    version = _project_version()
    heading = f"## [{version}]"

    assert heading in CHANGELOG.read_text(), (
        f"CHANGELOG.md has no '{heading}' section. `release.yml`'s validate job "
        f"rejects the tag without it."
    )


def test_the_version_is_a_valid_pep440_release_or_prerelease():
    """Guards the shape the release workflow's tag classifier branches on.

    `release.yml` routes `a`/`b` to TestPyPI and everything else to PyPI by
    regex. A version this pattern rejects is one the classifier would route by
    falling through to its `else` — that is, to the real index.
    """
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:(?:a|b|rc)\d+)?", _project_version()), (
        f"{_project_version()!r} is not a plain release or a/b/rc prerelease; "
        f"release.yml's tag classifier would route it to PyPI by default."
    )
