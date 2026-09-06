"""The four dotted paths `redstring/__init__.py` promises will not move.

`__all__` is the whole public API and everything reached by a dotted path is
internal -- ADR 0006, and it stands. The trouble is that the adapters anyone
actually deploys are all on dotted paths, while the only providers in
`__all__` are `FakeLlmProvider` and `FakeEmbeddingProvider`. So the supported
surface was the one nobody ships, and the README's quickstart imported from a
path the package described as liable to change without notice.

They are deliberately not exported: `import redstring` would then pull in
LangChain, neo4j and asyncpg, and the extras exist so a caller pays only for
the backends they use. What is fixed instead is the *path*, and this module is
what fixes it. A rename now fails here and becomes a decision someone makes in
review, rather than a silent break in every consumer's imports.

**Each path is imported, not merely spelled.** `importlib` resolving a module
proves nothing about the attribute a caller writes at the end of the line; a
test over strings would pass against a renamed class in a module that still
exists, which is the more likely half of this to happen.

The optional dependency is *not* required to run this. Every adapter here is
importable without its backend installed -- the guarded import raises only
when the class is constructed -- so this suite belongs in the default gate
rather than behind `-m integration`. If that ever stops being true, the fix is
the adapter's import guard, not a skip here.
"""

from __future__ import annotations

import importlib

import pytest

#: Module path, attribute, and the extra a caller installs to use it. Kept
#: beside the promise in `redstring/__init__.py`; a change to one without the
#: other is what `test_the_module_docstring_names_every_path` catches.
DEPLOYED_ADAPTERS = [
    ("redstring.graph.adapters.neo4j", "Neo4jGraphStore", "neo4j"),
    ("redstring.vector.adapters.pgvector", "PgVectorStore", "pgvector"),
    ("redstring.llm.adapters.langchain", "LangChainLlmProvider", "llm"),
    ("redstring.llm.adapters.langchain_embedding", "LangChainEmbeddingProvider", "llm"),
]


@pytest.mark.parametrize(("module_path", "attribute", "extra"), DEPLOYED_ADAPTERS)
def test_a_deployed_adapter_path_still_resolves(
    module_path: str, attribute: str, extra: str
) -> None:
    module = importlib.import_module(module_path)

    assert hasattr(module, attribute), (
        f"{module_path}.{attribute} no longer resolves. `redstring/__init__.py` "
        f"promises this import path is stable, so moving or renaming it is a "
        f"major version and a changelog note -- or update the promise and this "
        f"test together, deliberately. Callers install it as "
        f"`redstring[{extra}]`."
    )
    assert isinstance(getattr(module, attribute), type)


def test_the_module_docstring_names_every_path() -> None:
    """The promise and this list are two declaration sites; keep them equal.

    `redstring/__init__.py` states the four paths in prose, because that is
    where a caller reads them. This module states them as data, because that
    is what can be executed. Two sites for one fact is `recurring-defects.md`
    section 2, and the mitigation it asks for is a test that fails when they
    disagree -- so a fifth adapter added here without a word in the docstring
    fails, and so does a path quietly dropped from the docstring.
    """
    import redstring

    documented = redstring.__doc__ or ""
    for module_path, attribute, _ in DEPLOYED_ADAPTERS:
        assert f"{module_path}.{attribute}" in documented, (
            f"{module_path}.{attribute} is promised here but not named in "
            f"`redstring/__init__.py`'s docstring, where a caller would look."
        )


def test_the_promise_is_not_silently_empty() -> None:
    """Guard the guard: a parametrised suite over an empty list passes.

    Same reasoning as the compliance coverage gates -- a checker that finds
    nothing is indistinguishable from one that works.
    """
    assert len(DEPLOYED_ADAPTERS) == 4
