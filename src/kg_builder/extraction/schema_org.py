"""
Schema.org and Open Graph entity extraction.

Extracts entities from structured data embedded in web pages:
- JSON-LD Schema.org markup
- Microdata
- Open Graph metadata
"""

import logging
from typing import Any, Optional

from kg_builder.models.extracted_entity import EntityType, ExtractionMethod

logger = logging.getLogger(__name__)

# Mapping from Schema.org types to our entity types
SCHEMA_TYPE_MAP = {
    # Person types
    "Person": EntityType.PERSON,
    "Author": EntityType.PERSON,

    # Organization types
    "Organization": EntityType.ORGANIZATION,
    "Corporation": EntityType.ORGANIZATION,
    "LocalBusiness": EntityType.ORGANIZATION,
    "Company": EntityType.ORGANIZATION,
    "EducationalOrganization": EntityType.ORGANIZATION,
    "GovernmentOrganization": EntityType.ORGANIZATION,
    "NGO": EntityType.ORGANIZATION,
    "SportsOrganization": EntityType.ORGANIZATION,

    # Location types
    "Place": EntityType.LOCATION,
    "City": EntityType.LOCATION,
    "Country": EntityType.LOCATION,
    "AdministrativeArea": EntityType.LOCATION,
    "GeoCoordinates": EntityType.LOCATION,
    "PostalAddress": EntityType.LOCATION,
    "Landmark": EntityType.LOCATION,

    # Event types
    "Event": EntityType.EVENT,
    "BusinessEvent": EntityType.EVENT,
    "ChildrensEvent": EntityType.EVENT,
    "ComedyEvent": EntityType.EVENT,
    "CourseInstance": EntityType.EVENT,
    "DanceEvent": EntityType.EVENT,
    "DeliveryEvent": EntityType.EVENT,
    "EducationEvent": EntityType.EVENT,
    "ExhibitionEvent": EntityType.EVENT,
    "Festival": EntityType.EVENT,
    "FoodEvent": EntityType.EVENT,
    "Hackathon": EntityType.EVENT,
    "LiteraryEvent": EntityType.EVENT,
    "MusicEvent": EntityType.EVENT,
    "PublicationEvent": EntityType.EVENT,
    "SaleEvent": EntityType.EVENT,
    "ScreeningEvent": EntityType.EVENT,
    "SocialEvent": EntityType.EVENT,
    "SportsEvent": EntityType.EVENT,
    "TheaterEvent": EntityType.EVENT,
    "VisualArtsEvent": EntityType.EVENT,

    # Product types
    "Product": EntityType.PRODUCT,
    "ProductModel": EntityType.PRODUCT,
    "IndividualProduct": EntityType.PRODUCT,
    "SoftwareApplication": EntityType.PRODUCT,
    "MobileApplication": EntityType.PRODUCT,
    "WebApplication": EntityType.PRODUCT,
    "Book": EntityType.PRODUCT,
    "Movie": EntityType.PRODUCT,
    "MusicAlbum": EntityType.PRODUCT,
    "VideoGame": EntityType.PRODUCT,

    # Document types
    "Article": EntityType.DOCUMENT,
    "NewsArticle": EntityType.DOCUMENT,
    "BlogPosting": EntityType.DOCUMENT,
    "ScholarlyArticle": EntityType.DOCUMENT,
    "TechArticle": EntityType.DOCUMENT,
    "Report": EntityType.DOCUMENT,
    "WebPage": EntityType.DOCUMENT,
    "CreativeWork": EntityType.DOCUMENT,

    # Date-related
    "Date": EntityType.DATE,
    "DateTime": EntityType.DATE,

    # Concept types
    "Thing": EntityType.CONCEPT,
    "Intangible": EntityType.CONCEPT,
}


def extract_entities_from_schema_org(schema_data: list) -> list[dict]:
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


def _extract_entity_from_schema_item(item: dict) -> Optional[dict]:
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
        entity_type = EntityType.CONCEPT

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


def _get_name_from_item(item: dict) -> Optional[str]:
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


def _extract_properties(item: dict) -> dict:
    """Extract relevant properties from Schema.org item."""
    properties = {}

    # Common properties to extract
    property_fields = [
        "url", "image", "logo", "email", "telephone",
        "address", "location", "geo", "startDate", "endDate",
        "datePublished", "dateCreated", "dateModified",
        "author", "creator", "publisher", "brand",
        "jobTitle", "worksFor", "memberOf",
        "price", "priceCurrency", "offers",
        "aggregateRating", "review", "ratingValue",
        "category", "genre", "keywords",
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


def _extract_nested_entities(item: dict) -> list[dict]:
    """Extract entities from nested Schema.org objects."""
    entities = []

    # Fields that may contain nested entities
    nested_fields = [
        "author", "creator", "publisher", "brand",
        "worksFor", "memberOf", "performer", "organizer",
        "location", "address", "sponsor", "funder",
        "mentions", "about",
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


def extract_entities_from_open_graph(og_data: dict) -> list[dict]:
    """
    Extract entities from Open Graph metadata.

    Args:
        og_data: Open Graph metadata dictionary

    Returns:
        List of entity dictionaries
    """
    entities = []

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


def _map_og_type(og_type: str) -> EntityType:
    """Map Open Graph type to entity type."""
    og_type = og_type.lower()

    if og_type in ("website", "article", "blog"):
        return EntityType.DOCUMENT
    elif og_type == "profile":
        return EntityType.PERSON
    elif og_type in ("product", "book", "music.album", "video.movie"):
        return EntityType.PRODUCT
    elif og_type in ("place", "business.business"):
        return EntityType.LOCATION
    elif og_type in ("music.song", "music.playlist", "video.episode"):
        return EntityType.DOCUMENT
    else:
        return EntityType.CONCEPT
