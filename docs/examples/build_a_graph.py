"""Build a knowledge graph from one document, end to end, in one screen.

Everything here comes from `redstring`'s public surface. `FakeLlmProvider`
and `InMemoryGraphStore` are real implementations, not mocks -- swap them for
`LangChainLlmProvider` and `Neo4jGraphStore` and *this composition* does not
change. The program does: both need their extra installed, and the LangChain
one needs a chat model constructed first, which is langchain's step rather
than this library's. README.md shows it.

Executed by `tests/unit/test_end_to_end_example.py`, which also asserts that
every import in this file is from `redstring` itself. An example nothing runs
is an example that rots.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

from redstring import (
    FakeEmbeddingProvider,
    FakeLlmProvider,
    InMemoryGraphStore,
    InMemoryVectorStore,
    Retriever,
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


async def main() -> tuple[list[str], list[str], list[str]]:
    """Extract one document into a graph, then query and search it."""
    tenant_id = uuid4()
    store = InMemoryGraphStore()
    embeddings = FakeEmbeddingProvider()
    vectors = InMemoryVectorStore(dimension=embeddings.dimension)

    report = await build_graph(
        SourceDocument(
            id="lovelace-notes",
            text="Ada Lovelace worked with Charles Babbage on the Analytical Engine.",
        ),
        provider=FakeLlmProvider(by_substring={"Ada": ANSWER}),
        store=store,
        tenant_id=tenant_id,
        embedding_provider=embeddings,
        vector_store=vectors,
    )

    people = await store.find_entities(tenant_id, entity_type="Person")
    babbage = next(entity for entity in people if entity.name == "Charles Babbage")
    neighbours = await store.neighbors(babbage.id, tenant_id)

    # Retrieval over the same two stores. The query is *misspelled*, which is
    # the case the lexical channel exists for -- it shares a blocking key with
    # the stored name, and Jaro-Winkler scores it highly.
    retriever = Retriever(embeddings=embeddings, vectors=vectors, graph=store)
    found = await retriever.retrieve("Charles Babage", tenant_id, k=3)

    print(f"{report.entities} entities, {report.relationships} relationships")
    return (
        sorted(entity.name for entity in people),
        sorted(entity.name for entity in neighbours),
        [match.entity.name for match in found.matches],
    )


if __name__ == "__main__":
    asyncio.run(main())
