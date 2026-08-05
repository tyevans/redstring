#!/usr/bin/env bash
# Sweep for stale references to a retired redstring package.
# Usage: .claude/skills/migrating-modules-to-rings/sweep.sh <pkg>
#   e.g. sweep.sh schemas   (checks for redstring.schemas)
# Run from the repo root.
#
# Exit status is driven by the FATAL checks only:
#   1. import-shaped references (`from redstring.<pkg> ...` /
#      `import redstring.<pkg>`) anywhere except locations that reference
#      the retired path by design:
#        docs/adr/     - ADR bodies are immutable
#        docs/plans/   - live plan artifacts name old paths freely
#        docs/history/ - archived plans are kept unchanged
#        tests/unit/test_public_surface_is_self_contained.py,
#        tests/unit/test_end_to_end_example.py - public-surface guards
#      (ModuleNotFoundError guard tests should use
#       importlib.import_module("...") so they don't trip this check)
#   2. leftover dirs at src/redstring/<pkg> or tests/unit/<pkg> -- even
#      __pycache__-only debris resurrects the old import path as a PEP 420
#      namespace package.
#      tests/integration/ and tests/compliance/ are NOT checked: they are
#      organised by backend and by port contract rather than by layer, so
#      only their imports are repointed.
# Bare path mentions (logger names, prose, comments) are printed for triage
# but are not fatal -- many are intentional.
set -u

if [ $# -ne 1 ]; then
    echo "usage: $0 <pkg>  (the retired top-level package name)" >&2
    exit 2
fi

pkg="$1"
fail=0

# Drop grep hits (file:line:text) whose file is git-ignored: ignored files
# (local scratch, caches) never ship, so a stale reference there is noise.
drop_ignored() {
    while IFS= read -r line; do
        git check-ignore -q "${line%%:*}" || printf '%s\n' "$line"
    done
}

# Denylist, not allowlist: sweep the whole tree and exclude only build
# debris and the by-design locations named in the header.
GREP_EXCLUDES=(
    --exclude-dir=.git --exclude-dir=.venv --exclude-dir=__pycache__
    --exclude-dir=.claude --exclude-dir=node_modules --exclude-dir=.mypy_cache
    --exclude-dir=.ruff_cache --exclude-dir=.pytest_cache --exclude-dir=htmlcov
    --exclude-dir=adr --exclude-dir=plans --exclude-dir=history
    --exclude=test_public_surface_is_self_contained.py
    --exclude=test_end_to_end_example.py
)

echo "== FATAL: import-shaped references to redstring.${pkg} =="
imports=$(grep -rnE "(from|import)[[:space:]]+redstring\.${pkg}\b" . "${GREP_EXCLUDES[@]}" | drop_ignored)
if [ -n "$imports" ]; then
    printf '%s\n' "$imports"
    fail=1
else
    echo "(none)"
fi

echo "== FATAL: leftover dirs at retired paths =="
leftovers=0
for old in "src/redstring/${pkg}" "tests/unit/${pkg}"; do
    if [ -d "$old" ]; then
        echo "$old still exists:"
        find "$old" -not -type d | head -10
        leftovers=1
    fi
done
if [ "$leftovers" -eq 0 ]; then
    echo "(none)"
else
    fail=1
fi

echo "== triage (non-fatal): other mentions of redstring.${pkg} =="
triage=$(grep -rn "redstring\.${pkg}\b" . "${GREP_EXCLUDES[@]}" \
    | grep -vE "(from|import)[[:space:]]+redstring\.${pkg}\b" | drop_ignored)
if [ -n "$triage" ]; then printf '%s\n' "$triage"; else echo "(none)"; fi

if [ "$fail" -ne 0 ]; then
    echo "SWEEP FAILED: fatal findings above" >&2
else
    echo "sweep clean for redstring.${pkg} (triage list may need review)"
fi
exit "$fail"
