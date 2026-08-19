"""Clustering a tenant's graph and describing each cluster. No mocks.

Ids are `UUID(int=n)`, never `uuid4()`. Three rows of CLAUDE.md's failure
table are tests that passed because a random id happened to sort the right
way, and every ordering claim here -- which member the prompt shows first,
which theme comes first -- is decided by an id comparison somewhere.

The topology is a **barbell** wherever one is enough: two triangles joined by
a single edge. A chain would let "one community" and "two communities" be the
same answer for the wrong reason, and a partition of a chain is exactly the
input on which a clustering that does nothing looks correct.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest

from redstring.composition.themes import CommunityReport, summarize_themes
from redstring.domain.chunk import StoredChunk, chunk_id
from redstring.domain.entity import Entity
from redstring.domain.exceptions import EmptyCompletionError
from redstring.domain.provenance import ExtractionMethod, Provenance
from redstring.domain.relationship import Relationship
from redstring.graph.adapters.memory import InMemoryGraphStore
from redstring.temporal.query import CursorStalledError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import BaseModel

    from redstring.domain.chunk import ChunkId
    from redstring.domain.ids import EntityId, TenantId

pytestmark = pytest.mark.asyncio

OBSERVED = datetime(2026, 8, 18, 9, 30, tzinfo=UTC)
TENANT = UUID(int=0x7E)
OTHER_TENANT = UUID(int=0x7F)


def eid(n: int) -> UUID:
    """A pinned entity id. `UUID(int=n)` renders in ascending order of `n`."""
    return UUID(int=n)


def entity(n: int, name: str, *, tenant: UUID = TENANT, description: str | None = None) -> Entity:
    return Entity(
        id=eid(n),
        tenant_id=tenant,
        name=name,
        normalized_name=name.lower(),
        entity_type="Person",
        description=description,
        provenance=Provenance(
            observed_at=OBSERVED,
            extraction_method=ExtractionMethod.LLM,
            confidence=0.9,
            source_id="doc-1",
        ),
    )


def edge(n: int, source: int, target: int, *, tenant: UUID = TENANT) -> Relationship:
    return Relationship(
        id=UUID(int=0x1000 + n),
        tenant_id=tenant,
        source_entity_id=eid(source),
        target_entity_id=eid(target),
        relationship_type="KNOWS",
        confidence=0.8,
    )


class RecordingProvider:
    """An `LlmProvider` that remembers every prompt and can be told to fail.

    Asserting on `calls` is the only way "the prompt showed the passages" and
    "the prompt showed nothing" are different tests.
    """

    def __init__(self, *, fail_titles: Sequence[str] = ()) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self._fail_titles = set(fail_titles)

    @property
    def model(self) -> str:
        return "fake/recording"

    async def extract[S: BaseModel](
        self,
        text: str,
        schema: type[S],
        *,
        system_prompt: str | None = None,
    ) -> S:
        self.calls.append((text, system_prompt))
        # Name the report after the first entity listed, so a test can tell
        # which community produced which theme without the model inventing
        # anything.
        first = next(line[2:].split(" (")[0] for line in text.splitlines() if line.startswith("- "))
        if first in self._fail_titles:
            raise EmptyCompletionError(model=self.model)
        return schema.model_validate({"title": first, "summary": f"about {first}"})

    @property
    def prompts(self) -> list[str]:
        return [text for text, _ in self.calls]


class OneChunkPerEntity:
    """A `ChunkReader` holding one passage per entity."""

    def __init__(self, texts: dict[int, str]) -> None:
        self._texts = texts

    async def get(self, chunk_id_: ChunkId, tenant_id: TenantId) -> StoredChunk | None:
        raise NotImplementedError

    async def get_by_source(self, source_id: str, tenant_id: TenantId) -> list[StoredChunk]:
        raise NotImplementedError

    async def get_by_entity(self, entity_id: EntityId, tenant_id: TenantId) -> list[StoredChunk]:
        text = self._texts.get(entity_id.int)
        if text is None:
            return []
        return [
            StoredChunk(
                id=chunk_id("doc-1", text),
                tenant_id=tenant_id,
                source_id="doc-1",
                text=text,
                chunk_index=0,
                start_char=0,
                end_char=len(text),
                entity_ids=[entity_id],
            )
        ]

    async def close(self) -> None:
        return None


async def barbell() -> InMemoryGraphStore:
    """Two triangles, {1,2,3} and {4,5,6}, joined by the single edge 3--4."""
    store = InMemoryGraphStore()
    await store.upsert_entities(
        [
            entity(1, "Ada"),
            entity(2, "Bea"),
            entity(3, "Cal"),
            entity(4, "Dee"),
            entity(5, "Eve"),
            entity(6, "Fay"),
        ]
    )
    await store.upsert_relationships(
        [
            edge(1, 1, 2),
            edge(2, 2, 3),
            edge(3, 1, 3),
            edge(4, 4, 5),
            edge(5, 5, 6),
            edge(6, 4, 6),
            edge(7, 3, 4),
        ]
    )
    return store


class TestPartitioning:
    async def test_a_barbell_is_two_themes(self):
        store = await barbell()
        provider = RecordingProvider()
        report = await summarize_themes(TENANT, graph=store, relationships=store, provider=provider)
        assert report.communities == 2
        assert [set(theme.members) for theme in report.themes] == [
            {eid(1), eid(2), eid(3)},
            {eid(4), eid(5), eid(6)},
        ]
        assert report.too_small == 0
        assert report.failed == 0

    async def test_one_triangle_is_one_theme(self):
        """The counterpart the barbell exists for: same code, one cluster."""
        store = InMemoryGraphStore()
        await store.upsert_entities([entity(1, "Ada"), entity(2, "Bea"), entity(3, "Cal")])
        await store.upsert_relationships([edge(1, 1, 2), edge(2, 2, 3), edge(3, 1, 3)])
        report = await summarize_themes(
            TENANT, graph=store, relationships=store, provider=RecordingProvider()
        )
        assert len(report.themes) == 1

    async def test_themes_are_largest_first(self):
        """A four-clique and a triangle, written smallest-first into the store.

        The store hands entities back in id order, so the larger community's
        ids are the *higher* ones -- an implementation returning them
        unsorted, or sorted by first member, returns the triangle first.
        """
        store = InMemoryGraphStore()
        await store.upsert_entities([entity(n, f"E{n}") for n in range(1, 8)])
        await store.upsert_relationships(
            [
                edge(1, 1, 2),
                edge(2, 2, 3),
                edge(3, 1, 3),
                edge(4, 4, 5),
                edge(5, 5, 6),
                edge(6, 6, 7),
                edge(7, 4, 6),
                edge(8, 5, 7),
                edge(9, 4, 7),
            ]
        )
        report = await summarize_themes(
            TENANT, graph=store, relationships=store, provider=RecordingProvider()
        )
        assert [len(theme.members) for theme in report.themes] == [4, 3]
        assert set(report.themes[0].members) == {eid(4), eid(5), eid(6), eid(7)}

    async def test_an_empty_tenant_yields_nothing_and_asks_nothing(self):
        store = InMemoryGraphStore()
        provider = RecordingProvider()
        report = await summarize_themes(TENANT, graph=store, relationships=store, provider=provider)
        assert report == report.__class__(
            themes=(), communities=0, too_small=0, failed=0, dangling_edges=0
        )
        assert provider.calls == []


class TestTenantIsolation:
    async def test_another_tenants_graph_contributes_nothing(self):
        """The other tenant holds a barbell of its own under the same ids.

        Same ids on purpose: an edge read made under the wrong tenant would
        find them, and every assertion below would still be about six
        entities. What it could not do is find *these* six entities' edges.
        """
        store = await barbell()
        await store.upsert_entities([entity(n, f"X{n}", tenant=OTHER_TENANT) for n in range(1, 7)])
        await store.upsert_relationships(
            [edge(n, s, t, tenant=OTHER_TENANT) for n, (s, t) in enumerate([(1, 4), (2, 5)], 20)]
        )
        report = await summarize_themes(
            TENANT, graph=store, relationships=store, provider=RecordingProvider()
        )
        assert [set(theme.members) for theme in report.themes] == [
            {eid(1), eid(2), eid(3)},
            {eid(4), eid(5), eid(6)},
        ]


class TestPagination:
    async def test_an_edge_spanning_two_pages_is_counted_once(self):
        """`page_size=3` over six entities, with 1--4 crossing the boundary.

        Entities 1--2, 1--4 and 3--4: two pairs joined by the spanning edge.
        Counted once, the partition is `{1,2}` and `{3,4}`; counted twice, the
        spanning edge outweighs both intra-pair edges and the four collapse
        into one community. So the membership itself is the assertion, and a
        corpus fitting in one page cannot produce it.
        """
        store = InMemoryGraphStore()
        await store.upsert_entities([entity(n, f"E{n}") for n in range(1, 7)])
        await store.upsert_relationships([edge(1, 1, 2), edge(2, 1, 4), edge(3, 3, 4)])
        report = await summarize_themes(
            TENANT,
            graph=store,
            relationships=store,
            provider=RecordingProvider(),
            page_size=3,
        )
        assert [set(theme.members) for theme in report.themes] == [
            {eid(1), eid(2)},
            {eid(3), eid(4)},
        ]
        # 5 and 6 are in no edge, so each is its own singleton.
        assert report.too_small == 2

    async def test_the_store_really_returns_the_spanning_edge_twice(self):
        """The premise of the test above, asserted rather than assumed.

        Without this, a page size that silently stopped splitting the corpus
        would make the dedup test vacuous and nothing would say so.
        """
        store = await barbell()
        returned: list[UUID] = []

        class Counting:
            async def get_relationships_for(self, entity_ids, tenant_id, **kwargs):
                found = await store.get_relationships_for(entity_ids, tenant_id, **kwargs)
                returned.extend(relationship.id for relationship in found)
                return found

        await summarize_themes(
            TENANT,
            graph=store,
            relationships=Counting(),  # type: ignore[arg-type]
            provider=RecordingProvider(),
            page_size=3,
        )
        assert returned.count(UUID(int=0x1007)) == 2

    async def test_a_cursor_that_never_advances_fails_rather_than_hangs(self):
        class Stuck:
            async def find_entities(self, tenant_id, **kwargs):
                return [entity(1, "Ada"), entity(2, "Bea")]

        with pytest.raises(CursorStalledError):
            await summarize_themes(
                TENANT,
                graph=Stuck(),  # type: ignore[arg-type]
                relationships=InMemoryGraphStore(),
                provider=RecordingProvider(),
                page_size=2,
            )


class TestMinimumSize:
    async def test_a_singleton_gets_no_call_and_is_counted(self):
        store = await barbell()
        await store.upsert_entity(entity(9, "Lonely"))
        provider = RecordingProvider()
        report = await summarize_themes(TENANT, graph=store, relationships=store, provider=provider)
        assert report.communities == 3
        assert report.too_small == 1
        assert len(report.themes) == 2
        assert len(provider.calls) == 2
        assert all("Lonely" not in prompt for prompt in provider.prompts)

    async def test_min_size_above_a_communitys_size_excludes_it(self):
        """A triangle and a pair, with `min_size=3`: only the triangle survives."""
        store = InMemoryGraphStore()
        await store.upsert_entities([entity(n, f"E{n}") for n in range(1, 6)])
        await store.upsert_relationships(
            [edge(1, 1, 2), edge(2, 2, 3), edge(3, 1, 3), edge(4, 4, 5)]
        )
        report = await summarize_themes(
            TENANT,
            graph=store,
            relationships=store,
            provider=RecordingProvider(),
            min_size=3,
        )
        assert report.too_small == 1
        assert [len(theme.members) for theme in report.themes] == [3]

    async def test_min_size_one_admits_the_singleton(self):
        """The boundary, pinned as an example: `min_size=1` summarises everything."""
        store = await barbell()
        await store.upsert_entity(entity(9, "Lonely"))
        report = await summarize_themes(
            TENANT,
            graph=store,
            relationships=store,
            provider=RecordingProvider(),
            min_size=1,
        )
        assert report.too_small == 0
        assert len(report.themes) == 3


class TestWhatTheModelIsShown:
    async def test_the_prompt_names_the_members_and_their_types(self):
        store = await barbell()
        provider = RecordingProvider()
        await summarize_themes(TENANT, graph=store, relationships=store, provider=provider)
        first = provider.prompts[0]
        assert "- Ada (Person)" in first
        assert "- Bea (Person)" in first
        assert "Dee" not in first

    async def test_a_description_is_shown_when_there_is_one(self):
        store = InMemoryGraphStore()
        await store.upsert_entities(
            [entity(1, "Ada", description="a mathematician"), entity(2, "Bea")]
        )
        await store.upsert_relationships([edge(1, 1, 2)])
        provider = RecordingProvider()
        await summarize_themes(TENANT, graph=store, relationships=store, provider=provider)
        assert "- Ada (Person) -- a mathematician" in provider.prompts[0]
        assert provider.prompts[0].endswith("- Bea (Person)")

    async def test_passages_reach_the_prompt_when_a_chunk_reader_is_given(self):
        store = await barbell()
        provider = RecordingProvider()
        report = await summarize_themes(
            TENANT,
            graph=store,
            relationships=store,
            provider=provider,
            chunks=OneChunkPerEntity({1: "Ada wrote the first program."}),
        )
        assert "Ada wrote the first program." in provider.prompts[0]
        assert report.themes[0].passages_shown == 1

    async def test_without_a_chunk_reader_no_passage_is_shown(self):
        """The other half of the test above; alone, either would pass on both."""
        store = await barbell()
        provider = RecordingProvider()
        report = await summarize_themes(TENANT, graph=store, relationships=store, provider=provider)
        assert "Passages:" not in provider.prompts[0]
        assert report.themes[0].passages_shown == 0

    async def test_passages_are_capped_and_taken_from_the_centre_outwards(self):
        store = await barbell()
        provider = RecordingProvider()
        report = await summarize_themes(
            TENANT,
            graph=store,
            relationships=store,
            provider=provider,
            chunks=OneChunkPerEntity({1: "about Ada", 2: "about Bea", 3: "about Cal"}),
            max_passages_shown=1,
        )
        # Within {1,2,3} the bridge to the other triangle makes `Cal` degree 3
        # while `Ada` and `Bea` have 2, so `Cal` is shown first and its
        # passage is the one that survives the cap.
        assert "about Cal" in provider.prompts[0]
        assert "about Ada" not in provider.prompts[0]
        assert report.themes[0].passages_shown == 1

    async def test_the_system_prompt_is_passed_through(self):
        store = await barbell()
        provider = RecordingProvider()
        await summarize_themes(
            TENANT,
            graph=store,
            relationships=store,
            provider=provider,
            system_prompt="be terse",
        )
        assert {system for _, system in provider.calls} == {"be terse"}

    async def test_capping_membership_shows_the_most_connected_and_says_so(self):
        store = await barbell()
        provider = RecordingProvider()
        report = await summarize_themes(
            TENANT,
            graph=store,
            relationships=store,
            provider=provider,
            max_members_shown=2,
        )
        theme = report.themes[0]
        assert theme.members_shown == 2
        assert len(theme.members) == 3
        assert "The 2 most connected are listed." in provider.prompts[0]
        # `Cal` carries the bridge, so it outranks both its triangle-mates.
        assert "- Cal (Person)" in provider.prompts[0]


class TestFailures:
    async def test_a_failed_call_aborts_by_default(self):
        store = await barbell()
        with pytest.raises(EmptyCompletionError):
            await summarize_themes(
                TENANT,
                graph=store,
                relationships=store,
                provider=RecordingProvider(fail_titles=["Cal"]),
            )

    async def test_a_failure_followed_by_a_success_is_skipped_and_counted(self):
        """The bad element is *first*, and a good one follows it.

        On a run where the only failure is the last community, `break` and
        `continue` are the same function -- so the failing community here is
        the one that comes first in the report's order.
        """
        store = await barbell()
        report = await summarize_themes(
            TENANT,
            graph=store,
            relationships=store,
            provider=RecordingProvider(fail_titles=["Cal"]),
            skip_failed=True,
        )
        assert report.failed == 1
        assert report.communities == 2
        assert [theme.title for theme in report.themes] == ["Dee"]

    async def test_dangling_edges_are_dropped_and_counted(self):
        """An edge whose other endpoint the scan never returned.

        Produced here by a relationship store that answers with one more edge
        than the graph holds -- which is what a write landing between two
        pages looks like from inside this function.
        """
        store = await barbell()

        class WithAGhost:
            async def get_relationships_for(self, entity_ids, tenant_id, **kwargs):
                found = await store.get_relationships_for(entity_ids, tenant_id, **kwargs)
                if eid(1) in entity_ids:
                    found = [*found, edge(99, 1, 4242)]
                return found

        report = await summarize_themes(
            TENANT,
            graph=store,
            relationships=WithAGhost(),  # type: ignore[arg-type]
            provider=RecordingProvider(),
        )
        assert report.dangling_edges == 1
        assert len(report.themes) == 2


class TestGuards:
    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"page_size": 0}, "page_size must be at least 1, got 0"),
            ({"min_size": 0}, "min_size must be at least 1, got 0"),
            ({"max_members_shown": 0}, "max_members_shown must be at least 1, got 0"),
            ({"max_passages_shown": -1}, "max_passages_shown must not be negative, got -1"),
        ],
    )
    async def test_a_setting_below_its_floor_is_refused(self, kwargs: dict[str, Any], message: str):
        """The message names the setting, so four guards cannot share one test.

        A bare `pytest.raises(ValueError)` here would pass with every guard
        wired to the wrong parameter.
        """
        store = InMemoryGraphStore()
        with pytest.raises(ValueError, match=re.escape(message)):
            await summarize_themes(
                TENANT,
                graph=store,
                relationships=store,
                provider=RecordingProvider(),
                **kwargs,
            )

    async def test_read_only_collaborators_are_enough(self):
        """Nothing is written, so nothing that can write need be supplied.

        The two objects here have no write method at all. ADR 0042's
        "recomputed, never stored" is a claim about behaviour that the
        narrowed annotations only advertise; this is what checks it.
        """
        real = await barbell()

        class ReadsEntities:
            async def find_entities(self, tenant_id, **kwargs):
                return await real.find_entities(tenant_id, **kwargs)

        class ReadsEdges:
            async def get_relationships_for(self, entity_ids, tenant_id, **kwargs):
                return await real.get_relationships_for(entity_ids, tenant_id, **kwargs)

        report = await summarize_themes(
            TENANT,
            graph=ReadsEntities(),  # type: ignore[arg-type]
            relationships=ReadsEdges(),  # type: ignore[arg-type]
            provider=RecordingProvider(),
        )
        assert len(report.themes) == 2

    async def test_the_schema_asked_for_is_the_community_report(self):
        store = await barbell()
        seen: list[type] = []

        class Watching(RecordingProvider):
            async def extract(self, text, schema, *, system_prompt=None):
                seen.append(schema)
                return await super().extract(text, schema, system_prompt=system_prompt)

        await summarize_themes(TENANT, graph=store, relationships=store, provider=Watching())
        assert seen == [CommunityReport, CommunityReport]
