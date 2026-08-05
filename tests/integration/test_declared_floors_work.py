"""The lowest permitted `eventsource-py` must actually work.

**CI cannot catch this and never could.** There is no lockfile — deliberately,
so a new release of a dependency breaks the build here rather than at a user —
which means every run resolves to the *newest* version the constraint permits.
The floor is therefore the one point in the range that is never exercised, and
a floor that is too low is invisible by construction.

It was too low. `0.1.0` shipped declaring `eventsource-py>=0.9.1` while
`projections/base.py` forwards `retry_policy` and `tracer` to
`DeclarativeProjection.__init__`, which gained them in 0.10.0. A resolver
picking the low end got `TypeError: unexpected keyword argument 'retry_policy'`
on the first projection it built. **Not at import** — so a smoke test that
imports the package passes, and the failure lands in the caller's code rather
than anywhere near the declaration that caused it. It was reported by a
downstream project (`BACKLOG.md` B70).

So this test installs the floor and *uses* it, which is the only measurement
that means anything — the same reasoning as `test_wheel_contents.py` installing
a built wheel rather than reading `pyproject.toml`, and the same reasoning as
deleting a lint exemption before measuring what it hid.

Marked `integration` for cost rather than infrastructure: it creates a
virtualenv and downloads a package, seconds rather than milliseconds. It needs
no container and no model.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _declared_floor(distribution: str) -> str:
    """The `>=` bound this project declares for `distribution`.

    Parsed from the dependency list rather than from the installed
    environment, because the installed version is precisely what this test is
    not allowed to trust.
    """
    deps = tomllib.loads(PYPROJECT.read_text())["project"]["dependencies"]
    for spec in deps:
        if spec.split(">=")[0].strip().lower().replace("_", "-") == distribution:
            match = re.search(r">=\s*([0-9][^,\s]*)", spec)
            if match is None:
                raise AssertionError(f"{distribution} has no >= bound: {spec!r}")
            return match.group(1)
    raise AssertionError(f"{distribution} is not a declared dependency")


def test_the_declared_eventsource_floor_can_build_a_projection(tmp_path: Path):
    """Install the floor exactly, and construct what the library constructs.

    `GraphProjection` is built with the arguments `StoreProjection.__init__`
    forwards, because forwarding is where the incompatibility lives — a
    default-argument construction would pass against 0.9.1 and prove nothing.
    """
    floor = _declared_floor("eventsource-py")
    venv = tmp_path / "floor"

    subprocess.run(["uv", "venv", str(venv)], check=True, capture_output=True)
    python = venv / "bin" / "python"

    # The project itself, then the floor pinned exactly on top. Installing the
    # project first would resolve eventsource to the newest permitted version,
    # which is the thing being avoided.
    subprocess.run(
        ["uv", "pip", "install", "--python", str(python), str(REPO_ROOT)],
        check=True,
        capture_output=True,
    )
    pinned = subprocess.run(
        ["uv", "pip", "install", "--python", str(python), f"eventsource-py=={floor}"],
        capture_output=True,
        text=True,
    )
    if pinned.returncode != 0:
        pytest.fail(
            f"could not install the declared floor eventsource-py=={floor}:\n{pinned.stderr}"
        )

    script = """
from redstring import GraphProjection, InMemoryGraphStore

# Exactly what StoreProjection.__init__ forwards. Passing none of these would
# construct fine on a version that lacks them, which is the false pass.
GraphProjection(
    store=InMemoryGraphStore(),
    checkpoint_repo=None,
    dlq_repo=None,
    retry_policy=None,
    tracer=None,
    tenant_filter=None,
)
print("constructed")
"""
    result = subprocess.run([str(python), "-c", script], capture_output=True, text=True)

    assert result.returncode == 0, (
        f"the declared floor eventsource-py=={floor} cannot construct a "
        f"projection, so the `>=` bound in pyproject.toml is wrong:\n"
        f"{result.stderr}"
    )
    assert "constructed" in result.stdout


def test_the_floor_parser_finds_a_real_bound():
    """Guard the guard: a parser returning nothing would pass vacuously.

    Same reasoning as the compliance-coverage modules asserting their detector
    finds something — a check over an empty set is indistinguishable from a
    working one.
    """
    floor = _declared_floor("eventsource-py")

    assert re.fullmatch(r"\d+\.\d+(\.\d+)?", floor), floor


def test_the_parser_rejects_a_distribution_that_is_not_declared():
    with pytest.raises(AssertionError, match="not a declared dependency"):
        _declared_floor("a-package-nobody-depends-on")
