"""Every CI job declares `timeout-minutes`, so nothing can hang for six hours.

GitHub's default job timeout is **360 minutes**, and it applies silently. No
job in this repository set one, which was found the only way a ceiling that
high ever is: it failed to fire. The `integration` job spent roughly 90
minutes a run waiting out embedding probes against an unreachable address --
on green runs as well as red, for a full day -- and nothing capped it, because
six hours is so far above the real cost that the job would have had to be an
order of magnitude wrong before the default noticed.

The caps are set from measurement rather than from taste: the p100 of each job
over recent runs, times a generous multiple. A cap below the real distribution
turns a slow-but-fine run into a red X that reads as a test failure, which is
the same misdiagnosis in the other direction.

**This test is the half worth having.** Adding the caps is a one-off edit that
a single new job silently undoes; nothing about a missing `timeout-minutes`
looks wrong in a diff, and the consequence only shows up on the day something
hangs. Deriving the job list from the workflow files means a new job fails
here until someone chooses its number.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOWS = sorted((Path(__file__).resolve().parents[2] / ".github" / "workflows").glob("*.yml"))

#: The default GitHub applies when a job declares nothing. Named because it is
#: the whole reason this file exists, and because a reader meeting
#: `timeout-minutes: 20` should be able to see what it replaced.
GITHUB_DEFAULT_MINUTES = 360

#: Jobs that legitimately declare no timeout, with the reason. A `uses:` job
#: calling a reusable workflow does not run steps of its own -- the called
#: workflow's jobs carry their own caps -- and GitHub rejects
#: `timeout-minutes` at the caller.
EXEMPT_CALLERS: dict[str, str] = {
    "release.yml::ci": "calls ./.github/workflows/ci.yml, whose jobs are capped individually",
}


def _jobs() -> list[tuple[str, str, dict[str, Any]]]:
    found = []
    for workflow in WORKFLOWS:
        document = yaml.safe_load(workflow.read_text())
        for name, job in document.get("jobs", {}).items():
            found.append((workflow.name, name, job))
    return found


def test_there_are_workflows_and_jobs_to_check() -> None:
    """Guard the guard: a check over an empty glob passes vacuously.

    Same reasoning as the compliance coverage gates. If the workflow directory
    moves, this file would otherwise keep reporting success about nothing.
    """
    assert len(WORKFLOWS) >= 3
    assert len(_jobs()) >= 10


@pytest.mark.parametrize(("workflow", "name", "job"), _jobs(), ids=lambda value: str(value)[:40])
def test_a_job_declares_its_own_timeout(workflow: str, name: str, job: dict[str, Any]) -> None:
    key = f"{workflow}::{name}"
    if key in EXEMPT_CALLERS:
        assert "uses" in job, (
            f"{key} is exempt as a reusable-workflow caller, but it has no "
            f"`uses:`. Either it is no longer a caller -- give it a "
            f"`timeout-minutes` and drop the exemption -- or the exemption is "
            f"describing something that is not there."
        )
        return

    timeout = job.get("timeout-minutes")
    assert timeout is not None, (
        f"{key} declares no `timeout-minutes`, so it inherits GitHub's "
        f"{GITHUB_DEFAULT_MINUTES}-minute default. Set one from the job's "
        f"observed p100 times a generous multiple. A cap below the real "
        f"distribution reads as a test failure; no cap at all means a hang "
        f"burns six hours of runner time and, in `release.yml`, spends the "
        f"version number with it."
    )
    assert 0 < timeout < GITHUB_DEFAULT_MINUTES, (
        f"{key} sets `timeout-minutes: {timeout}`, which is not below "
        f"GitHub's own default of {GITHUB_DEFAULT_MINUTES}. A cap at or above "
        f"the default is the default wearing a number."
    )


def test_the_exemption_list_does_not_outlive_its_jobs() -> None:
    """An exemption naming a job that no longer exists passes silently.

    ADR 0014 applied to this list: an entry is a visible decision, and a stale
    one is an exemption nobody chose.
    """
    known = {f"{workflow}::{name}" for workflow, name, _ in _jobs()}
    stale = set(EXEMPT_CALLERS) - known
    assert not stale, f"EXEMPT_CALLERS names jobs no workflow has: {sorted(stale)}"


def test_exemptions_carry_a_reason() -> None:
    for key, reason in EXEMPT_CALLERS.items():
        assert reason.strip(), f"{key} is exempt with no reason given"
