# Property Provenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `Entity` a `Provenance` value object carrying `observed_at`, and
rewrite `resolve` to take claims rather than bare values, so that the strategy
today called `LATEST` becomes a question the library can actually answer.

**Architecture:** Five fields move off `Entity` into a new
`domain/provenance.py`. `domain/merge_strategy.py` stops taking bare values and
takes `PropertyClaim`s — a value plus the observation that produced it — which
makes `MOST_RECENTLY_OBSERVED` (renamed from `LATEST`) and `PREFER_MERGED`
implementable. `observed_at` is required and supplied by the caller, never read
from a clock below `composition`.

**Tech Stack:** Python 3.13, pydantic v2, pytest, hypothesis, `uv`.

**Spec:** `docs/superpowers/specs/2026-08-12-property-provenance-design.md` —
read it first; it argues every decision this plan merely executes.

## Global Constraints

- **`uv` manages everything.** Run project-scoped commands through `uv run`.
  Never hand-edit `pyproject.toml` dependency tables. This work adds no
  dependencies.
- **Do not run ruff, bandit, `lint-imports` or pytest as separate pre-commit
  steps.** They run on `git commit`. Write the change, then commit. Running the
  *specific test you just wrote* with `uv run pytest <path> -v` is expected and
  is not what that rule forbids.
- **Deferred work goes in `BACKLOG.md` in the same commit that passes it by.**
  No TODO comments, no notes in commit messages instead.
- **A clean break is sanctioned.** No back-compatibility shims, no deprecation
  aliases, no `LATEST = MOST_RECENTLY_OBSERVED` synonym. There is no persisted
  log to migrate.
- **`observed_at` is required and timezone-aware everywhere.** No `| None`, no
  default, no `datetime.now()` below the `composition` layer.
- **Commit messages:** imperative, capitalised, no trailing period, no
  `feat:`/`fix:` prefix. Body says what it cost and what was learned. End with
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.
- **Layer contract:** `domain` is the bottom layer and may import nothing else
  from `redstring`. `domain/provenance.py` may import `domain/ids.py`,
  `domain/entity.py` (for `ExtractionMethod`) and `domain/json_safety.py` only.

---

### Task 1: `Provenance` value object

**Files:**
- Create: `src/redstring/domain/provenance.py`
- Create: `tests/unit/domain/test_provenance.py`

**Interfaces:**
- Consumes: `ExtractionMethod` from `redstring.domain.entity`; `SourceId` from
  `redstring.domain.ids`; `reject_unstorable_text` from
  `redstring.domain.json_safety`.
- Produces: `Provenance(observed_at, extraction_method, confidence, source_id=None, source_text=None, model=None)`
  — a frozen-by-convention pydantic `BaseModel`. Every later task constructs it.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/domain/test_provenance.py`:

```python
"""`Provenance` holds the invariants that were on `Entity` for want of a home."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from redstring.domain.entity import ExtractionMethod
from redstring.domain.provenance import Provenance

OBSERVED = datetime(2026, 8, 12, 9, 30, tzinfo=UTC)


def test_a_provenance_records_when_and_how_the_claim_was_made() -> None:
    provenance = Provenance(
        observed_at=OBSERVED,
        extraction_method=ExtractionMethod.LLM,
        confidence=0.8,
        model="ollama/qwen3.6-27b-mtp",
    )
    assert provenance.observed_at == OBSERVED
    assert provenance.model == "ollama/qwen3.6-27b-mtp"


def test_a_naive_observed_at_is_refused() -> None:
    """A naive datetime raises `TypeError` only at the moment of comparison,
    which for `MOST_RECENTLY_OBSERVED` is deep inside a merge. Refuse it here.
    """
    with pytest.raises(ValidationError, match="timezone-aware"):
        Provenance(
            observed_at=datetime(2026, 8, 12, 9, 30),  # noqa: DTZ001
            extraction_method=ExtractionMethod.PATTERN,
            confidence=0.5,
        )


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_confidence_outside_the_unit_interval_is_refused(confidence: float) -> None:
    with pytest.raises(ValidationError, match="confidence"):
        Provenance(
            observed_at=OBSERVED,
            extraction_method=ExtractionMethod.PATTERN,
            confidence=confidence,
        )


@pytest.mark.parametrize("confidence", [0.0, 1.0])
def test_the_bounds_of_the_unit_interval_are_allowed(confidence: float) -> None:
    """Pinned as examples rather than left to a range check nobody exercises:
    `0.0 <= x <= 1.0` mutated to `<` at either end passes every interior value.
    """
    assert (
        Provenance(
            observed_at=OBSERVED,
            extraction_method=ExtractionMethod.PATTERN,
            confidence=confidence,
        ).confidence
        == confidence
    )


@pytest.mark.parametrize(
    "method",
    [
        ExtractionMethod.PATTERN,
        ExtractionMethod.SCHEMA_ORG,
        ExtractionMethod.OPEN_GRAPH,
        ExtractionMethod.MANUAL,
    ],
)
def test_a_method_that_invokes_no_model_may_not_name_one(
    method: ExtractionMethod,
) -> None:
    with pytest.raises(ValidationError, match="invokes no model"):
        Provenance(
            observed_at=OBSERVED,
            extraction_method=method,
            confidence=0.5,
            model="ollama/qwen3.6-27b-mtp",
        )


@pytest.mark.parametrize("method", [ExtractionMethod.LLM, ExtractionMethod.HYBRID])
def test_a_model_bearing_method_may_name_one(method: ExtractionMethod) -> None:
    """`HYBRID` is the case worth pinning: pattern-matching *plus* a model is
    precisely where knowing which model contributed matters.
    """
    assert (
        Provenance(
            observed_at=OBSERVED,
            extraction_method=method,
            confidence=0.5,
            model="anthropic/claude-opus-4-20250514",
        ).model
        is not None
    )


def test_unstorable_text_in_a_free_form_field_is_refused() -> None:
    with pytest.raises(ValidationError):
        Provenance(
            observed_at=OBSERVED,
            extraction_method=ExtractionMethod.PATTERN,
            confidence=0.5,
            source_text="before\x00after",
        )
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `uv run pytest tests/unit/domain/test_provenance.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'redstring.domain.provenance'`.

- [ ] **Step 3: Write `src/redstring/domain/provenance.py`**

```python
"""Where a claim about an entity came from, and when.

`Entity` describes a thing in the world; `Provenance` describes the *claiming*.
`name`, `entity_type`, `description`, `properties` and `temporal` are what was
said about the thing. These six fields are who said it, when, how, from where,
and how sure -- and separating them is what lets a merge ask "which of these
competing values was observed most recently" without asking an `Entity` a
question about itself.

## `observed_at` is required, and that is the whole point

The strategy now called `MOST_RECENTLY_OBSERVED` was previously `LATEST` and
raised, because nothing in the library recorded when anything was observed --
not per property, and not per entity either. An optional `observed_at` would
have rebuilt that hole one level down: the strategy would work for some callers
and refuse for others, and no caller could tell which it was going to be until
it ran. Required means the question is answerable by construction.

The cost is that every construction site supplies one and no `DocumentExtracted`
written before this change validates. Both were accepted deliberately; there was
no persisted log to migrate.

## It is not `TemporalExtent`, and the two must not be confused

`TemporalExtent` is *world* time -- when the fact held. `observed_at` is
*record* time -- when this library was told. A document published in 1923 and
extracted today has both, and they answer different questions. Nothing here
infers one from the other. See ADR 0005.

## Why `Relationship` does not get one

Symmetry is tempting and would be wrong. `Relationship` carries `confidence`
and `source_id` but no `extraction_method` and no `model`, so its provenance is
a different shape; sharing this type would mean three fields that are always
absent. The asymmetry is real and BACKLOG B76 tracks the relationship side on
its own terms.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, field_validator, model_validator

from redstring.domain.entity import ExtractionMethod, _MODEL_BEARING_METHODS
from redstring.domain.ids import SourceId
from redstring.domain.json_safety import Passthrough, reject_unstorable_text

if TYPE_CHECKING:
    from datetime import datetime


class Provenance(BaseModel):
    """What observed a claim, when, how, from where, and how sure."""

    observed_at: datetime
    extraction_method: ExtractionMethod
    confidence: float
    source_id: SourceId | None = None
    source_text: str | None = None
    model: str | None = None

    @field_validator("source_id", "source_text", "model")
    @classmethod
    def _reject_unstorable_in_free_form_text(cls, value: Passthrough) -> Passthrough:
        """No field reaching the event log may carry text that cannot be
        stored. See `domain/json_safety.py` for why this raises rather than
        strips, and `domain/entity.py` for why it is listed per field.
        """
        reject_unstorable_text(value, what="provenance field")
        return value

    @field_validator("observed_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        """Naive and aware datetimes raise `TypeError` when compared, and the
        comparison that matters happens inside `resolve`, several layers from
        anything that could say which entity was at fault. Refuse it at
        construction, where the offending value is in hand -- same reasoning as
        `Alias.merged_at`.
        """
        if value.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        return value

    @field_validator("confidence")
    @classmethod
    def _require_confidence_in_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        return value

    @model_validator(mode="after")
    def _reject_model_without_a_model_call(self) -> Provenance:
        """`model` records which model ran, so a method that runs none cannot
        carry one.

        `HYBRID` is permitted alongside `LLM`: a hybrid extraction is
        pattern-matching *plus* a model, and it is precisely the case where
        knowing which model contributed matters. The rule constrains only the
        methods that cannot involve one at all.
        """
        if self.model is not None and self.extraction_method not in _MODEL_BEARING_METHODS:
            raise ValueError(
                f"model must be None for extraction_method "
                f"{self.extraction_method.value!r}, which invokes no model"
            )
        return self
```

If importing the private `_MODEL_BEARING_METHODS` trips a lint rule, rename it
to `MODEL_BEARING_METHODS` in `domain/entity.py` and update its one other
reference — do not duplicate the frozenset, which would be defect shape §2
(one fact, two declaration sites).

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `uv run pytest tests/unit/domain/test_provenance.py -v`
Expected: all pass.

- [ ] **Step 5: Prove the timezone validator is not decorative**

Comment out the `_require_timezone` body's `raise` and re-run. Expected:
`test_a_naive_observed_at_is_refused` fails. Restore it. Do the same for
`_require_confidence_in_range`. A gate you have never watched fail is not yet
evidence — `CLAUDE.md` says so and this project has paid for it.

- [ ] **Step 6: Commit**

```bash
git add src/redstring/domain/provenance.py tests/unit/domain/test_provenance.py
git commit
```

Message subject: `Add Provenance, the value object Entity's five fields wanted`

---

### Task 2: `Entity` adopts `provenance`

**Files:**
- Modify: `src/redstring/domain/entity.py`
- Modify: `src/redstring/domain/preference.py`
- Modify: `tests/unit/domain/test_entity.py`
- Modify: every construction site the compiler and suite name (~23 across
  `src/` and `tests/`, including `src/redstring/testing/strategies.py` and
  `src/redstring/testing/graph_store.py`)

**Interfaces:**
- Consumes: `Provenance` from Task 1.
- Produces: `Entity.provenance: Provenance`. `Entity.confidence`,
  `.extraction_method`, `.model`, `.source_id` and `.source_text` **no longer
  exist**; readers use `entity.provenance.<field>`. Tasks 3–6 depend on this.

- [ ] **Step 1: Move the fields**

In `src/redstring/domain/entity.py`, delete `source_id`, `source_text`,
`extraction_method`, `model` and `confidence` from `Entity`, delete
`_require_confidence_in_range` and `_reject_model_without_a_model_call`, drop
`source_id`, `source_text` and `model` from the `_reject_unstorable_in_free_form_text`
field list, and add:

```python
    provenance: Provenance
```

Update `Entity`'s docstring to say where the five went and why — one paragraph,
pointing at `domain/provenance.py`, in the register of the surrounding prose.
Keep the existing sentences about alias-ness being an edge and there being no
`synced_at`; they are still true and still load-bearing.

`ExtractionMethod` stays in this module. `Provenance` imports it, so
`entity.py` must **not** import `provenance.py` at module scope for the type —
use the import that does not cycle: `provenance.py` imports from `entity.py`,
so `entity.py` importing `Provenance` back is a cycle. Break it by moving
`ExtractionMethod` and `_MODEL_BEARING_METHODS` into `provenance.py` and
re-exporting from `entity.py` **only if** the cycle is real; prefer moving
`ExtractionMethod` to `provenance.py` outright, since it is a property of the
observation. Update every importer of `redstring.domain.entity.ExtractionMethod`
accordingly, and keep `redstring.__all__` exporting it.

- [ ] **Step 2: Run the suite to enumerate the blast radius**

Run: `uv run pytest -x -q 2>&1 | tail -40`
Expected: many failures. This run is the worklist, not a defect.

- [ ] **Step 3: Update `domain/preference.py`**

`preference` reads `entity.confidence`; change to `entity.provenance.confidence`.
The tuple is otherwise unchanged — ADR 0010's order **stands**, and this is a
field read moving, not a new order. Update the docstring's three-group
enumeration: the "fixed by the caller" group now names
`entity.provenance.extraction_method` and `.model`, and the "never populated"
group loses `source_text` to provenance.

- [ ] **Step 4: Update every construction site**

Work through the failures. Each `Entity(...)` gains
`provenance=Provenance(observed_at=..., extraction_method=..., confidence=...)`
and loses the five flattened kwargs.

For test fixtures, use a single explicit `datetime(..., tzinfo=UTC)` constant
per module rather than `datetime.now(UTC)` — a fixture that varies per run
makes `MOST_RECENTLY_OBSERVED` tests non-deterministic.

**In `src/redstring/testing/strategies.py`, `observed_at` must be *drawn*, not
fixed.** A generator that hands every entity the same instant makes every
`observed_at` comparison a tie, and the compliance suites would then pass
against an adapter that dropped the field entirely. Draw from
`st.datetimes(...).map(lambda d: d.replace(tzinfo=UTC))`, as the module already
does at line 86 for temporal bounds.

- [ ] **Step 5: Run the suite until green**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 6: Prove the field survives a round trip through both adapters**

The `GraphStore` compliance suite already round-trips entities. Add to
`src/redstring/testing/graph_store.py` a case asserting `observed_at` comes back
**equal**, with a distinctive non-midnight, non-UTC-offset value:

```python
    async def test_an_entitys_observed_at_survives_a_round_trip(self) -> None:
        """Neo4j stores this as ISO text on purpose -- the driver's
        `neo4j.time.DateTime` round-trip is lossy for offsets, which
        `_alias_row` already documents for `merged_at`. A midnight UTC value
        would round-trip through every wrong implementation too.
        """
```

Use `datetime(2026, 3, 1, 14, 45, 30, tzinfo=timezone(timedelta(hours=-5)))`.
This is the coinciding-bounds lesson in another costume: a value that is
midnight, or UTC, or whole-second-aligned, agrees with implementations that
truncate.

- [ ] **Step 7: Commit**

Message subject: `Move Entity's five provenance fields into Provenance`

Body: name the count of construction sites updated and anything the sweep
surfaced. Counts belong in the commit message, never in an ADR
(`recurring-defects.md` §5).

---

### Task 3: Claims, and the strategies they make answerable

**Files:**
- Modify: `src/redstring/domain/merge_strategy.py`
- Modify: `tests/unit/domain/test_merge_strategy.py`
- Modify: `tests/unit/test_enum_values_are_a_wire_format.py:79-85`

**Interfaces:**
- Consumes: `Provenance` (Task 1), `Entity.provenance` (Task 2), `EntityId`
  from `redstring.domain.ids`.
- Produces:
  - `PropertyClaim(value: Any, provenance: Provenance, origin: EntityId)`
  - `resolve(strategy: PropertyMergeStrategy, claims: Sequence[PropertyClaim]) -> Any`
  - `claims_for(property_name: str, canonical: Entity, others: Sequence[Entity]) -> list[PropertyClaim]`
  - `PropertyMergeStrategy.MOST_RECENTLY_OBSERVED` replaces `.LATEST`
  - `IMPLEMENTED` grows to four members.

- [ ] **Step 1: Write the failing tests**

Replace the body of `tests/unit/domain/test_merge_strategy.py`. Keep the
existing `UNION` cases — that behaviour is unchanged and its tests should not be
rewritten for a signature change (a refactor whose tests need editing is a
behaviour change; here only the *call* shape changes, so adapt the calls and
leave every assertion alone).

Add:

```python
def claim(value: object, *, at: datetime, confidence: float = 0.5) -> PropertyClaim:
    return PropertyClaim(
        value=value,
        provenance=Provenance(
            observed_at=at,
            extraction_method=ExtractionMethod.PATTERN,
            confidence=confidence,
        ),
        origin=EntityId(uuid4()),
    )


EARLY = datetime(2026, 1, 1, tzinfo=UTC)
LATE = datetime(2026, 6, 1, tzinfo=UTC)


def test_most_recently_observed_takes_the_later_claim_when_it_is_not_canonical() -> None:
    """The canonical claim losing is the case that distinguishes this strategy
    from `PREFER_CANONICAL`. A test where the canonical value happens to be the
    most recent cannot tell the two apart.
    """
    result = resolve(
        PropertyMergeStrategy.MOST_RECENTLY_OBSERVED,
        [claim("old", at=EARLY), claim("new", at=LATE)],
    )
    assert result == "new"


def test_most_recently_observed_takes_the_canonical_claim_when_it_is_later() -> None:
    result = resolve(
        PropertyMergeStrategy.MOST_RECENTLY_OBSERVED,
        [claim("new", at=LATE), claim("old", at=EARLY)],
    )
    assert result == "new"


def test_simultaneous_claims_are_broken_by_confidence_not_by_position() -> None:
    """Two entities extracted in one batch share an instant exactly. Without a
    tie-break the winner is decided by arrival order, in a replayable log --
    ADR 0010's rule, applied to a narrower order.
    """
    result = resolve(
        PropertyMergeStrategy.MOST_RECENTLY_OBSERVED,
        [claim("unsure", at=LATE, confidence=0.2), claim("sure", at=LATE, confidence=0.9)],
    )
    assert result == "sure"


def test_claims_agreeing_on_instant_and_confidence_still_have_one_winner() -> None:
    """The third component carries no meaning and exists only so that no two
    distinct claims compare equal. Asserted as determinism across a reordering,
    because *which* one wins is arbitrary and must not be pinned.
    """
    first = claim("a", at=LATE, confidence=0.5)
    second = claim("b", at=LATE, confidence=0.5)
    forwards = resolve(PropertyMergeStrategy.MOST_RECENTLY_OBSERVED, [first, second])
    backwards = resolve(PropertyMergeStrategy.MOST_RECENTLY_OBSERVED, [second, first])
    assert forwards == backwards


def test_prefer_merged_takes_the_first_absorbed_claim() -> None:
    result = resolve(
        PropertyMergeStrategy.PREFER_MERGED,
        [claim("canonical", at=LATE), claim("absorbed", at=EARLY), claim("later", at=EARLY)],
    )
    assert result == "absorbed"


def test_prefer_merged_falls_back_to_canonical_when_nothing_was_absorbed() -> None:
    result = resolve(PropertyMergeStrategy.PREFER_MERGED, [claim("only", at=LATE)])
    assert result == "only"


def test_resolve_refuses_an_empty_claim_list() -> None:
    with pytest.raises(ValueError, match="at least one claim"):
        resolve(PropertyMergeStrategy.PREFER_CANONICAL, [])


def test_deep_merge_still_raises_and_still_names_the_backlog_entry() -> None:
    with pytest.raises(NotImplementedError, match="B28"):
        resolve(PropertyMergeStrategy.DEEP_MERGE, [claim("x", at=LATE)])


def test_claims_for_skips_entities_that_say_nothing_about_the_property() -> None:
    """Silence is not a `None` claim. An entity with no opinion must not be
    able to outvote one with an opinion under MOST_RECENTLY_OBSERVED.
    """
    silent = entity_with(properties={}, at=LATE)
    speaking = entity_with(properties={"role": "engineer"}, at=EARLY)
    claims = claims_for("role", silent, [speaking])
    assert [c.value for c in claims] == ["engineer"]


def test_claims_for_keeps_an_explicit_none_which_is_not_silence() -> None:
    speaking = entity_with(properties={"role": None}, at=LATE)
    assert len(claims_for("role", speaking, [])) == 1


def test_claims_for_returns_nothing_when_nobody_claims_the_property() -> None:
    assert claims_for("absent", entity_with(properties={}, at=LATE), []) == []
```

Write `entity_with(*, properties, at)` as a local helper building a valid
`Entity` with the given `properties` and `Provenance(observed_at=at, ...)`.

- [ ] **Step 2: Add the totality property**

```python
@given(claims=st.lists(claim_strategy(), min_size=2, max_size=8))
def test_the_claim_order_is_total(claims: list[PropertyClaim]) -> None:
    """ADR 0010: a `>` mutated to `>=` is equivalent only when the order really
    is total. Assert the totality rather than labelling the survivor.
    """
    by_key: dict[tuple[object, ...], list[PropertyClaim]] = defaultdict(list)
    for c in claims:
        by_key[_order_key(c)].append(c)
    for sharing in by_key.values():
        for other in sharing[1:]:
            assert other == sharing[0]
```

`claim_strategy()` must draw `observed_at` from a **small** set of instants and
confidence from a **small** set of values — otherwise ties never occur and the
property passes vacuously, which is exactly the "input on which every
implementation agrees" shape. Use `st.sampled_from([EARLY, LATE])` and
`st.sampled_from([0.2, 0.5])`, and let `origin` vary freely.

- [ ] **Step 3: Run the tests and confirm they fail**

Run: `uv run pytest tests/unit/domain/test_merge_strategy.py -v`
Expected: `ImportError` for `PropertyClaim` / `claims_for`, and
`AttributeError: MOST_RECENTLY_OBSERVED`.

- [ ] **Step 4: Rewrite `merge_strategy.py`**

Rename the enum member and its wire value:

```python
    MOST_RECENTLY_OBSERVED = "most_recently_observed"
```

Add above `resolve`:

```python
class PropertyClaim(BaseModel):
    """One entity's value for one property, with the observation behind it.

    `resolve` used to take bare values, and that -- not any missing timestamp --
    is why three of five strategies raised. A value alone cannot answer "which
    of these is most recent" or "how sure was whoever said this", so every
    strategy needing more than the value itself was unanswerable by
    construction. This type is the fix; the timestamp is a consequence of it.
    """

    value: Any
    provenance: Provenance
    origin: EntityId


def _order_key(claim: PropertyClaim) -> tuple[datetime, float, str]:
    """The total order `MOST_RECENTLY_OBSERVED` picks its winner under.

    Recency first, which is the strategy's whole content. Confidence second,
    because two observations made in the same batch share an instant *exactly*
    and preferring the surer one is the only meaningful thing left to say.

    `str(origin)` carries no meaning at all and is here solely so that no two
    distinct claims compare equal. The moment two do, `max` returns whichever
    arrived first and the winner depends on the order a caller happened to list
    the merged entities -- in a durable, replayable log. That is ADR 0010's
    argument, and it composes the same way `duplicate_preference` does: a
    meaningful order with an id appended.

    Deliberately *not* `domain.preference.preference`, which orders whole
    entities on `name`, `description` and `temporal` -- fields one property's
    claim does not have and cannot be asked about.
    """
    return (claim.provenance.observed_at, claim.provenance.confidence, str(claim.origin))
```

`resolve` becomes:

```python
def resolve(strategy: PropertyMergeStrategy, claims: Sequence[PropertyClaim]) -> Any:  # noqa: ANN401
    if not claims:
        raise ValueError("resolve needs at least one claim; use claims_for, which may return none")
    if strategy is PropertyMergeStrategy.PREFER_CANONICAL:
        return claims[0].value
    if strategy is PropertyMergeStrategy.PREFER_MERGED:
        return claims[1].value if len(claims) > 1 else claims[0].value
    if strategy is PropertyMergeStrategy.UNION:
        return _union([c.value for c in claims])
    if strategy is PropertyMergeStrategy.MOST_RECENTLY_OBSERVED:
        return max(claims, key=_order_key).value
    raise NotImplementedError(f"PropertyMergeStrategy.{strategy.name} is {_B28}")
```

`_union` changes to take one ordered sequence; its body and docstring are
otherwise untouched.

Add `claims_for`:

```python
def claims_for(
    property_name: str, canonical: Entity, others: Sequence[Entity]
) -> list[PropertyClaim]:
    """Every claim about `property_name`, canonical first.

    An entity whose `properties` lack the key is **skipped**, not given a
    `None` claim. Silence is not an assertion, and treating it as one would let
    an entity with no opinion outvote one with an opinion under
    `MOST_RECENTLY_OBSERVED` merely by being newer. An explicit `None` *is* a
    claim and is kept -- which is why this tests `in`, not truthiness.

    Returns `[]` when nobody claims the property, which the caller must
    distinguish from "everybody claimed `None`". `resolve` refuses an empty
    list rather than inventing an answer for it.
    """
    return [
        PropertyClaim(value=e.properties[property_name], provenance=e.provenance, origin=e.id)
        for e in (canonical, *others)
        if property_name in e.properties
    ]
```

Rewrite the module docstring. It currently argues that `LATEST` is unanswerable;
that argument is now wrong twice over (there were no per-entity timestamps
either, and the obstacle was the signature). Say what replaced it. Keep the
paragraph about refusing rather than falling back to the default — that reasoning
is untouched and still governs `DEEP_MERGE`.

- [ ] **Step 5: Update the wire-format table**

In `tests/unit/test_enum_values_are_a_wire_format.py`, `"LATEST": "latest"`
becomes `"MOST_RECENTLY_OBSERVED": "most_recently_observed"`.

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `uv run pytest tests/unit/domain/test_merge_strategy.py tests/unit/test_enum_values_are_a_wire_format.py -v`

- [ ] **Step 7: Break the implementation on purpose, three ways**

Before trusting any of the above, confirm each defect is caught:

1. `MOST_RECENTLY_OBSERVED` returning `claims[0].value` → the "later claim is
   not canonical" test must fail.
2. `_order_key` dropping its `confidence` component → the simultaneous-claims
   test must fail.
3. `_order_key` dropping `str(origin)` → the totality property must fail.

If any of those stays green, the test is the problem, not the mutant. Restore
after each.

- [ ] **Step 8: Commit**

Message subject: `Take claims rather than bare values, and name LATEST honestly`

Body: record that the obstacle was the signature and not a missing timestamp,
and that the rename is a promise-narrowing rather than tidying.

---

### Task 4: Supply `observed_at` from the composition layer

**Files:**
- Modify: `src/redstring/extraction/mapping.py:143-226` (`map_extraction`),
  `:227-289` (`_build_entity`)
- Modify: `src/redstring/extraction/pipeline.py:299` (`extract`) and its
  `map_extraction` call at `:357`
- Modify: `src/redstring/composition/build_graph.py:342`
- Modify: `tests/unit/extraction/test_mapping.py`,
  `tests/unit/extraction/test_pipeline.py`, `tests/unit/composition/*`

**Interfaces:**
- Consumes: `Provenance` (Task 1), `Entity.provenance` (Task 2).
- Produces: `map_extraction(..., observed_at: datetime)` and
  `ExtractionPipeline.extract(document, tenant_id, *, observed_at: datetime)`,
  both required keyword arguments. `build_graph` reads the clock.

- [ ] **Step 1: Write the failing test**

```python
async def test_the_pipeline_stamps_every_entity_with_the_observation_instant() -> None:
    observed = datetime(2026, 8, 12, 11, 0, tzinfo=UTC)
    result = await pipeline.extract(document, tenant_id, observed_at=observed)
    assert {e.provenance.observed_at for e in result.entities} == {observed}
```

And a test that the clock is *not* read below composition:

```python
def test_map_extraction_reads_no_clock() -> None:
    """The vantage point comes from the caller, exactly as `reference_date`
    does. A clock here would make a re-extraction of one document stamp its
    entities differently every run, which is the property `reference_date` was
    designed to preserve for world time.
    """
    source = inspect.getsource(redstring.extraction.mapping)
    assert "datetime.now" not in source
```

- [ ] **Step 2: Run and confirm failure**

Run: `uv run pytest tests/unit/extraction/test_pipeline.py -k observation -v`
Expected: `TypeError: extract() got an unexpected keyword argument 'observed_at'`.

- [ ] **Step 3: Thread the parameter**

`_build_entity` gains `observed_at: datetime` and builds the `Provenance`.
`map_extraction` gains `observed_at: datetime` (keyword-only, required) and
passes it down; document it in the Args block in the same register as
`reference_date`'s entry, saying explicitly that it is record time and
`reference_date` is world time.

`ExtractionPipeline.extract` gains `observed_at: datetime` (keyword-only,
required) and passes it to `map_extraction`. One instant for the whole document,
captured by the caller — not one per chunk, which would make two entities from
one document differ for no reason a reader could act on.

In `build_graph.py`, read the clock at the call site:

```python
    result = await pipeline.extract(document, tenant_id, observed_at=datetime.now(UTC))
```

Give `build_graph` an `observed_at: datetime | None = None` parameter defaulting
to `None`, and use `observed_at or datetime.now(UTC)`. `composition` is the only
layer permitted to read a clock; a test needing determinism passes one.

- [ ] **Step 4: Run the suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

Message subject: `Take the observation instant from the caller, not from a clock`

---

### Task 5: Adapters, events, exports and docs

**Files:**
- Modify: `src/redstring/graph/adapters/neo4j.py` (`_entity_row` ~`:704-727`,
  `_entity_from` ~`:730`)
- Modify: `src/redstring/events/document.py` (`DocumentExtracted.event_version`)
- Modify: `src/redstring/__init__.py` (`__all__`)
- Create: `docs/adr/0035-provenance-is-a-value-object.md` (number provisional)
- Modify: `docs/adr/0001-event-log-schema-and-granularity.md` (Consequences),
  `docs/adr/0010-one-total-order-for-preference.md` (Consequences)
- Modify: `BACKLOG.md`
- Modify: `docs/reference/domain-value-types.md` and any other doc naming the
  moved fields

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces: `Provenance` in `redstring.__all__`.

- [ ] **Step 1: Neo4j row shape**

`_entity_row` writes `SET e = row`, which **replaces the whole property set** —
any key the row builder omits is erased on every write. So the six provenance
fields must all appear in the row and all be read back by `_entity_from`.

Store `observed_at` as ISO **text**, not a `neo4j.time.DateTime`: `_alias_row`
already documents at `:771-774` that the driver's round-trip is lossy for
offsets. Copy that decision and cite it, do not re-derive it.

- [ ] **Step 2: Run the store suites**

Run these as **separate invocations** — BACKLOG B10m:

```bash
uv run pytest tests/unit/graph/ -q
uv run pytest tests/unit/vector/ -q
```

Neo4j integration needs a container; if unavailable, say so in the commit
message rather than claiming the adapter is verified.

- [ ] **Step 3: Bump the event version**

`DocumentExtracted.event_version = 2`. The payload's `Entity` shape changed and
no v1 payload validates.

- [ ] **Step 4: Export `Provenance`**

Add to `redstring.__all__`. This is forced, not chosen: `Entity.provenance` is
in an exported type's signature, and the public-API gate
(`tests/unit/test_public_api*.py`) fails otherwise. Run it and watch it pass
having previously failed — if it never failed, the gate is not seeing the field.

`PropertyMergeStrategy`, `PropertyClaim`, `resolve` and `claims_for` stay
**unexported**. They have no production caller; exporting an uncalled capability
makes a promise by accident.

- [ ] **Step 5: Write the ADR**

`docs/adr/0035-provenance-is-a-value-object.md`. Two decisions: provenance is a
value object on `Entity`, and a merge strategy is named for the question it can
answer. No counts, no file tables (`recurring-defects.md` §5). Add "Amended by"
pointers to the Status of ADR 0001 and note in ADR 0010's Consequences that the
claim order composes rather than competes.

**Re-check the number against `main` before merging:**

```bash
git ls-tree --name-only main docs/adr/ | sort | tail -1
```

Renumbering means the filename, the H1, **and every inbound citation** — a
half-finished renumber is this project's §6 instance and cost it 43 broken
links.

- [ ] **Step 6: Update `BACKLOG.md`**

B28 **shrinks, it does not close.** Rewrite it: `PREFER_MERGED` and
`MOST_RECENTLY_OBSERVED` are implemented; `DEEP_MERGE` remains deferred for its
original reason (a wrong deep merge is unrecoverable because the pre-merge shape
is not derivable from the result). Add the two deferrals this work creates:

1. **`resolve` still has no production caller.** Consolidation merges edges and
   discards the absorbed entities' properties. Wiring it up needs a
   merged-properties payload on `EntitiesMerged`, a projection that applies it,
   and an undo that restores the pre-merge values. Say that the strategies are
   now implemented *and unreached*, so this is defect shape §3 (`code that is
   fully tested and never invoked passes every gate this repository has`) held
   deliberately open with its eyes open.
2. **`Relationship` has no `Provenance`.** Different shape, deliberately not
   forced into the same type; relates to B76.

- [ ] **Step 7: Sweep the prose**

`grep -rn "extraction_method\|LATEST\|\.confidence" docs/ README.md CLAUDE.md
.claude/` and fix what the move invalidated. A deletion sweep's grep must cover
`.claude/` and `CLAUDE.md`, not just `docs/` — that is §5's local instance (e).

Check specifically: `docs/reference/domain-value-types.md`,
`docs/adr/0010-one-total-order-for-preference.md`'s quoted `preference` tuple,
and `preference.py`'s own reference to B28.

- [ ] **Step 8: Full gate**

Run: `uv run pytest -q` then commit — the hook runs ruff, bandit,
`lint-imports`, mypy and the coverage ratchet.

If coverage drops, say why in the commit message rather than adding tests to
paper over it.

- [ ] **Step 9: Commit**

Message subject: `Record provenance in the adapters, the events and the surface`

---

## Self-Review

**Spec coverage.** Every spec section maps to a task: §1 `Provenance` → Task 1
and 2; §2 claims and strategies → Task 3; §3 the total order → Task 3 steps 2
and 7; §4 `claims_for` → Task 3; §5 public API → Task 5 step 4; the
ADR-verdict table → Task 5 step 5; "what this deliberately does not do" → Task 5
step 6. The one spec item with no task of its own is the `observed_at` plumbing,
which the spec assumed and the plan makes Task 4 — added rather than left
implicit.

**Type consistency.** `Provenance`, `PropertyClaim`, `_order_key`, `claims_for`
and `resolve` keep the same signatures in Tasks 1, 3 and 5.
`MOST_RECENTLY_OBSERVED` is spelled identically in the enum, the tests and the
wire table.

**Known risk, stated rather than discovered.** Task 2 step 1 flags a possible
import cycle between `entity.py` and `provenance.py` and gives the resolution
(move `ExtractionMethod` down) rather than leaving the executor to find it.
