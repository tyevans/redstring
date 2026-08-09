"""Non-Python files this package promises must survive being packaged.

Two of them, and they fail the same way: invisibly, in the *distribution*
only, with a green suite behind them. A source checkout has every file on
disk whether or not the build backend selects it, which is exactly the shape
CLAUDE.md warns about -- an input on which the right implementation and the
wrong one agree. Only a built wheel, installed somewhere that is not the
checkout, can tell them apart.

**The six bundled domain schemas.** `domain_system_prompt("news_journalism")`
is public API and works by reading `extraction/domains/schemas/*.yaml` off
disk. Hatchling includes non-Python files under a listed package by default,
so this *should* work; "should" is what this module replaces. The failure it
guards against is total: a `KeyError` on every domain id, for every installed
user.

**The `py.typed` marker.** `pyproject.toml` claims `Typing :: Typed`, and the
package is checked under `mypy --strict` with no `exclude`. None of that
reaches a downstream caller: PEP 561 says a type checker ignores a
dependency's annotations entirely unless the installed package carries
`py.typed`. Without it every annotation this library exports is invisible,
`redstring` resolves as `Any`, and the classifier is a false claim -- and
nothing in a source checkout, where the tests import from `src/`, can
notice. The irony is on the record: `[[tool.mypy.overrides]]` in
`pyproject.toml` exists because *asyncpg* ships no marker.

Marked `integration` because it builds a wheel and creates a virtualenv --
seconds, not milliseconds, and it needs the network only if `uv` has to fetch
a build backend it has not cached. Run it before a release:

    uv run pytest -m integration tests/integration/test_wheel_contents.py
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[2]

#: What a caller must be able to ask for after `pip install redstring`. The
#: whole bundled set, not one of them: a packaging rule that caught five files
#: and missed the sixth is exactly the partial failure a single-domain check
#: would report as success.
BUNDLED_DOMAINS = (
    "academic_research",
    "business_corporate",
    "encyclopedia_wiki",
    "literature_fiction",
    "news_journalism",
    "technical_documentation",
)

#: Run inside the throwaway environment. Imports only the public API, so it
#: also fails if `__init__` needs something the wheel's dependency metadata
#: does not pull in.
PROBE = """
import sys
from redstring import domain_system_prompt, load_schema_from_string  # noqa: F401
import redstring

# Not the checkout: if the wheel were shadowed by the source tree the whole
# test would be measuring the thing it is trying to avoid measuring.
assert "site-packages" in redstring.__file__, redstring.__file__

for domain in sys.argv[1:]:
    prompt = domain_system_prompt(domain)
    assert prompt.strip(), f"{domain} rendered an empty prompt"
    assert "{entity_descriptions}" not in prompt, f"{domain} did not render"
print("OK")
"""


#: Asserts the PEP 561 marker is present *in the installed package*, which is
#: the only place it does anything. Resolved through `redstring.__file__`
#: rather than by globbing site-packages, so a marker sitting next to some
#: other distribution cannot answer for this one.
TYPED_PROBE = """
import pathlib
import redstring

assert "site-packages" in redstring.__file__, redstring.__file__
marker = pathlib.Path(redstring.__file__).parent / "py.typed"
assert marker.is_file(), (
    f"no py.typed at {marker}; every annotation this package exports is "
    f"invisible to a downstream type checker and `Typing :: Typed` is false"
)
print("OK")
"""


#: Asserts the compliance suites are *in the wheel and importable from it*.
#:
#: The claim `redstring.testing` makes is entirely about the distribution: an
#: adapter author outside this repository installs `redstring[test]` and
#: subclasses `GraphStoreCompliance`. Every test in this repo imports the same
#: classes from `src/`, so a build that dropped the package -- or a
#: `[tool.hatch.build]` change that stopped selecting it -- leaves the whole
#: suite green while the promise is false. This is the `py.typed` lesson
#: applied to a second artifact-only claim: when a claim is about the
#: artifact, only the artifact can falsify it.
#:
#: `pytest` and `hypothesis` are installed into the probe venv because the
#: package imports them at module scope; that is the `test` extra's whole
#: content, and importing without it is covered separately by
#: `test_redstring_imports_without_the_test_extra`.
COMPLIANCE_PROBE = """
import redstring.testing as testing

assert "site-packages" in testing.__file__, testing.__file__

for name in testing.__all__:
    assert hasattr(testing, name), f"{name} is in __all__ and not importable"

# Named explicitly rather than trusted from `__all__`: a build that shipped an
# empty package would satisfy the loop above and nothing else.
from redstring.testing.graph_store import GraphStoreCompliance
from redstring.testing.vector_store import VectorStoreCompliance
from redstring.testing.chunk_store import ChunkStoreCompliance
from redstring.testing.cache import CacheCompliance
from redstring.testing.embedding_provider import EmbeddingProviderCompliance

# The opt-in hook an adapter overrides. If this stops existing, the documented
# two-line subclass in the how-to stops working.
assert hasattr(GraphStoreCompliance, "new_store")
print("OK")
"""

#: The other half, and the one a passing `COMPLIANCE_PROBE` cannot give:
#: `import redstring` must not drag in `pytest`. The compliance suites are the
#: only modules in the package that import a test library, and if anything
#: else grows such an import, every consumer who did not install
#: `redstring[test]` gets an `ImportError` from a library they installed for
#: knowledge graphs.
#:
#: `tests/unit/test_dependencies_stay_confined.py` checks this by parsing the
#: source. This checks it by *running* it in an environment where the libraries
#: genuinely are not installed, which is the only way to catch an import
#: reached at runtime rather than at module scope.
NO_TEST_DEPS_PROBE = """
import sys
import redstring

assert "site-packages" in redstring.__file__, redstring.__file__
leaked = sorted(name for name in sys.modules if name.split(".")[0] in {"pytest", "hypothesis"})
assert not leaked, f"importing redstring pulled in {leaked}"
print("OK")
"""


@pytest.fixture(scope="module")
def installed_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build a wheel, install it into a throwaway venv, return that venv's python.

    Module-scoped: building and installing costs seconds, and every test here
    asks a different question about the *same* artifact. Two builds would also
    let the tests disagree about which wheel they were describing.
    """
    tmp_path = tmp_path_factory.mktemp("wheel")
    build = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=PROJECT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, f"uv build failed:\n{build.stderr}"

    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {[w.name for w in wheels]}"

    venv = tmp_path / "venv"
    python = venv / "bin" / "python"
    for command in (
        ["uv", "venv", str(venv)],
        ["uv", "pip", "install", "--python", str(python), str(wheels[0])],
    ):
        step = subprocess.run(command, capture_output=True, text=True, check=False)
        assert step.returncode == 0, f"{' '.join(command[:2])} failed:\n{step.stderr}"

    return python


@pytest.fixture(scope="module")
def installed_wheel_with_test_extra(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The same wheel, installed as `redstring[test]`, in its own venv.

    A second venv rather than a second install into the first: the point of
    `installed_wheel` is that `pytest` and `hypothesis` are *absent* there, and
    `test_redstring_imports_without_the_test_extra` is only meaningful while
    that stays true. Installing the extra into it would quietly retire that
    test, which is the kind of thing nothing would report.
    """
    tmp_path = tmp_path_factory.mktemp("wheel-test-extra")
    build = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=PROJECT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert build.returncode == 0, f"uv build failed:\n{build.stderr}"

    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {[w.name for w in wheels]}"

    venv = tmp_path / "venv"
    python = venv / "bin" / "python"
    for command in (
        ["uv", "venv", str(venv)],
        ["uv", "pip", "install", "--python", str(python), f"{wheels[0]}[test]"],
    ):
        step = subprocess.run(command, capture_output=True, text=True, check=False)
        assert step.returncode == 0, f"{' '.join(command[:2])} failed:\n{step.stderr}"

    return python


@pytest.mark.integration
def test_the_compliance_suites_ship_and_import_from_an_installed_wheel(
    installed_wheel_with_test_extra: Path,
) -> None:
    """`redstring.testing` is a promise about the distribution, like `py.typed`.

    Its entire purpose is an adapter written somewhere else, so a build that
    stopped selecting the package would leave every test in this repository
    green -- they all import the same classes from `src/` -- while the
    documented two-line subclass in
    `docs/how-to/implement-a-store-adapter.md` failed for everyone who
    installed the library.
    """
    probe = subprocess.run(
        [str(installed_wheel_with_test_extra), "-c", COMPLIANCE_PROBE],
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0, (
        f"the installed wheel does not carry usable compliance suites:\n"
        f"{probe.stdout}\n{probe.stderr}"
    )


@pytest.mark.integration
def test_redstring_imports_without_the_test_extra(installed_wheel: Path) -> None:
    """The cost of shipping the suites, held to zero.

    `redstring.testing` imports `pytest` and `hypothesis` at module scope and
    neither is a dependency of `redstring`. If any other module in the package
    grew such an import, `import redstring` would raise for every consumer who
    installed it for knowledge graphs and not for testing.
    """
    probe = subprocess.run(
        [str(installed_wheel), "-c", NO_TEST_DEPS_PROBE],
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0, (
        f"importing redstring reached a test dependency:\n{probe.stdout}\n{probe.stderr}"
    )


@pytest.mark.integration
def test_every_bundled_domain_renders_from_an_installed_wheel(installed_wheel: Path) -> None:
    probe = subprocess.run(
        [str(installed_wheel), "-c", PROBE, *BUNDLED_DOMAINS],
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0, (
        f"the installed wheel cannot render its own domain schemas -- they are "
        f"probably not in it:\n{probe.stdout}\n{probe.stderr}"
    )


@pytest.mark.integration
def test_the_installed_wheel_carries_its_py_typed_marker(installed_wheel: Path) -> None:
    """`Typing :: Typed` is a claim about the distribution, not the checkout.

    Every test in this repo imports from `src/`, where annotations are read
    directly and no marker is consulted. A build that dropped `py.typed` would
    leave all of them green while silently downgrading every downstream
    caller's view of this package to `Any`.
    """
    probe = subprocess.run(
        [str(installed_wheel), "-c", TYPED_PROBE],
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0, (
        f"the installed wheel has no PEP 561 marker:\n{probe.stdout}\n{probe.stderr}"
    )
