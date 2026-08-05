# Releasing redstring

Maintainers only. Releases are driven by pushing a tag; everything after that
is `.github/workflows/release.yml`.

## What a tag does

| Tag | Goes to | GitHub release |
|---|---|---|
| `v1.2.3` | PyPI | stable |
| `v1.2.3rc1` | PyPI | pre-release |
| `v1.2.3a1` | TestPyPI | pre-release |
| `v1.2.3b1` | TestPyPI | pre-release |

The workflow validates the tag, runs the whole of CI, builds, smoke-tests the
built wheel in a clean environment, publishes, creates a GitHub release from
the changelog, and then installs the *published* artifact from the real index
and builds a graph with it.

## One-time setup

Nothing below has to be repeated per release, and the first release cannot
happen without it.

### 1. Pending trusted publishers

`redstring` does not exist on PyPI yet, so there is no project to attach a
publisher to. PyPI has a "pending publisher" for exactly this — register it
against the name and it activates on first upload.

At <https://pypi.org/manage/account/publishing/>:

| Field | Value |
|---|---|
| PyPI project name | `redstring` |
| Owner | `tyevans` |
| Repository name | `redstring` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

Repeat at <https://test.pypi.org/manage/account/publishing/> with environment
`testpypi`.

**There is no API token anywhere in this process, and there should never be
one.** The workflow mints a short-lived OIDC identity that PyPI exchanges for
an upload token scoped to this project and valid for minutes. Nothing in the
repository can leak, because nothing is stored.

### 2. GitHub environments

Settings → Environments → New environment, twice: `pypi` and `testpypi`.

PyPI treats the environment as optional and *strongly* recommends it, and the
reason is worth stating plainly: the environment is where a **required
reviewer** rule lives. Without one, anybody who can push a tag can publish;
with one, the publish job waits for a human. Add yourself as a required
reviewer on `pypi` at minimum.

### 3. GitHub Pages

Settings → Pages → Source: **GitHub Actions**. The docs deploy on every push
to `main`.

## Cutting a release

### 1. Update the version

`pyproject.toml`:

```toml
[project]
version = "X.Y.Z"
```

### 2. Update the changelog

Move `[Unreleased]` entries into a new `## [X.Y.Z] - YYYY-MM-DD` section, and
update the comparison links at the bottom.

The workflow **fails before building** if `CHANGELOG.md` has no section for
the tagged version. That is deliberate: a release whose notes say nothing is
worse than a delayed one, and the check costs a re-tag rather than a version
number.

### 3. Commit and push

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "Prepare release X.Y.Z"
git push origin main
```

### 4. Tag

```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

### 5. Watch it

[Actions](https://github.com/tyevans/redstring/actions) → the "Release" run.
If you configured a required reviewer, it will pause before publishing and
wait for you.

Then check the [PyPI project page](https://pypi.org/project/redstring/): each
file should carry a **Provenance** badge linking back to the workflow run that
produced it.

## Before the first stable release, use TestPyPI

The whole pipeline is worth exercising on a version you do not mind burning,
because **PyPI filenames cannot be reused**. A `0.1.0` uploaded with a broken
wheel is not replaceable; it can only be yanked and superseded by `0.1.1`.

```bash
# pyproject.toml: version = "0.1.0a1"
git commit -am "Prepare alpha release 0.1.0a1"
git tag v0.1.0a1
git push origin main v0.1.0a1
```

Then install it the way a user would, from a clean environment:

```bash
uv venv /tmp/t && uv pip install --python /tmp/t/bin/python \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  redstring==0.1.0a1
```

The extra index is not optional: TestPyPI does not mirror the real one, so
without it the install fails resolving `pydantic` rather than telling you
anything about your package.

## Provenance and attestations

Publishing under Trusted Publishing generates [PEP 740](https://peps.python.org/pep-0740/)
attestations automatically — `gh-action-pypi-publish` does it with no
configuration from v1.11 onward. Each file gets a Sigstore-signed statement
binding it to this repository and workflow, using a short-lived key derived
from the OIDC identity rather than a key anybody holds.

**Passing a `password:` to the publish action turns this off**, along with the
tokenless flow, and nothing warns you. If a future change to `release.yml`
introduces a token, it is a downgrade in two ways at once.

Verify a release:

```bash
curl -s https://pypi.org/integrity/redstring/0.1.0/redstring-0.1.0-py3-none-any.whl/provenance | jq .
```

## Versioning

Standard [SemVer](https://semver.org/), against a narrower surface than usual:
**the public API is `redstring.__all__` and nothing else.** A rename inside
`redstring.consolidation.service` is not a breaking change, because nothing
promised it. See
[ADR 0006](docs/adr/0006-the-public-surface-is-gated.md).

Pre-1.0, minor versions may break the exported surface. The changelog says so
when they do.

## When something goes wrong

**Tag does not match `pyproject.toml`.** Nothing was built or published.

```bash
git tag -d vX.Y.Z && git push origin :refs/tags/vX.Y.Z
# fix the version, commit, re-tag
```

**CI fails after the tag is pushed.** Nothing was published — `build` needs
`ci`. Fix on `main`, delete and recreate the tag.

**The upload fails.** Check whether it failed *before* or *after* the files
landed. PyPI rejects a re-upload of an existing filename, so a partial success
means moving to the next patch version rather than retrying.

**A bad release is already on PyPI.** Yank it (this hides it from resolvers
without breaking anyone who pinned it) and publish a fix:

```bash
# PyPI project page -> Manage -> Releases -> Yank
```

Deleting a release is almost always the wrong move: it breaks every lockfile
that references it, and the filename still cannot be reused.

## Hotfixes

```bash
git checkout -b hotfix/X.Y.Z vX.Y.W
# fix, bump the patch version, add a CHANGELOG section
git tag vX.Y.Z && git push origin vX.Y.Z
git checkout main && git merge hotfix/X.Y.Z && git push origin main
```

## If an agent prepares a release

Analysing commits since the last tag, drafting changelog entries and bumping
the version are all reasonable things to hand off. **Creating and pushing the
tag is not.** A tag is the trigger for an irreversible publish, and it should
be a deliberate human action every time.
