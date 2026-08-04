"""The six bundled domain schemas must survive being packaged.

`domain_system_prompt("news_journalism")` is public API, and it works by
reading `extraction/domains/schemas/*.yaml` off disk. In a source checkout
those files are simply there, so every other test in the suite passes whether
or not they are in the distribution -- which is the shape CLAUDE.md warns
about: an input that makes two candidate implementations agree.

Hatchling includes non-Python files under a listed package by default, so this
*should* work. "Should" is what this replaces. A wheel built, installed into a
throwaway environment, and asked for a prompt is the only thing that answers
it, and the failure it guards against is silent and total: a `KeyError` on
every domain id, for every installed user, with the whole suite green.

Marked `integration` because it builds a wheel and creates a virtualenv --
seconds, not milliseconds, and it needs the network only if `uv` has to fetch
a build backend it has not cached. Run it before a release:

    uv run pytest -m integration tests/integration/test_wheel_ships_the_domain_schemas.py
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[2]

#: What a caller must be able to ask for after `pip install kg-builder`. The
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
from kg_builder import domain_system_prompt, load_schema_from_string  # noqa: F401
import kg_builder

# Not the checkout: if the wheel were shadowed by the source tree the whole
# test would be measuring the thing it is trying to avoid measuring.
assert "site-packages" in kg_builder.__file__, kg_builder.__file__

for domain in sys.argv[1:]:
    prompt = domain_system_prompt(domain)
    assert prompt.strip(), f"{domain} rendered an empty prompt"
    assert "{entity_descriptions}" not in prompt, f"{domain} did not render"
print("OK")
"""


@pytest.mark.integration
def test_every_bundled_domain_renders_from_an_installed_wheel(tmp_path: Path) -> None:
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

    probe = subprocess.run(
        [str(python), "-c", PROBE, *BUNDLED_DOMAINS],
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0, (
        f"the installed wheel cannot render its own domain schemas -- they are "
        f"probably not in it:\n{probe.stdout}\n{probe.stderr}"
    )
