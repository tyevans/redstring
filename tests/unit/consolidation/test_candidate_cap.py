"""The per-subject candidate cap: which candidates are scored when a block is too large.

Every id here is pinned. A `uuid4()` id sorts differently on each run, so a tie
broken by id would pass about half the time whatever the implementation did.
The names are chosen so that the name-similarity order is not in doubt:
"Ada Lovelac" and "Ada Lovelacx" are one edit from the subject, and the rest
share no more than the type key with it.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from redstring.consolidation.candidates import (
    DEFAULT_MAX_CANDIDATES,
    MAX_CANDIDATES,
    CandidateFinder,
)
from redstring.domain.blocking import blocking_keys_for
from redstring.graph.adapters.memory import InMemoryGraphStore

from .conftest import entity

SUBJECT_ID = UUID("00000000-0000-4000-8000-0000000000a0")
NEAR_ID = UUID("00000000-0000-4000-8000-0000000000a1")
NEARER_ID = UUID("00000000-0000-4000-8000-0000000000a2")
FAR_ID = UUID("00000000-0000-4000-8000-0000000000a3")
FARTHER_ID = UUID("00000000-0000-4000-8000-0000000000a4")
TIE_LOW_ID = UUID("00000000-0000-4000-8000-0000000000b1")
TIE_HIGH_ID = UUID("00000000-0000-4000-8000-0000000000b2")
TIE_OTHER_ID = UUID("00000000-0000-4000-8000-0000000000b3")


def keyed(tenant_id, name, entity_id):
    built = entity(tenant_id, name=name, entity_id=entity_id)
    return built.model_copy(update={"blocking_keys": blocking_keys_for(built)})


class _ReversedBlockStore(InMemoryGraphStore):
    """Returns every block in reverse of the adapter's order.

    The in-memory adapter returns blocks in id order, which is also the
    tie-break order. Without this, "take the first N the store returned" and
    "take the lowest ids" would be the same function, and a test of either
    would pass against the other.
    """

    async def find_by_blocking_keys(self, keys, tenant_id):
        grouped = await super().find_by_blocking_keys(keys, tenant_id)
        return {key: list(reversed(found)) for key, found in grouped.items()}


async def _finder(entities, *, max_candidates):
    store = _ReversedBlockStore()
    await store.upsert_entities(list(entities))
    return CandidateFinder(store, max_candidates=max_candidates)


@pytest.fixture
def tenant():
    return uuid4()


class TestWhichCandidatesAreScored:
    async def test_a_block_larger_than_the_cap_scores_only_the_closest_names(self, tenant):
        subject = keyed(tenant, "Ada Lovelace", SUBJECT_ID)
        entities = [
            subject,
            keyed(tenant, "Ada Lovelac", NEAR_ID),
            keyed(tenant, "Ada Lovelacx", NEARER_ID),
            keyed(tenant, "Zebra Crossing", FAR_ID),
            keyed(tenant, "Quentin Brandt", FARTHER_ID),
        ]
        finder = await _finder(entities, max_candidates=2)

        scored = await finder.candidates(subject)

        assert {candidate.entity.id for candidate in scored} == {NEAR_ID, NEARER_ID}

    async def test_a_block_within_the_cap_is_not_cut(self, tenant):
        subject = keyed(tenant, "Ada Lovelace", SUBJECT_ID)
        entities = [
            subject,
            keyed(tenant, "Ada Lovelac", NEAR_ID),
            keyed(tenant, "Zebra Crossing", FAR_ID),
        ]
        finder = await _finder(entities, max_candidates=2)

        scored = await finder.candidates(subject)

        assert {candidate.entity.id for candidate in scored} == {NEAR_ID, FAR_ID}

    async def test_a_tie_on_name_goes_to_the_lower_entity_id(self, tenant):
        subject = keyed(tenant, "Ada Lovelace", SUBJECT_ID)
        entities = [
            subject,
            keyed(tenant, "Ada Lovelax", TIE_HIGH_ID),
            keyed(tenant, "Ada Lovelax", TIE_LOW_ID),
            keyed(tenant, "Zebra Crossing", TIE_OTHER_ID),
        ]
        finder = await _finder(entities, max_candidates=1)

        scored = await finder.candidates(subject)

        assert [candidate.entity.id for candidate in scored] == [TIE_LOW_ID]

    async def test_the_selection_does_not_depend_on_store_order(self, tenant):
        subject = keyed(tenant, "Ada Lovelace", SUBJECT_ID)
        entities = [
            subject,
            keyed(tenant, "Ada Lovelax", TIE_HIGH_ID),
            keyed(tenant, "Ada Lovelax", TIE_LOW_ID),
            keyed(tenant, "Zebra Crossing", TIE_OTHER_ID),
        ]
        forward = await _finder(entities, max_candidates=1)
        backward = await _finder(list(reversed(entities)), max_candidates=1)

        assert (
            [c.entity.id for c in await forward.candidates(subject)]
            == [c.entity.id for c in await backward.candidates(subject)]
            == [TIE_LOW_ID]
        )


class TestTheCapItself:
    def test_the_default_is_documented_and_within_the_maximum(self):
        assert 1 <= DEFAULT_MAX_CANDIDATES <= MAX_CANDIDATES

    async def test_the_maximum_cap_is_accepted(self, tenant):
        await _finder([], max_candidates=MAX_CANDIDATES)

    async def test_one_above_the_maximum_cap_is_refused(self, tenant):
        with pytest.raises(ValueError, match="max_candidates must be at most"):
            await _finder([], max_candidates=MAX_CANDIDATES + 1)

    async def test_a_cap_below_one_is_refused(self, tenant):
        with pytest.raises(ValueError, match="max_candidates must be at least 1"):
            await _finder([], max_candidates=0)
