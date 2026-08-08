"""The commit gate only gates if the hook is installed, and nothing checked.

CLAUDE.md's instruction is "do not run ruff, bandit, lint-imports or pytest as
separate steps before committing" -- correct, and it rests entirely on
`.git/hooks/pre-commit` existing. In a clone where `pre-commit install` was
never run, that instruction becomes "do not run the checks", every `git commit`
succeeds unconditionally, and the first signal is CI failing on a pushed
branch.

That is what this module exists for, and it is not hypothetical: eight commits
landed in one session in a clone with no hook, reported as having passed the
gate. `ruff format` was the one that surfaced it, on CI, after the push.

## Why a test rather than a line in the setup instructions

The setup instructions already said to run it. So did CLAUDE.md. This is the
project's own recurring lesson -- a written rule is what failed the first four
times a read method shipped without an isolation test, and the fix each time
was a gate. An uninstalled hook is exactly the "check you have never seen
fail" shape from `.claude/rules/recurring-defects.md` §3, with the twist that
here the check was not merely inert: it was absent, and absence is
indistinguishable from green.

## Why it skips on CI

CI does not use pre-commit. `.github/workflows/ci.yml` runs `ruff check .`,
`ruff format --check .`, `lint-imports`, `bandit -r src/` and pytest as
separate jobs, deliberately -- see its header comment about pinning through
`uv run`. So a hook is neither present nor wanted on a runner, and asserting
one there would fail for a reason that is not a defect.

The skip is therefore a statement about *where the mechanism lives*, not a
weakening: on a developer's machine the hook is the gate, and on CI the
workflow is.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

#: A token from the hook body pre-commit generates (`pre-commit hook-impl
#: --config=...`), chosen because it appears in *no* other hook.
#:
#: The first version of this used `"pre-commit"`, which reads as the obvious
#: marker and cannot fail: git's own `pre-commit.sample` contains that string
#: too, being named after the hook it samples. Copying the sample into place
#: passed the assertion. That is CLAUDE.md's table exactly -- an input on
#: which the right implementation and a useless one agree -- and it was caught
#: only by deliberately breaking the gate to watch it fail, which is why that
#: habit is worth more than the rule it checks.
INSTALLED_MARKER = "hook-impl"

REPO_ROOT = Path(__file__).resolve().parents[2]

INSTALL_COMMAND = "uv sync --all-extras && uv run pre-commit install"


def _git_dir() -> Path | None:
    """The repository's `.git`, or `None` when this is not a working clone.

    A worktree's `.git` is a *file* pointing at the real directory, and this
    project uses worktrees for mutation runs (`scripts/mutation.py`), so the
    file case is reachable rather than theoretical.
    """
    candidate = REPO_ROOT / ".git"
    if candidate.is_dir():
        return candidate
    if candidate.is_file():
        pointer = candidate.read_text().strip()
        if pointer.startswith("gitdir:"):
            resolved = Path(pointer.removeprefix("gitdir:").strip())
            target = resolved if resolved.is_absolute() else REPO_ROOT / resolved
            return target if target.is_dir() else None
    return None


@pytest.mark.skipif(
    os.environ.get("CI") is not None,
    reason="CI runs ruff, bandit, lint-imports and pytest as separate jobs, not via pre-commit",
)
def test_the_pre_commit_hook_is_installed() -> None:
    git_dir = _git_dir()
    if git_dir is None:
        pytest.skip("not a git working clone, so there is no hook to install")

    hook = git_dir / "hooks" / "pre-commit"

    assert hook.exists(), (
        f"{hook} does not exist, so `git commit` runs no checks at all -- "
        f"not ruff, not bandit, not lint-imports, not pytest. Every commit "
        f"will succeed and CI will be the first thing to disagree. Fix with:\n"
        f"    {INSTALL_COMMAND}"
    )
    assert INSTALLED_MARKER in hook.read_text(), (
        f"{hook} exists but is not pre-commit's hook -- git's own sample file "
        f"and a hand-written hook both satisfy mere existence. Fix with:\n"
        f"    {INSTALL_COMMAND}"
    )
