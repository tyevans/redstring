"""Build a knowledge graph from one document, end to end, in one screen.

Everything here comes from `kg_builder`'s public surface. `FakeLlmProvider`
and `InMemoryGraphStore` are real implementations, not mocks -- swap them for
`LangChainLlmProvider` and `Neo4jGraphStore` and nothing else changes.

Executed by `tests/unit/test_end_to_end_example.py`, which also asserts that
every import in this file is from `kg_builder` itself. An example nothing runs
is an example that rots.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from kg_builder import (
    FakeLlmProvider,
    InMemoryGraphStore,
    SourceDocument,
    build_graph,
)

#: What the model "finds" in the text below. A real provider reads the text.
ANSWER = {
    "entities": [
        {"name": "Ada Lovelace", "entity_type": "Person"},
        {"name": "Charles Babbage", "entity_type": "Person"},
        {"name": "Analytical Engine", "entity_type": "Machine"},
    ],
    "relationships": [
        {
            "source_name": "Ada Lovelace",
            "target_name": "Charles Babbage",
            "relationship_type": "WORKED_WITH",
        },
        {
            "source_name": "Charles Babbage",
            "target_name": "Analytical Engine",
            "relationship_type": "DESIGNED",
        },
    ],
}


async def main() -> tuple[list[str], list[str]]:
    """Extract one document into a graph, then query it. Returns what it found."""
    tenant_id = uuid4()
    store = InMemoryGraphStore()

    report = await build_graph(
        SourceDocument(
            id="lovelace-notes",
            text="Ada Lovelace worked with Charles Babbage on the Analytical Engine.",
        ),
        provider=FakeLlmProvider(by_substring={"Ada": ANSWER}),
        store=store,
        tenant_id=tenant_id,
    )

    people = await store.find_entities(tenant_id, entity_type="Person")
    babbage = next(entity for entity in people if entity.name == "Charles Babbage")
    neighbours = await store.neighbors(babbage.id, tenant_id)

    print(f"{report.entities} entities, {report.relationships} relationships")
    return (
        sorted(entity.name for entity in people),
        sorted(entity.name for entity in neighbours),
    )


if __name__ == "__main__":
    asyncio.run(main())
