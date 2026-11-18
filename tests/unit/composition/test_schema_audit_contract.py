"""Contract tests for schema audit: registry-id resolution and the page-size guard."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from redstring.composition.schema_audit import audit_schema_conformance, format_schema_mermaid
from redstring.domain.ids import TenantId
from redstring.extraction.domains.loader import get_schema_directory, load_schema_from_file
from redstring.graph.adapters.memory import InMemoryGraphStore

if TYPE_CHECKING:
    from redstring.extraction.domains.models import DomainSchema


def _loaded_literature_fiction() -> DomainSchema:
    return load_schema_from_file("literature_fiction.yaml", schema_dir=get_schema_directory())


def test_a_registry_id_renders_the_same_diagram_as_the_loaded_schema() -> None:
    by_id = format_schema_mermaid("literature_fiction")

    assert by_id == format_schema_mermaid(_loaded_literature_fiction())
    assert by_id.startswith("flowchart LR\n")


def test_an_unknown_registry_id_is_refused_naming_the_id() -> None:
    with pytest.raises(KeyError, match="no_such_domain"):
        format_schema_mermaid("no_such_domain")


async def test_an_audit_by_registry_id_on_an_empty_tenant_conforms_trivially() -> None:
    tenant = TenantId(uuid4())
    graph = InMemoryGraphStore()

    report = await audit_schema_conformance(
        tenant, "literature_fiction", graph=graph, relationships=graph
    )

    assert report.domain_id == "literature_fiction"
    assert report.entities_checked == 0
    assert report.relationships_checked == 0
    assert report.conformance_rate == 1.0
    assert report.violations == ()


@pytest.mark.parametrize("page_size", [0, -2])
async def test_audit_refuses_a_page_size_below_one_naming_it(page_size: int) -> None:
    tenant = TenantId(uuid4())
    graph = InMemoryGraphStore()

    with pytest.raises(ValueError, match=f"page_size must be >= 1, got {page_size}"):
        await audit_schema_conformance(
            tenant, "literature_fiction", graph=graph, relationships=graph, page_size=page_size
        )
