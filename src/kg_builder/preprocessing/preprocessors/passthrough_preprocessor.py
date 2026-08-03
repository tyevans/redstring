"""
Passthrough preprocessor that returns content with minimal processing.

Useful for:
- Plain text content that doesn't need HTML extraction
- Testing and debugging the pipeline
- Cases where content is already clean
"""

import logging
import re

from kg_builder.preprocessing.factory import PreprocessorFactory, PreprocessorType
from kg_builder.preprocessing.schemas import PreprocessingResult

logger = logging.getLogger(__name__)


@PreprocessorFactory.register(PreprocessorType.PASSTHROUGH)
class PassthroughPreprocessor:
    """Preprocessor that returns content with minimal processing.

    Only performs basic whitespace normalization by default.
    Does not attempt to extract main content or remove boilerplate.

    Useful for:
    - Plain text content
    - Pre-cleaned content
    - Testing and debugging

    Attributes:
        normalize_whitespace: Whether to normalize whitespace (default: True)

    Example:
        preprocessor = PassthroughPreprocessor()
        result = preprocessor.preprocess(text_content)
        print(result.clean_text)
    """

    def __init__(
        self,
        normalize_whitespace: bool = True,
    ):
        """Initialize passthrough preprocessor.

        Args:
            normalize_whitespace: Whether to normalize whitespace (default: True)
        """
        self._normalize_whitespace = normalize_whitespace

        logger.info(
            "PassthroughPreprocessor initialized",
            extra={"normalize_whitespace": normalize_whitespace},
        )

    @property
    def preprocessor_type(self) -> str:
        """Return the type identifier for this preprocessor."""
        return PreprocessorType.PASSTHROUGH.value

    def preprocess(
        self,
        content: str,
        content_type: str = "text/plain",
        url: str | None = None,
    ) -> PreprocessingResult:
        """Pass through content with minimal processing.

        Args:
            content: Content to process
            content_type: MIME type (not used)
            url: Source URL (not used)

        Returns:
            PreprocessingResult with cleaned text
        """
        original_length = len(content)

        if self._normalize_whitespace:
            clean_text = re.sub(r"\s+", " ", content).strip()
        else:
            clean_text = content

        return PreprocessingResult(
            clean_text=clean_text,
            original_length=original_length,
            cleaned_length=len(clean_text),
            preprocessing_method=self.preprocessor_type,
            metadata={"passthrough": True, "normalized_whitespace": self._normalize_whitespace},
        )
