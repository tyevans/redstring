"""The commit gate and CI must check the same files with the same tools.

They did not, and the failure had the shape this repository keeps meeting: two
declarations of one fact, with nothing that fails when they disagree.

`.pre-commit-config.yaml` passed filenames to ruff under
`types_or: [python, pyi]`, so a `.md` file was never handed to it.
`.github/workflows/ci.yml` ran `ruff format --check .`, so it was. The two
agreed for as long as ruff had nothing to say about markdown -- and the day it
gained formatting for Python blocks inside markdown, 22 documentation files
became unformatted by CI's definition and correctly formatted by the hook's.

**Five Dependabot pull requests went red at once**, every one of them on
`ruff format --check` against a documentation file its diff had not touched.
Five unrelated dependency bumps, one cause, none of them the dependency. That
is the expensive part: the failure names the diff and is not about the diff,
so the natural response is to re-run it, or to distrust the bump.

So the invariant is not "ruff is configured" -- it is **neither gate may look
at a narrower set of files than the other**. These tests assert that in the
two places it can be broken:

- a hook that goes back to passing filenames, or drops `markdown`
- a CI step that narrows `.` to `src/` or `tests/`

Both are edits a reasonable person makes for a good reason (speed, usually),
and neither announces what it costs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

PROJECT = Path(__file__).resolve().parents[2]
PRE_COMMIT = PROJECT / ".pre-commit-config.yaml"
CI_WORKFLOW = PROJECT / ".github" / "workflows" / "ci.yml"

#: The hook ids this module governs, and the ruff subcommand each one runs.
RUFF_HOOKS = {"ruff-check": "check", "ruff-format": "format"}


def _local_hooks() -> dict[str, dict[str, Any]]:
    config = yaml.safe_load(PRE_COMMIT.read_text())
    return {
        hook["id"]: hook
        for repo in config["repos"]
        if repo["repo"] == "local"
        for hook in repo["hooks"]
    }


def _ci_run_steps() -> list[str]:
    # `yaml.safe_load` turns the `on:` key into the boolean True (YAML 1.1
    # treats `on` as truthy), which is harmless here -- nothing below reads it.
    workflow = yaml.safe_load(CI_WORKFLOW.read_text())
    return [
        step["run"]
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if isinstance(step, dict) and isinstance(step.get("run"), str)
    ]


def _ci_ruff_commands() -> list[str]:
    return [line.strip() for run in _ci_run_steps() for line in run.splitlines() if "ruff " in line]


class TestTheDetectorsFindSomething:
    """A gate over an empty set passes vacuously and looks identical to one
    that works. Every assertion below is worthless without these."""

    def test_both_configuration_files_exist(self):
        assert PRE_COMMIT.is_file(), PRE_COMMIT
        assert CI_WORKFLOW.is_file(), CI_WORKFLOW

    def test_the_ruff_hooks_are_present(self):
        hooks = _local_hooks()
        missing = set(RUFF_HOOKS) - set(hooks)
        assert not missing, f"no local hook(s) named {sorted(missing)} in {PRE_COMMIT.name}"

    def test_ci_runs_ruff_at_all(self):
        commands = _ci_ruff_commands()
        assert len(commands) >= 2, (
            f"expected a ruff check and a ruff format step in {CI_WORKFLOW.name}, found {commands}"
        )


@pytest.mark.parametrize("hook_id", sorted(RUFF_HOOKS))
class TestTheHookLooksAtTheWholeRepository:
    def test_it_does_not_pass_filenames(self, hook_id: str):
        """Passing filenames is what made the hook's file set narrower than
        CI's -- pre-commit hands over only the staged files matching
        `types_or`, so nothing else is ever examined."""
        hook = _local_hooks()[hook_id]
        assert hook.get("pass_filenames") is False, (
            f"{hook_id} passes filenames, so it checks only staged files while CI "
            f"checks the whole tree. Set `pass_filenames: false`."
        )

    def test_it_triggers_on_markdown(self, hook_id: str):
        """`types_or` no longer decides what is *checked* (the hook checks
        everything), but it still decides whether the hook *runs*. Without
        `markdown`, a commit touching only documentation skips ruff entirely
        and CI is the first thing to notice."""
        hook = _local_hooks()[hook_id]
        types = hook.get("types_or", [])
        assert "markdown" in types, (
            f"{hook_id} does not run on markdown ({types}), so a docs-only commit "
            f"skips ruff locally and fails in CI instead."
        )

    def test_it_runs_the_expected_subcommand(self, hook_id: str):
        hook = _local_hooks()[hook_id]
        expected = RUFF_HOOKS[hook_id]
        assert f"ruff {expected}" in hook["entry"], (
            f"{hook_id} runs {hook['entry']!r}, which is not `ruff {expected}`"
        )


class TestCiLooksAtTheWholeRepository:
    def test_every_ruff_command_targets_the_whole_tree(self):
        """`ruff check src/` would pass while `tests/` rotted, and would do it
        quietly -- the narrowing is invisible in the run's output, which
        reports success either way."""
        offenders = [
            command
            for command in _ci_ruff_commands()
            if not command.rstrip("\\").rstrip().endswith(".")
        ]
        assert not offenders, (
            f"CI ruff commands must target the whole tree with a trailing `.`; "
            f"these narrow it: {offenders}"
        )

    def test_ci_checks_rather_than_fixes(self):
        """The two gates differ in exactly one intended way, and it is worth
        pinning: the hook rewrites files, CI must only report. A CI that
        silently fixed would go green on a tree nobody had formatted."""
        format_commands = [c for c in _ci_ruff_commands() if "ruff format" in c]
        assert format_commands, "no `ruff format` step in CI"
        for command in format_commands:
            assert "--check" in command, f"CI must not rewrite files: {command!r} lacks --check"
