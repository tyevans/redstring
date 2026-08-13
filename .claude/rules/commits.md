---
paths:
  - "**/*"
---

# Commit Message Convention

Inferred from this project's git history. Note that this is **not**
conventional commits — there are no `feat:` / `fix:` type prefixes.

## Format

```
<Imperative sentence, capitalised, no trailing period>
```

Examples from history:

```
Add BACKLOG.md and require deferred work to land in it
Fix all 42 pre-existing test failures
Extract knowledge-graph backend from knowledge-graph-mcp
Configure ruff, bandit, coverage ratchet
Apply ruff safe autofixes
```

## Notes

- Imperative mood ("Add", "Fix", "Extract"), not past tense or gerund.
- Sentence case: first word capitalised, rest lowercase unless a proper noun.
- No period at the end.
- The subject line says *what changed*. The body — which is where counts,
  file tables, and survivor lists belong — says what it cost and what you
  learned. See `.claude/rules/recurring-defects.md` §5: a commit message is
  immutable and correctly scoped to a moment in time, so specifics that decay
  live there rather than in an ADR or a doc.

## Scope

Prefer many small commits over one large one. Each `git commit` runs the lint,
type, security and architecture gate (see `CLAUDE.md`) — the suite is no
longer part of it, so run `uv run pytest` yourself before committing — and
small commits keep each hook run fast and keep the failure surface legible
when a gate trips.

Deferred work must land in `BACKLOG.md` **in the same commit that passes it
by** — see `CLAUDE.md`. A commit that defers something and does not touch
`BACKLOG.md` is incomplete.
