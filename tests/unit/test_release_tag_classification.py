"""The release workflow's tag classifier, executed rather than inspected.

`release.yml` decides four things from a tag — whether it is a prerelease,
which index to publish to, what name the distribution carries there, and the
install line the GitHub release advertises. Those four have to agree, and the
place they can disagree is a shell `if` with three branches that nothing runs
until a tag is pushed.

**The consequences are not symmetric with the usual cost of a CI mistake.**
A wrong `publish_target` uploads a prerelease to the real index; a wrong
`dist_name` fails at upload with a 403 that reads as a publisher
misconfiguration; and PyPI never permits re-uploading a filename, so the
version number is spent either way. This is the rare case where the first
execution of a code path is also the irreversible one -- which is precisely
the argument for executing it here instead.

So this test extracts the actual `run:` block from the workflow and runs it
under `bash` with a `GITHUB_REF` and a scratch `GITHUB_OUTPUT`, then asserts on
what it wrote. Rewriting the logic in Python would test the copy: the two would
be free to drift, and the copy is not what runs on a tag.

The name divergence being pinned here is real and external. `redstring` on
TestPyPI belongs to an unrelated project (`RedString 0.0.1`, a different
account), so the rehearsal publishes as `redstring-test` while the import
package stays `redstring`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml"


def _classifier_script() -> str:
    """The `run:` body of `validate`'s classify step, straight from the file.

    Located by step `id`, not by index: a step inserted above it should not
    silently change what this test executes.
    """
    workflow = yaml.safe_load(WORKFLOW.read_text())
    steps = workflow["jobs"]["validate"]["steps"]
    for step in steps:
        if step.get("id") == "version":
            return str(step["run"])
    raise AssertionError("no step with id 'version' in the validate job")


def _classify(tag: str, tmp_path: Path) -> dict[str, str]:
    """Run the real script for `tag` and return what it wrote to GITHUB_OUTPUT."""
    output = tmp_path / "github_output"
    output.touch()
    result = subprocess.run(
        ["bash", "-c", _classifier_script()],
        env={
            "PATH": "/usr/bin:/bin",
            "GITHUB_REF": f"refs/tags/{tag}",
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"classifier failed for {tag}: {result.stderr}"

    written: dict[str, str] = {}
    for line in output.read_text().splitlines():
        if line:
            key, _, value = line.partition("=")
            written[key] = value
    return written


# Columns, in order: the tag, the index it must publish to, the distribution
# name it must carry there, and whether it is a prerelease.
#
# `rc` is listed before the alpha/beta cases for the same reason the shell
# tests it first, and `0.1.0a1rc1` is here to hold that ordering down: it
# matches both patterns, and swapping the branches would route it to TestPyPI
# under the wrong name. That is the one input where the branch order is
# observable at all.
CASES = [
    ("v0.1.0", "pypi", "redstring", "false"),
    ("v1.2.3", "pypi", "redstring", "false"),
    ("v0.1.0rc1", "pypi", "redstring", "true"),
    ("v0.1.0a1rc1", "pypi", "redstring", "true"),
    ("v0.1.0a1", "testpypi", "redstring-test", "true"),
    ("v0.1.0b2", "testpypi", "redstring-test", "true"),
    ("v0.2.0a10", "testpypi", "redstring-test", "true"),
]


@pytest.mark.parametrize(("tag", "target", "dist_name", "is_prerelease"), CASES)
def test_the_tag_routes_to_the_right_index_under_the_right_name(
    tag: str, target: str, dist_name: str, is_prerelease: str, tmp_path: Path
):
    written = _classify(tag, tmp_path)

    assert written["version"] == tag.removeprefix("v")
    assert written["publish_target"] == target
    assert written["dist_name"] == dist_name
    assert written["is_prerelease"] == is_prerelease


@pytest.mark.parametrize("tag", [case[0] for case in CASES])
def test_every_branch_sets_every_output(tag: str, tmp_path: Path):
    """A branch that forgets one output yields an empty string, not an error.

    GitHub Actions resolves an unset output to `''` and carries on, so a
    missing `dist_name` would reach `uv pip install "==0.1.0a1"` rather than
    failing where it was introduced.
    """
    written = _classify(tag, tmp_path)

    assert set(written) == {
        "version",
        "is_prerelease",
        "publish_target",
        "dist_name",
        "install_cmd",
    }
    assert all(value for value in written.values()), written


@pytest.mark.parametrize("tag", [case[0] for case in CASES])
def test_the_advertised_install_command_matches_the_index_it_published_to(tag: str, tmp_path: Path):
    """The release note's install line has to name the right project and index.

    Checked against `dist_name`/`publish_target` rather than against a literal,
    because the failure this guards is the *two* drifting apart -- an assertion
    written as its own literal would keep passing while the workflow published
    somewhere else.
    """
    written = _classify(tag, tmp_path)
    install = written["install_cmd"]

    assert f"{written['dist_name']}=={written['version']}" in install

    if written["publish_target"] == "testpypi":
        assert "test.pypi.org/simple" in install, install
        # TestPyPI does not mirror PyPI, so an install from it alone cannot
        # resolve `pydantic`. Same reasoning as the `verify` job's indexes.
        assert "--extra-index-url https://pypi.org/simple/" in install, install
    else:
        assert "test.pypi.org" not in install, install
        assert install == f"pip install redstring=={written['version']}"


def test_a_pypi_tag_never_advertises_the_rehearsal_package(tmp_path: Path):
    """`redstring-test` must not escape onto the real index's release notes.

    Stated separately from the parametrised check above because it is the
    claim that would survive someone "simplifying" the two branches into one
    shared `install_cmd`.
    """
    for tag, target, _, _ in CASES:
        written = _classify(tag, tmp_path)
        if target == "pypi":
            assert "redstring-test" not in written["install_cmd"], tag
