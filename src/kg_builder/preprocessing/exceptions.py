"""
Custom exceptions for the preprocessing module.

Provides a hierarchy of exceptions for different failure modes
in preprocessing, chunking, and entity merging operations.
"""


class PreprocessingError(Exception):
    """Base exception for all preprocessing errors.

    This is the root exception class for the preprocessing module.
    Catch this to handle any preprocessing-related error.
    """

    def __init__(self, message: str, cause: Exception | None = None):
        super().__init__(message)
        self.message = message
        self.cause = cause


# =============================================================================
# Preprocessor Exceptions
# =============================================================================


class PreprocessorError(PreprocessingError):
    """Base exception for preprocessor errors.

    Raised when a preprocessor fails to clean/extract content.
    """

    pass


class PreprocessorNotRegisteredError(PreprocessorError):
    """Raised when attempting to create an unregistered preprocessor type.

    This indicates a configuration error where the requested preprocessor
    type is not available in the factory registry.
    """

    pass


class ContentExtractionError(PreprocessorError):
    """Raised when content extraction fails.

    This may occur when:
    - HTML is malformed and cannot be parsed
    - No main content can be identified
    - External extraction library fails
    """

    pass


# =============================================================================
# Chunker Exceptions
# =============================================================================


class ChunkerError(PreprocessingError):
    """Base exception for chunker errors.

    Raised when a chunker fails to split content.
    """

    pass


class ChunkerNotRegisteredError(ChunkerError):
    """Raised when attempting to create an unregistered chunker type.

    This indicates a configuration error where the requested chunker
    type is not available in the factory registry.
    """

    pass


class ChunkSizeError(ChunkerError):
    """Raised when chunk size configuration is invalid.

    This may occur when:
    - Chunk size is less than minimum allowed
    - Overlap size exceeds chunk size
    - Configuration values are inconsistent
    """

    pass


# =============================================================================
# Entity Merger Exceptions
# =============================================================================


class EntityMergerError(PreprocessingError):
    """Base exception for entity merger errors.

    Raised when entity merging fails.
    """

    pass


class EntityMergerNotRegisteredError(EntityMergerError):
    """Raised when attempting to create an unregistered merger type.

    This indicates a configuration error where the requested merger
    type is not available in the factory registry.
    """

    pass


class EntityResolutionError(EntityMergerError):
    """Raised when entity resolution fails.

    This may occur when:
    - LLM call fails during resolution
    - Response parsing fails
    - Merge decision cannot be determined
    """

    pass


# =============================================================================
# Pipeline Exceptions
# =============================================================================


class PipelineError(PreprocessingError):
    """Raised when the preprocessing pipeline fails.

    This is a general error for pipeline-level failures that
    don't fit into the more specific categories.
    """

    pass


class PipelineConfigError(PipelineError):
    """Raised when pipeline configuration is invalid.

    This may occur when:
    - Required configuration is missing
    - Configuration values are out of valid range
    - Incompatible options are combined
    """

    pass
