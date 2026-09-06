#!/usr/bin/env python
"""Run the test suite under coverage and enforce a one-way coverage ratchet.

The baseline lives in ``.coverage-baseline`` as a single float (percent).
Coverage may never drop below it; when it rises, the baseline rises with it and
the updated file is staged so it travels with the commit that earned it.

Two modes, because the ratchet's two halves now run in different places.

``coverage_ratchet.py`` with no arguments is the original: run the suite,
compare, and stage a rise into the commit that earned it. Run it by hand when
your change adds tests.

``coverage_ratchet.py --check-rise`` is the CI half. It does **not** run the
suite -- CI has already run it, and running it twice to ask one question is
minutes for nothing -- it measures the ``.coverage`` that run left behind and
fails if the total has risen clear of the baseline. Failing on a *rise* reads
oddly until you notice what the alternative is: CI cannot stage a file into a
commit that already exists, so without this the baseline sits wherever it was
last edited while the real number drifts up, and a later regression back to
the stale figure passes silently. That is the "passing check you have never
seen fail" shape in `CLAUDE.md`, and the gate keeps reporting green the whole
time.

A bot that pushed the raised baseline itself was the obvious alternative and
was not taken: it needs a token with write access to a protected branch, and
it turns every coverage improvement into a second commit. This asks one
command of the author who earned the rise, at the moment they earned it.

Both halves read one `TOLERANCE` and one baseline file, so the band is
symmetric by construction rather than by two constants agreeing.
"""

from __future__ import annotations

import argparse
import subprocess  # nosec B404
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO_ROOT / ".coverage-baseline"

#: Slack, in percentage points, on **both** comparisons below: a run fails only
#: if it is more than this under the baseline, and raises the baseline only if
#: it is more than this over it.
#:
#: **This is measurement noise, not floating-point slack**, which is what it was
#: originally sized for at 0.01. Total coverage is not a function of the tree
#: here: the suite runs under `pytest-randomly` and `-n auto`, and the
#: compliance suites draw hypothesis examples, so which lines execute varies
#: run to run. Three consecutive runs on one unchanged tree measured 96.30,
#: 96.27 and 96.30.
#:
#: At 0.01 that spread broke the gate in both directions at once, and the
#: second direction is the one that is easy to miss. A lucky run ratcheted the
#: baseline to 96.33 -- above every subsequent measurement of the same tree --
#: so the next commit failed at 96.30 having changed nothing but a version
#: string. Lowering the baseline by hand did not help either: the next run
#: measured over it and the ratchet staged the high-water mark straight back.
#: **An unstable quantity ratcheted to its maximum converges on its maximum**,
#: and then blocks everything.
#:
#: 0.1 covers the measured spread with room. The cost is honest and worth
#: stating: a real regression smaller than 0.1pp now passes. That is the width
#: of the noise, so it was never actually being detected -- what 0.01 bought
#: was false precision, not sensitivity. `BACKLOG.md` B125 carries the fixes
#: that would let this be tightened again, all of which mean making the
#: measurement deterministic rather than widening the window further.
TOLERANCE = 0.1

PYTEST_ARGS = [
    "pytest",
    "-q",
    "-p",
    "no:cacheprovider",
    "-n",
    "auto",
    "--cov",
    "--cov-report=",
]


def read_baseline() -> float | None:
    if not BASELINE_PATH.exists():
        return None
    text = BASELINE_PATH.read_text().strip()
    return float(text) if text else None


def write_baseline(value: float) -> None:
    """Write the new baseline and stage it, so it travels with the commit.

    `# nosec` here and on `run_tests`: both argv lists are literals defined in
    this file, nothing reaches them from a caller, and `shell=True` is never
    used. B404/B603/B607 are about `subprocess` being *capable* of injection
    rather than about a call that is.
    """
    BASELINE_PATH.write_text(f"{value:.2f}\n")
    subprocess.run(  # nosec B603 B607
        ["git", "add", str(BASELINE_PATH)], cwd=REPO_ROOT, check=False
    )


def run_tests() -> int:
    return subprocess.run(PYTEST_ARGS, cwd=REPO_ROOT, check=False).returncode  # nosec B603


def measure(*, quiet: bool = False) -> float:
    """Total coverage percent from the `.coverage` on disk.

    `quiet` suppresses the per-file table. `--check-rise` asks one question
    and answers it in one line; printing a 90-row report underneath the
    answer buries it, and CI logs are where that matters most.
    """
    import io

    import coverage

    cov = coverage.Coverage(data_file=str(REPO_ROOT / ".coverage"))
    cov.load()
    destination = io.StringIO() if quiet else None
    return cov.report(show_missing=False, skip_covered=True, file=destination)


def check_rise() -> int:
    """Fail if measured coverage has risen clear of the baseline.

    Measures the `.coverage` an earlier run left behind rather than running
    the suite, so this is cheap enough to be a separate CI step.

    Silent when there is no baseline yet: the first green run is what creates
    one, and that is `main`'s job, not this one. Failing here instead would
    make a fresh checkout fail a gate about a file it is supposed to be
    generating.

    `TOLERANCE` is shared with the fall check on purpose. Total coverage is
    not a function of the tree -- `pytest-randomly`, `-n auto` and hypothesis
    all move it -- so a rise check without the same slack would fire on noise
    and demand a baseline commit for a run that measured nothing new.
    """
    baseline = read_baseline()
    if baseline is None:
        print("No baseline yet; nothing to compare a rise against.")
        return 0

    total = measure(quiet=True)
    if total > baseline + TOLERANCE:
        print(
            f"\nCoverage has risen to {total:.2f}%, and {BASELINE_PATH.name} "
            f"still reads {baseline:.2f}%.\n\n"
            f"Run:  uv run python scripts/coverage_ratchet.py\n"
            f"and commit {BASELINE_PATH.name} with this change.\n\n"
            f"The ratchet is meant to follow the work. A floor nobody moves "
            f"is a check you never see fail: coverage drifts up, the baseline "
            f"does not, and a later regression back to {baseline:.2f}% passes "
            f"silently.",
            file=sys.stderr,
        )
        return 1

    print(f"Baseline {baseline:.2f}% is current (measured {total:.2f}%).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-rise",
        action="store_true",
        help="Measure an existing .coverage and fail if the baseline is stale. "
        "Does not run the suite.",
    )
    if parser.parse_args().check_rise:
        return check_rise()

    rc = run_tests()
    if rc != 0:
        print("\nTests failed; coverage ratchet not evaluated.", file=sys.stderr)
        return rc

    total = measure()
    baseline = read_baseline()

    if baseline is None:
        write_baseline(total)
        print(f"\nCoverage baseline initialised at {total:.2f}%.")
        return 0

    if total + TOLERANCE < baseline:
        print(
            f"\nCoverage ratchet: {total:.2f}% is below the baseline of "
            f"{baseline:.2f}%. Add tests or justify the drop by editing "
            f"{BASELINE_PATH.name}.",
            file=sys.stderr,
        )
        return 1

    if total > baseline + TOLERANCE:
        write_baseline(total)
        print(f"\nCoverage ratchet raised: {baseline:.2f}% -> {total:.2f}%.")
    else:
        print(f"\nCoverage held at {total:.2f}% (baseline {baseline:.2f}%).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
