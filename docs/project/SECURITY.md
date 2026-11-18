# Security Policy and Vulnerability Disclosure

## 1. How to report a vulnerability

The policy is in the root [`SECURITY.md`](../../SECURITY.md). GitHub shows that file on the repository's Security tab. Use it to report a vulnerability. Do not use this file for that.

Use GitHub private vulnerability reporting. Do not open a public issue, pull request or discussion.

## 2. Why this file is short

This file had placeholder contacts (`security@example.com`) and a PGP fingerprint that was not a real key. A reporter who used them could not send or encrypt a report. One policy in one place prevents that.

## 3. Supply-chain controls

- ADR-0009: `uv.lock` is committed. CI installs with `uv sync --locked`, and pre-commit runs `uv lock --check`.
- ADR-0010: CI runs gitleaks over the full history (`ci.yml`, job `secrets`). Pre-commit runs gitleaks over the staged changes, at the same version, and `detect-private-key`.
- Every GitHub Action and remote pre-commit hook is pinned to a commit SHA.

## 4. Autonomous Worker Execution Restrictions

Autonomous coding agents executing in this repository must operate under zero-trust constraints:
1. Forbids agents from hardcoding credentials, API keys, or secrets.
2. Forbids agents from modifying unapproved lockfiles.
3. Forbids agents from executing non-allowlisted shell commands.
