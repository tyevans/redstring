"""An independent account of what a graph holds, for comparing before to after.

**Independent of the code under test, and that is the whole point.** The
round-trip claim is "merge then undo reproduces the pre-merge graph exactly",
and a comparison built out of `plan_redirections` or `GraphProjection` would be
checking that those two agree with themselves.

So this module knows nothing about redirections, aliases or merges. It reads a
`GraphStore` through the port and writes down what is there, in a form two
snapshots can be compared in. Every read goes through the port for the same
reason `tests/unit/projections/conftest.py` gives: a dump reaching into an
adapter's internals would pass on a store that had diverged from what its own
port reports.

`tests/unit/projections/log_builder.py` is deliberately *not* reused. Its own
docstring records that its oracle is independent of the fold but not of
`_redirections_for`, which is the same computation this slice's service
performs -- so it would agree with a wrong plan.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redstring.domain.ids import TenantId
    from redstring.ports.graph_store import GraphStore

#: Page size for the entity cursor. Small on purpose: the paging loop is part
#: of what is being trusted, and a page size larger than any test's graph would
#: never exercise a second page.
PAGE = 3

#: A `find_entities` cursor that fails to advance would hang rather than fail,
#: and a hanging test reads as infrastructure trouble in CI and gets retried.
MAX_PAGES = 500


async def snapshot(store: GraphStore, tenant_id: TenantId) -> dict:
    """Everything `tenant_id` has, as comparable plain data.

    Entities, their relationships and their aliases. Sorted throughout, because
    `get_relationships_for` and `find_by_blocking_keys` promise no order and
    two snapshots must not differ over one.
    """
    entities = await _all_entities(store, tenant_id)
    entity_ids = [entity.id for entity in entities]
    relationships = await store.get_relationships_for(entity_ids, tenant_id)
    aliases = [
        alias
        for entity_id in entity_ids
        for alias in await store.find_aliases(entity_id, tenant_id)
    ]
    return {
        "entities": sorted((entity.model_dump(mode="json") for entity in entities), key=str),
        "relationships": sorted((edge.model_dump(mode="json") for edge in relationships), key=str),
        "aliases": sorted((alias.model_dump(mode="json") for alias in aliases), key=str),
    }


async def _all_entities(store: GraphStore, tenant_id: TenantId) -> list:
    """Every entity of a tenant, paged through the cursor the port defines."""
    entities: list = []
    after = None
    for _ in range(MAX_PAGES):
        page = await store.find_entities(tenant_id, limit=PAGE, after=after)
        entities.extend(page)
        if len(page) < PAGE:
            return entities
        after = page[-1].id
    raise AssertionError(
        f"paging tenant {tenant_id} did not terminate in {MAX_PAGES} pages; the "
        f"`after` cursor is probably not advancing"
    )
