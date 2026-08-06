"""An exported name may not mention a type a caller cannot reach.

`redstring.__init__` promises that everything in `__all__` is supported and
everything else is internal. Slice 10's review found four places where the
promise could not be kept: `RefusedCompletionError` is raised by exported code
and was not exported, so `except RefusedCompletionError` needed a dotted path
into an internal module; `build_graph(domain=...)` accepted and *advertised* a
`DomainSchema` that had no public constructor; `Chunker` was the same shape;
and `project`'s entire signature was `eventsource` types with nothing saying
where they came from. (`project` has since gone upstream and off this
surface -- the entries it required went with it, which is
`test_no_documented_foreign_type_is_stale` doing its job.)

Four occurrences is a missing gate, not four mistakes -- the same conclusion
`test_compliance_coverage.py` reached about isolation tests. Ruff's F822 covers
the other direction (an `__all__` entry naming nothing) and is structurally
blind to this one.

## The rule

For every exported name, every identifier in every annotation of its public
signature must be one of:

- **exported itself**, or
- **not a `redstring` type at all**, and then listed in
  `DOCUMENTED_FOREIGN_TYPES` with the reason. Re-exporting another library's
  types under our own name is worse than depending on them openly, but a
  caller still has to be told which package to import from -- so the list is
  the documentation, and adding to it is a decision someone makes in review
  rather than a thing that happens.

## Why this reads source rather than importing

Every module here uses `from __future__ import annotations`, so annotations
are strings, and most of the types in them are imported under
`if TYPE_CHECKING:` and therefore absent from the module's runtime globals.
One thing this cannot see: the constructor a class inherits from a *foreign*
base. `Document(...)` is `AggregateRoot.__init__`, which lives in eventsource,
so no annotation of ours mentions the `StreamId` it takes. That is why
`document_stream` is exported -- reachability there had to be reasoned about
rather than measured.

`GraphProjection` and `VectorProjection` became the second instance when
`StoreProjection` went upstream in `eventsource-py` 0.12.0. Their constructor
is now `StoreProjection.__init__(store, **options: Unpack[ProjectionOptions])`,
which lives in eventsource, so `RetryPolicy`, `Tracer`, `TenantFilter`,
`ProjectionCheckpoints` and `DLQRepository` stopped being mentioned by any
signature of ours and `test_no_documented_foreign_type_is_stale` correctly
struck their entries. That is the check working, not a gap opening: the five
names are no longer *ours* to document, and `ProjectionOptions` -- eventsource's
own name for exactly that option set -- is where a caller reads them. It is
recorded in `redstring.projections`, reasoned about rather than measured, for
the same reason `document_stream` is.

`typing.get_type_hints` raises `NameError` on exactly the modules this matters
most for -- `composition.py` holds all eight of its annotation imports that
way. Parsing the module instead means a `TYPE_CHECKING`-only import is
resolved as readily as a runtime one, which is the point: a caller reading the
signature does not care which kind it was.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil
from contextlib import suppress
from functools import cache
from pathlib import Path

import pytest

import redstring
from redstring.domain.exceptions import RedstringError

SRC = Path(redstring.__file__).resolve().parent

#: Types from other packages that a caller of the public API has to know
#: about, and where each comes from. Keyed by the name as it is *written* in
#: our signatures.
#:
#: This is not an exemption list in the sense CLAUDE.md warns about -- it does
#: not silence a check, it records an answer. An entry that stops appearing in
#: any signature is caught by `test_no_documented_foreign_type_is_stale`.
DOCUMENTED_FOREIGN_TYPES = {
    "StreamId": (
        "eventsource.ports.positions -- what `document_stream` returns and "
        "`Document` is constructed from"
    ),
    "AggregateStore": (
        "eventsource.ports.store -- the log `Consolidator` records merges in, "
        "and the one `undo` reads back"
    ),
    "SnapshotStore": (
        "eventsource.ports.snapshots -- companion to `AggregateStore`; "
        "`Consolidator` needs both or neither"
    ),
}

#: Names that are not types to resolve: builtins, `typing` spellings, and the
#: type variables our own generics introduce.
_NOT_A_TYPE_REFERENCE = frozenset(
    {
        "Any",
        "BaseException",
        "Exception",
        "Callable",
        "ClassVar",
        # `collections.abc`, like its four siblings below. Added when
        # `TemporalQuery` was exported -- it was the first exported signature
        # to say `Collection`, so the omission had never been reachable.
        "Collection",
        "Final",
        "Iterable",
        "Iterator",
        "Literal",
        "Mapping",
        "None",
        "Path",
        "Protocol",
        "Self",
        "Sequence",
        "S",
        "TStore",
        "UUID",
        "bool",
        "bytes",
        "datetime",
        "dict",
        "float",
        "frozenset",
        "int",
        "list",
        "object",
        "set",
        "str",
        "timedelta",
        "tuple",
        "type",
    }
)

EXPORTED = frozenset(redstring.__all__)


@cache
def _module_tree(module_name: str) -> ast.Module:
    path = SRC / (module_name.removeprefix("redstring.").replace(".", "/") + ".py")
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


@cache
def _origins(module_name: str) -> dict[str, str]:
    """Where each name visible in `module_name` comes from.

    Includes `if TYPE_CHECKING:` imports, which is the whole reason this is
    done by reading rather than by `get_type_hints`. Names defined in the
    module itself map to the module itself.
    """
    origins: dict[str, str] = {}
    tree = _module_tree(module_name)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                origins[alias.asname or alias.name] = node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                origins[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            origins[node.name] = module_name
    return origins


def _surface_of(name: str) -> list[tuple[str, str, str]]:
    """(defining module, where, annotation) over `name`'s whole public surface.

    **Base classes count.** `GraphProjection` declares no `__init__`; the one
    a caller actually calls lives on `StoreProjection`, which is not exported
    and whose parameters are five `eventsource` types. A gate that read only
    the class's own body would have reported it clean, which is the shape of
    green that means nothing. So the MRO is walked, and every base defined
    under `redstring` contributes its annotations too.
    """
    obj = getattr(redstring, name)
    if inspect.isfunction(obj):
        module = obj.__module__
        return [(module, where, ann) for where, ann in _annotations_at(module, obj.__name__)]
    if not inspect.isclass(obj):
        return []

    found = []
    for klass in inspect.getmro(obj):
        module = getattr(klass, "__module__", "")
        if not module.startswith("redstring"):
            continue
        for where, ann in _annotations_at(module, klass.__name__):
            found.append((module, where, ann))
    return found


def _annotations_at(module_name: str, qualname: str) -> list[tuple[str, str]]:
    """Every annotation in `qualname`'s public surface, as (where, source)."""
    tree = _module_tree(module_name)
    node = _find(tree, qualname)
    if node is None:
        return []

    found: list[tuple[str, str]] = []

    def take(where: str, annotation: ast.expr | None) -> None:
        if annotation is not None:
            found.append((where, ast.unparse(annotation)))

    def from_function(where: str, fn: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        args = fn.args
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
            if arg is not None:
                take(f"{where}({arg.arg})", arg.annotation)
        take(f"{where} -> ", fn.returns)

    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
        from_function(qualname, node)
    else:
        for child in node.body:
            if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                # Dataclass and pydantic fields: a caller reads these exactly
                # as they read a parameter.
                take(f"{qualname}.{child.target.id}", child.annotation)
            elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) and (
                not child.name.startswith("_") or child.name == "__init__"
            ):
                from_function(f"{qualname}.{child.name}", child)
    return found


def _find(tree: ast.Module, name: str) -> ast.stmt | None:
    for node in tree.body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) and (
            node.name == name
        ):
            return node
    return None


def _identifiers(annotation: str) -> set[str]:
    """The bare names an annotation refers to, minus builtins and typing."""
    try:
        parsed = ast.parse(annotation, mode="eval")
    except SyntaxError:  # pragma: no cover - an annotation that is not an expression
        return set()
    names = set()
    for node in ast.walk(parsed):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            # `module.Type` -- only the leaf is the type.
            names.add(node.attr)
    return names - _NOT_A_TYPE_REFERENCE


def _gated_names() -> list[str]:
    """The exported names that have a signature at all."""
    names = [
        name
        for name in sorted(redstring.__all__)
        if inspect.isclass(getattr(redstring, name)) or inspect.isfunction(getattr(redstring, name))
    ]
    # `__version__` and the `AUTO` sentinel instance are the only two without
    # one. If this ever collapses to a handful, the gate has stopped looking
    # at the surface it claims to.
    assert len(names) > 20, f"expected most of the {len(redstring.__all__)} exports, got {names}"
    return names


GATED_NAMES = _gated_names()


@pytest.mark.parametrize("name", GATED_NAMES)
def test_exported_name_mentions_only_reachable_types(name: str) -> None:
    leaks = []
    for module, where, annotation in _surface_of(name):
        for identifier in _identifiers(annotation):
            if identifier in EXPORTED or identifier in DOCUMENTED_FOREIGN_TYPES:
                continue
            origin = _origins(module).get(identifier, "")
            kind = "internal" if origin.startswith("redstring") else f"foreign ({origin or '?'})"
            leaks.append(f"{where}: {annotation}  ->  {identifier} [{kind}]")

    assert not leaks, (
        f"`{name}` is exported but its signature names types a caller cannot reach:\n  "
        + "\n  ".join(sorted(leaks))
        + f"\n\nEither export the type from `redstring/__init__.py`, or -- if it belongs "
        f"to another package -- add it to DOCUMENTED_FOREIGN_TYPES in {Path(__file__).name} "
        f"with the import path, so a caller is told where to get it."
    )


def test_no_documented_foreign_type_is_stale() -> None:
    """A list that outlives what it describes stops being documentation.

    CLAUDE.md's rule for exemption lists, applied to this one: an entry that
    no signature mentions any more is silently inert, and the next reader
    believes it is load-bearing.
    """
    mentioned: set[str] = set()
    for name in GATED_NAMES:
        for _, _, annotation in _surface_of(name):
            mentioned |= _identifiers(annotation)

    stale = sorted(set(DOCUMENTED_FOREIGN_TYPES) - mentioned)
    assert not stale, (
        f"DOCUMENTED_FOREIGN_TYPES names {stale}, which no exported signature mentions "
        f"any more. Delete the entries."
    )


#: `RedstringError` subclasses that are deliberately not exported, and why.
#:
#: Every one of these is raised only by a capability that is itself not on the
#: public surface. That makes the entry a *pair* -- when the capability is
#: exported, its errors have to be, and the reason string is what tells the
#: next person that.
UNEXPORTED_BECAUSE_THEIR_RAISER_IS = {
    # The four consolidation errors left this dict when `Consolidator` was
    # exported, which is the pairing working as designed: exporting a
    # capability drags its errors onto the surface with it, and the reason
    # string is what told the next person so.
    "CircuitOpen": "redstring.llm.circuit_breaker is middleware, not exported",
    "RateLimitExceeded": "redstring.llm.rate_limiter is middleware, not exported",
}


def _redstring_error_subclasses() -> list[type]:
    """Every `RedstringError` subclass, with the whole package imported first.

    Importing the package is what makes this complete: `__subclasses__` only
    knows about classes whose module has been executed, so a subclass in a
    module `redstring/__init__.py` does not reach would be invisible and the
    check would pass by not looking.
    """
    for module in pkgutil.walk_packages(redstring.__path__, "redstring."):
        with suppress(ImportError):  # optional extras: neo4j, langchain
            importlib.import_module(module.name)

    found: set[type] = set()

    def descend(klass: type) -> None:
        for subclass in klass.__subclasses__():
            found.add(subclass)
            descend(subclass)

    descend(RedstringError)
    assert len(found) > 10, (
        f"expected the whole hierarchy, found {sorted(c.__name__ for c in found)}"
    )
    return sorted(found, key=lambda c: c.__name__)


@pytest.mark.parametrize(
    "error", _redstring_error_subclasses(), ids=lambda c: f"{c.__module__}.{c.__name__}"
)
def test_every_error_is_catchable_from_the_public_surface(error: type) -> None:
    """`RedstringError` promises to be the base of every deliberate error.

    A promise a caller cannot act on is not one. `RefusedCompletionError` was
    the case that made this: its own docstring argues at length that a caller
    "must" distinguish it from `EmptyCompletionError` -- which was exported
    while it was not, so the distinction needed a dotted path into an internal
    module.

    Signature-shaped gates cannot see this. An exception appears nowhere in a
    signature; it appears in a `raise`.
    """
    name = error.__name__
    assert name in EXPORTED or name in UNEXPORTED_BECAUSE_THEIR_RAISER_IS, (
        f"`{name}` ({error.__module__}) is a RedstringError and a caller cannot name it. "
        f"Export it, or add it to UNEXPORTED_BECAUSE_THEIR_RAISER_IS with the capability "
        f"whose export would bring it along."
    )


def test_no_unexported_error_reason_is_stale() -> None:
    live = {error.__name__ for error in _redstring_error_subclasses()}
    stale = sorted(set(UNEXPORTED_BECAUSE_THEIR_RAISER_IS) - live)
    assert not stale, f"UNEXPORTED_BECAUSE_THEIR_RAISER_IS names {stale}, which no longer exist."


def test_an_exported_error_is_not_also_listed_as_unexported() -> None:
    """The two lists must not overlap, or an export is silently excused.

    Without this, exporting an error while leaving its entry in place would
    leave the entry as a false statement that nothing contradicts -- and the
    entry is the only record of *why* something is not exported.
    """
    both = sorted(EXPORTED & set(UNEXPORTED_BECAUSE_THEIR_RAISER_IS))
    assert not both, f"{both} are exported and still listed as deliberately unexported."
