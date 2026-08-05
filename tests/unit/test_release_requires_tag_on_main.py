"""The release guard that refuses to publish a tag main does not contain.

`on.push.tags` cannot be restricted to a branch — a tag is not on one, and the
trigger fires wherever the commit lives. So the check has to be a step, and a
step is a thing that can be edited into uselessness without any test noticing.

**What it prevents is a wrong record rather than a broken artifact**, which is
why it needs a test rather than a reviewer. A tag on an unmerged branch
validates, builds and uploads successfully; the branch then merges under a new
SHA, and the published release's PEP 740 attestation names a commit `git log
main` does not contain. Provenance pointing at an unreachable commit looks like
evidence and cannot be followed. PyPI never permits reusing a filename, so the
version number is spent before the mismatch is visible.

This nearly happened: the alpha tag was created on the feature branch whose
pull request had not merged.

As with the tag classifier, the real `run:` body is extracted from the YAML and
executed, with `gh` stubbed to return a chosen compare status. A Python
reimplementation of the `case` statement would be free to drift from the one
that runs on a tag.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml"
STEP_NAME = "The tagged commit must be on main"


def _guard_script() -> str:
    """The `run:` body of the ancestry step, straight from the file."""
    workflow = yaml.safe_load(WORKFLOW.read_text())
    for step in workflow["jobs"]["validate"]["steps"]:
        if step.get("name") == STEP_NAME:
            return str(step["run"])
    raise AssertionError(f"no step named {STEP_NAME!r} in the validate job")


def _run_guard(tmp_path: Path, *, status: str = "behind", gh_exit: int = 0):
    """Execute the guard with `gh` stubbed to report `status`.

    The stub is a real executable placed first on `PATH`, so the step runs
    unmodified — no substitution of the command, which would test a different
    string than the one in the workflow.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f'#!/bin/sh\necho "{status}"\nexit {gh_exit}\n')
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    return subprocess.run(
        ["bash", "-c", _guard_script()],
        env={
            "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
            "GH_TOKEN": "stub",
            "REPO": "tyevans/redstring",
            "SHA": "0123456789abcdef0123456789abcdef01234567",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize("status", ["behind", "identical"])
def test_a_commit_main_contains_is_allowed(status: str, tmp_path: Path):
    """`behind` and `identical` are the two compare statuses that mean "on main".

    `compare/main...SHA` reports the *head* relative to the base, so a tagged
    commit already in main is `behind` it, and the tip of main is `identical`.
    """
    result = _run_guard(tmp_path, status=status)

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("status", ["ahead", "diverged"])
def test_a_commit_main_does_not_contain_is_rejected(status: str, tmp_path: Path):
    """`ahead` is the unmerged feature branch — the case that nearly shipped."""
    result = _run_guard(tmp_path, status=status)

    assert result.returncode != 0, result.stdout
    assert "not on main" in result.stdout


def test_an_api_failure_is_not_treated_as_success(tmp_path: Path):
    """A failed lookup must fail the step, not fall through to publishing.

    This is the half a `case` statement gets wrong: with the status unset or
    the call broken, the natural reading is "no evidence it is off main". The
    guard needs the opposite default, and `set -euo pipefail` plus the command
    substitution is what supplies it — so this test is really pinning that the
    `set` line stays.
    """
    result = _run_guard(tmp_path, status="behind", gh_exit=1)

    assert result.returncode != 0, result.stdout


def test_an_unrecognised_status_is_rejected(tmp_path: Path):
    """Anything not explicitly permitted must fail closed.

    If GitHub ever adds a fifth status, the guard must refuse it rather than
    pattern-match its way past. Written as its own case because a `*)` arm is
    exactly what a well-meaning edit turns into `*) echo warning ;;`.
    """
    result = _run_guard(tmp_path, status="something_new")

    assert result.returncode != 0, result.stdout


def test_every_publishing_job_waits_on_validate():
    """The guard is only worth anything if publishing cannot start without it.

    A `needs:` edit that dropped `validate` would leave the step passing in its
    own job while the upload happened regardless — the check still green, and
    no longer connected to anything.
    """
    workflow = yaml.safe_load(WORKFLOW.read_text())

    for job_name in ("build", "publish-pypi", "publish-testpypi"):
        needs = workflow["jobs"][job_name]["needs"]
        needs = [needs] if isinstance(needs, str) else needs
        assert "validate" in needs, f"{job_name} does not depend on validate"
