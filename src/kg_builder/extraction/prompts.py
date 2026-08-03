"""
Extraction prompts for documentation analysis.

These prompts are optimized for extracting entities and relationships
from technical documentation, particularly Python/eventsource-py docs.

Note: This module avoids importing from the __init__.py chain to prevent
loading database-dependent modules during prompt generation.
"""

from enum import Enum


class DocumentationType(str, Enum):
    """Types of documentation for prompt optimization.

    Different documentation types require different extraction strategies:
    - API_REFERENCE: Focus on function signatures, classes, parameters
    - TUTORIAL: Focus on concepts being taught and examples
    - CONCEPTUAL: Focus on design patterns and architectural concepts
    - EXAMPLE_CODE: Focus on code snippets and what they demonstrate
    - GENERAL: Balanced approach for mixed content
    """

    API_REFERENCE = "api_reference"
    TUTORIAL = "tutorial"
    CONCEPTUAL = "conceptual"
    EXAMPLE_CODE = "example_code"
    GENERAL = "general"


# Entity types matching EntityTypeLiteral in schemas.py
# Kept in sync manually to avoid circular/database imports
_ENTITY_TYPES: list[str] = [
    "function",
    "class",
    "module",
    "pattern",
    "example",
    "parameter",
    "return_type",
    "exception",
    "concept",
    "person",
    "organization",
    "location",
    "event",
    "product",
    "document",
    "date",
    "custom",
]

# Relationship types matching RelationshipTypeLiteral in schemas.py
# Kept in sync manually to avoid circular/database imports
_RELATIONSHIP_TYPES: list[str] = [
    "uses",
    "implements",
    "extends",
    "inherits_from",
    "contains",
    "part_of",
    "calls",
    "returns",
    "accepts",
    "raises",
    "depends_on",
    "imports",
    "requires",
    "documented_in",
    "example_of",
    "demonstrates",
    "related_to",
    "references",
    "defines",
    "instantiates",
]


SYSTEM_PROMPT_BASE = '''You are an expert technical documentation analyzer specializing in Python libraries and frameworks.

Your task is to extract structured information about code entities and their relationships from documentation.

## Entity Types

Extract these types of entities:

### CODE ENTITIES (primary focus)
- **function**: Python functions with signatures, parameters, return types
  Properties: signature, parameters (list), return_type, is_async, is_generator, decorators, docstring
- **class**: Python classes with methods, attributes, inheritance
  Properties: base_classes (list), methods (list), class_methods, static_methods, properties, attributes (list), is_abstract, is_dataclass, is_pydantic_model, docstring
- **module**: Python modules and packages
  Properties: path, package, submodules (list), public_api (list), dependencies, docstring
- **exception**: Exception classes
  Properties: base_exception, raised_by, message_template, is_custom
- **parameter**: Function or method parameters
  Properties: type_annotation, default_value, is_required, is_keyword_only, is_positional_only, is_variadic
- **return_type**: Return type annotations

### CONCEPTUAL ENTITIES
- **concept**: Abstract ideas, design principles (e.g., "event sourcing", "aggregate")
  Properties: definition, related_concepts (list)
- **pattern**: Design patterns, architectural patterns (e.g., "repository pattern")
  Properties: category, problem, solution, consequences, related_patterns, implementation_notes

### DOCUMENTATION ENTITIES
- **example**: Code examples demonstrating usage
  Properties: code_snippet, language, demonstrates (list), prerequisites, expected_output, is_runnable

### OTHER ENTITIES (use when more specific types don't apply)
- **person**: People mentioned in documentation
- **organization**: Organizations or companies
- **location**: Geographic locations
- **event**: Events (conferences, releases, etc.)
- **product**: Software products, libraries, tools
- **document**: Documentation pages, specifications
- **date**: Dates and time periods
- **custom**: Other entities that don't fit above categories

## Relationship Types

Identify these relationships between entities:

### CODE STRUCTURE RELATIONSHIPS
- **extends**: Class inheritance (ChildClass extends ParentClass)
- **inherits_from**: Alias for extends
- **implements**: Implements interface/protocol
- **contains**: Entity contains another (class contains method)
- **part_of**: Entity is part of a larger entity

### FUNCTION/METHOD RELATIONSHIPS
- **calls**: Function A calls function B
- **returns**: Function returns a type
- **accepts**: Function accepts parameter type
- **raises**: Function raises exception

### DEPENDENCY RELATIONSHIPS
- **uses**: Entity uses another entity
- **depends_on**: Module depends on another module
- **imports**: Module imports another module
- **requires**: Entity requires another entity

### DOCUMENTATION RELATIONSHIPS
- **documented_in**: Entity is documented on this page
- **example_of**: Example demonstrates a concept/class/function
- **demonstrates**: Same as example_of (example demonstrates concept)

### GENERIC RELATIONSHIPS
- **related_to**: General relationship when more specific type doesn't apply
- **references**: Entity references another
- **defines**: Entity defines another (module defines class)
- **instantiates**: Code instantiates a class

## Extraction Guidelines

1. **Be Precise**: Only extract entities you're confident about (confidence >= 0.7)
2. **Prefer Specific Types**: Use "function" or "class" over generic "concept" when applicable
3. **Normalize Names**: Use the canonical name (e.g., "DomainEvent" not "domain event")
4. **Include Context**: Add source_text for entities to show where they were found
5. **Capture Properties**: Fill in type-specific properties when available
6. **Relationship Direction**: source -> relationship -> target (e.g., ChildClass extends ParentClass)
7. **Avoid Duplicates**: Each entity should appear only once in the output

## Output Format

Return a JSON object with:
- entities: array of extracted entities
- relationships: array of discovered relationships

Each entity must have: name, entity_type, confidence (0.0-1.0)
Each relationship must have: source_name, target_name, relationship_type, confidence

Only include relationships where both source and target entities are in the entities list.'''


SYSTEM_PROMPT_API_REFERENCE = SYSTEM_PROMPT_BASE + '''

## API Reference Focus

This is API reference documentation. Focus on:
- Function signatures with full parameter details (names, types, defaults)
- Class definitions with methods, attributes, and inheritance hierarchy
- Module structure and exports (__all__ members)
- Type annotations and return values
- Exception documentation (what's raised and when)
- Decorator usage and their effects

Be thorough in capturing the API surface. Extract:
- All public functions and classes
- Constructor parameters and their types
- Method signatures including async/generator status
- Property definitions
- Class and instance attributes
- Dependencies and imports'''


SYSTEM_PROMPT_TUTORIAL = SYSTEM_PROMPT_BASE + '''

## Tutorial Focus

This is tutorial documentation. Focus on:
- Concepts being taught and their definitions
- Code examples demonstrating concepts
- Step-by-step patterns and workflows
- Relationships between concepts (what depends on what)
- Best practices and recommendations mentioned
- Common pitfalls and how to avoid them

Connect examples to the concepts they demonstrate. Track:
- The learning progression (basic -> advanced concepts)
- Prerequisites for understanding each concept
- Practical applications shown in examples'''


SYSTEM_PROMPT_CONCEPTUAL = SYSTEM_PROMPT_BASE + '''

## Conceptual Documentation Focus

This is conceptual/guide documentation. Focus on:
- Core concepts and their definitions
- Design patterns and architectural decisions
- Relationships between concepts (composition, dependency)
- Trade-offs and alternatives mentioned
- Key terminology and domain vocabulary
- Principles and guidelines

Capture the "why" behind design decisions:
- Motivations for architectural choices
- Problems that patterns solve
- When to use (and not use) certain approaches'''


SYSTEM_PROMPT_EXAMPLE_CODE = SYSTEM_PROMPT_BASE + '''

## Example Code Focus

This is code example documentation. Focus on:
- What each example demonstrates
- Functions, classes, and patterns used in examples
- Prerequisites and imports required
- Expected output or behavior
- Variations and alternative approaches shown

For each example, identify:
- The main concept or feature being demonstrated
- Supporting concepts used but not explained
- How examples build on each other'''


def get_system_prompt(doc_type: DocumentationType = DocumentationType.GENERAL) -> str:
    """Get the appropriate system prompt for document type.

    Selects an optimized system prompt based on the type of documentation
    being analyzed. Each prompt variant includes the base extraction
    instructions plus type-specific guidance.

    Args:
        doc_type: Type of documentation being analyzed. Defaults to GENERAL
            which uses balanced extraction guidance.

    Returns:
        System prompt string optimized for the document type.

    Example:
        >>> prompt = get_system_prompt(DocumentationType.API_REFERENCE)
        >>> assert "API Reference Focus" in prompt
    """
    prompts = {
        DocumentationType.API_REFERENCE: SYSTEM_PROMPT_API_REFERENCE,
        DocumentationType.TUTORIAL: SYSTEM_PROMPT_TUTORIAL,
        DocumentationType.CONCEPTUAL: SYSTEM_PROMPT_CONCEPTUAL,
        DocumentationType.EXAMPLE_CODE: SYSTEM_PROMPT_EXAMPLE_CODE,
        DocumentationType.GENERAL: SYSTEM_PROMPT_BASE,
    }
    return prompts.get(doc_type, SYSTEM_PROMPT_BASE)


def build_user_prompt(
    content: str,
    page_url: str,
    doc_type: DocumentationType | None = None,
    additional_context: str | None = None,
) -> str:
    """Build the user prompt for extraction.

    Constructs a user prompt that includes the content to analyze along
    with contextual information to guide extraction.

    Args:
        content: Page content to analyze. Should be the main text/code
            from the documentation page.
        page_url: URL of the page. Provides context about the documentation
            structure and helps with entity disambiguation.
        doc_type: Optional document type hint. When provided, adds a hint
            to the prompt about what type of content to expect.
        additional_context: Optional additional context. Can include
            information about the library being documented, related pages,
            or extraction priorities.

    Returns:
        User prompt string ready to send to the LLM.

    Example:
        >>> prompt = build_user_prompt(
        ...     content="class DomainEvent: ...",
        ...     page_url="https://docs.example.com/events",
        ...     doc_type=DocumentationType.API_REFERENCE,
        ... )
        >>> assert "DomainEvent" in prompt
        >>> assert "api_reference" in prompt
    """
    type_hint = ""
    if doc_type:
        type_hint = f"\nDocument Type: {doc_type.value}"

    context_section = ""
    if additional_context:
        context_section = f"\nAdditional Context: {additional_context}"

    return f'''Analyze this technical documentation and extract all entities and relationships.

URL: {page_url}{type_hint}{context_section}

---
{content}
---

Extract all code entities (functions, classes, modules, exceptions, parameters) and conceptual entities (concepts, patterns, examples).
Identify relationships between entities.
Focus on accuracy - only include entities and relationships you're confident about (confidence >= 0.7).'''


def get_entity_types() -> list[str]:
    """Get the list of all supported entity types.

    Returns:
        List of entity type strings that can be used in extraction.
        These match the EntityTypeLiteral values from schemas.py.
    """
    return _ENTITY_TYPES.copy()


def get_relationship_types() -> list[str]:
    """Get the list of all supported relationship types.

    Returns:
        List of relationship type strings that can be used in extraction.
        These match the RelationshipTypeLiteral values from schemas.py.
    """
    return _RELATIONSHIP_TYPES.copy()
