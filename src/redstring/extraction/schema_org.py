"""Schema.org and Open Graph entity extraction.

Extracts entities from structured data embedded in a document:

- JSON-LD Schema.org markup
- Microdata
- Open Graph metadata

## Entity type is a string, and that is a decision (slice 9)

`SCHEMA_TYPE_MAP` used to map onto `models.extracted_entity.EntityType`, an
enum that died with the relational layer. It was **not** replaced with an enum
in `domain/`, and the reasons are worth keeping because "a deleted module
defined one, so we need one" is the argument that would put it back:

- `domain/entity.py` already decided this. `Entity.entity_type` is a `str`,
  and `extraction/mapping.py::entity_id_for` hashes it as one. A `domain`
  enum would either duplicate that freedom or contradict it, and if it
  contradicted it, every id in the log would depend on which.
- **The deleted enum had already conceded the point.** Its own docstring said
  the column was `String(100)` "to support dynamic domain-specific types",
  and it carried `is_valid` / `get_or_none` -- two helpers whose entire
  purpose was to answer "this is not one of mine" without raising. An enum
  that needs a way to say a value is legitimately outside it is a vocabulary,
  not a type.
- `extraction/domains/` defines entity types per domain in YAML, an open set
  by construction. A closed enum beside an open registry is two answers to
  one question.
- The live-model suite deliberately does not pin which `entity_type` a model
  assigns (see BACKLOG B12); an enum would make that unpinnable claim a
  validation error instead.

So the eight values below are **this extractor's vocabulary** -- what
Schema.org's type hierarchy collapses to here -- and not the library's. They
are pinned as literals in `tests/unit/extraction/test_schema_org.py`, which
is now the only record of what the enum's members were.
"""

import logging
from typing import Any

from redstring.domain.provenance import ExtractionMethod

logger = logging.getLogger(__name__)

# Mapping from Schema.org types to our entity types
SCHEMA_TYPE_MAP = {
    # Person types
    "Person": "person",
    "Author": "person",
    # Organization types
    "Organization": "organization",
    "Corporation": "organization",
    "LocalBusiness": "organization",
    "Company": "organization",
    "EducationalOrganization": "organization",
    "GovernmentOrganization": "organization",
    "NGO": "organization",
    "SportsOrganization": "organization",
    # Location types
    "Place": "location",
    "City": "location",
    "Country": "location",
    "AdministrativeArea": "location",
    "GeoCoordinates": "location",
    "PostalAddress": "location",
    "Landmark": "location",
    # Event types
    "Event": "event",
    "BusinessEvent": "event",
    "ChildrensEvent": "event",
    "ComedyEvent": "event",
    "CourseInstance": "event",
    "DanceEvent": "event",
    "DeliveryEvent": "event",
    "EducationEvent": "event",
    "ExhibitionEvent": "event",
    "Festival": "event",
    "FoodEvent": "event",
    "Hackathon": "event",
    "LiteraryEvent": "event",
    "MusicEvent": "event",
    "PublicationEvent": "event",
    "SaleEvent": "event",
    "ScreeningEvent": "event",
    "SocialEvent": "event",
    "SportsEvent": "event",
    "TheaterEvent": "event",
    "VisualArtsEvent": "event",
    # Product types
    "Product": "product",
    "ProductModel": "product",
    "IndividualProduct": "product",
    "SoftwareApplication": "product",
    "MobileApplication": "product",
    "WebApplication": "product",
    "Book": "product",
    "Movie": "product",
    "MusicAlbum": "product",
    "VideoGame": "product",
    # Document types
    "Article": "document",
    "NewsArticle": "document",
    "BlogPosting": "document",
    "ScholarlyArticle": "document",
    "TechArticle": "document",
    "Report": "document",
    "WebPage": "document",
    "CreativeWork": "document",
    # Date-related
    "Date": "date",
    "DateTime": "date",
    # Concept types
    "Thing": "concept",
    "Intangible": "concept",
}


def extract_entities_from_schema_org(schema_data: list[Any]) -> list[dict[str, Any]]:
    """
    Extract entities from Schema.org JSON-LD data.

    Args:
        schema_data: List of JSON-LD objects from the page

    Returns:
        List of entity dictionaries with type, name, properties, etc.
    """
    entities = []

    for item in schema_data:
        if not isinstance(item, dict):
            continue

        try:
            entity = _extract_entity_from_schema_item(item)
            if entity:
                entities.append(entity)

            # Also extract nested entities
            nested = _extract_nested_entities(item)
            entities.extend(nested)

        except Exception as e:
            logger.warning(f"Failed to extract entity from schema item: {e}")
            continue

    logger.debug(f"Extracted {len(entities)} entities from Schema.org data")
    return entities


def _extract_entity_from_schema_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """
    Extract an entity from a single Schema.org item.

    Args:
        item: Schema.org JSON-LD object

    Returns:
        Entity dictionary or None
    """
    # Get type
    schema_type = item.get("@type")
    if not schema_type:
        return None

    # Handle array types (take first)
    if isinstance(schema_type, list):
        schema_type = schema_type[0]

    # Map to our entity type
    entity_type = SCHEMA_TYPE_MAP.get(schema_type)
    if not entity_type:
        # Use CONCEPT as fallback for unknown types
        entity_type = "concept"

    # Get name
    name = _get_name_from_item(item)
    if not name:
        return None

    # Get description
    description = item.get("description")
    if isinstance(description, list):
        description = description[0] if description else None

    # Extract external IDs
    external_ids = {}
    if item.get("@id"):
        external_ids["schema_org_id"] = item["@id"]
    if item.get("sameAs"):
        same_as = item["sameAs"]
        if isinstance(same_as, str):
            same_as = [same_as]
        external_ids["same_as"] = same_as

    # Build properties from remaining fields
    properties = _extract_properties(item)

    return {
        "type": entity_type,
        "name": name,
        "description": description,
        "properties": properties,
        "external_ids": external_ids,
        "method": ExtractionMethod.SCHEMA_ORG,
        "confidence": 0.95,  # High confidence for structured data
        "source_text": None,
    }


def _get_name_from_item(item: dict[str, Any]) -> str | None:
    """Get the name from a Schema.org item."""
    # Try various name fields
    for field in ["name", "headline", "title", "alternateName", "legalName"]:
        value = item.get(field)
        if value:
            if isinstance(value, list):
                return str(value[0])
            return str(value)

    # Try to construct from other fields
    if item.get("givenName") or item.get("familyName"):
        parts = [
            item.get("givenName", ""),
            item.get("familyName", ""),
        ]
        return " ".join(p for p in parts if p).strip()

    return None


def _extract_properties(item: dict[str, Any]) -> dict[str, Any]:
    """Extract relevant properties from Schema.org item."""
    properties = {}

    # Common properties to extract
    property_fields = [
        "url",
        "image",
        "logo",
        "email",
        "telephone",
        "address",
        "location",
        "geo",
        "startDate",
        "endDate",
        "datePublished",
        "dateCreated",
        "dateModified",
        "author",
        "creator",
        "publisher",
        "brand",
        "jobTitle",
        "worksFor",
        "memberOf",
        "price",
        "priceCurrency",
        "offers",
        "aggregateRating",
        "review",
        "ratingValue",
        "category",
        "genre",
        "keywords",
    ]

    for field in property_fields:
        value = item.get(field)
        if value:
            # Simplify nested objects
            if isinstance(value, dict):
                if value.get("name"):
                    value = value["name"]
                elif value.get("@value"):
                    value = value["@value"]
                elif value.get("url"):
                    value = value["url"]
            properties[field] = value

    return properties


def _extract_nested_entities(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract entities from nested Schema.org objects."""
    entities = []

    # Fields that may contain nested entities
    nested_fields = [
        "author",
        "creator",
        "publisher",
        "brand",
        "worksFor",
        "memberOf",
        "performer",
        "organizer",
        "location",
        "address",
        "sponsor",
        "funder",
        "mentions",
        "about",
    ]

    for field in nested_fields:
        value = item.get(field)
        if not value:
            continue

        # Handle single object or list
        items_to_process = [value] if isinstance(value, dict) else value
        if not isinstance(items_to_process, list):
            continue

        for nested_item in items_to_process:
            if isinstance(nested_item, dict) and nested_item.get("@type"):
                entity = _extract_entity_from_schema_item(nested_item)
                if entity:
                    entities.append(entity)

    return entities


def extract_entities_from_open_graph(og_data: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Extract entities from Open Graph metadata.

    Args:
        og_data: Open Graph metadata dictionary

    Returns:
        List of entity dictionaries
    """
    entities: list[dict[str, Any]] = []

    if not og_data:
        return entities

    try:
        # Determine type from og:type
        og_type = og_data.get("type", "website")
        entity_type = _map_og_type(og_type)

        # Get title
        title = og_data.get("title")
        if not title:
            return entities

        # Build entity
        entity = {
            "type": entity_type,
            "name": title,
            "description": og_data.get("description"),
            "properties": {
                "url": og_data.get("url"),
                "image": og_data.get("image"),
                "site_name": og_data.get("site_name"),
                "locale": og_data.get("locale"),
            },
            "external_ids": {},
            "method": ExtractionMethod.OPEN_GRAPH,
            "confidence": 0.8,  # Lower confidence than JSON-LD
            "source_text": None,
        }

        # Add type-specific properties
        if og_type.startswith("article"):
            entity["properties"]["published_time"] = og_data.get("article:published_time")
            entity["properties"]["author"] = og_data.get("article:author")
            entity["properties"]["section"] = og_data.get("article:section")

        elif og_type.startswith("product"):
            entity["properties"]["price_amount"] = og_data.get("product:price:amount")
            entity["properties"]["price_currency"] = og_data.get("product:price:currency")

        entities.append(entity)

    except Exception as e:
        logger.warning(f"Failed to extract entity from Open Graph data: {e}")

    return entities


def _map_og_type(og_type: str) -> str:
    """Map Open Graph type to entity type."""
    og_type = og_type.lower()

    if og_type in ("website", "article", "blog"):
        return "document"
    if og_type == "profile":
        return "person"
    if og_type in ("product", "book", "music.album", "video.movie"):
        return "product"
    if og_type in ("place", "business.business"):
        return "location"
    if og_type in ("music.song", "music.playlist", "video.episode"):
        return "document"
    return "concept"
