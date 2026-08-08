"""A new collaborator may not quietly take a whole composed port.

ADR 0027 narrowed the four first-party collaborators that were wide, and
`tests/unit/vector/test_capability_segregation.py` and
`tests/unit/consolidation/test_graph_capability_segregation.py` assert the
resulting annotations **by name**. That pins the four that exist and does
nothing about the fifth, which is `BACKLOG.md` B111 and the gap
`tests/unit/graph/test_compliance_coverage.py` closed for isolation tests. The
fix has the same shape: derive the subject list rather than hand-write it.

**Why a type checker cannot do this job.** The configured `uv run mypy` covers
`src/redstring` and not `tests/`, and a *widened* annotation is type-correct by
construction -- passing a `VectorWriter` where `VectorStore` is asked for is the
error, not the reverse. It was measured during 0027: reverting all four
narrowings left `uv run mypy` completely silent. So the only thing that can
report a collaborator taking more authority than it uses is a test that reads
the declaration.

## What counts as a subject

Two kinds of declaration, because the four composed ports arrive by two routes:

- **A class's own `__init__`**, read from `vars(klass)` rather than through
  the MRO. A subclass inheriting a redstring parent's wide constructor is
  covered by the parent's entry, so the finding is reported once and at the
  line that would have to change.
- **A class's generic base arguments.** `GraphProjection` declares no
  `__init__` at all -- it is `StoreProjection[GraphStore]`, and the constructor
  a caller actually calls belongs to eventsource and is annotated `StoreT`.
  Reading only `__init__` would report that class clean while its store is the
  whole port. CLAUDE.md records the same trap in the public-surface gate,
  where a body-only check called `GraphProjection` clean and its inherited
  constructor took five foreign types.
- **Public module-level functions.** `build_graph` and `index_documents` are
  entry points rather than classes, and a new one taking a whole port is the
  same defect.

## How "names a composed port" is detected, and what it cannot see

Annotations are read with `inspect.get_annotations` -- **raw strings, never
evaluated**. Every module here uses `from __future__ import annotations` and
imports its ports under `if TYPE_CHECKING`, so there is frequently nothing to
resolve them against: `typing.get_type_hints` raises `NameError` on exactly the
modules this gate is about, and making it work would mean hand-maintaining a
namespace per module -- a second declaration site for the fact under test.
The string is tokenised into dotted names and reduced to the last segment, so
`VectorStore`, `vector_store.VectorStore` and `VectorStore | None` all match
while `KeyValueCache` does not (a substring test would have called
`CircuitBreaker` a finding).

The alternative was `ast` over the source. It reads the file rather than the
imported object, which sees modules that fail to import -- and cannot resolve a
generic base to the class it parameterises, cannot follow an inherited
constructor, and would need its own import graph to know which `Cache` a name
refers to. Both approaches share the deeper limit, so it was not the deciding
factor.

**Blind spots, stated rather than discovered:**

- **An alias.** `Store: TypeAlias = GraphStore` annotated as `Store` is not
  matched. Nothing in the tree does this; if something starts to, this gate
  goes quiet rather than failing.
- **Module-private functions.** `_embed_entities(vector_store: VectorStore)`
  in `build_graph.py` is not scanned. A private helper is not a collaborator
  anyone annotates against -- it is handed the port by a public entry point in
  its own module, which *is* a subject. The cost is that a private helper
  taking a whole port in a module with no wide public surface would pass.
- **An annotation that is narrow but still wider than the body uses.** This
  gate asks one question -- "is this the whole port?" -- and
  `EntityReader` where `find_entities` alone is called is invisible to it.
  That is ADR 0027's judgement, not a mechanism.
- **Anything not reachable by importing `redstring`.** A module that raises on
  import is skipped by `pkgutil.walk_packages`, which is why
  `test_the_scan_reaches_the_tree` asserts a floor on how many classes were
  examined at all.
"""

from __future__ import annotations

import inspect
import pkgutil
import re
import typing
from importlib import import_module

import pytest

import redstring
from redstring.ports.cache import Cache
from redstring.ports.chunk_store import ChunkStore
from redstring.ports.graph_store import GraphStore
from redstring.ports.vector_store import VectorStore

#: The composed ports, by the name an annotation would spell them with. Taken
#: from the port objects rather than typed as literals, so a rename that leaves
#: this file untouched still moves the gate with it.
COMPOSED_PORTS = frozenset(port.__name__ for port in (GraphStore, VectorStore, ChunkStore, Cache))

#: Subjects allowed to name a whole composed port, each with the argument for
#: why the whole port is the honest annotation. Keyed `module:qualname`.
#:
#: Per `docs/adr/0014-exemption-lists-are-empty-and-must-stay-falsifiable.md`
#: this list is checked in both directions -- an entry naming something that no
#: longer exists, or that has since been narrowed, fails as loudly as a missing
#: entry. An entry is a visible decision; an absent entry is the omission this
#: module exists to catch.
WHOLE_PORT_IS_HONEST: dict[str, str] = {
    "redstring.composition.build_graph:build_graph": (
        "The composition layer's reason to exist. It writes entities and "
        "relationships through GraphProjection, resolves aliases, and hands "
        "the same store to Consolidator -- ADR 0007 places it at the top "
        "precisely because it spans. Its vector store reaches VectorProjection "
        "and its chunk store reaches ChunkProjection, both of which are "
        "themselves narrowed; what the caller supplies at the front door is "
        "their whole corpus and their whole graph."
    ),
    "redstring.composition.build_graph:Consolidator": (
        "Holds a GraphProjection over the same store, which is "
        "StoreProjection[GraphStore] and therefore spans the port by "
        "construction. Narrowing this parameter without narrowing that "
        "projection would only move the widening one line."
    ),
    "redstring.composition.index_documents:index_documents": (
        "The public entry point for the corpus half, taking the caller's "
        "ChunkStore. Its own use is narrower -- ChunkProjection, which ADR "
        "0026 narrowed to ChunkWriter -- so unlike the two above this one is "
        "a candidate for narrowing rather than a settled span; it is tracked "
        "in BACKLOG.md rather than left to be rediscovered here."
    ),
    "redstring.projections.graph:GraphProjection": (
        "Genuinely spans GraphStore: upsert_entities and upsert_relationships "
        "from EntityWriter and RelationshipStore, resolve_entity_ids from "
        "AliasStore, delete_relationship, and the alias writes a merge folds. "
        "Four of the port's five capabilities, and the fifth (TenantPurge) is "
        "declined explicitly with a raise rather than by omission."
    ),
}

#: A dotted name, as an annotation string spells it.
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")


def ports_named(annotation: object) -> set[str]:
    """Composed ports mentioned by `annotation`, which is usually a string.

    Matching is on whole dotted-name tails, never on substrings: `Cache` must
    not match `KeyValueCache`, or every narrowed cache consumer becomes a
    finding and the gate reports the opposite of the truth.
    """
    text = annotation if isinstance(annotation, str) else getattr(annotation, "__name__", "")
    return {
        name.rsplit(".", maxsplit=1)[-1]
        for name in _NAME.findall(str(text))
        if name.rsplit(".", maxsplit=1)[-1] in COMPOSED_PORTS
    }


def _own_init_annotations(klass: type) -> dict[str, object]:
    """`klass`'s *own* constructor annotations, or nothing if it has none."""
    initialiser = vars(klass).get("__init__")
    if not inspect.isfunction(initialiser):
        return {}
    return inspect.get_annotations(initialiser)


def _generic_base_arguments(klass: type) -> dict[str, object]:
    """The arguments `klass` supplies to its generic bases, as annotations.

    `StoreProjection[GraphStore]` binds a type variable the inherited
    constructor is annotated with, so this is the only route by which
    `GraphProjection`'s store is visible at all.
    """
    found: dict[str, object] = {}
    for index, argument in enumerate(
        argument
        for base in getattr(klass, "__orig_bases__", ())
        for argument in typing.get_args(base)
    ):
        found[f"<generic argument {index}>"] = argument
    return found


def _modules() -> list[str]:
    return [
        info.name
        for info in pkgutil.walk_packages(redstring.__path__, prefix="redstring.")
        if not info.ispkg
    ]


def _scan() -> tuple[dict[str, dict[str, set[str]]], int]:
    """Every subject naming a composed port, plus how many were examined.

    The second element is what `test_the_scan_reaches_the_tree` guards: a
    walker that imported nothing would report no findings, which is
    indistinguishable from a tree with none.
    """
    findings: dict[str, dict[str, set[str]]] = {}
    examined = 0
    for module_name in _modules():
        module = import_module(module_name)
        for name, obj in vars(module).items():
            if getattr(obj, "__module__", None) != module_name:
                continue
            if inspect.isclass(obj):
                declarations = _own_init_annotations(obj) | _generic_base_arguments(obj)
            elif inspect.isfunction(obj) and not name.startswith("_"):
                declarations = inspect.get_annotations(obj)
            else:
                continue
            examined += 1
            named = {
                parameter: ports
                for parameter, annotation in declarations.items()
                for ports in [ports_named(annotation)]
                if ports
            }
            if named:
                findings[f"{module_name}:{name}"] = named
    return findings, examined


class TestTheDetectorDetects:
    """Guard the guard. Every assertion below this class is `not findings`,
    which a detector that can find nothing satisfies perfectly."""

    def test_a_whole_port_annotation_is_recognised(self):
        assert ports_named("GraphStore") == {"GraphStore"}
        assert ports_named("VectorStore | None") == {"VectorStore"}
        assert ports_named("vector_store.VectorStore") == {"VectorStore"}
        assert ports_named("Sequence[ChunkStore]") == {"ChunkStore"}

    def test_a_narrowed_annotation_is_not(self):
        """The false positive that would have made this gate useless.

        `Cache` is a substring of `KeyValueCache`, so a naive `in` test reports
        every consumer ADR 0027 *fixed* -- `CircuitBreaker`, `RateLimiter` --
        as a violation, and the honest response to that gate is to delete it.
        """
        for narrow in ("KeyValueCache | None", "HitWindow", "VectorWriter", "EntityReader"):
            assert ports_named(narrow) == set(), narrow

    def test_a_class_declaring_a_port_is_found(self):
        """A live end-to-end check of the class route, against a subject the
        tree does not supply -- so it keeps meaning something after every real
        collaborator has been narrowed."""

        class Wide:
            def __init__(self, store: GraphStore) -> None:
                pass

        assert ports_named(_own_init_annotations(Wide)["store"]) == {"GraphStore"}

    def test_the_generic_base_route_finds_the_projection(self):
        """`GraphProjection` declares no `__init__`. Reading only constructors
        would call it clean while its store is the whole port, which is the
        MRO trap CLAUDE.md records against the public-surface gate."""
        from redstring.projections.graph import GraphProjection

        assert _own_init_annotations(GraphProjection) == {}
        arguments = _generic_base_arguments(GraphProjection)
        assert any(ports_named(argument) == {"GraphStore"} for argument in arguments.values())

    def test_the_scan_reaches_the_tree(self):
        """A walker that imported nothing reports no findings, and so does a
        clean tree. `exhaustive = true` on the import contract is the same
        reasoning (`recurring-defects.md` §3)."""
        findings, examined = _scan()
        assert examined >= 100, f"the scan examined only {examined} subjects"
        assert findings, "the scan found no subject naming a composed port at all"


class TestEveryWideCollaboratorIsAccountedFor:
    def test_no_unexplained_subject_takes_a_whole_port(self):
        findings, _ = _scan()
        unexplained = {
            subject: parameters
            for subject, parameters in findings.items()
            if subject not in WHOLE_PORT_IS_HONEST
        }
        named = {subject: sorted(parameters) for subject, parameters in unexplained.items()}
        assert not unexplained, (
            f"these name a whole composed port: {named}. "
            f"Narrow the annotation to the capability actually used -- "
            f"`ports/graph_store.py` names the capabilities and ADR 0027 the "
            f"reasoning -- or add the subject to WHOLE_PORT_IS_HONEST with the "
            f"argument for why the whole port is the honest annotation. "
            f"A whole port grants authority a collaborator does not use: "
            f"`TenantPurge` exists so that 'this can wipe a tenant' is a "
            f"visible fact about a signature, and it stops being one the "
            f"moment it is granted by default."
        )

    def test_the_exemptions_still_name_something_that_takes_a_whole_port(self):
        """The staleness half, per ADR 0014.

        Two failures wear one message because both mean the same thing -- the
        entry is no longer buying anything. A narrowed subject whose exemption
        survives is the worse of the two: it silently re-permits the widening
        the narrowing removed.
        """
        findings, _ = _scan()
        stale = sorted(set(WHOLE_PORT_IS_HONEST) - set(findings))
        assert not stale, (
            f"WHOLE_PORT_IS_HONEST names subjects that no longer take a whole "
            f"composed port (renamed, deleted, or since narrowed): {stale}. "
            f"Delete the entry -- an exemption matching nothing passes forever "
            f"and stops reporting the thing it was written for."
        )

    def test_every_exemption_carries_a_reason(self):
        for subject, reason in WHOLE_PORT_IS_HONEST.items():
            assert reason.strip(), f"{subject!r} is exempt with no reason given"

    @pytest.mark.parametrize("subject", sorted(WHOLE_PORT_IS_HONEST))
    def test_each_exempt_subject_still_exists(self, subject):
        """Narrower than the staleness test above and worth having separately:
        that one cannot distinguish "deleted" from "narrowed", and only one of
        those is good news."""
        module_name, _, name = subject.partition(":")
        assert hasattr(import_module(module_name), name), f"{subject} is gone"
