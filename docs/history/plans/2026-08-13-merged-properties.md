# Merged Properties Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `merge_strategy.resolve` its one production caller, so a merge
decides the canonical entity's `description`, `external_ids` and `properties`,
records that decision in `EntitiesMerged`, and can undo it.

**Architecture:** Read-plan-emit on the write side, exactly as
`plan_redirections` already works. `ConsolidationService` reads the group's
entities, `plan_properties` resolves each dotted path under a
`PropertyMergePolicy`, and the resulting `PropertyResolution` (a `before` and
an `after` on the canonical entity only) rides on the event. `GraphProjection`
applies `after`; the undo applies `before`. No recomputation anywhere on the
read side.

**Tech Stack:** Python 3.13, pydantic v2, `eventsource-py`, pytest,
hypothesis, `uv`.

**Spec:** `docs/superpowers/specs/2026-08-13-merged-properties-design.md`

## Global Constraints

- **Never edit `pyproject.toml` dependency tables by hand.** This plan adds no
  dependencies; if one ever seems needed, stop and escalate.
- **Do not run ruff, mypy, bandit, `lint-imports` or pytest as separate
  pre-commit steps.** They are wired into `git commit`. Write the change, then
  commit; re-`git add` and re-commit when the hook fixes files in place.
  Running a *targeted* pytest to watch one test fail is expected and different.
- **Every deferral goes in `BACKLOG.md` in the same commit that passes it by.**
  Not a TODO comment, not the PR body.
- **Prefer many small commits.** Each commit runs the full gate.
- **`from __future__ import annotations` at the top of every module**, but
  pydantic models need their field types imported **at runtime**, not under
  `if TYPE_CHECKING`. A type-checking-only import leaves the model "not fully
  defined" and every construction raises `PydanticUserError`. This has already
  cost this project a round trip.
- **Test inputs must distinguish the implementation from a wrong one.** Never
  `uuid4()` for an id whose ordering a test depends on — use the pinned
  constants each task supplies. Prefer groups of *three* entities, so
  `PREFER_MERGED` and `MOST_RECENTLY_OBSERVED` cannot agree by accident.
- **Break each new decision on purpose and watch the test fail** before
  believing it. A deliberate break that stays green is a finding about the
  fixture, not a pass. Report it in your task report.
- The dotted-path vocabulary is fixed and identical everywhere:
  `description`, `properties`, `external_ids`, `properties.<key>`,
  `external_ids.<key>`.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/redstring/domain/merge_strategy.py` | Add `PropertyMergePolicy`; change `claims_for` to take a dotted path (Task 1). |
| `src/redstring/domain/consolidation.py` | Add `MergeableFields` and `PropertyResolution` beside `RelationshipRedirection` (Task 2). |
| `src/redstring/events/merge.py` | `EntitiesMerged.resolution` + `event_version = 2`; `MergeUndone.restored_fields` (Task 2). |
| `src/redstring/consolidation/planning.py` | Add `plan_properties`, sibling to `plan_redirections` (Task 3). |
| `src/redstring/aggregates/consolidation_log.py` | Carry the resolution into the event, store it on `MergeRecord`, hand `before` back on undo (Task 4). |
| `src/redstring/consolidation/service.py` | Read the group's entities, plan, pass it on; hold the policy (Task 4). |
| `src/redstring/composition/build_graph.py` | `Consolidator` accepts and forwards a policy (Task 4). |
| `src/redstring/projections/graph.py` | Apply `after` on merge, `before` on undo (Task 5). |
| `src/redstring/__init__.py` | Export the policy's closure (Task 6). |
| `docs/adr/0036-*.md`, `BACKLOG.md`, `CLAUDE.md` | Record the decision, close B127, shrink B28 (Task 6). |

---

### Task 1: `PropertyMergePolicy`, and `claims_for` on paths

**Files:**
- Modify: `src/redstring/domain/merge_strategy.py`
- Test: `tests/unit/domain/test_merge_strategy.py`

**Interfaces:**
- Consumes: `PropertyMergeStrategy`, `PropertyClaim`, `resolve` (all already in
  this module); `Entity` from `redstring.domain.entity`.
- Produces:
  - `PropertyMergePolicy(default=..., overrides={...})`, frozen pydantic model,
    with `strategy_for(path: str) -> PropertyMergeStrategy`.
  - `MERGEABLE_FIELDS: frozenset[str]` = `{"description", "external_ids", "properties"}`.
  - `claims_for(path: str, canonical: Entity, others: Sequence[Entity]) -> list[PropertyClaim]`
    — **signature change**: the first argument was `property_name` and meant a
    key of `.properties`; it is now a dotted path.

**Context:** `claims_for`'s existing tests in
`tests/unit/domain/test_merge_strategy.py` call it as `claims_for("role", ...)`.
Under the new vocabulary that path names the *field* `role`, which does not
exist, so those calls must become `claims_for("properties.role", ...)`. That is
a mechanical edit of the call sites; do not change what those tests assert.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/domain/test_merge_strategy.py`. Read the top of that file
first — it already defines `EARLY`, `LATE`, `LOWEST_ID`, `HIGHEST_ID` and an
`entity_with(...)` helper. Use them; do not build parallel fixtures.

```python
class TestPolicyLookup:
    """`strategy_for` resolves exact path, then field default, then default."""

    def test_an_exact_path_wins_over_its_fields_default(self):
        policy = PropertyMergePolicy(
            default=PropertyMergeStrategy.PREFER_CANONICAL,
            overrides={
                "properties": PropertyMergeStrategy.PREFER_MERGED,
                "properties.role": PropertyMergeStrategy.UNION,
            },
        )
        assert policy.strategy_for("properties.role") is PropertyMergeStrategy.UNION

    def test_a_fields_default_covers_a_key_with_no_entry(self):
        policy = PropertyMergePolicy(
            default=PropertyMergeStrategy.PREFER_CANONICAL,
            overrides={"properties": PropertyMergeStrategy.PREFER_MERGED},
        )
        assert policy.strategy_for("properties.role") is PropertyMergeStrategy.PREFER_MERGED

    def test_the_policy_default_covers_a_field_with_no_entry(self):
        policy = PropertyMergePolicy(default=PropertyMergeStrategy.MOST_RECENTLY_OBSERVED)
        assert policy.strategy_for("external_ids.wikidata") is (
            PropertyMergeStrategy.MOST_RECENTLY_OBSERVED
        )

    def test_all_three_tiers_are_consulted_for_one_field(self):
        """The three tiers must be distinguishable at once.

        Asserting them in separate tests leaves an implementation that
        consults only two of them passing every one: each test names a policy
        where the tier below happens to give the same answer. Three distinct
        strategies over three paths of the same field is the input where a
        dropped tier changes an answer.
        """
        policy = PropertyMergePolicy(
            default=PropertyMergeStrategy.PREFER_CANONICAL,
            overrides={
                "properties": PropertyMergeStrategy.PREFER_MERGED,
                "properties.role": PropertyMergeStrategy.UNION,
            },
        )
        assert policy.strategy_for("properties.role") is PropertyMergeStrategy.UNION
        assert policy.strategy_for("properties.era") is PropertyMergeStrategy.PREFER_MERGED
        assert policy.strategy_for("description") is PropertyMergeStrategy.PREFER_CANONICAL

    def test_the_default_policy_prefers_the_canonical_entity(self):
        assert PropertyMergePolicy().strategy_for("properties.role") is (
            PropertyMergeStrategy.PREFER_CANONICAL
        )


class TestPolicyRefusals:
    def test_an_override_naming_no_real_field_is_refused(self):
        """A typo would otherwise be silently inert -- every merge applies the
        default and nothing says the entry did nothing."""
        with pytest.raises(ValidationError, match="properities"):
            PropertyMergePolicy(overrides={"properities.role": PropertyMergeStrategy.UNION})

    def test_a_bare_unknown_field_is_refused_too(self):
        with pytest.raises(ValidationError, match="name"):
            PropertyMergePolicy(overrides={"name": PropertyMergeStrategy.PREFER_MERGED})

    def test_union_is_refused_on_external_ids(self):
        """`external_ids` is `dict[str, str]`; UNION returns a list, so the
        upsert would raise inside a fold with the event already in the log."""
        with pytest.raises(ValidationError, match="UNION"):
            PropertyMergePolicy(overrides={"external_ids": PropertyMergeStrategy.UNION})

    def test_union_is_refused_on_an_external_ids_key(self):
        with pytest.raises(ValidationError, match="UNION"):
            PropertyMergePolicy(overrides={"external_ids.wikidata": PropertyMergeStrategy.UNION})

    def test_union_is_refused_on_description(self):
        with pytest.raises(ValidationError, match="UNION"):
            PropertyMergePolicy(overrides={"description": PropertyMergeStrategy.UNION})

    def test_union_is_refused_as_the_policy_default(self):
        """A UNION default would reach `description` and `external_ids`, which
        is the case the per-path refusals exist to prevent."""
        with pytest.raises(ValidationError, match="UNION"):
            PropertyMergePolicy(default=PropertyMergeStrategy.UNION)

    def test_union_is_allowed_on_properties_and_its_keys(self):
        """The permitting case, so the refusals above are not passing because
        the validator rejects everything."""
        policy = PropertyMergePolicy(
            overrides={
                "properties": PropertyMergeStrategy.UNION,
                "properties.aka": PropertyMergeStrategy.UNION,
            }
        )
        assert policy.strategy_for("properties.aka") is PropertyMergeStrategy.UNION

    def test_the_policy_is_frozen(self):
        policy = PropertyMergePolicy()
        with pytest.raises(ValidationError):
            policy.default = PropertyMergeStrategy.UNION


class TestClaimsForPaths:
    def test_a_description_claim_is_read_from_the_field(self):
        canonical = entity_with(description="the first analyst", at=EARLY)
        absorbed = entity_with(description="a mathematician", at=LATE)

        claims = claims_for("description", canonical, [absorbed])

        assert [c.value for c in claims] == ["the first analyst", "a mathematician"]

    def test_a_none_description_is_silence_and_is_skipped(self):
        """The asymmetry with `properties`, where an explicit `None` is a claim.

        `description` has no present/absent distinction -- `None` *is* the
        absence -- so an entity with no description must not outvote one with a
        description merely by being newer.
        """
        canonical = entity_with(description="the first analyst", at=EARLY)
        silent = entity_with(description=None, at=LATE)

        claims = claims_for("description", canonical, [silent])

        assert [c.value for c in claims] == ["the first analyst"]

    def test_an_explicit_none_property_is_still_a_claim(self):
        """The other half of the asymmetry, asserted beside it so neither can
        be changed without the other failing."""
        canonical = entity_with(properties={"role": None}, at=EARLY)

        claims = claims_for("properties.role", canonical, [])

        assert [c.value for c in claims] == [None]

    def test_an_external_id_claim_is_read_from_external_ids(self):
        canonical = entity_with(external_ids={"wikidata": "Q7259"}, at=EARLY)
        absorbed = entity_with(external_ids={"orcid": "0000-1"}, at=LATE)

        assert [c.value for c in claims_for("external_ids.wikidata", canonical, [absorbed])] == [
            "Q7259"
        ]
        assert [c.value for c in claims_for("external_ids.orcid", canonical, [absorbed])] == [
            "0000-1"
        ]

    def test_a_path_naming_no_real_field_is_refused(self):
        with pytest.raises(ValueError, match="name"):
            claims_for("name", entity_with(at=EARLY), [])

    def test_a_key_under_description_is_refused(self):
        """`description` is a scalar; `description.x` names nothing."""
        with pytest.raises(ValueError, match="description"):
            claims_for("description.x", entity_with(at=EARLY), [])

    def test_a_bare_dict_field_is_refused_as_a_claim_path(self):
        """`properties` is a policy key, not a claim target. Resolving the whole
        dict as one value is not what any strategy means."""
        with pytest.raises(ValueError, match="properties"):
            claims_for("properties", entity_with(properties={"role": "x"}, at=EARLY), [])
```

The `entity_with` helper in that file may not accept `description` or
`external_ids` yet. If it does not, widen it by forwarding `**overrides` into
`Entity(...)` — do not add a second helper.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/domain/test_merge_strategy.py -x -q`
Expected: FAIL — `ImportError` / `NameError` for `PropertyMergePolicy`.

- [ ] **Step 3: Implement**

In `src/redstring/domain/merge_strategy.py`, add near the top (after the
`IMPLEMENTED` frozenset):

```python
#: The `Entity` fields a merge may decide. `name`, `entity_type` and `temporal`
#: are deliberately absent: preference between whole entities is ADR 0010's
#: `domain.preference`, and re-deciding `name` here would put two answers to
#: one question in the codebase.
MERGEABLE_FIELDS = frozenset({"description", "external_ids", "properties"})

#: The one field whose values `UNION` can legally produce. `external_ids` is
#: `dict[str, str]` and `description` is `str | None`; a list type-checks
#: against neither.
_UNION_FIELD = "properties"


class PropertyMergePolicy(BaseModel, frozen=True):
    """Which strategy applies to which field, by dotted path.

    One key space for a scalar field and a dict key:

    | Path | Means |
    |---|---|
    | `description` | the scalar field |
    | `properties` | the default for every key of `properties` |
    | `properties.role` | that one key |

    `strategy_for` resolves **exact path, then field default, then
    `default`**, and that order is the whole content of this type.

    ## Two refusals, at two different times, on purpose

    `UNION` outside `properties` is refused **here, at construction**. It is a
    type error that nothing downstream can fix: the projection would hand a
    `list` to `Entity.external_ids`, pydantic would raise inside a fold, and
    the event is durably in the log by then with no way to make progress.
    Refusing when a caller wires up a service is the only point at which that
    is cheap.

    `DEEP_MERGE` is **not** refused here. It raises from `resolve` at plan
    time, on the write side, before any event exists -- so the failure is
    already cheap and already names BACKLOG B28. Encoding "which strategies
    are implemented" a second time in this validator would give that question
    two answers, and the one here would be the one nobody updates.
    """

    default: PropertyMergeStrategy = PropertyMergeStrategy.PREFER_CANONICAL
    overrides: Mapping[str, PropertyMergeStrategy] = {}

    @model_validator(mode="after")
    def _paths_are_real_and_union_stays_in_properties(self) -> PropertyMergePolicy:
        if self.default is PropertyMergeStrategy.UNION:
            raise ValueError(
                "UNION cannot be the policy default: it would reach description "
                "and external_ids, whose types a list does not satisfy"
            )
        for path, strategy in self.overrides.items():
            field = path.partition(".")[0]
            if field not in MERGEABLE_FIELDS:
                raise ValueError(
                    f"override path {path!r} names no mergeable field; "
                    f"expected one of {sorted(MERGEABLE_FIELDS)}"
                )
            if strategy is PropertyMergeStrategy.UNION and field != _UNION_FIELD:
                raise ValueError(
                    f"UNION is not legal on {path!r}: it returns a list, which "
                    f"{field} does not accept"
                )
        return self

    def strategy_for(self, path: str) -> PropertyMergeStrategy:
        """The strategy for `path`: exact, then its field's default, then `default`."""
        exact = self.overrides.get(path)
        if exact is not None:
            return exact
        field = self.overrides.get(path.partition(".")[0])
        if field is not None:
            return field
        return self.default
```

Imports to add at runtime (not under `TYPE_CHECKING`) — `Mapping` is a field
annotation, so it must resolve at schema-build time:

```python
from collections.abc import Mapping

from pydantic import BaseModel, model_validator
```

Then replace `claims_for` entirely:

```python
def claims_for(path: str, canonical: Entity, others: Sequence[Entity]) -> list[PropertyClaim]:
    """Every claim about `path`, canonical first.

    `path` is `description`, or `properties.<key>`, or `external_ids.<key>`.

    ## Silence is not an assertion, and the two field shapes say it differently

    An entity whose `properties` lack the key is **skipped**, not given a
    `None` claim -- treating absence as a claim would let an entity with no
    opinion outvote one with an opinion under `MOST_RECENTLY_OBSERVED` merely
    by being newer. An explicit `None` *is* a claim and is kept, which is why
    this tests `in`, not truthiness.

    `description` has no such distinction: the field always exists and `None`
    is its absence, so **a `None` description is silence and is skipped**. The
    asymmetry is real rather than an inconsistency, and it is stated here
    because a reader who knows the dict rule will expect the opposite.

    A bare `properties` or `external_ids` is refused. Those are policy keys --
    a default for every key of the field -- and resolving a whole dict as one
    value is not what any strategy means.

    Returns `[]` when nobody claims the path, which the caller must
    distinguish from "everybody claimed `None`". `resolve` refuses an empty
    list rather than inventing an answer for it.
    """
    field, dot, key = path.partition(".")
    if field not in MERGEABLE_FIELDS:
        raise ValueError(
            f"{path!r} names no mergeable field; expected one of {sorted(MERGEABLE_FIELDS)}"
        )
    entities = (canonical, *others)
    if field == "description":
        if dot:
            raise ValueError(f"description is a scalar field; {path!r} names nothing")
        return [
            PropertyClaim(value=e.description, provenance=e.provenance, origin=e.id)
            for e in entities
            if e.description is not None
        ]
    if not dot:
        raise ValueError(
            f"{field!r} is a policy key, not a claim path; name a key, as in {field}.role"
        )
    return [
        PropertyClaim(value=getattr(e, field)[key], provenance=e.provenance, origin=e.id)
        for e in entities
        if key in getattr(e, field)
    ]
```

- [ ] **Step 4: Fix the existing call sites**

In `tests/unit/domain/test_merge_strategy.py`, every existing
`claims_for("role", ...)` becomes `claims_for("properties.role", ...)`, and
`claims_for("absent", ...)` becomes `claims_for("properties.absent", ...)`.
Change nothing else about those tests. Then grep the whole tree for other
callers:

Run: `grep -rn "claims_for" src/ tests/`
Every call must use a dotted path. There should be no callers under `src/` yet.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/domain/test_merge_strategy.py -q`
Expected: PASS.

- [ ] **Step 6: Break it on purpose, twice**

Both breaks must be **reverted** before committing. Report what each did.

1. Delete the field-default tier from `strategy_for` (remove the middle
   lookup). Expected: `test_a_fields_default_covers_a_key_with_no_entry` and
   `test_all_three_tiers_are_consulted_for_one_field` fail.
2. Invert the description skip to `if e.description is None`. Expected:
   `test_a_none_description_is_silence_and_is_skipped` and
   `test_a_description_claim_is_read_from_the_field` both fail — the inversion
   must break the permitting case as well as the refusing one, so neither is
   resting on the other.

- [ ] **Step 7: Commit**

```bash
git add src/redstring/domain/merge_strategy.py tests/unit/domain/test_merge_strategy.py
git commit -F <message file>
```

Subject: `Add PropertyMergePolicy and give claims_for a dotted path`.
Body: what the two deliberate breaks did, and the `description`/`properties`
silence asymmetry with its reason.

---

### Task 2: The payload types, and the events that carry them

**Files:**
- Modify: `src/redstring/domain/consolidation.py`
- Modify: `src/redstring/events/merge.py`
- Modify: `tests/unit/events/test_schema.py:37-43` (the `EXPECTED_EVENT_VERSIONS` table)
- Test: `tests/unit/domain/test_consolidation.py`, `tests/unit/events/` (a merge-event test module already exists there or in `tests/unit/aggregates/`; put event-payload tests beside the existing `EntitiesMerged` validator tests — find them with `grep -rln "EntitiesMerged" tests/unit`)

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces:
  - `MergeableFields(description: str | None = None, external_ids: dict[str, str] = {}, properties: dict[str, Any] = {})`
  - `PropertyResolution(entity_id: EntityId, before: MergeableFields, after: MergeableFields)`
  - `EntitiesMerged.resolution: PropertyResolution | None = None`, `event_version = 2`
  - `MergeUndone.restored_fields: MergeableFields | None = None`

- [ ] **Step 1: Write the failing tests**

In `tests/unit/domain/test_consolidation.py`:

```python
class TestMergeableFields:
    def test_it_defaults_to_saying_nothing(self):
        fields = MergeableFields()
        assert fields.description is None
        assert fields.external_ids == {}
        assert fields.properties == {}

    def test_it_holds_exactly_the_three_mergeable_fields(self):
        """Pinned against `MERGEABLE_FIELDS`, so adding a target to one and not
        the other fails rather than silently dropping the new field from every
        event payload."""
        assert set(MergeableFields.model_fields) == MERGEABLE_FIELDS
```

In the module holding the `EntitiesMerged` validator tests, add:

```python
CANONICAL = EntityId(UUID(int=1))
ABSORBED = EntityId(UUID(int=2))


def _resolution(entity_id):
    return PropertyResolution(
        entity_id=entity_id,
        before=MergeableFields(properties={"role": "analyst"}),
        after=MergeableFields(properties={"role": "mathematician"}),
    )


class TestResolutionBelongsToTheCanonicalEntity:
    def test_a_resolution_naming_an_absorbed_entity_is_refused(self):
        """The projection upserts the row the resolution names. Naming an
        absorbed entity would overwrite the wrong row and have undo restore it,
        with nothing downstream able to tell."""
        with pytest.raises(ValidationError, match="canonical"):
            EntitiesMerged(
                tenant_id=TENANT,
                canonical_entity_id=CANONICAL,
                merged_entity_ids=[ABSORBED],
                resolution=_resolution(ABSORBED),
            )

    def test_a_resolution_naming_the_canonical_entity_is_accepted(self):
        event = EntitiesMerged(
            tenant_id=TENANT,
            canonical_entity_id=CANONICAL,
            merged_entity_ids=[ABSORBED],
            resolution=_resolution(CANONICAL),
        )
        assert event.resolution is not None
        assert event.resolution.after.properties == {"role": "mathematician"}

    def test_a_merge_may_decide_nothing_about_fields(self):
        """`None` is a true state: `ConsolidationLog` holds no entity data, so
        a direct aggregate caller genuinely has no resolution to give."""
        event = EntitiesMerged(
            tenant_id=TENANT, canonical_entity_id=CANONICAL, merged_entity_ids=[ABSORBED]
        )
        assert event.resolution is None
```

Use the tenant constant already defined in that module rather than inventing
`TENANT`; if none exists, define it as `uuid4()` — the tenant plays no part in
these assertions, so a random one is safe here.

In `tests/unit/events/test_schema.py`, change the table entry:

```python
    "EntitiesMerged": 2,
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/domain/test_consolidation.py tests/unit/events/test_schema.py -x -q`
Expected: FAIL — `MergeableFields` undefined, and the version table disagrees
with the declared `event_version = 1`.

- [ ] **Step 3: Implement the payload types**

Append to `src/redstring/domain/consolidation.py`:

```python
class MergeableFields(BaseModel):
    """Exactly the `Entity` fields a merge may decide.

    Held as a value object rather than as three fields on the event because it
    appears twice -- as a merge's `after` and as an undo's restoration -- and
    the two must not be able to drift apart.
    """

    description: str | None = None
    external_ids: dict[str, str] = {}
    properties: dict[str, Any] = {}


class PropertyResolution(BaseModel):
    """What a merge decided about one entity's fields, before and after.

    ## Why one entity, when a merge combines several

    A merge does not touch the entities it absorbs. `GraphStore` has no
    `delete_entity` (ADR 0002), the projection writes an `Alias` per absorbed
    entity and nothing else, and those rows survive unchanged. So the whole
    effect of a merge on entity data is one before/after pair on the canonical
    entity, and an undo restores it by upserting `before`.

    BACKLOG B127 asked for every absorbed entity's originals here, reasoning
    that a `UNION` result cannot say who claimed what. True, and not needed:
    nothing downstream has a row to put them back into.

    ## `after` is the complete post-merge value, not a diff

    The projection replaces all three fields wholesale, so a key omitted from
    `after` is a key *deleted*. A resolution must therefore be exhaustive over
    the union of the group's keys rather than over what changed.
    """

    entity_id: EntityId
    before: MergeableFields
    after: MergeableFields
```

Add the runtime imports that file needs: `from typing import Any` and
`from redstring.domain.ids import EntityId`. `EntityId` is a field annotation,
so it must be imported at runtime.

- [ ] **Step 4: Implement the event changes**

In `src/redstring/events/merge.py`:

- import `MergeableFields` and `PropertyResolution` from
  `redstring.domain.consolidation` (runtime import — they are field types),
- on `EntitiesMerged`: `event_version: int = 2` and
  `resolution: PropertyResolution | None = None`,
- on `MergeUndone`: `restored_fields: MergeableFields | None = None`,
- extend `EntitiesMerged._the_merge_is_coherent` with:

```python
        if self.resolution is not None and self.resolution.entity_id != self.canonical_entity_id:
            raise ValueError(
                f"resolution must name the canonical entity: "
                f"{self.resolution.entity_id} != {self.canonical_entity_id}"
            )
```

Extend the module docstring with a paragraph: a merge's effect on entity data
is one before/after pair on the canonical entity, recorded rather than
recomputed for the same reason `redirections` is — the projection overwrote
the pre-merge value when it applied the event.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/domain/test_consolidation.py tests/unit/events -q`
Expected: PASS.

- [ ] **Step 6: Break it on purpose**

Change the validator's `!=` to `==` (so it refuses the canonical and accepts
the absorbed). Expected: both
`test_a_resolution_naming_an_absorbed_entity_is_refused` and
`test_a_resolution_naming_the_canonical_entity_is_accepted` fail. Revert.

- [ ] **Step 7: Commit**

```bash
git add src/redstring/domain/consolidation.py src/redstring/events/merge.py tests/
git commit -F <message file>
```

Subject: `Carry a merge's field decision on EntitiesMerged, at version 2`.
Body: why one entity rather than many, and the B127 correction.

---

### Task 3: `plan_properties`

**Files:**
- Modify: `src/redstring/consolidation/planning.py`
- Test: `tests/unit/consolidation/test_planning.py`

**Interfaces:**
- Consumes: `PropertyMergePolicy`, `claims_for`, `resolve`,
  `PropertyMergeStrategy` (Task 1); `MergeableFields`, `PropertyResolution`
  (Task 2); the `entity(...)` builder in `tests/unit/consolidation/conftest.py`,
  which already takes `observed_at=`, `confidence=` and `**overrides`.
- Produces:
  `plan_properties(*, policy: PropertyMergePolicy, canonical: Entity, others: Sequence[Entity]) -> PropertyResolution`

**Context:** `tests/unit/consolidation/conftest.py` defines `OBSERVED` and an
`entity(tenant_id, *, name=..., entity_id=None, source_id=..., confidence=1.0,
observed_at=OBSERVED, **overrides)` builder. Pass `description=`,
`properties=` and `external_ids=` through `**overrides`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/consolidation/test_planning.py`:

```python
from datetime import UTC, datetime

from redstring.consolidation.planning import plan_properties
from redstring.domain.merge_strategy import PropertyMergePolicy, PropertyMergeStrategy

#: Three instants, so the newest is not also the last-listed. A test where the
#: most recent entity is the last argument cannot tell
#: `MOST_RECENTLY_OBSERVED` from `PREFER_MERGED`-taking-the-last.
FIRST = datetime(2026, 1, 1, tzinfo=UTC)
MIDDLE = datetime(2026, 2, 1, tzinfo=UTC)
NEWEST = datetime(2026, 3, 1, tzinfo=UTC)


def _group(tenant, canonical_fields, first_fields, second_fields):
    """A canonical entity and two absorbed ones.

    Two absorbed entities, not one: with a single absorbed entity
    `PREFER_MERGED` and `MOST_RECENTLY_OBSERVED` pick the same claim for any
    input, so a one-absorbed test cannot distinguish them.
    """
    canonical = entity(tenant, name="Ada", observed_at=MIDDLE, **canonical_fields)
    first = entity(tenant, name="A. Lovelace", observed_at=NEWEST, **first_fields)
    second = entity(tenant, name="Countess Lovelace", observed_at=FIRST, **second_fields)
    return canonical, [first, second]


class TestPlanProperties:
    def test_the_default_policy_keeps_every_canonical_value(self):
        tenant = uuid4()
        canonical, others = _group(
            tenant,
            {"description": "the analyst", "properties": {"role": "analyst"}},
            {"description": "a mathematician", "properties": {"role": "mathematician"}},
            {"description": "a countess", "properties": {"role": "countess"}},
        )

        plan = plan_properties(policy=PropertyMergePolicy(), canonical=canonical, others=others)

        assert plan.after.description == "the analyst"
        assert plan.after.properties == {"role": "analyst"}

    def test_before_holds_the_canonical_entitys_values_as_read(self):
        tenant = uuid4()
        canonical, others = _group(
            tenant,
            {"description": "the analyst", "properties": {"role": "analyst"}},
            {"properties": {"role": "mathematician"}},
            {"properties": {"role": "countess"}},
        )

        plan = plan_properties(
            policy=PropertyMergePolicy(default=PropertyMergeStrategy.PREFER_MERGED),
            canonical=canonical,
            others=others,
        )

        assert plan.before.description == "the analyst"
        assert plan.before.properties == {"role": "analyst"}
        assert plan.entity_id == canonical.id

    def test_most_recently_observed_takes_the_newest_claim_not_the_last(self):
        """`first` is newest and is listed before `second`. An implementation
        taking the last claim, or the last absorbed entity, gives "countess"."""
        tenant = uuid4()
        canonical, others = _group(
            tenant,
            {"properties": {"role": "analyst"}},
            {"properties": {"role": "mathematician"}},
            {"properties": {"role": "countess"}},
        )

        plan = plan_properties(
            policy=PropertyMergePolicy(default=PropertyMergeStrategy.MOST_RECENTLY_OBSERVED),
            canonical=canonical,
            others=others,
        )

        assert plan.after.properties == {"role": "mathematician"}

    def test_prefer_merged_takes_the_first_absorbed_entity(self):
        """Same group, same instants, different strategy, different answer --
        which is what makes either assertion mean anything."""
        tenant = uuid4()
        canonical, others = _group(
            tenant,
            {"properties": {"role": "analyst"}},
            {"properties": {"role": "mathematician"}},
            {"properties": {"role": "countess"}},
        )

        plan = plan_properties(
            policy=PropertyMergePolicy(default=PropertyMergeStrategy.PREFER_MERGED),
            canonical=canonical,
            others=others,
        )

        assert plan.after.properties == {"role": "mathematician"}

    def test_after_is_exhaustive_over_every_entitys_keys(self):
        """`after` replaces the field wholesale, so a key the canonical entity
        never had must still appear -- and a key only an absorbed entity had
        must not be dropped."""
        tenant = uuid4()
        canonical, others = _group(
            tenant,
            {"properties": {"role": "analyst"}},
            {"properties": {"era": "victorian"}},
            {"properties": {"field": "computing"}},
        )

        plan = plan_properties(policy=PropertyMergePolicy(), canonical=canonical, others=others)

        assert plan.after.properties == {
            "role": "analyst",
            "era": "victorian",
            "field": "computing",
        }

    def test_union_accumulates_across_the_group(self):
        tenant = uuid4()
        canonical, others = _group(
            tenant,
            {"properties": {"aka": "Ada"}},
            {"properties": {"aka": "A. Lovelace"}},
            {"properties": {"aka": "Ada"}},
        )

        plan = plan_properties(
            policy=PropertyMergePolicy(overrides={"properties.aka": PropertyMergeStrategy.UNION}),
            canonical=canonical,
            others=others,
        )

        assert plan.after.properties == {"aka": ["Ada", "A. Lovelace"]}

    def test_external_ids_accumulate_key_by_key(self):
        tenant = uuid4()
        canonical, others = _group(
            tenant,
            {"external_ids": {"wikidata": "Q7259"}},
            {"external_ids": {"orcid": "0000-1"}},
            {"external_ids": {"viaf": "12345"}},
        )

        plan = plan_properties(policy=PropertyMergePolicy(), canonical=canonical, others=others)

        assert plan.after.external_ids == {
            "wikidata": "Q7259",
            "orcid": "0000-1",
            "viaf": "12345",
        }

    def test_a_field_nobody_described_stays_none(self):
        """`claims_for` returns `[]` and `resolve` refuses an empty list, so
        this path must not call it."""
        tenant = uuid4()
        canonical, others = _group(tenant, {}, {}, {})

        plan = plan_properties(
            policy=PropertyMergePolicy(default=PropertyMergeStrategy.MOST_RECENTLY_OBSERVED),
            canonical=canonical,
            others=others,
        )

        assert plan.after.description is None

    def test_the_key_order_is_deterministic_and_canonical_first(self):
        """Two replays of one log must produce byte-identical payloads, so the
        union's iteration order cannot depend on a set."""
        tenant = uuid4()
        canonical, others = _group(
            tenant,
            {"properties": {"role": "analyst"}},
            {"properties": {"era": "victorian"}},
            {"properties": {"field": "computing"}},
        )

        plan = plan_properties(policy=PropertyMergePolicy(), canonical=canonical, others=others)

        assert list(plan.after.properties) == ["role", "era", "field"]

    def test_a_merge_with_nothing_absorbed_keeps_the_canonical_values(self):
        tenant = uuid4()
        canonical = entity(
            tenant, description="the analyst", properties={"role": "analyst"}, observed_at=MIDDLE
        )

        plan = plan_properties(
            policy=PropertyMergePolicy(default=PropertyMergeStrategy.PREFER_MERGED),
            canonical=canonical,
            others=[],
        )

        assert plan.after.description == "the analyst"
        assert plan.after.properties == {"role": "analyst"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/consolidation/test_planning.py -x -q`
Expected: FAIL — `ImportError: cannot import name 'plan_properties'`.

- [ ] **Step 3: Implement**

Append to `src/redstring/consolidation/planning.py`:

```python
def plan_properties(
    *,
    policy: PropertyMergePolicy,
    canonical: Entity,
    others: Sequence[Entity],
) -> PropertyResolution:
    """What the merge decides about the canonical entity's fields.

    `before` is the canonical entity as read; `after` is the complete
    post-merge value of all three fields. Exhaustive rather than a diff,
    because the projection replaces each field wholesale -- an omitted key is
    a deleted key.

    Key order is the canonical entity's own, then each absorbed entity's new
    keys in the order the merge listed them. Deterministic rather than a
    `set`, so two replays of one log produce identical payloads; the
    replay-equivalence tests compare them.
    """
    return PropertyResolution(
        entity_id=canonical.id,
        before=MergeableFields(
            description=canonical.description,
            external_ids=dict(canonical.external_ids),
            properties=dict(canonical.properties),
        ),
        after=MergeableFields(
            description=_resolved_description(policy, canonical, others),
            external_ids=_resolved_mapping(policy, "external_ids", canonical, others),
            properties=_resolved_mapping(policy, "properties", canonical, others),
        ),
    )


def _resolved_description(
    policy: PropertyMergePolicy, canonical: Entity, others: Sequence[Entity]
) -> str | None:
    """`None` when nobody described the entity.

    `claims_for` skips a `None` description -- for a scalar field, `None` is
    the absence and absence is silence -- so an undescribed group produces no
    claims at all. `resolve` refuses an empty list rather than inventing an
    answer, and `None` is the only correct one here.
    """
    claims = claims_for("description", canonical, others)
    if not claims:
        return None
    return cast("str | None", resolve(policy.strategy_for("description"), claims))


def _resolved_mapping(
    policy: PropertyMergePolicy, field: str, canonical: Entity, others: Sequence[Entity]
) -> dict[str, Any]:
    """Every key any entity in the group claims, resolved one at a time.

    The claim list for a key in the union is never empty: the key is in the
    union because some entity has it, and that entity claims it.
    """
    resolved: dict[str, Any] = {}
    for entity in (canonical, *others):
        for key in getattr(entity, field):
            if key in resolved:
                continue
            path = f"{field}.{key}"
            resolved[key] = resolve(policy.strategy_for(path), claims_for(path, canonical, others))
    return resolved
```

Imports: `plan_properties` and its helpers need, at runtime,
`from redstring.domain.consolidation import MergeableFields, PropertyResolution`
and `from redstring.domain.merge_strategy import PropertyMergePolicy, claims_for, resolve`.
`Entity`, `Sequence`, `Any` and `cast` go under `TYPE_CHECKING` where the file
already puts them (`cast` comes from `typing` and must be a runtime import).

Extend the module docstring: what `plan_properties` decides, that `after` is
exhaustive rather than a diff, and why key order is deterministic.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/consolidation/test_planning.py -q`
Expected: PASS.

- [ ] **Step 5: Break it on purpose**

Change `_resolved_mapping` to iterate only `canonical` (drop the `others`).
Expected: `test_after_is_exhaustive_over_every_entitys_keys` and
`test_external_ids_accumulate_key_by_key` fail. Revert.

Then change `_resolved_description` to return `resolve(...)` unconditionally
without the empty guard. Expected: `test_a_field_nobody_described_stays_none`
fails with `ValueError` from `resolve`. Revert.

- [ ] **Step 6: Commit**

```bash
git add src/redstring/consolidation/planning.py tests/unit/consolidation/test_planning.py
git commit -F <message file>
```

Subject: `Plan what a merge decides about the canonical entity's fields`.

---

### Task 4: Wire it through the aggregate, the service and `Consolidator`

**Files:**
- Modify: `src/redstring/aggregates/consolidation_log.py`
- Modify: `src/redstring/consolidation/service.py`
- Modify: `src/redstring/composition/build_graph.py:673-713`
- Test: `tests/unit/aggregates/` (find the `ConsolidationLog` module with
  `grep -rln "ConsolidationLog" tests/unit`), `tests/unit/consolidation/test_resolve.py`

**Interfaces:**
- Consumes: `PropertyResolution`, `MergeableFields` (Task 2);
  `plan_properties`, `PropertyMergePolicy` (Tasks 1 and 3).
- Produces:
  - `MergeRecord.resolution: PropertyResolution | None = None`
  - `ConsolidationLog.merge(..., resolution: PropertyResolution | None = None)`
  - `ConsolidationService.__init__(..., merge_policy: PropertyMergePolicy | None = None)`
  - `ConsolidationService.merge(..., policy: PropertyMergePolicy | None = None)`
  - `Consolidator.__init__(..., merge_policy: PropertyMergePolicy | None = None)`

- [ ] **Step 1: Write the failing tests**

In the `ConsolidationLog` test module:

```python
def _resolution(entity_id):
    return PropertyResolution(
        entity_id=entity_id,
        before=MergeableFields(properties={"role": "analyst"}),
        after=MergeableFields(properties={"role": "mathematician"}),
    )


class TestUndoRestoresFields:
    def test_undo_hands_back_the_pre_merge_fields(self):
        """Derived from replayed state, not from the caller -- the same rule
        `restored_relationships` already follows."""
        log = ConsolidationLog(...)  # match the module's existing setup
        merged = log.merge(
            tenant_id=TENANT,
            canonical_entity_id=CANONICAL,
            merged_entity_ids=[ABSORBED],
            resolution=_resolution(CANONICAL),
        )
        log._apply(merged)  # or the module's replay helper

        undone = log.undo_merge(tenant_id=TENANT, merge_event_id=merged.event_id)

        assert undone.restored_fields is not None
        assert undone.restored_fields.properties == {"role": "analyst"}

    def test_undoing_a_merge_that_decided_nothing_restores_nothing(self):
        log = ConsolidationLog(...)
        merged = log.merge(
            tenant_id=TENANT, canonical_entity_id=CANONICAL, merged_entity_ids=[ABSORBED]
        )
        log._apply(merged)

        undone = log.undo_merge(tenant_id=TENANT, merge_event_id=merged.event_id)

        assert undone.restored_fields is None
```

Follow whatever construction and replay idiom that module already uses; do not
introduce a new one. If it builds the log through a repository, do the same.

In `tests/unit/consolidation/test_resolve.py` (or wherever
`ConsolidationService.merge` is exercised against a real in-memory graph):

```python
class TestMergeDecidesFields:
    async def test_the_emitted_event_carries_a_resolution(self):
        """The wiring test: without it, `plan_properties` has no caller and
        this whole change is another unreached component."""
        # Build a store holding a canonical entity and one absorbed entity with
        # different `properties`, using the suite's existing fixtures.
        event = await service.merge(
            tenant_id=tenant,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[absorbed.id],
        )

        assert event.resolution is not None
        assert event.resolution.entity_id == canonical.id
        assert event.resolution.before.properties == canonical.properties

    async def test_the_services_policy_decides(self):
        """A non-default policy must reach `plan_properties`. With the policy
        dropped on the floor the default applies and the canonical value wins,
        so this is the assertion that catches an ignored argument."""
        service = ConsolidationService(
            event_store=...,
            snapshot_store=...,
            graph_store=store,
            merge_policy=PropertyMergePolicy(default=PropertyMergeStrategy.PREFER_MERGED),
        )

        event = await service.merge(
            tenant_id=tenant,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[absorbed.id],
        )

        assert event.resolution.after.properties == absorbed.properties

    async def test_a_per_call_policy_overrides_the_services(self):
        event = await service.merge(
            tenant_id=tenant,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[absorbed.id],
            policy=PropertyMergePolicy(default=PropertyMergeStrategy.PREFER_MERGED),
        )

        assert event.resolution.after.properties == absorbed.properties

    async def test_a_canonical_entity_with_no_row_is_refused(self):
        """The log and the graph disagreeing, which is what
        `MissingEntityError` names -- not a routine miss."""
        with pytest.raises(MissingEntityError):
            await service.merge(
                tenant_id=tenant,
                canonical_entity_id=EntityId(uuid4()),
                merged_entity_ids=[absorbed.id],
            )

    async def test_an_absorbed_entity_with_no_row_is_tolerated(self):
        """`_apply_merge` already tolerates this when writing aliases; the plan
        must agree with it rather than refuse where the projection shrugs."""
        event = await service.merge(
            tenant_id=tenant,
            canonical_entity_id=canonical.id,
            merged_entity_ids=[absorbed.id, EntityId(uuid4())],
        )

        assert event.resolution is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/consolidation tests/unit/aggregates -x -q`
Expected: FAIL — `merge() got an unexpected keyword argument 'resolution'`.

- [ ] **Step 3: Implement the aggregate**

In `src/redstring/aggregates/consolidation_log.py`:

- add `resolution: PropertyResolution | None = None` to `MergeRecord`, with a
  docstring line: kept for the same reason `redirections` is — an undo
  restores it, and reconstructing it later would need the pre-merge entity the
  projection overwrote,
- add `resolution: PropertyResolution | None = None` to `merge()`'s
  keyword-only parameters and pass it into `create_event(EntitiesMerged, ...)`,
- carry it into the `MergeRecord` in `_apply_merged`,
- in `undo_merge`, pass
  `restored_fields=record.resolution.before if record.resolution is not None else None`.

Runtime import of `PropertyResolution` from `redstring.domain.consolidation`
(it is a pydantic field annotation on `MergeRecord`).

- [ ] **Step 4: Implement the service**

In `src/redstring/consolidation/service.py`:

```python
    def __init__(
        self,
        *,
        event_store: AggregateStore,
        snapshot_store: SnapshotStore,
        graph_store: ConsolidationGraph,
        merge_policy: PropertyMergePolicy | None = None,
    ) -> None:
        ...
        self._policy = merge_policy if merge_policy is not None else PropertyMergePolicy()
```

and in `merge()`, after the existing `plan_redirections` call and before the
`tenant_scope` block:

```python
        entities = await self._graph.get_entities(group, tenant_id)
        by_id = {entity.id: entity for entity in entities}
        canonical = by_id.get(canonical_entity_id)
        if canonical is None:
            # The log and the graph disagreeing, not a routine miss: a merge
            # whose canonical entity has no row cannot decide anything about
            # its fields, and guessing would write a decision nobody made.
            # Same reading as `_resolved_subject`.
            raise MissingEntityError(entity_id=canonical_entity_id, tenant_id=tenant_id)
        # An absorbed entity with no row is *tolerated*, deliberately:
        # `GraphProjection._apply_merge` already writes an alias with a null
        # name for exactly this case, and refusing here would make the plan
        # stricter than the fold that applies it.
        others = [by_id[entity_id] for entity_id in merged_entity_ids if entity_id in by_id]
        resolution = plan_properties(
            policy=policy if policy is not None else self._policy,
            canonical=canonical,
            others=others,
        )
```

Add `policy: PropertyMergePolicy | None = None` to `merge()`'s keyword-only
parameters, and pass `resolution=resolution` to `log.merge(...)`.

Extend the class docstring: the service now reads entities as well as edges,
and why the policy lives here (constructed once, overridable per call).

- [ ] **Step 5: Implement `Consolidator` pass-through**

In `src/redstring/composition/build_graph.py`, add
`merge_policy: PropertyMergePolicy | None = None` to `Consolidator.__init__`'s
keyword-only parameters, document it in the existing Args block ("how a merge
reconciles the canonical entity's fields; the default keeps every canonical
value"), and forward it to `ConsolidationService(...)`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/consolidation tests/unit/aggregates tests/unit/composition -q`
Expected: PASS.

- [ ] **Step 7: Break it on purpose**

Drop `policy` on the floor in `ConsolidationService.merge` (always use
`self._policy`). Expected: `test_a_per_call_policy_overrides_the_services`
fails. Revert.

Then make `undo_merge` pass `record.resolution.after` instead of `.before`.
Expected: `test_undo_hands_back_the_pre_merge_fields` fails. Revert.

- [ ] **Step 8: Commit**

```bash
git add src/redstring/aggregates/consolidation_log.py src/redstring/consolidation/service.py src/redstring/composition/build_graph.py tests/
git commit -F <message file>
```

Subject: `Decide the canonical entity's fields when a merge is emitted`.

---

### Task 5: Apply it, and undo it

**Files:**
- Modify: `src/redstring/projections/graph.py:156-210`
- Test: `tests/unit/consolidation/test_merge_undo_round_trip.py`, plus the
  projection's own test module (`grep -rln "GraphProjection" tests/unit`)

**Interfaces:**
- Consumes: everything from Tasks 2 and 4.
- Produces: no new public names.

- [ ] **Step 1: Write the failing tests**

In the projection test module:

```python
class TestApplyingAFieldDecision:
    async def test_a_merge_writes_the_resolved_fields_onto_the_canonical_entity(self):
        # store already holds `canonical` with properties {"role": "analyst"}
        await projection.handle(
            EntitiesMerged(
                tenant_id=tenant,
                canonical_entity_id=canonical.id,
                merged_entity_ids=[absorbed.id],
                resolution=PropertyResolution(
                    entity_id=canonical.id,
                    before=MergeableFields(properties={"role": "analyst"}),
                    after=MergeableFields(
                        description="a mathematician",
                        external_ids={"wikidata": "Q7259"},
                        properties={"role": "mathematician"},
                    ),
                ),
            )
        )

        stored = await store.get_entity(canonical.id, tenant)
        assert stored.properties == {"role": "mathematician"}
        assert stored.description == "a mathematician"
        assert stored.external_ids == {"wikidata": "Q7259"}

    async def test_it_changes_nothing_else_about_the_entity(self):
        """The upsert must be a copy with three fields replaced, not a rebuilt
        entity. `name` and `provenance` surviving is what says so."""
        stored = ...  # apply the same event as above
        assert stored.name == canonical.name
        assert stored.provenance == canonical.provenance

    async def test_a_merge_that_decided_nothing_leaves_the_entity_alone(self):
        await projection.handle(
            EntitiesMerged(
                tenant_id=tenant,
                canonical_entity_id=canonical.id,
                merged_entity_ids=[absorbed.id],
            )
        )

        stored = await store.get_entity(canonical.id, tenant)
        assert stored == canonical

    async def test_it_leaves_the_absorbed_entity_untouched(self):
        """The premise the one-entity payload rests on. If a merge ever did
        write to an absorbed entity, the undo payload would be incomplete and
        nothing else in the suite would notice."""
        stored = await store.get_entity(absorbed.id, tenant)
        assert stored == absorbed

    async def test_applying_the_same_merge_twice_is_the_same_as_once(self):
        # apply the event, snapshot the entity, apply it again
        assert after_second == after_first

    async def test_a_canonical_entity_with_no_row_is_a_poison_event(self):
        with pytest.raises(MissingEntityError):
            await projection.handle(
                EntitiesMerged(
                    tenant_id=tenant,
                    canonical_entity_id=EntityId(uuid4()),
                    merged_entity_ids=[absorbed.id],
                    resolution=PropertyResolution(
                        entity_id=...,  # the same unknown id
                        before=MergeableFields(),
                        after=MergeableFields(properties={"role": "x"}),
                    ),
                )
            )
```

In `tests/unit/consolidation/test_merge_undo_round_trip.py`:

```python
async def test_merge_then_undo_restores_the_canonical_entitys_fields():
    """The expectation is recorded **before** the merge and independently of
    the projection.

    Asserting that the entity after undo equals the entity the projection
    produced would be a self-consistency check: both sides run the same fold,
    so a fold that does too little leaves both agreeing on the same wrong
    state. That is the replay-equivalence lesson in CLAUDE.md, and it is why
    `expected` is captured from the store before anything is applied.
    """
    expected = await store.get_entity(canonical.id, tenant)

    merged = await service.merge(
        tenant_id=tenant,
        canonical_entity_id=canonical.id,
        merged_entity_ids=[absorbed.id],
        policy=PropertyMergePolicy(default=PropertyMergeStrategy.PREFER_MERGED),
    )
    await projection.handle(merged)
    # The merge must actually have changed something, or the round trip is
    # vacuous -- a no-op fold passes any restoration test ever written.
    assert (await store.get_entity(canonical.id, tenant)) != expected

    undone = await service.undo(tenant_id=tenant, merge_event_id=merged.event_id)
    await projection.handle(undone)

    assert (await store.get_entity(canonical.id, tenant)) == expected
```

Follow the existing module's fixture and application idiom (it already merges
and undoes through a real service and projection); extend it rather than
building a parallel rig.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/consolidation/test_merge_undo_round_trip.py -x -q`
Expected: FAIL — the entity's properties are unchanged by the merge, so the
`!= expected` assertion fails.

- [ ] **Step 3: Implement**

In `src/redstring/projections/graph.py`, add a helper and call it from both
handlers:

```python
    async def _apply_fields(
        self, entity_id: EntityId, tenant_id: TenantId, fields: MergeableFields
    ) -> None:
        """Replace the canonical entity's three mergeable fields.

        A copy with three fields replaced rather than a rebuilt entity: the
        resolution says nothing about `name`, `provenance` or `temporal`, and
        an upsert that dropped them would be deciding questions nobody asked.

        Idempotent by construction. `fields` is a literal snapshot rather than
        a recomputation, so applying it twice -- or replaying the whole log --
        produces the identical row.
        """
        entity = await self._store.get_entity(entity_id, tenant_id)
        if entity is None:
            # A poison event, routed to the DLQ. A merge whose canonical
            # entity has no row means the log and the graph disagree; skipping
            # would drop the decision with nothing to notice.
            raise MissingEntityError(entity_id=entity_id, tenant_id=tenant_id)
        await self._store.upsert_entity(entity.model_copy(update=fields.model_dump()))
```

In `_apply_merge`, after the redirection loop:

```python
        if event.resolution is not None:
            await self._apply_fields(event.resolution.entity_id, tenant_id, event.resolution.after)
```

In `_apply_undo`, after the alias removals and relationship upserts:

```python
        if event.restored_fields is not None:
            await self._apply_fields(
                event.canonical_entity_id, TenantId(event.tenant_id), event.restored_fields
            )
```

Check the port for the singular upsert's name — if `GraphStore` has only
`upsert_entities`, call that with a one-element list rather than adding a
method. Do **not** widen the port; a new port method would trigger the
compliance-coverage gate and is not needed here.

Extend the module docstring's "What each event does to the store" list: both
`EntitiesMerged` and `MergeUndone` now also write the canonical entity's
mergeable fields, from a snapshot rather than a recomputation.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/consolidation tests/unit/projections -q`
Expected: PASS.

- [ ] **Step 5: Break it on purpose**

Swap `event.resolution.after` for `event.resolution.before` in `_apply_merge`.
Expected: the round-trip's `!= expected` assertion fails — a merge that writes
`before` changes nothing. Revert.

Then delete the `_apply_fields` call from `_apply_undo`. Expected: the
round-trip's final equality fails. Revert.

- [ ] **Step 6: Run the whole unit suite**

Run: `uv run pytest tests/unit -q`
Expected: PASS. Report the count; it was 3065 passed, 2 skipped before this
branch.

- [ ] **Step 7: Commit**

```bash
git add src/redstring/projections/graph.py tests/
git commit -F <message file>
```

Subject: `Apply a merge's field decision, and restore it on undo`.
Body: what both deliberate breaks did, and why the round-trip's expectation is
captured before the merge rather than compared against the fold's own output.

---

### Task 6: Exports, ADR, and the backlog

**Files:**
- Modify: `src/redstring/__init__.py`
- Create: `docs/adr/0036-a-merge-resolves-the-canonical-entitys-fields.md`
- Modify: `docs/adr/0001-event-log-schema-and-granularity.md` (Status +
  Consequences only — never rewrite a Decision)
- Modify: `BACKLOG.md`
- Modify: `docs/adr/index.md` if it lists ADRs explicitly

**Interfaces:** consumes every public name added by Tasks 1, 2 and 4.

- [ ] **Step 1: Add the exports**

Add to the imports and to `__all__` in `src/redstring/__init__.py`, keeping
both lists alphabetically ordered as the file already does:

- `MergeableFields`, `PropertyResolution` from `redstring.domain.consolidation`
- `PropertyMergePolicy`, `PropertyMergeStrategy` from
  `redstring.domain.merge_strategy`

Extend the module docstring where it describes consolidation: a merge now
decides the canonical entity's `description`, `external_ids` and `properties`
under a `PropertyMergePolicy`, and `EntitiesMerged` carries the decision.

- [ ] **Step 2: Run the public-surface gates**

Run: `uv run pytest tests/unit/test_public_api.py -q` (find the real path with
`grep -rln "__all__" tests/unit`).
Expected: PASS. If a gate names a further type reachable from an exported
signature, export that too — the closure is the point of the gate, not a
surprise. Do **not** silence a gate.

- [ ] **Step 3: Write ADR 0036**

`docs/adr/0036-a-merge-resolves-the-canonical-entitys-fields.md`, following the
shape of `docs/adr/0035-provenance-is-a-value-object.md`. No counts, no file
tables — those belong in commit messages.

Decisions to record:

1. A merge decides `description`, `external_ids` and `properties` on the
   canonical entity only, because a merge does not touch the entities it
   absorbs.
2. The decision is recorded in `EntitiesMerged` as a before/after pair, not
   recomputed by the projection — ADR 0004's rule applied to entity data,
   exactly as `redirections` already applies it to edges.
3. Strategy selection is a `PropertyMergePolicy` keyed by dotted path, with
   three resolution tiers.
4. `UNION` outside `properties` is refused at policy construction, because the
   alternative failure is a pydantic error inside a fold with the event
   already durable.
5. A `None` description is silence; an explicit `None` property value is a
   claim.

**Re-check the number against `main` before merging.** 0035 is the highest as
of this plan's writing; a parallel branch landing first means renumbering the
filename, the H1 **and every inbound citation**.

- [ ] **Step 4: Amend ADR 0001**

Add to its **Status**: "Amended by ADR 0036 (`EntitiesMerged` gains a field
resolution and goes to version 2; `MergeUndone` gains its restoration)." Add a
Consequences paragraph saying the same. Do not touch its Decisions.

- [ ] **Step 5: Update `BACKLOG.md`**

- **Delete B127 entirely.** Its work is done.
- **Shrink B28** to `DEEP_MERGE` alone: delete the "and nothing calls the rest"
  half of the title and the paragraph saying nothing calls `resolve`, and
  rewrite the opening to say the strategies are now reached through
  `plan_properties`. Keep every word of the `DEEP_MERGE` reasoning.
- **Leave B128 alone.**
- Add any deferral this branch created. If there is none, add none — do not
  invent an entry.

- [ ] **Step 6: Add the failure-shape row to `CLAUDE.md`**

Only if one of the deliberate breaks in Tasks 1–5 stayed green and revealed a
weak fixture. If every break failed as expected, change nothing in `CLAUDE.md`
and say so in your report — a row added for a defect that never happened is
doc rot.

- [ ] **Step 7: Build the docs**

Run: `uv run mkdocs build --strict`
Expected: clean. A broken inbound ADR link fails here, which is the mechanism
that exists for exactly the renumbering hazard in Step 3.

- [ ] **Step 8: Commit**

```bash
git add src/redstring/__init__.py docs/ BACKLOG.md CLAUDE.md
git commit -F <message file>
```

Subject: `Export the merge policy, record ADR 0036, and close B127`.

---

## Self-Review

**Spec coverage.** §1 targets → Task 3 (`plan_properties` handles all three).
§2 dotted paths → Task 1. §3 UNION refusal → Task 1. §4 silence asymmetry →
Task 1. §5 payload → Task 2, plus `MergeRecord` in Task 4. §6 planning and the
service read → Tasks 3 and 4. §7 applying → Task 5. §8 public surface →
Task 6. "Against the existing ADRs" → Task 6. Verification section →
the deliberate-break steps in Tasks 1–5 and the round-trip in Task 5. No gaps.

**Type consistency.** `PropertyMergePolicy`, `MergeableFields`,
`PropertyResolution`, `plan_properties`, `strategy_for`, `MERGEABLE_FIELDS`
and `claims_for(path, ...)` are spelled identically in every task that names
them. `resolution` is the parameter name on the aggregate and the event;
`merge_policy` on the two constructors and `policy` on the per-call override —
deliberately different, because one is a default and the other overrides it.

**Known soft spot.** Tasks 4 and 5 tell the implementer to follow existing test
fixtures rather than quoting them, because those modules' setup is long and
copying it into this plan would put a second, drifting copy in the tree. Each
of those steps names the module and the idiom to match. If a fixture turns out
not to exist as described, that is a plan defect: the controller rules on it
and records the ruling rather than the implementer inventing a parallel rig.
