"""
LLM-based entity extraction using Claude.

Uses Claude's tool use capability to extract structured entities
from unstructured text content.
"""

import asyncio
import json
import logging
import time
from typing import Any, Optional

from kg_builder.config import settings
from kg_builder.models.extracted_entity import EntityType, ExtractionMethod

logger = logging.getLogger(__name__)


# Entity extraction tool definition for Claude
EXTRACTION_TOOL = {
    "name": "extract_entities",
    "description": "Extract named entities from the provided text. Identify people, organizations, locations, events, products, and other significant entities.",
    "input_schema": {
        "type": "object",
        "properties": {
            "entities": {
                "type": "array",
                "description": "List of extracted entities",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "The name of the entity",
                        },
                        "type": {
                            "type": "string",
                            "enum": [
                                "person",
                                "organization",
                                "location",
                                "event",
                                "product",
                                "concept",
                                "document",
                                "date",
                            ],
                            "description": "The type of entity",
                        },
                        "description": {
                            "type": "string",
                            "description": "Brief description of the entity in context",
                        },
                        "context": {
                            "type": "string",
                            "description": "The text snippet where the entity was found",
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                            "description": "Confidence score (0-1) for this extraction",
                        },
                        "properties": {
                            "type": "object",
                            "description": "Additional properties extracted for this entity",
                        },
                    },
                    "required": ["name", "type", "confidence"],
                },
            },
            "relationships": {
                "type": "array",
                "description": "Relationships between extracted entities",
                "items": {
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "description": "Name of the source entity",
                        },
                        "target": {
                            "type": "string",
                            "description": "Name of the target entity",
                        },
                        "relationship": {
                            "type": "string",
                            "description": "Type of relationship (e.g., 'works_for', 'located_in', 'member_of')",
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                        },
                    },
                    "required": ["source", "target", "relationship"],
                },
            },
        },
        "required": ["entities"],
    },
}

EXTRACTION_PROMPT = """Analyze the following text and extract named entities. Focus on:

1. **People**: Names of individuals mentioned
2. **Organizations**: Companies, institutions, groups
3. **Locations**: Places, cities, countries, addresses
4. **Events**: Conferences, meetings, occurrences with dates
5. **Products**: Specific products, services, or technologies
6. **Concepts**: Important ideas, topics, or themes
7. **Documents**: Referenced articles, papers, or publications
8. **Dates**: Specific dates or time periods mentioned

For each entity, provide:
- A clear, canonical name
- The entity type
- A brief description in context
- The text snippet where it was found
- A confidence score (0-1)
- Any additional properties you can extract

Also identify relationships between entities when apparent.

Text to analyze:
---
{text}
---

Use the extract_entities tool to provide your structured extraction."""


class RateLimiter:
    """Simple rate limiter for LLM API calls."""

    def __init__(self, requests_per_minute: int):
        self.rpm = requests_per_minute
        self.interval = 60.0 / requests_per_minute
        self.last_request_time: dict[str, float] = {}

    async def acquire(self, tenant_id: str) -> bool:
        """
        Acquire a rate limit slot for a tenant.

        Args:
            tenant_id: Tenant identifier

        Returns:
            True if slot acquired, False if rate limited
        """
        now = time.time()
        last_time = self.last_request_time.get(tenant_id, 0)

        if now - last_time < self.interval:
            # Rate limited - wait until slot available
            wait_time = self.interval - (now - last_time)
            await asyncio.sleep(wait_time)

        self.last_request_time[tenant_id] = time.time()
        return True


# Global rate limiter instance
_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """Get or create the rate limiter."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter(settings.LLM_RATE_LIMIT_RPM)
    return _rate_limiter


def extract_entities_with_llm(
    text: str,
    tenant_id: str,
    max_text_length: int = 8000,
) -> list[dict]:
    """
    Extract entities from text using Claude LLM.

    This is the synchronous version for use in Celery tasks.

    Args:
        text: Text content to analyze
        tenant_id: Tenant identifier for rate limiting
        max_text_length: Maximum text length to process

    Returns:
        List of extracted entity dictionaries
    """
    # Run async function in event loop
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop.run_until_complete(
        extract_entities_with_llm_async(text, tenant_id, max_text_length)
    )


async def extract_entities_with_llm_async(
    text: str,
    tenant_id: str,
    max_text_length: int = 8000,
) -> list[dict]:
    """
    Extract entities from text using Claude LLM (async).

    Args:
        text: Text content to analyze
        tenant_id: Tenant identifier for rate limiting
        max_text_length: Maximum text length to process

    Returns:
        List of extracted entity dictionaries
    """
    if not settings.ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not configured, skipping LLM extraction")
        return []

    # Truncate text if too long
    if len(text) > max_text_length:
        text = text[:max_text_length] + "..."
        logger.debug(f"Truncated text to {max_text_length} characters")

    # Skip if text is too short
    if len(text.strip()) < 50:
        logger.debug("Text too short for LLM extraction")
        return []

    # Rate limiting
    rate_limiter = get_rate_limiter()
    await rate_limiter.acquire(tenant_id)

    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

        # Call Claude with tool use
        response = await client.messages.create(
            model=settings.LLM_MODEL,
            max_tokens=4096,
            tools=[EXTRACTION_TOOL],
            tool_choice={"type": "tool", "name": "extract_entities"},
            messages=[
                {
                    "role": "user",
                    "content": EXTRACTION_PROMPT.format(text=text),
                }
            ],
        )

        # Parse response
        entities = _parse_extraction_response(response)

        logger.debug(
            f"LLM extracted {len(entities)} entities",
            extra={
                "tenant_id": tenant_id,
                "entity_count": len(entities),
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        )

        return entities

    except anthropic.RateLimitError as e:
        logger.warning(f"Anthropic rate limit hit: {e}")
        return []

    except anthropic.APIError as e:
        logger.error(f"Anthropic API error: {e}")
        return []

    except Exception as e:
        logger.exception(f"LLM extraction failed: {e}")
        return []


def _parse_extraction_response(response: Any) -> list[dict]:
    """
    Parse Claude's tool use response into entities.

    Args:
        response: Anthropic API response

    Returns:
        List of entity dictionaries
    """
    entities = []

    for content_block in response.content:
        if content_block.type != "tool_use":
            continue

        if content_block.name != "extract_entities":
            continue

        try:
            tool_input = content_block.input
            raw_entities = tool_input.get("entities", [])

            for raw_entity in raw_entities:
                entity = _convert_llm_entity(raw_entity)
                if entity:
                    entities.append(entity)

        except Exception as e:
            logger.warning(f"Failed to parse extraction response: {e}")

    return entities


def _convert_llm_entity(raw_entity: dict) -> Optional[dict]:
    """
    Convert LLM-extracted entity to our format.

    Args:
        raw_entity: Raw entity from LLM response

    Returns:
        Converted entity dictionary or None
    """
    name = raw_entity.get("name")
    if not name:
        return None

    # Map type
    type_str = raw_entity.get("type", "concept")
    entity_type = _map_llm_type(type_str)

    return {
        "type": entity_type,
        "name": name,
        "description": raw_entity.get("description"),
        "properties": raw_entity.get("properties", {}),
        "external_ids": {},
        "method": ExtractionMethod.LLM_CLAUDE,
        "confidence": raw_entity.get("confidence", 0.7),
        "source_text": raw_entity.get("context"),
    }


def _map_llm_type(type_str: str) -> EntityType:
    """Map LLM entity type string to EntityType enum."""
    type_map = {
        "person": EntityType.PERSON,
        "organization": EntityType.ORGANIZATION,
        "location": EntityType.LOCATION,
        "event": EntityType.EVENT,
        "product": EntityType.PRODUCT,
        "concept": EntityType.CONCEPT,
        "document": EntityType.DOCUMENT,
        "date": EntityType.DATE,
    }
    return type_map.get(type_str.lower(), EntityType.CONCEPT)


async def extract_relationships_with_llm(
    entities: list[dict],
    text: str,
    tenant_id: str,
) -> list[dict]:
    """
    Extract relationships between entities using LLM.

    Args:
        entities: List of already-extracted entities
        text: Original text content
        tenant_id: Tenant identifier

    Returns:
        List of relationship dictionaries
    """
    if not entities or len(entities) < 2:
        return []

    # Use the same extraction call - relationships are included
    # This is a placeholder for more sophisticated relationship extraction
    return []
