"""The habit, enforced rather than written down.

**Every event type in `KG_EVENT_TYPES` must appear in a log that the pinned
replay cases actually project.** Slice 3 learned the cost of the written-down
version four times over: four read methods each shipped with full behavioural
tests and no mutation-isolation test, because the rule lived in prose. The
equivalent rule here is that a new event class arrives with a replay case, and
the equivalent enforcement is this module.

It works the same way as `tests/unit/graph/test_compliance_coverage.py`: derive
the list from the source of truth by introspection, project the pinned
scenarios, and fail on anything the scenarios never emitted. Adding an event
and forgetting the case is then a red test with a message naming the event,
rather than an omission nobody notices until a rebuild is wrong in production.
"""

from __future__ import annotations

import pytest
from eventsource.adapters.memory import InMemorySnapshotStore

from kg_builder.events import KG_EVENT_TYPES

from .conftest import fresh_rig
from .log_builder import build_log
from .test_replay_equivalence import PINNED


@pytest.fixture
async def event_types_the_pinned_cases_emit():
    emitted = set()
    for scenario in PINNED.values():
        rig = fresh_rig()
        await build_log(rig.event_store, InMemorySnapshotStore(), scenario)
        async for envelope in rig.event_store.read_all():
            emitted.add(type(envelope.event))
    return emitted


@pytest.mark.parametrize("event_type", KG_EVENT_TYPES, ids=lambda t: t.__name__)
async def test_every_event_type_is_replayed_by_a_pinned_case(
    event_type, event_types_the_pinned_cases_emit
):
    assert event_type in event_types_the_pinned_cases_emit, (
        f"{event_type.__name__} is in KG_EVENT_TYPES but no pinned scenario in "
        f"test_replay_equivalence.PINNED produces one, so nothing proves it "
        f"replays. Add a scenario that emits it."
    )


@pytest.mark.parametrize("event_type", KG_EVENT_TYPES, ids=lambda t: t.__name__)
def test_every_event_type_has_a_projection_handler(event_type):
    """An event no projection folds is a fact the read models never learn.

    That is occasionally the right answer -- an event kept purely for audit --
    but never the right *accident*, so it has to be argued for here rather
    than discovered when a query comes back empty.
    """
    from kg_builder.graph.adapters.memory import InMemoryGraphStore
    from kg_builder.projections import GraphProjection, VectorProjection
    from kg_builder.vector.adapters.memory import InMemoryVectorStore

    handled: set = set()
    handled.update(GraphProjection(InMemoryGraphStore()).subscribed_to())
    handled.update(VectorProjection(InMemoryVectorStore(dimension=4)).subscribed_to())

    assert event_type in handled, (
        f"{event_type.__name__} is in KG_EVENT_TYPES but neither projection "
        f"handles it, so folding the log silently ignores it"
    )
