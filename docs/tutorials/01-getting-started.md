# Tutorial: Contributor Onboarding & SpecOps Workflow

Welcome to **redstring**! This tutorial walks new human contributors and autonomous AI agents through setting up a complete development environment, verifying codebase health invariants, and executing your first feature cycle using the **SpecOps** Project Management as Code (PMaC) framework.

---

## Prerequisites

Before beginning, ensure your system has:

- **Python 3.13+** (enforced by `requires-python` in `pyproject.toml`)
- **UV Package Manager** (0.6.0+ recommended):
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
- **Git** (2.34+ with worktree support)
- **Docker & Docker Compose** (optional, required only for running integration tests against live Neo4j and PostgreSQL/pgvector)

---

## Step 1: Clone and Set Up Workspace

Clone the repository and install all dependencies including optional extras and development tooling:

```bash
git clone https://github.com/tyevans/redstring.git
cd redstring

# Always sync with --all-extras to ensure all optional adapters and tools are present
uv sync --all-extras

# Install native pre-commit quality gate hooks
uv run pre-commit install
```

!!! tip "Why `--all-extras` matters"
    The `dev` extra alone installs developer tooling but omits adapter backends (`neo4j`, `pgvector`, `langchain`). Omitting them causes test collection errors rather than skipping. Always sync with `--all-extras`.

---

## Step 2: Verify System Health

Run the SpecOps invariant verification scanner to check file length invariants (<500 lines per file), artifact numbering uniqueness, and backlog index synchronization:

```bash
uv run spec-ops health
```

You should see:
```text
=== SpecOps Health Check (redstring) ===
Limit: <500 lines per file
✅ Invariant Met: Zero source files exceed length limit.
✅ Numbering Invariant Met: All ADR, PRD, Task, and User Story IDs are unique.
✅ 0 file limit violations (<500 lines) and 0 constitution drift warnings.
```

---

## Step 3: Run the Test Suite

Run the unit test suite through the UV workspace runner:

```bash
uv run pytest
```

All unit tests exercise public blackbox interfaces (ADR-0003) with zero private mock backdoors. CI enforces a minimum code coverage ratchet (>=96.42%) on every commit.

---

## Step 4: The SpecOps Development Lifecycle

Every task on `redstring` follows a strict, disciplined lifecycle version-locked in git:

```
[PRD / Persona] -> [User Story (BDD)] -> [Task in Refined Queue]
                                                │
                                                ▼
                                    [Isolated Worktree]
                                                │
                                                ▼
                                    [TDD Implementation]
                                                │
                                                ▼
                                    [Preflight Verification]
                                                │
                                                ▼
                                    [Review & Integration]
```

### 1. Inspect the Priority Queue
Check the next ready task in `docs/project/backlog/PRIORITY.md`:

```bash
uv run spec-ops queue next
```

### 2. Spawn an Isolated Worktree
Work is never performed directly on `main` or generic shared branches. Spawn a dedicated git worktree:

```bash
uv run spec-ops worktree start <TASK_ID>
```

This creates `.worktrees/task-<TASK_ID>` checked out to branch `feat/TASK-<TASK_ID>`.

### 3. Implement Using Frontdoor TDD
Inside the worktree:
- Write blackbox tests exercising public domain interfaces or CLI entry points.
- Implement executable Gherkin scenarios for governing BDD user stories (`tests/bdd/`).
- Maintain property-based invariants with Hypothesis (`@given(...)`).
- Keep all touched files under 500 lines (proactively refactor if >=400 lines).

### 4. Verify Quality Gates Before Commit
Before opening a pull request, run preflight checks:

```bash
# 1. SpecOps health & invariants check
uv run spec-ops health

# 2. Test suite & coverage floor
uv run pytest

# 3. Documentation build with strict link checking
uv run mkdocs build --strict

# 4. Lockfile integrity check
uv lock --check
```

### 5. Open Pull Request & Sign Off
Commit using Conventional Commits with SpecOps trailers:

```bash
git commit -m "feat(extraction): add stream parsing support

SpecOps-Task: TASK-0042
SpecOps-Story: US-0005"
```

Push your branch, open a PR on GitHub, and ensure all 7 CI checks pass green. Sign off on the architectural review:

```bash
uv run spec-ops review sign <TASK_ID> --identity "Your Name <you@example.com>"
uv run spec-ops queue complete <TASK_ID>
```

---

## Next Steps

- Consult the [Operating Manual](../operating-manual.md) for full architectural invariants and non-negotiables.
- Review [Contributing Guide](../contributing.md) for testing conventions and layer boundaries.
- Browse [How-to Guides](../how-to/index.md) for specific workflows such as authoring domain schemas or running mutation suites.
