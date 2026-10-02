# Library Release Workflows and SpecOps

This document explains how **redstring** executes versioned library releases, how that workflow is and is not supported by **SpecOps** today, and how the two systems interface.

---

## 1. Redstring's Library Release Flow

As an open-source Python library published to PyPI and TestPyPI, `redstring` adheres to strict release invariants governed by [`RELEASING.md`](../RELEASING.md) and automated test gates:

### Dual-Declaration Version Synchronization
A release version is declared in exactly two files:
1. `pyproject.toml`: `[project] version = "X.Y.Z"`
2. `src/redstring/__init__.py`: `__version__ = "X.Y.Z"`

These two declarations are guarded against silent drift by [`tests/unit/test_version_is_declared_once.py`](../../tests/unit/test_version_is_declared_once.py). Any release attempt where these files disagree causes immediate preflight failure.

### Tag-Driven OIDC Publishing Pipeline
Releases are triggered by pushing a versioned git tag (`vX.Y.Z` for stable releases, `vX.Y.Zrc1` for release candidates, or `vX.Y.Za1` for alpha/TestPyPI):

```bash
git tag -s v0.12.0 -m "Release v0.12.0"
git push origin v0.12.0
```

Once pushed, GitHub Actions executes [`.github/workflows/release.yml`](../../.github/workflows/release.yml):
1. **Tag Validation**: Asserts that the pushed git tag matches the version declared in `pyproject.toml` and `__init__.py`.
2. **Preflight Verification**: Executes the full test matrix (`uv run pytest`) across supported Python versions.
3. **Distribution Packaging**: Builds source distributions (`.tar.gz`) and platform wheels (`.whl`).
4. **Clean-Room Smoke Verification**: Installs the newly built wheel into an isolated virtual environment and verifies module imports and basic operations before publishing.
5. **Trusted Publisher (OIDC) Upload**: Minting a short-lived OIDC token to upload artifacts directly to PyPI or TestPyPI without static API tokens.
6. **Post-Publish Verification**: Pulls the published artifact from the real index and builds an end-to-end knowledge graph.

---

## 2. Capabilities Supported by SpecOps Today

SpecOps provides strong project management and release governance tooling through `spec-ops release`:

- **Customer-Facing Release Notes (`spec-ops release notes`)**: Automatically parses completed backlog tasks, closed user stories, and git commit history since the prior milestone to generate structured Markdown changelogs.
- **Delivery Velocity & Cognitive Churn (`spec-ops release velocity`)**: Analyzes burndown velocity, task completion throughput, and code churn heatmaps across bounded contexts.
- **Cryptographic Release Manifest Verification (`spec-ops release verify`)**: Verifies commit signatures, dual-custody authorizations, and tamper-evident Merkle tree digests across delivery artifacts.

---

## 3. Current Limitations & Unsupported Capabilities in SpecOps

While SpecOps excels at tracking milestone progression, it currently lacks native workflows for **versioned library releases**:

1. **Unversioned/Rolling Architecture**:
   - SpecOps itself is an unversioned, rolling tool (anchored to `0.1.0` in `specops.toml`) focused on milestone delivery rather than SemVer artifact distribution.
2. **No Automated Version Bumping**:
   - SpecOps provides no CLI workflow (such as `spec-ops release bump --patch|minor|major`) to advance version numbers, compute SemVer progression, or validate breaking API changes.
3. **No Multi-File Version Synchronization**:
   - SpecOps does not inspect or coordinate synchronized version declarations across language-specific manifests (e.g. `pyproject.toml` and `__init__.py`).
4. **No Git Tag or Publishing Orchestration**:
   - SpecOps provides no native hooks for creating signed release tags, coordinating release branches (`release-X.Y.Z`), or triggering package index publishing workflows (PyPI, npm, crates.io).

---

## 4. Operational Division of Responsibility

Until native library release workflows land upstream in SpecOps, the lifecycle responsibilities are partitioned as follows:

| Lifecycle Phase | Handled By | Responsibility |
|---|---|---|
| **Discovery & Requirements** | SpecOps | PRDs (`docs/project/product/`), Personas, BDD User Stories |
| **Task Breakdown & Slicing** | SpecOps | INVEST tasks in `docs/project/backlog/`, Ready Buffer Curation |
| **Implementation & Preflight** | SpecOps | Isolated worktrees (`.worktrees/`), Frontdoor TDD, Health Gates |
| **Integration & Dual Custody** | SpecOps | Signed commits, Human review sign-off, Queue integration |
| **Changelog Generation** | SpecOps | `spec-ops release notes` summarizes completed work items |
| **Version Progression** | Redstring | Maintainer updates `pyproject.toml` & `src/redstring/__init__.py` |
| **Tagging & Publishing** | Redstring | Git tag push triggers `.github/workflows/release.yml` with OIDC PyPI |

---

## 5. Upstream Roadmap Recommendations for SpecOps

To support versioned libraries and SDKs natively, the following capabilities should be introduced to `spec-ops release`:

- `spec-ops release cut --version <X.Y.Z>`: Interactively validates clean git status, runs test preflights, updates version declarations across configured targets, generates changelog entries, and cuts release branches.
- `[project.versioning]` configuration in `specops.toml`: Allows projects to declare version file targets and enforce consistency gates.
- `spec-ops release tag`: Cryptographically signs and pushes the git tag matching the accepted release manifest.
