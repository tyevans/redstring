"""The release guard that refuses to publish a tag no protected branch contains.

`on.push.tags` cannot be restricted to a branch — a tag is not on one, and the
trigger fires wherever the commit lives. So the check has to be a step, and a
step is a thing that can be edited into uselessness without any test noticing.

**What it prevents is a wrong record rather than a broken artifact**, which is
why it needs a test rather than a reviewer. A tag on an unmerged branch
validates, builds and uploads successfully; the branch then merges under a new
SHA, and the published release's PEP 740 attestation names a commit nothing can
reach. Provenance pointing at an unreachable commit looks like evidence and
cannot be followed. PyPI never permits reusing a filename, so the version
number is spent before the mismatch is visible.

This nearly happened: the alpha tag was created on the feature branch whose
pull request had not merged.

The rule is *protected branch*, not `main` and not `release/*`. `main` alone
rejects a legitimate patch release cut from a maintenance branch; `release/*`
gives most of the hole back, because a branch is cheap to create. Protection is
a claim about repository settings that cannot be satisfied by pushing one.

As with the tag classifier, the real `run:` body is extracted from the YAML and
executed with `gh` stubbed. The stub dispatches on the endpoint, because the
step makes two different calls and the interesting failures are in how it
combines them.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml"
STEP_NAME = "The tagged commit must be on a protected branch"
SHA = "0123456789abcdef0123456789abcdef01234567"


def _guard_script() -> str:
    """The `run:` body of the ancestry step, straight from the file."""
    workflow = yaml.safe_load(WORKFLOW.read_text())
    for step in workflow["jobs"]["validate"]["steps"]:
        if step.get("name") == STEP_NAME:
            return str(step["run"])
    raise AssertionError(f"no step named {STEP_NAME!r} in the validate job")


def _run_guard(
    tmp_path: Path,
    *,
    protected: list[str],
    unprotected: list[str] = (),
    status: dict[str, str] | None = None,
    branches_exit: int = 0,
    compare_exit: int = 0,
):
    """Execute the guard against a stubbed GitHub API.

    **The stub honours `protected=true` in the URL**, and that is the whole
    reason it exists rather than a fixed list. A first version returned
    `protected` for any `branches?` call, and the consequence was measured
    rather than reasoned about: rewriting the step to fetch *all* branches and
    filter by name — the `release/*` widening this rule exists to reject — left
    all ten tests green. The stub could not tell the two implementations apart,
    so the suite was checking the guard's shape rather than its rule.

    `unprotected` therefore has to be populated by any test that cares about
    the distinction, and `test_an_unprotected_branch_does_not_count` is the one
    that does. `status` maps a branch to its compare status, defaulting to
    `diverged`, so a test states only what it means to state.
    """
    status = status or {}
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"protected = {json.dumps(protected)}\n"
        f"unprotected = {json.dumps(list(unprotected))}\n"
        f"status = {json.dumps(status)}\n"
        f"branches_exit, compare_exit = {branches_exit}, {compare_exit}\n"
        "arg = next(a for a in sys.argv[1:] if a.startswith('repos/'))\n"
        "if 'branches' in arg and 'compare' not in arg:\n"
        "    if branches_exit:\n"
        "        sys.exit(branches_exit)\n"
        "    # The filter the real endpoint applies. Dropping it is the defect\n"
        "    # this stub is built to expose.\n"
        "    names = protected if 'protected=true' in arg else protected + unprotected\n"
        "    print('\\n'.join(names))\n"
        "else:\n"
        "    if compare_exit:\n"
        "        sys.exit(compare_exit)\n"
        "    base = arg.split('compare/')[1].split('...')[0]\n"
        "    print(status.get(base, 'diverged'))\n"
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    return subprocess.run(
        ["bash", "-c", _guard_script()],
        env={
            "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
            "GH_TOKEN": "stub",
            "REPO": "tyevans/redstring",
            "SHA": SHA,
        },
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize("compare_status", ["behind", "identical"])
def test_a_commit_on_the_protected_branch_is_allowed(compare_status: str, tmp_path: Path):
    """`behind` and `identical` are the two statuses that mean "contained".

    `compare/BASE...SHA` reports the head relative to the base, so a commit
    already in the branch is `behind` it and the tip is `identical`.
    """
    result = _run_guard(tmp_path, protected=["main"], status={"main": compare_status})

    assert result.returncode == 0, result.stderr + result.stdout


def test_an_unmerged_feature_branch_is_rejected(tmp_path: Path):
    """The case that nearly shipped: a tag on a branch whose PR had not merged."""
    result = _run_guard(tmp_path, protected=["main"], status={"main": "ahead"})

    assert result.returncode != 0
    assert "not on any protected branch" in result.stdout


def test_a_maintenance_branch_is_allowed_when_it_is_protected(tmp_path: Path):
    """The B64 case, and the reason the rule is not `main`.

    A `v0.1.1` cut from `release/0.1` after `main` moved to `0.2` is a real
    release. It passes because the branch is protected, not because of what it
    is called.
    """
    result = _run_guard(
        tmp_path,
        protected=["main", "release/0.1"],
        status={"main": "diverged", "release/0.1": "behind"},
    )

    assert result.returncode == 0, result.stdout
    assert "release/0.1" in result.stdout


def test_an_unprotected_branch_does_not_count_however_it_is_named(tmp_path: Path):
    """A `release/9.9` that nobody protected must not publish.

    This is the argument against matching on the name: whoever pushes supplies
    the name, and supplies it for free. The branch here *would* satisfy a
    `release/*` rule — it exists, it is named right, and the tagged commit is
    on it — and must still be refused.

    **This is the test that distinguishes the two implementations**, and it can
    only do so because `release/9.9` is passed as `unprotected` rather than
    omitted. Omit it and a name-matching guard passes every test in this file.
    """
    result = _run_guard(
        tmp_path,
        protected=["main"],
        unprotected=["release/9.9"],
        status={"main": "diverged", "release/9.9": "behind"},
    )

    assert result.returncode != 0
    assert "not on any protected branch" in result.stdout


def test_a_repository_with_no_protected_branches_publishes_nothing(tmp_path: Path):
    """Fail closed, and say why.

    An empty list means no tag can be shown to follow from a reviewed state.
    The loop would reach its error anyway; the explicit branch exists so the
    message names the cause instead of listing zero branches checked.
    """
    result = _run_guard(tmp_path, protected=[])

    assert result.returncode != 0
    assert "no protected branches" in result.stdout


@pytest.mark.parametrize(("branches_exit", "compare_exit"), [(1, 0), (0, 1)])
def test_an_api_failure_is_not_treated_as_success(
    branches_exit: int, compare_exit: int, tmp_path: Path
):
    """A failed lookup must fail the step, not fall through to publishing.

    This is the half a shell guard gets wrong: with the output empty or the
    call broken, the natural reading is "no evidence it is off the branch", and
    the guard needs the opposite default.

    **The default is structural here, not `set -euo pipefail`.** Deleting the
    `set` line was tried and every test stayed green, which is worth recording
    rather than glossing: a failed branch lookup leaves `BRANCHES` empty and
    hits the explicit empty check, and a failed compare leaves `STATUS` empty
    and falls to the `case` default. Both paths reach an error without needing
    `set -e` to notice. That is a stronger arrangement than the earlier
    single-compare version, where removing `set -e` did make a failed lookup
    exit 0 and publish — so the property survived a rewrite that could easily
    have lost it, and this test is what would say so.

    Parametrised over both calls because they fail independently: rate limiting
    hits whichever comes first.
    """
    result = _run_guard(
        tmp_path,
        protected=["main"],
        status={"main": "behind"},
        branches_exit=branches_exit,
        compare_exit=compare_exit,
    )

    assert result.returncode != 0, result.stdout


def test_an_unrecognised_compare_status_is_rejected(tmp_path: Path):
    """Anything not explicitly permitted must fail closed.

    If GitHub adds a fifth status the guard must refuse it rather than
    pattern-match its way past. Its own case because a permissive fallback is
    what a well-meaning edit introduces.
    """
    result = _run_guard(tmp_path, protected=["main"], status={"main": "something_new"})

    assert result.returncode != 0


def test_every_publishing_job_waits_on_validate():
    """The guard is worth nothing if publishing can start without it.

    A `needs:` edit dropping `validate` would leave the step passing in its own
    job while the upload happened regardless — still green, no longer connected
    to anything.
    """
    workflow = yaml.safe_load(WORKFLOW.read_text())

    for job_name in ("build", "publish-pypi", "publish-testpypi"):
        needs = workflow["jobs"][job_name]["needs"]
        needs = [needs] if isinstance(needs, str) else needs
        assert "validate" in needs, f"{job_name} does not depend on validate"
