"""
Trafilatura-based content preprocessor.

Uses trafilatura library for robust extraction of main content
from HTML pages, removing boilerplate elements like navigation,
ads, footers, etc.

Trafilatura is particularly good at:
- Identifying and extracting the main article content
- Removing navigation, sidebars, footers, ads
- Preserving document structure (headings, paragraphs)
- Handling a wide variety of HTML layouts
"""

import logging
import re
from typing import Any

import trafilatura
from trafilatura.settings import use_config

from kg_builder.preprocessing.exceptions import ContentExtractionError
from kg_builder.preprocessing.factory import PreprocessorFactory, PreprocessorType
from kg_builder.preprocessing.schemas import PreprocessingResult

logger = logging.getLogger(__name__)


@PreprocessorFactory.register(PreprocessorType.TRAFILATURA)
class TrafilaturaPreprocessor:
    """Preprocessor using trafilatura for HTML content extraction.

    Trafilatura excels at:
    - Removing boilerplate (navigation, sidebars, footers, ads)
    - Extracting main article content
    - Preserving document structure
    - Handling various HTML layouts

    When trafilatura fails to extract sufficient content, it falls back
    to a basic BeautifulSoup extraction.

    Attributes:
        include_comments: Whether to include HTML comments in output
        include_tables: Whether to include table content
        include_links: Whether to include link URLs in text
        favor_recall: Prioritize getting more content over precision
        min_output_length: Minimum characters for valid output

    Example:
        preprocessor = TrafilaturaPreprocessor(favor_recall=True)
        result = preprocessor.preprocess(html_content, url="https://example.com")
        print(result.clean_text)
    """

    def __init__(
        self,
        include_comments: bool = False,
        include_tables: bool = True,
        include_links: bool = False,
        favor_recall: bool = True,
        min_output_length: int = 50,
        config_overrides: dict[str, Any] | None = None,
    ):
        """Initialize trafilatura preprocessor.

        Args:
            include_comments: Include HTML comments in output (default: False)
            include_tables: Include table content (default: True)
            include_links: Include link URLs with link text (default: False)
            favor_recall: Favor recall over precision - get more content (default: True)
            min_output_length: Minimum length for valid output before fallback (default: 50)
            config_overrides: Additional trafilatura config options
        """
        self._include_comments = include_comments
        self._include_tables = include_tables
        self._include_links = include_links
        self._favor_recall = favor_recall
        self._min_output_length = min_output_length

        # Configure trafilatura
        self._config = use_config()
        self._config.set("DEFAULT", "EXTRACTION_TIMEOUT", "30")

        if config_overrides:
            for section, values in config_overrides.items():
                if isinstance(values, dict):
                    for key, value in values.items():
                        self._config.set(section, key, str(value))

        logger.info(
            "TrafilaturaPreprocessor initialized",
            extra={
                "include_tables": include_tables,
                "favor_recall": favor_recall,
                "min_output_length": min_output_length,
            },
        )

    @property
    def preprocessor_type(self) -> str:
        """Return the type identifier for this preprocessor."""
        return PreprocessorType.TRAFILATURA.value

    def preprocess(
        self,
        content: str,
        content_type: str = "text/html",
        url: str | None = None,
    ) -> PreprocessingResult:
        """Extract main content from HTML using trafilatura.

        Args:
            content: Raw HTML content
            content_type: MIME type of content (only text/html fully supported)
            url: Source URL for context (helps trafilatura make better decisions)

        Returns:
            PreprocessingResult with cleaned text and metadata

        Raises:
            ContentExtractionError: If extraction fails completely
        """
        original_length = len(content)

        # For non-HTML content, apply minimal cleaning only
        if "html" not in content_type.lower():
            clean_text = self._clean_plain_text(content)
            return PreprocessingResult(
                clean_text=clean_text,
                original_length=original_length,
                cleaned_length=len(clean_text),
                preprocessing_method=self.preprocessor_type,
                metadata={"skipped_extraction": True, "reason": "non-html content"},
            )

        try:
            # Extract main content
            extracted = trafilatura.extract(
                content,
                url=url,
                include_comments=self._include_comments,
                include_tables=self._include_tables,
                include_links=self._include_links,
                favor_recall=self._favor_recall,
                config=self._config,
            )

            fallback_used = False

            if not extracted or len(extracted) < self._min_output_length:
                # Fallback to basic extraction
                logger.warning(
                    "Trafilatura extraction too short, using fallback",
                    extra={
                        "extracted_length": len(extracted or ""),
                        "min_length": self._min_output_length,
                        "url": url,
                    },
                )
                extracted = self._fallback_extract(content)
                fallback_used = True

            # Build metadata
            metadata: dict[str, Any] = {
                "extractor": "trafilatura",
                "fallback_used": fallback_used,
            }

            # Extract document metadata if available
            try:
                doc_metadata = trafilatura.extract_metadata(content, url=url)
                if doc_metadata:
                    metadata.update(
                        {
                            "title": doc_metadata.title,
                            "author": doc_metadata.author,
                            "date": str(doc_metadata.date) if doc_metadata.date else None,
                            "sitename": doc_metadata.sitename,
                            "description": doc_metadata.description,
                        }
                    )
            except Exception as e:
                logger.debug(f"Failed to extract metadata: {e}")

            clean_text = extracted or ""

            logger.info(
                "Trafilatura preprocessing complete",
                extra={
                    "original_length": original_length,
                    "cleaned_length": len(clean_text),
                    "reduction_pct": round(
                        (1 - len(clean_text) / max(original_length, 1)) * 100, 1
                    ),
                    "fallback_used": fallback_used,
                    "url": url,
                },
            )

            return PreprocessingResult(
                clean_text=clean_text,
                original_length=original_length,
                cleaned_length=len(clean_text),
                preprocessing_method=self.preprocessor_type,
                metadata=metadata,
            )

        except Exception as e:
            logger.error(
                "Trafilatura extraction failed, attempting fallback",
                extra={"error": str(e), "url": url},
            )

            # Last resort fallback
            try:
                fallback_text = self._fallback_extract(content)
                return PreprocessingResult(
                    clean_text=fallback_text,
                    original_length=original_length,
                    cleaned_length=len(fallback_text),
                    preprocessing_method=self.preprocessor_type,
                    metadata={"error": str(e), "fallback_used": True},
                )
            except Exception as fallback_error:
                raise ContentExtractionError(
                    f"Content extraction failed: {e}; Fallback also failed: {fallback_error}",
                    cause=e,
                ) from e

    def _fallback_extract(self, html: str) -> str:
        """Basic fallback extraction using BeautifulSoup.

        Used when trafilatura fails to extract sufficient content.

        Args:
            html: Raw HTML content

        Returns:
            Extracted text
        """
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "lxml")

            # Remove unwanted elements
            for tag in soup(
                ["script", "style", "nav", "footer", "header", "aside", "noscript", "iframe"]
            ):
                tag.decompose()

            # Remove common boilerplate classes/ids
            for selector in [
                '[class*="nav"]',
                '[class*="menu"]',
                '[class*="sidebar"]',
                '[class*="footer"]',
                '[class*="header"]',
                '[class*="ad-"]',
                '[class*="advertisement"]',
                '[id*="nav"]',
                '[id*="menu"]',
                '[id*="sidebar"]',
                '[id*="footer"]',
                '[id*="header"]',
            ]:
                try:
                    for element in soup.select(selector):
                        element.decompose()
                except Exception:
                    pass  # Selector might not be valid

            text = soup.get_text(separator=" ", strip=True)
            text = re.sub(r"\s+", " ", text)
            return text.strip()

        except Exception as e:
            logger.error(f"Fallback extraction failed: {e}")
            return ""

    def _clean_plain_text(self, text: str) -> str:
        """Clean plain text content.

        Args:
            text: Plain text content

        Returns:
            Cleaned text
        """
        # Normalize whitespace
        text = re.sub(r"\s+", " ", text)
        return text.strip()
