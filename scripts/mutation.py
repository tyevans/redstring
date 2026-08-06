#!/usr/bin/env python
"""Run a mutation session, refusing to start against a baseline that is not green.

    uv run python scripts/mutation.py cosmic-ray
    uv run python scripts/mutation.py mutmut

**Why this exists rather than the four commands in a row.** Slice 7's first
cosmic-ray run reported 0 survivors out of 426, and a planner-only run before
it reported 0 out of 45. Both were worthless: the worktree had been synced
without a required extra, so every mutant "died" on a collection error, and
`cr-report` showed `WorkerOutcome.NORMAL, TestOutcome.KILLED` for all 426 --
indistinguishable from an outstanding suite. Slice 9 hit the same cause with
the sign flipped, 47 mypy errors in files nobody had touched. The environment
lying about the code is not detectable from the output, which is what makes
this a control rather than a habit (BACKLOG B45).

Three properties, each of which is the whole point of one of them:

- **The baseline runs the tool's own configured test command**, read from
  `cosmic-ray.toml` or `[tool.mutmut]` rather than restated here. A baseline
  that runs a different command than the mutants answers a different question,
  which is the shape `.claude/rules/recurring-defects.md` calls the command
  that measures an exemption being subject to it.
- **The baseline runs in the worktree the mutants will run in.** cosmic-ray's
  `local` distributor mutates the working tree in place, so the run belongs in
  a worktree -- and a worktree is exactly where a missing extra goes
  unnoticed, so a baseline in the main tree would pass and prove nothing.
- **A green baseline that ran no tests is refused too.** "0 failed" and "0
  collected" are the same exit code, and the incident this script exists for
  produced a suite that raised on collection. Requiring a positive pass count
  is what distinguishes the two.

Both tools are wrapped, deliberately. CLAUDE.md keeps mutmut and cosmic-ray
because mutmut 3.x will not mutate decorated functions and cosmic-ray will;
wrapping one would leave the other as the unguarded path, and the run someone
reaches for in a hurry is the one that needs the guard.
"""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess  # nosec B404
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Where the mutation worktree lives. Under `.mutation/` rather than beside the
#: repository so a stray `rm -rf` here cannot reach a sibling checkout.
WORKTREE = REPO_ROOT / ".mutation" / "worktree"

#: `uv sync` in the worktree uses this. `--all-extras` and not `--extra dev`:
#: the dev extra holds only the tooling, and a venv without `neo4j` or `llm`
#: fails *collection* on the modules that import them rather than skipping
#: them -- which is the exact shape of slice 7's phantom perfect score.
SYNC = ["uv", "sync", "--all-extras"]

#: Matches pytest's summary line: "12 passed", "3 failed, 9 passed".
_PASSED = re.compile(r"(\d+) passed")


def baseline_command(tool: str, config_path: str = "cosmic-ray.toml") -> list[str]:
    """The test command the mutants will run, read from that tool's own config.

    Restating it here would let the two drift, and a baseline that runs a
    different command than the mutation session is not a baseline for it.
    """
    if tool == "cosmic-ray":
        config = tomllib.loads((REPO_ROOT / config_path).read_text())
        return shlex.split(config["cosmic-ray"]["test-command"])

    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    mutmut = config["tool"]["mutmut"]
    return [*shlex.split(mutmut["runner"]), *mutmut["tests_dir"]]


def passed_count(output: str) -> int | None:
    """Tests reported passing, or `None` if pytest printed no such summary.

    `None` is not zero and the caller must not treat it as one: no summary
    means the run did not get as far as reporting, which is what a collection
    error looks like and is precisely the failure this script exists to catch.
    """
    matches = _PASSED.findall(output)
    return int(matches[-1]) if matches else None


def baseline_verdict(returncode: int, output: str) -> str | None:
    """`None` if the baseline is trustworthy, else why it is not.

    Split out from running it so the decision is testable without a suite:
    the whole value of this script is in refusing, and a refusal that has
    never been exercised is indistinguishable from one that cannot fire.
    """
    if returncode != 0:
        return f"the baseline suite failed (exit {returncode})"
    passed = passed_count(output)
    if passed is None:
        return (
            "the baseline exited 0 but printed no pytest summary, so nothing "
            "ran -- this is what a collection error looks like, and it is the "
            "failure that makes a mutation run report a perfect score"
        )
    if passed == 0:
        return "the baseline passed 0 tests, which is not evidence of anything"
    return None


def run(command: list[str], *, cwd: Path, capture: bool = False) -> subprocess.CompletedProcess:
    """Run `command` in `cwd`, echoing it first so a transcript is reproducible.

    `# nosec` on the call and on the import: every argv is a list built here
    from a literal or from this repository's own config files, nothing reaches
    it from a caller, and `shell=True` is never used. B404 and B603 are about
    the module being *capable* of injection rather than about a call that is.
    """
    print(f"$ {shlex.join(command)}    (in {cwd})", flush=True)
    return subprocess.run(  # nosec B603
        command,
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )


def ensure_worktree() -> Path:
    """A worktree at `WORKTREE` holding the current `HEAD`, synced.

    Reused rather than recreated per run: a mutation session is long, and the
    sync is the slow part. `git worktree add` is idempotent enough for this --
    it errors if the path exists, which is the signal to reuse it.
    """
    if not WORKTREE.exists():
        WORKTREE.parent.mkdir(parents=True, exist_ok=True)
        result = run(["git", "worktree", "add", "--detach", str(WORKTREE), "HEAD"], cwd=REPO_ROOT)
        if result.returncode != 0:
            sys.exit("could not create the mutation worktree")
    else:
        # **Reset, never merely reuse.** A worktree left at a previous HEAD
        # mutates code that is not the code you are asking about, and reports
        # survivors against tests that have since changed -- a result wrong in
        # the direction this whole script exists to prevent, because it looks
        # exactly like a result. `--hard` is safe here precisely because
        # nothing in this directory is authored: cosmic-ray's `local`
        # distributor writes mutants into it and expects them thrown away.
        print(f"resetting the worktree at {WORKTREE} to HEAD", flush=True)
        head = run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture=True)
        if head.returncode != 0:
            sys.exit("could not read HEAD")
        for step in (
            ["git", "reset", "--hard", head.stdout.strip()],
            ["git", "clean", "-fdx", "--exclude=.venv"],
        ):
            if run(step, cwd=WORKTREE).returncode != 0:
                sys.exit("could not reset the mutation worktree")

    if run(SYNC, cwd=WORKTREE).returncode != 0:
        sys.exit("`uv sync --all-extras` failed in the worktree")
    return WORKTREE


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tool", choices=["cosmic-ray", "mutmut"])
    parser.add_argument(
        "--config",
        default="cosmic-ray.toml",
        help=(
            "cosmic-ray config, relative to the repo root. A scoped session -- a "
            "narrower `test-command`, one module in `module-path` -- is how a "
            "target too big to mutate whole becomes affordable; the baseline "
            "then runs that config's own command, which is the point."
        ),
    )
    parser.add_argument(
        "--session",
        default="session.sqlite",
        help="cosmic-ray session file, relative to the worktree",
    )
    parser.add_argument(
        "--baseline-only",
        action="store_true",
        help="run and judge the baseline, then stop. Use to check an environment.",
    )
    args = parser.parse_args()

    worktree = ensure_worktree()

    print("\n== baseline ==", flush=True)
    command = baseline_command(args.tool, args.config)
    result = run(command, cwd=worktree, capture=True)
    print(result.stdout, flush=True)

    refusal = baseline_verdict(result.returncode, result.stdout)
    if refusal is not None:
        print(f"\nREFUSING TO RUN {args.tool}: {refusal}.", file=sys.stderr)
        print(
            "Fix the environment or the tests first. A mutation result read "
            "against a broken baseline is worse than no result, because a "
            "perfect score is what it looks like.",
            file=sys.stderr,
        )
        return 1

    print(f"baseline green: {passed_count(result.stdout)} passed.", flush=True)
    if args.baseline_only:
        return 0

    print(f"\n== {args.tool} ==", flush=True)
    if args.tool == "cosmic-ray":
        config = str(REPO_ROOT / args.config)
        for step in (
            ["uv", "run", "cosmic-ray", "init", config, args.session],
            ["uv", "run", "cosmic-ray", "exec", config, args.session],
        ):
            if run(step, cwd=worktree).returncode != 0:
                return 1
        return run(["uv", "run", "cr-report", args.session], cwd=worktree).returncode

    return run(["uv", "run", "mutmut", "run"], cwd=worktree).returncode


if __name__ == "__main__":
    sys.exit(main())
