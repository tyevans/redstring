"""`verify` retries the install rather than sleeping at it.

**This exists because v0.2.0 failed here after publishing successfully.** Both
files uploaded `200 OK`; the step slept 60 seconds, ran one `uv pip install`,
and got "there is no version of redstring==0.2.0". Nothing was wrong with the
artifact -- it installed and passed this same smoke test by hand minutes later.

That failure shape is the expensive one: it happens *after* an irreversible
publish, on the one job whose purpose is to say whether the publish was good,
and it reads as a broken release when nothing is broken. PyPI never permits
reusing a filename, so the obvious response -- re-run the workflow -- cannot
succeed on that version, which is how a green pipeline gets abandoned as
flaky.

The tests run the **real `run:` body** out of `release.yml` under `bash`, with
`uv` and `sleep` stubbed as executables first on `PATH`. That is the same
technique as `tests/unit/test_release_requires_protected_branch.py`, and for
the same reason: a test that reimplements the shell it is checking proves only
that two scripts agree.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml"


def _install_script() -> str:
    """The `run:` body of `verify`'s install step, straight from the file.

    Located by step `id` rather than by index, so inserting a step above it
    does not silently change what these tests execute.
    """
    workflow = yaml.safe_load(WORKFLOW.read_text())
    for step in workflow["jobs"]["verify"]["steps"]:
        if step.get("id") == "install":
            return str(step["run"])
    raise AssertionError("no step with id 'install' in the verify job")


def _stub_uv(directory: Path, *, fail_installs: int) -> Path:
    """A `uv` that fails `fail_installs` installs before succeeding.

    `fail_installs` of a large number never succeeds. `venv` always works --
    the failure being modelled is resolution, not environment creation.

    Every invocation is appended to `calls.log`, which is how the assertions
    below check *what* was passed rather than only how many times.
    """
    directory.mkdir(parents=True, exist_ok=True)
    counter = directory / "install_count"
    counter.write_text("0")

    uv = directory / "uv"
    uv.write_text(
        f"""#!/usr/bin/env bash
echo "uv $*" >> {directory}/calls.log
if [[ "$1" == "venv" ]]; then
  exit 0
fi
n=$(cat {counter})
n=$((n + 1))
echo "$n" > {counter}
if [[ "$n" -le {fail_installs} ]]; then
  echo "  x No solution found when resolving dependencies:"
  echo "  because there is no version of redstring==9.9.9"
  exit 1
fi
echo "Installed redstring==9.9.9"
exit 0
"""
    )
    uv.chmod(0o755)

    # Real sleeps would make the exhaustion case take ten minutes. Stubbing it
    # also records that the loop *does* back off between attempts, which a
    # tight retry loop would not.
    sleep = directory / "sleep"
    sleep.write_text(f'#!/usr/bin/env bash\necho "sleep $*" >> {directory}/calls.log\nexit 0\n')
    sleep.chmod(0o755)

    return directory


def _run(tmp_path: Path, *, fail_installs: int) -> subprocess.CompletedProcess[str]:
    stubs = _stub_uv(tmp_path / "bin", fail_installs=fail_installs)
    return subprocess.run(
        ["bash", "-c", _install_script()],
        env={
            "PATH": f"{stubs}:/usr/bin:/bin",
            "TARGET": "pypi",
            "VERSION": "9.9.9",
            "DIST_NAME": "redstring",
            "HOME": str(tmp_path),
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "bin" / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


def _loop_succeeded(result: subprocess.CompletedProcess[str]) -> bool:
    """Whether the retry loop resolved the package, by its own success line.

    **Deliberately not the step's exit code.** The step goes on to run a smoke
    test through `/tmp/verify/bin/python`, which a stubbed `uv` never creates,
    so the step always exits non-zero here regardless of the loop. Fabricating
    an interpreter at that path is not an option worth taking: it is hardcoded
    in the workflow, so every xdist worker would race the same file.

    What the smoke test does is covered where it can be covered honestly --
    the live `verify` job against the real index, and
    `tests/integration/test_wheel_contents.py` against a built wheel. This
    file is about the loop.
    """
    return "on attempt" in result.stdout


def test_a_first_time_success_installs_once_and_does_not_wait(tmp_path: Path):
    """The common case must not pay the retry budget.

    Pinned because the natural way to write a retry loop -- sleep, then try --
    makes every release wait for a delay only the unlucky ones need.
    """
    result = _run(tmp_path, fail_installs=0)

    assert _loop_succeeded(result), result.stdout + result.stderr
    installs = [call for call in _calls(tmp_path) if "pip install" in call]
    assert len(installs) == 1, installs
    assert not [call for call in _calls(tmp_path) if call.startswith("sleep")]


def test_an_index_that_is_slow_to_serve_is_retried_until_it_does(tmp_path: Path):
    """The v0.2.0 case exactly: publish worked, the index lagged.

    Two failures then success, which is the shape a fixed `sleep 60` cannot
    express -- it either waits long enough or does not, and the right number
    is a property of someone else's CDN on the day.
    """
    result = _run(tmp_path, fail_installs=2)

    assert _loop_succeeded(result), result.stdout + result.stderr
    installs = [call for call in _calls(tmp_path) if "pip install" in call]
    assert len(installs) == 3, installs
    assert "on attempt 3" in result.stdout
    assert len([call for call in _calls(tmp_path) if call.startswith("sleep")]) == 2


def test_it_gives_up_rather_than_retrying_forever(tmp_path: Path):
    """Bounded, per CLAUDE.md: a step that hangs reads as infrastructure
    trouble and gets retried instead of investigated.

    The bound is asserted as "more than one and fewer than fifty" rather than
    as the exact number, so tuning the budget does not break this test while
    removing the bound entirely still does.
    """
    result = _run(tmp_path, fail_installs=10_000)

    assert result.returncode != 0
    installs = [call for call in _calls(tmp_path) if "pip install" in call]
    assert 1 < len(installs) < 50, installs


def test_the_failure_message_says_the_publish_may_still_have_worked(tmp_path: Path):
    """The whole point of the fix, and the part a retry loop alone misses.

    Whoever reads this failure has an artifact already on PyPI and no way to
    republish that version. The message has to point at the index rather than
    at the package, or the reasonable response is to assume the release is
    broken and re-run a workflow that cannot succeed twice.
    """
    result = _run(tmp_path, fail_installs=10_000)
    combined = result.stdout + result.stderr

    assert "::error::" in combined
    assert "published" in combined
    assert "pypi.org/simple/redstring" in combined


def test_the_install_refreshes_the_cached_index_entry(tmp_path: Path):
    """The other cause with the same symptom, which time cannot fix.

    `UV_CACHE_DIR` is restored by `setup-uv` from an earlier run that may have
    looked this distribution up before the new version existed. A cached
    "no such version" does not expire because you waited, so a retry loop
    without this flag would still fail forever -- and identically.
    """
    _run(tmp_path, fail_installs=0)

    installs = [call for call in _calls(tmp_path) if "pip install" in call]
    assert installs
    assert all("--refresh-package redstring" in call for call in installs), installs


def test_the_stub_can_fail(tmp_path: Path):
    """Guards the harness: every assertion above rests on the stub being able
    to report both outcomes, and a stub stuck on success would make the
    exhaustion tests vacuous rather than failing."""
    stubs = _stub_uv(tmp_path / "bin", fail_installs=1)
    env = {**os.environ, "PATH": f"{stubs}:/usr/bin:/bin"}

    install = ["uv", "pip", "install", "x"]
    first = subprocess.run(install, env=env, capture_output=True, check=False)
    second = subprocess.run(install, env=env, capture_output=True, check=False)

    assert first.returncode == 1
    assert second.returncode == 0
