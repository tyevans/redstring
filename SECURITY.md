# Security Policy

## Supported versions

redstring is pre-1.0. Only the latest release receives fixes; there are no
maintained branches for earlier versions.

| Version | Supported |
|---|---|
| 0.1.x | ✅ |
| < 0.1 | ❌ (none exist) |

## Reporting a vulnerability

**Do not open a public issue.**

Use GitHub's private reporting:
[Report a vulnerability](https://github.com/tyevans/redstring/security/advisories/new).
It creates a private advisory only maintainers can see, and it is the fastest
route.

If that is unavailable, email <tyler@poorlythoughtout.com> with `redstring
security` in the subject.

Please include what you have: the version, what an attacker can do, and a
reproduction if you have one. A vague report is still worth sending — a
half-finished one that arrives beats a complete one that does not.

Expect an acknowledgement within a few days. This is a small project without a
staffed security rota, so treat those as intentions rather than an SLA. You
will be credited in the advisory and the changelog unless you ask not to be.

## What is in scope

The library's own behaviour, above all:

- **Tenant isolation.** Every store call takes a `tenant_id`, and a read that
  returns another tenant's entities is the most serious class of bug this
  project can have. The compliance suites assert it for every adapter; a gap
  they miss is very much in scope.
- **Injection through a store adapter.** `PgVectorStore` interpolates one
  value into its SQL — the table name — and validates it against
  `^[a-z_][a-z0-9_]{0,62}$` at construction. Everything a caller supplies
  travels as a `$n` parameter. A way around either is in scope.
- **The supply chain of published artifacts** — anything suggesting a release
  on PyPI does not match this repository.

## What is not a vulnerability

- **Prompt injection through document content.** Documents you pass to
  `build_graph` reach a language model, and a document that manipulates the
  model into extracting false entities is doing something this library cannot
  detect and does not attempt to. Treat extracted output as untrusted data
  derived from untrusted input — the graph records what a model *said*, not
  what is true. This is a property of the problem, not a defect.
- **Costs from a model you configured.** Rate limiting and circuit breaking
  are provided (`redstring.llm`) and are not on by default.
- **Anything requiring an attacker who already runs your code.** Constructor
  arguments are trusted input; a store handed a malicious pool is out of
  scope.

## How releases are secured

Every artifact on PyPI is built and published by
[`.github/workflows/release.yml`](.github/workflows/release.yml) through
**PyPI Trusted Publishing**. There is no API token — the workflow mints a
short-lived OIDC identity that PyPI exchanges for an upload token scoped to
this project and valid for minutes. There is no long-lived credential in this
repository to steal.

Uploads carry [PEP 740](https://peps.python.org/pep-0740/) attestations:
Sigstore-signed statements binding each file to this repository and workflow.
You can check one before installing:

```bash
curl -s https://pypi.org/integrity/redstring/0.1.0/redstring-0.1.0-py3-none-any.whl/provenance | jq .
```

Every file on the PyPI release page should show a **Provenance** badge. One
that does not, for a version claiming to be from here, is worth reporting.

Supporting practices:

- Third-party GitHub Actions are pinned to full commit SHAs, not tags. A tag
  is mutable by whoever owns the action.
- `permissions: contents: read` is the default for every workflow;
  `id-token: write` appears only on the two publish jobs.
- Publishing runs in a GitHub environment, so a required-reviewer rule can
  gate it.
- `bandit` and `pip-audit` run over the whole package on every pull request.
