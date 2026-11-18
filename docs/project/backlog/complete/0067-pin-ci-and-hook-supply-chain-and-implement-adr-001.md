---
id: '0067'
title: Pin CI and hook supply chain and implement ADR-0010 secret scanning
status: Complete
governing_adrs:
- ADR-0009
- ADR-0010
governing_prds:
- PRD-0013
governing_stories:
- US-0030
target_bc: architecture
---

# TASK-0067: Pin CI and hook supply chain and implement ADR-0010 secret scanning

## Summary
Pin every CI action and remote hook to a commit SHA, remove the unpinned remote tool run, and implement the secret scanning that ADR-0010 requires.

## Problem Statement & Context
Found by the 2026-11-17 security sweep (CI and supply-chain review).

- `.github/workflows/deploy-pages.yml:40` runs `uv tool run --from git+https://github.com/tyevans/spec-ops.git` from the moving default branch, in a job that has `pages: write` and `id-token: write`. `deploy-pages.yml:26,43` use tag-pinned actions, and the checkout keeps credentials.
- `.pre-commit-config.yaml:6` pins `pre-commit-hooks` to a tag.
- ADR-0010 requires secret scanning in pre-commit and CI. No hook and no job does it.
- `.github/workflows/release.yml` puts `${{ steps.version.outputs.version }}` straight into `run:` scripts at five places, and the release-branch check accepts any protected branch, not only `main`.
- GitHub release assets have no build provenance attestation.
- `docs/project/SECURITY.md` has placeholder contacts (`security@example.com`) and a fake PGP fingerprint, and it disagrees with the root `SECURITY.md`.
- ADR-0009 requires `uv lock --check`, but `uv.lock` was gitignored, so CI resolved a new transitive set on each run. Decision (maintainer, 2026-11-18): commit the lock.

Not changed, on purpose: the review also asked that the release check accept only `main`. `release.yml` documents why it accepts any *protected* branch (maintenance releases after `main` moves on), and today `main` is the only protected branch. That reasoning stands.

## Definition of Done (Blackbox Frontdoor TDD)
1. `grep -E "uses: [^@]+@v[0-9]" .github/workflows/*.yml` finds no match, and every `git+https` tool reference has a commit SHA.
2. Every checkout sets `persist-credentials: false`, and `id-token: write` is held only by jobs that need it.
3. `.pre-commit-config.yaml` remote `rev:` values are commit SHAs with the tag in a comment.
4. A gitleaks job runs in CI over the full history, and `detect-private-key` runs in pre-commit. The local gitleaks hook is BACKLOG B166 (it needs maintainer approval to run a downloaded binary on each commit).
5. `release.yml` reads step outputs through `env:` only, and the tag is checked against an anchored version pattern before it becomes an output.
6. The release job attests build provenance for `dist/*`.
7. `docs/project/SECURITY.md` points to the root `SECURITY.md` and has no placeholder contact.
8. `uv.lock` is committed; CI installs with `uv sync --locked`; pre-commit runs `uv lock --check`.
