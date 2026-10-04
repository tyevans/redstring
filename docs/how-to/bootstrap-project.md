# How-To: Bootstrap a Project with SpecOps PMaC

This guide demonstrates how to initialize or adopt the **SpecOps** Project Management as Code (PMaC) framework in a project, establishing version-locked specifications, Diataxis documentation, and continuous health gates.

---

## 1. Initializing a Greenfield Project

To bootstrap a brand-new project with opinionated PMaC invariants, Diataxis documentation, and pre-commit hooks:

```bash
uv run spec-ops init --name "MyService" --diataxis --pre-commit
```

This scaffolds:
- `docs/project/`: Personas, PRD lifecycle stages (`idea/`, `shaped/`, `accepted/`, `shipped/`), Gherkin user stories, ADR registry, and priority backlog.
- `docs/`: 4-quadrant Diataxis documentation (`tutorials/`, `how-to/`, `reference/`, `explanation/`).
- `AGENTS.md`: Operating constitution defining hard invariants, Definition of Ready (DoR), and Definition of Done (DoD).
- Git pre-commit hooks enforcing file limits and secret scanners.

---

## 2. Adopting SpecOps in a Brownfield Codebase

If you have an existing codebase with existing source files that exceed the strict file length limit (<500 lines), adopt SpecOps with grandfathered debt tracking:

```bash
uv run spec-ops adopt --grandfather-debt
```

This creates a baseline debt snapshot, allowing existing legacy files to pass health checks while strictly forbidding new files or modifications from exceeding the 500-line threshold.

---

## 3. Synchronizing Constitution & Operating Manual

Ensure that `AGENTS.md` and `docs/operating-manual.md` stay synchronized with repository configuration:

```bash
# Check for configuration drift in CI
uv run spec-ops constitution check

# Re-synchronize operating manual from constitution
uv run spec-ops constitution sync
```

---

## 4. Setting Up Continuous Health Gates

Verify system health at any time:

```bash
# Verify file limits, numbering uniqueness, and backlog sync
uv run spec-ops health

# Scan worktrees and diffs for leaked secrets and high-entropy tokens
uv run spec-ops health --security

# Audit bounded context architecture seams
uv run spec-ops architecture seams
```
