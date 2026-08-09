"""Constraining a run to a domain's declared vocabulary.

The assertions here are about the **JSON Schema**, not only about pydantic
validation, because the JSON Schema is what actually reaches the server: a
model whose python validator rejects an off-vocabulary type but whose emitted
schema carries no `enum` constrains nothing at the only place it matters, and
every validation-only test would still pass.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError

from redstring.extraction.constrained import (
    constrained_extraction,
    constrained_extraction_for,
    permitted_entity_types,
)
from redstring.extraction.domains.models import DomainSchema
from redstring.extraction.domains.registry import get_domain_schema
from redstring.extraction.mapping import map_extraction
from redstring.extraction.schema import Extraction

TENANT = UUID("11111111-1111-1111-1111-111111111111")

NEWS = "news_journalism"


def entity_enum(model: type[Extraction]) -> list[str]:
    """The permitted entity types, read off the emitted JSON Schema."""
    schema = model.model_json_schema()
    [definition] = [
        value for key, value in schema["$defs"].items() if "entity_type" in value["properties"]
    ]
    return definition["properties"]["entity_type"]["enum"]


def relationship_enum(model: type[Extraction]) -> list[str]:
    schema = model.model_json_schema()
    [definition] = [
        value
        for key, value in schema["$defs"].items()
        if "relationship_type" in value["properties"]
    ]
    return definition["properties"]["relationship_type"]["enum"]


class TestWhatReachesTheServer:
    def test_the_entity_type_becomes_an_enum_of_the_domains_ids(self):
        model = constrained_extraction(get_domain_schema(NEWS))

        assert entity_enum(model) == [t.id for t in get_domain_schema(NEWS).entity_types]

    def test_the_relationship_type_becomes_an_enum_too(self):
        """Asserted separately: constraining entities and leaving relationship
        types free is a coherent design someone might have built, and no test
        of the entity enum can tell that apart from this one."""
        model = constrained_extraction(get_domain_schema(NEWS))

        assert relationship_enum(model) == [
            t.id for t in get_domain_schema(NEWS).relationship_types
        ]

    def test_the_unconstrained_schema_carries_no_enum(self):
        """The control. Without it, every assertion above could be describing
        `Extraction` itself."""
        schema = Extraction.model_json_schema()
        entity = schema["$defs"]["ExtractedEntity"]["properties"]["entity_type"]

        assert "enum" not in entity
        assert entity["type"] == "string"

    def test_the_order_is_the_schemas_and_not_sorted(self):
        """A domain author who put the common types first meant it, and the
        enum's order reaches the model.

        `news_journalism` declares `person` before `organization` before
        `event`, which is not alphabetical -- so this fails against `sorted`.
        """
        model = constrained_extraction(get_domain_schema(NEWS))

        assert entity_enum(model) != sorted(entity_enum(model))

    def test_the_class_name_carries_the_domain(self):
        """pydantic puts it in the validation error a `MalformedCompletionError`
        wraps, which is the only place a caller learns *which* vocabulary
        refused the answer."""
        assert constrained_extraction(get_domain_schema(NEWS)).__name__.startswith("NewsJournalism")


class TestWhatItAcceptsAndRefuses:
    def test_a_declared_type_validates(self):
        model = constrained_extraction(get_domain_schema(NEWS))

        answer = model.model_validate(
            {"entities": [{"name": "Maria Chen", "entity_type": "person"}]}
        )

        assert answer.entities[0].entity_type == "person"

    def test_an_undeclared_type_is_refused(self):
        model = constrained_extraction(get_domain_schema(NEWS))

        with pytest.raises(ValidationError):
            model.model_validate(
                {"entities": [{"name": "Maria Chen", "entity_type": "chief executive"}]}
            )

    def test_the_unconstrained_schema_accepts_the_same_answer(self):
        """The pair that makes the test above mean something: the payload is
        refused *because of the vocabulary*, not because it is malformed."""
        answer = Extraction.model_validate(
            {"entities": [{"name": "Maria Chen", "entity_type": "chief executive"}]}
        )

        assert answer.entities[0].entity_type == "chief executive"

    def test_an_undeclared_relationship_type_is_refused(self):
        model = constrained_extraction(get_domain_schema(NEWS))

        with pytest.raises(ValidationError):
            model.model_validate(
                {
                    "entities": [
                        {"name": "A", "entity_type": "person"},
                        {"name": "B", "entity_type": "organization"},
                    ],
                    "relationships": [
                        {"source_name": "A", "target_name": "B", "relationship_type": "vibes"}
                    ],
                }
            )


class TestItStaysReadableDownstream:
    def test_the_mapper_reads_a_constrained_answer_unchanged(self):
        """The failure the deleted `generate_json_schema` actually had: it
        renamed the fields, so a model obeying it produced answers
        `map_extraction` cannot read -- and nothing noticed, because its output
        was never passed anywhere.

        Constraining by *subclassing* makes that unrepresentable, and this is
        the assertion that says so rather than the docstring.
        """
        model = constrained_extraction(get_domain_schema(NEWS))
        answer = model.model_validate(
            {
                "entities": [
                    {"name": "Maria Chen", "entity_type": "person"},
                    {"name": "Northwind Energy", "entity_type": "organization"},
                ],
                "relationships": [
                    {
                        "source_name": "Northwind Energy",
                        "target_name": "Maria Chen",
                        "relationship_type": "employs",
                    }
                ],
            }
        )

        mapped = map_extraction(
            answer, tenant_id=TENANT, source_id="doc-1", model="fake/v1", reference_date=None
        )

        assert sorted(e.name for e in mapped.entities) == ["Maria Chen", "Northwind Energy"]
        assert [r.relationship_type for r in mapped.relationships] == ["employs"]
        assert mapped.unresolved_relationships == 0
        assert mapped.dropped_entities == 0

    def test_a_constrained_answer_is_still_an_extraction(self):
        """`ExtractionPipeline` is annotated `type[Extraction]`, and mypy is
        not what runs in production."""
        model = constrained_extraction(get_domain_schema(NEWS))

        assert issubclass(model, Extraction)


class TestChoosingBetweenTheTwo:
    def test_no_domain_means_the_unconstrained_schema_itself(self):
        """Identity, not merely an equivalent schema: `build_graph` passes the
        result straight to the pipeline, whose default is this object."""
        assert constrained_extraction_for(None) is Extraction

    def test_a_domain_means_a_constrained_one(self):
        assert permitted_entity_types(constrained_extraction_for(get_domain_schema(NEWS)))

    def test_the_unconstrained_schema_reports_no_vocabulary(self):
        assert permitted_entity_types(Extraction) == ()


class TestTheAssumptionTheGuardWouldHaveDefended:
    def test_a_domain_cannot_declare_an_empty_vocabulary(self):
        """`Literal[()]` is not a type, so this module would raise an
        unhelpful `TypeError` out of `typing` if a domain could.

        It cannot: both lists are `min_length=1`. The guard that checked for it
        was deleted as unreachable, and this is what replaces it -- relaxing
        either constraint fails here, naming the reason, rather than in
        `constrained_extraction` naming `typing`.
        """
        base = get_domain_schema(NEWS).model_dump()

        with pytest.raises(ValidationError, match="at least 1 item"):
            DomainSchema.model_validate({**base, "entity_types": []})

        with pytest.raises(ValidationError, match="at least 1 item"):
            DomainSchema.model_validate({**base, "relationship_types": []})

    def test_every_bundled_domain_can_be_constrained(self):
        """Six schemas ship with the wheel and each is a caller-reachable
        argument to this function. A domain whose ids `Literal` refuses -- an
        empty string, a duplicate -- would fail here rather than for whoever
        first passed `constrain_to_domain=True`."""
        from redstring.extraction.domains.registry import get_domain_registry

        summaries = get_domain_registry().list_domains()
        assert len(summaries) >= 6

        for summary in summaries:
            model = constrained_extraction(get_domain_schema(summary.domain_id))
            assert permitted_entity_types(model)
