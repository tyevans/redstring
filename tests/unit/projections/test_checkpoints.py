"""Checkpoints advance, and a replay can resume from a reported position.

Two separate mechanisms, easily confused: the **checkpoint** is per
projection and lives in a `ProjectionCheckpoints` repository; the **position**
is per feed and is what `project` takes and returns. This suite pins what each
one means, because resuming from the wrong one skips events.
"""

from __future__ import annotations

from eventsource.adapters.memory import InMemorySnapshotStore

from kg_builder.projections import project

from .conftest import fresh_rig
from .log_builder import build_log
from .test_replay_equivalence import SINGLE, WITH_MERGE


async def _built(scenario):
    rig = fresh_rig()
    built = await build_log(rig.event_store, InMemorySnapshotStore(), scenario)
    return rig, built


class TestCheckpoints:
    async def test_a_projection_that_has_seen_nothing_has_no_checkpoint(self, rig):
        """The zero case. `None` and "the position of nothing" must not be
        confused: resuming from the latter skips the log's first event, and it
        does so silently and forever."""
        assert await rig.projections[0].get_checkpoint() is None

    async def test_the_checkpoint_advances_as_events_are_delivered(self):
        rig, _ = await _built(SINGLE)
        before = await rig.projections[0].get_checkpoint()
        await project(rig.event_store, rig.projections)
        after = await rig.projections[0].get_checkpoint()
        assert before is None
        assert after is not None

    async def test_the_checkpoint_tracks_delivery_and_not_application(self):
        """Both projections end on the **last event of the log**, not on the
        last one they had a handler for.

        `GraphProjection` has no handler for `EntitiesEmbedded` and
        `VectorProjection` none for `DocumentExtracted`, so it would be easy
        to assume each stops at its own last handled event. It does not, and
        that is right: a checkpoint is a resume point, and resuming *before*
        an event a projection deliberately ignored would re-deliver it
        forever. Pinned because the opposite assumption is the natural one and
        would make a resume look like it was losing work.
        """
        rig, _ = await _built(SINGLE)
        events = [envelope.event async for envelope in rig.event_store.read_all()]

        await project(rig.event_store, rig.projections)

        last = str(events[-1].event_id)
        assert await rig.projections[0].get_checkpoint() == last
        assert await rig.projections[1].get_checkpoint() == last


class TestResumingFromAPosition:
    async def test_resuming_mid_log_applies_only_what_follows(self):
        rig, _ = await _built(WITH_MERGE)
        envelopes = [envelope async for envelope in rig.event_store.read_all()]
        assert len(envelopes) >= 3

        resumed = await project(
            rig.event_store, rig.projections, from_position=envelopes[0].position
        )

        assert resumed.applied + resumed.failed == len(envelopes) - 1

    async def test_two_halves_project_to_the_same_state_as_one_whole(self):
        """The property a resume actually has to have. If the halves differed
        from the whole, every restart would leave the read models slightly
        wrong in a way no single run would reveal.
        """
        whole_rig, built = await _built(WITH_MERGE)
        await project(whole_rig.event_store, whole_rig.projections)
        whole = await whole_rig.dump(built.tenant_ids)

        # A *second build* of the same scenario, deliberately -- the two rigs
        # need independent checkpoint state, which sharing one log would not
        # give. The scenario fixes every id, so the two logs agree on
        # everything except wall-clock timestamps, which `_undated` drops. An
        # alias records `merged_at` from the merge event, so it is the only
        # dumped value that differs between two runs of the same scenario;
        # within one log it is fixed, which is what the replay-equivalence
        # suite checks and this test cannot.
        halves_rig, halves_built = await _built(WITH_MERGE)
        envelopes = [envelope async for envelope in halves_rig.event_store.read_all()]
        midpoint = envelopes[len(envelopes) // 2].position

        first = await project(halves_rig.event_store, halves_rig.projections)
        assert first.last_position is not None
        await project(halves_rig.event_store, halves_rig.projections)

        stopped_early = await project(
            halves_rig.event_store, halves_rig.projections, from_position=midpoint
        )
        assert stopped_early.applied > 0

        assert _undated(await halves_rig.dump(halves_built.tenant_ids)) == _undated(whole)


def _undated(dump):
    """`dump` with alias `merged_at` removed. See the caller for why."""
    return {
        tenant_id: {
            key: [
                {field: value for field, value in row.items() if field != "merged_at"}
                for row in rows
            ]
            if key == "aliases"
            else rows
            for key, rows in state.items()
        }
        for tenant_id, state in dump.items()
    }
