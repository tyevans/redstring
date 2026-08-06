#!/usr/bin/env python
"""Run the test suite under coverage and enforce a one-way coverage ratchet.

The baseline lives in ``.coverage-baseline`` as a single float (percent).
Coverage may never drop below it; when it rises, the baseline rises with it and
the updated file is staged so it travels with the commit that earned it.
"""

from __future__ import annotations

import subprocess  # nosec B404
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO_ROOT / ".coverage-baseline"

# Floating-point slack so an identical run never trips the ratchet.
TOLERANCE = 0.01

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


def measure() -> float:
    import coverage

    cov = coverage.Coverage(data_file=str(REPO_ROOT / ".coverage"))
    cov.load()
    return cov.report(show_missing=False, skip_covered=True)


def main() -> int:
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
