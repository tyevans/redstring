"""YAML domain schema loader utilities.

This module provides functions for loading and validating domain schemas
from YAML files. The loader ensures all schemas conform to the DomainSchema
Pydantic model before returning them.

Security Note:
    All YAML files are loaded using yaml.safe_load() to prevent code execution
    vulnerabilities from malicious YAML content.

Example usage:
    from kg_builder.extraction.domains.loader import (
        load_schema_from_file,
        load_schema_from_string,
        load_all_schemas,
        get_schema_directory,
    )

    # Load a single schema
    schema = load_schema_from_file("literature_fiction.yaml")

    # Load all schemas from the default directory
    schemas = load_all_schemas()
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import ValidationError

from kg_builder.extraction.domains.models import DomainSchema

logger = logging.getLogger(__name__)

# Default schema directory relative to this module
_SCHEMA_DIR = Path(__file__).parent / "schemas"


class SchemaLoadError(Exception):
    """Exception raised when a schema fails to load or validate.

    Attributes:
        file_path: Path to the schema file that failed
        cause: The underlying exception that caused the failure
    """

    def __init__(
        self,
        message: str,
        file_path: Path | str | None = None,
        cause: Exception | None = None,
    ) -> None:
        """Initialize SchemaLoadError.

        Args:
            message: Error message describing the failure
            file_path: Path to the schema file that failed
            cause: The underlying exception that caused the failure
        """
        super().__init__(message)
        self.file_path = Path(file_path) if file_path else None
        self.cause = cause


def get_schema_directory() -> Path:
    """Get the default directory containing YAML schema files.

    Returns:
        Path to the schemas directory
    """
    return _SCHEMA_DIR


def load_schema_from_string(yaml_content: str, source_name: str = "<string>") -> DomainSchema:
    """Load and validate a domain schema from a YAML string.

    Args:
        yaml_content: YAML content as a string
        source_name: Name to use in error messages (default: "<string>")

    Returns:
        Validated DomainSchema object

    Raises:
        SchemaLoadError: If the YAML is invalid or doesn't conform to DomainSchema
    """
    try:
        data = yaml.safe_load(yaml_content)
    except yaml.YAMLError as e:
        logger.error("Failed to parse YAML from %s: %s", source_name, e)
        raise SchemaLoadError(
            f"Invalid YAML syntax in {source_name}: {e}",
            file_path=source_name,
            cause=e,
        ) from e

    if data is None:
        raise SchemaLoadError(
            f"Empty YAML content in {source_name}",
            file_path=source_name,
        )

    if not isinstance(data, dict):
        raise SchemaLoadError(
            f"YAML content must be a mapping, got {type(data).__name__} in {source_name}",
            file_path=source_name,
        )

    try:
        schema = DomainSchema.model_validate(data)
        logger.debug("Successfully loaded schema '%s' from %s", schema.domain_id, source_name)
        return schema
    except ValidationError as e:
        logger.error("Schema validation failed for %s: %s", source_name, e)
        raise SchemaLoadError(
            f"Schema validation failed for {source_name}: {e}",
            file_path=source_name,
            cause=e,
        ) from e


def load_schema_from_file(
    file_path: Path | str,
    schema_dir: Path | None = None,
) -> DomainSchema:
    """Load and validate a domain schema from a YAML file.

    If file_path is relative, it is resolved relative to the schema_dir
    (or the default schema directory if not specified).

    Args:
        file_path: Path to the YAML file (absolute or relative)
        schema_dir: Base directory for relative paths (default: built-in schemas dir)

    Returns:
        Validated DomainSchema object

    Raises:
        SchemaLoadError: If the file doesn't exist, can't be read, or is invalid
    """
    path = Path(file_path)

    # Resolve relative paths
    if not path.is_absolute():
        base_dir = schema_dir or _SCHEMA_DIR
        path = base_dir / path

    if not path.exists():
        raise SchemaLoadError(
            f"Schema file not found: {path}",
            file_path=path,
        )

    if not path.is_file():
        raise SchemaLoadError(
            f"Schema path is not a file: {path}",
            file_path=path,
        )

    try:
        content = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.error("Failed to read schema file %s: %s", path, e)
        raise SchemaLoadError(
            f"Failed to read schema file {path}: {e}",
            file_path=path,
            cause=e,
        ) from e

    return load_schema_from_string(content, source_name=str(path))


def load_all_schemas(
    schema_dir: Path | None = None,
    *,
    ignore_errors: bool = False,
) -> dict[str, DomainSchema]:
    """Load all domain schemas from a directory.

    Loads all .yaml and .yml files from the specified directory and validates
    them against the DomainSchema model.

    Args:
        schema_dir: Directory containing YAML schema files (default: built-in schemas dir)
        ignore_errors: If True, skip invalid schemas instead of raising (default: False)

    Returns:
        Dictionary mapping domain_id to DomainSchema

    Raises:
        SchemaLoadError: If any schema fails to load and ignore_errors is False
    """
    directory = schema_dir or _SCHEMA_DIR

    if not directory.exists():
        logger.warning("Schema directory does not exist: %s", directory)
        return {}

    if not directory.is_dir():
        raise SchemaLoadError(
            f"Schema path is not a directory: {directory}",
            file_path=directory,
        )

    schemas: dict[str, DomainSchema] = {}
    errors: list[SchemaLoadError] = []

    # Find all YAML files
    yaml_files = list(directory.glob("*.yaml")) + list(directory.glob("*.yml"))
    logger.info("Found %d schema files in %s", len(yaml_files), directory)

    for file_path in sorted(yaml_files):
        try:
            schema = load_schema_from_file(file_path)
            if schema.domain_id in schemas:
                error = SchemaLoadError(
                    f"Duplicate domain_id '{schema.domain_id}' found in {file_path}. "
                    f"Already loaded from another file.",
                    file_path=file_path,
                )
                if ignore_errors:
                    logger.warning(str(error))
                    errors.append(error)
                else:
                    raise error
            else:
                schemas[schema.domain_id] = schema
                logger.debug("Loaded schema '%s' from %s", schema.domain_id, file_path)
        except SchemaLoadError as e:
            if ignore_errors:
                logger.warning("Skipping invalid schema %s: %s", file_path, e)
                errors.append(e)
            else:
                raise

    logger.info(
        "Loaded %d schemas successfully%s",
        len(schemas),
        f" ({len(errors)} errors ignored)" if errors else "",
    )

    return schemas


def get_available_domain_ids(schema_dir: Path | None = None) -> list[str]:
    """Get list of available domain IDs from schema files.

    This is a lightweight operation that only parses the domain_id from each file.

    Args:
        schema_dir: Directory containing YAML schema files (default: built-in schemas dir)

    Returns:
        List of domain IDs from valid schema files
    """
    schemas = load_all_schemas(schema_dir, ignore_errors=True)
    return sorted(schemas.keys())


def validate_schema_file(file_path: Path | str) -> tuple[bool, str | None]:
    """Validate a schema file without fully loading it.

    Args:
        file_path: Path to the schema file to validate

    Returns:
        Tuple of (is_valid, error_message). error_message is None if valid.
    """
    try:
        load_schema_from_file(file_path)
        return (True, None)
    except SchemaLoadError as e:
        return (False, str(e))
