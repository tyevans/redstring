"""Hypothesis deadline policy has exactly one declaration site.

`tests/conftest.py` registers a profile with `deadline=None` and loads it. That
is the whole policy, and it is only the whole policy for as long as no
`settings()` decorator names a deadline of its own -- **an explicit value in a
decorator outranks every profile**, silently, for that test alone.

That is not a hypothetical. Before this was consolidated, nineteen decorators
carried `deadline=None` individually and eight did not, and the difference was
not a judgement about those eight; it was whoever wrote them not thinking about
it. The suite therefore enforced a deadline on about a third of its property
tests, which detects nothing systematically and blocks a commit occasionally.

It duly blocked one, with `FlakyFailure: ... this test took 276.11ms, which
exceeded the deadline of 200.00ms, but on a subsequent run it took 1.28 ms` --
first-call cost on a busy machine, reported as a failure naming the interval
properties. `tests/compliance/graph_store.py` carries the same warning about
`max_examples` for the same reason, and this module is that warning made
executable for `deadline`.

Re-adding one is a legitimate thing to want (hunting a specific slowdown). Do
it with `--hypothesis-profile=strict`, which `conftest.py` registers, so it
applies to a run rather than being baked into a file where the next reader
inherits it without knowing.
"""

from __future__ import annotations

import ast
from pathlib import Path

from hypothesis import settings

TESTS = Path(__file__).resolve().parents[1]
CONFTEST = TESTS / "conftest.py"


def _settings_calls_naming_a_deadline() -> list[str]:
    """Every `settings(...)` under `tests/` that passes `deadline`, except the
    conftest that is allowed to.

    Parsed rather than grepped so a `deadline` in prose -- several modules
    explain *why* the policy is what it is -- cannot be mistaken for a setting.
    """
    offenders: list[str] = []
    for path in sorted(TESTS.rglob("*.py")):
        if path == CONFTEST:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name != "settings":
                continue
            if any(kw.arg == "deadline" for kw in node.keywords):
                offenders.append(f"{path.relative_to(TESTS.parent)}:{node.lineno}")
    return offenders


def test_the_profile_is_loaded_and_switches_deadlines_off():
    """The policy itself. `settings.default` reflects the loaded profile, so
    this fails if `conftest.py` stops registering or stops loading it."""
    assert settings.default is not None
    assert settings.default.deadline is None, (
        f"the loaded hypothesis profile has deadline={settings.default.deadline!r}; "
        f"tests/conftest.py should register and load a profile with deadline=None"
    )


def test_the_strict_profile_still_exists():
    """Removing the opt-out would make the trade one-way. It is the only
    documented route back to a timing check, so it should not quietly go."""
    settings.load_profile("strict")
    try:
        assert settings.default.deadline is not None, (
            "the `strict` profile no longer sets a deadline, so there is no way "
            "to run the suite with timing enforced at all"
        )
    finally:
        settings.load_profile("default")


def test_no_test_module_overrides_the_deadline():
    """The guard that matters: an inline `deadline=` outranks the profile for
    that test, so one reappearing makes the policy quietly untrue."""
    offenders = _settings_calls_naming_a_deadline()
    assert not offenders, (
        f"`settings(deadline=...)` outranks every hypothesis profile, so these sites "
        f"opt out of the policy in tests/conftest.py without saying so: {offenders}. "
        f"Use `--hypothesis-profile=strict` for a run instead."
    )


def test_the_detector_can_see_a_settings_call():
    """A checker that parses nothing passes vacuously. This proves the AST walk
    recognises the shape it is looking for, without waiting for a real
    violation to appear in the tree."""
    module = ast.parse("from hypothesis import settings\n@settings(deadline=200)\ndef t(): ...\n")
    found = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "settings"
        and any(kw.arg == "deadline" for kw in node.keywords)
    ]
    assert len(found) == 1, "the detector would not notice an inline deadline"


def test_the_detector_reaches_every_test_tree():
    """`tests/compliance/` holds no `test_*.py` and is never collected, so a
    checker written with pytest's collection in mind would walk straight past
    the shared contract suites -- the one place a deadline would affect every
    adapter at once."""
    scanned = {p.relative_to(TESTS).parts[0] for p in TESTS.rglob("*.py")}
    for required in ("unit", "compliance", "integration"):
        assert required in scanned, f"{required}/ is not being scanned"
