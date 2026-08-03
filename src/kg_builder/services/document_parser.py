"""
Document Parser Service for extracting text from uploaded documents.

Parses TXT, Markdown, and HTML files to extract plain text content
for LLM-based entity extraction.
"""

import logging
import re
from html.parser import HTMLParser

logger = logging.getLogger(__name__)


class HTMLTextExtractor(HTMLParser):
    """HTML parser that extracts text content only."""

    def __init__(self):
        super().__init__()
        self.text_parts: list[str] = []
        self._skip_tags = {"script", "style", "head", "meta", "link"}
        self._current_skip = False
        self._in_skip_tag = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self._skip_tags:
            self._in_skip_tag += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._skip_tags:
            self._in_skip_tag = max(0, self._in_skip_tag - 1)

    def handle_data(self, data: str) -> None:
        if self._in_skip_tag == 0:
            text = data.strip()
            if text:
                self.text_parts.append(text)

    def get_text(self) -> str:
        """Get extracted text joined with spaces."""
        return " ".join(self.text_parts)


class DocumentParser:
    """
    Service for parsing uploaded documents to extract text content.

    Supports:
    - text/plain: Returns content as-is (UTF-8 decoded)
    - text/markdown: Strips markdown formatting
    - text/html: Extracts text from HTML elements

    The extracted text is suitable for LLM entity extraction.
    """

    # Markdown patterns to strip
    MARKDOWN_PATTERNS = [
        # Headers
        (r"^#{1,6}\s+", ""),
        # Bold/italic
        (r"\*\*(.+?)\*\*", r"\1"),
        (r"\*(.+?)\*", r"\1"),
        (r"__(.+?)__", r"\1"),
        (r"_(.+?)_", r"\1"),
        # Links
        (r"\[([^\]]+)\]\([^)]+\)", r"\1"),
        # Images
        (r"!\[([^\]]*)\]\([^)]+\)", r"\1"),
        # Inline code
        (r"`([^`]+)`", r"\1"),
        # Code blocks (fenced)
        (r"```[\s\S]*?```", ""),
        # Blockquotes
        (r"^>\s+", ""),
        # Horizontal rules
        (r"^[-*_]{3,}\s*$", ""),
        # List markers
        (r"^[\s]*[-*+]\s+", ""),
        (r"^[\s]*\d+\.\s+", ""),
    ]

    def parse(self, content: bytes, content_type: str) -> str:
        """
        Parse document content to plain text.

        Args:
            content: Raw file content as bytes
            content_type: MIME type of the content

        Returns:
            Extracted plain text

        Raises:
            ValueError: If content type is not supported
        """
        content_type = content_type.lower().strip()

        if content_type in ("text/plain", "application/octet-stream"):
            return self._parse_text(content)
        elif content_type in ("text/markdown", "application/markdown", "text/x-markdown"):
            return self._parse_markdown(content)
        elif content_type == "text/html":
            return self._parse_html(content)
        else:
            raise ValueError(f"Unsupported content type: {content_type}")

    def _parse_text(self, content: bytes) -> str:
        """Parse plain text content."""
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            # Fallback to latin-1 for legacy files
            text = content.decode("latin-1", errors="replace")

        # Normalize line endings and clean up
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = self._clean_text(text)

        logger.debug(
            "Parsed plain text document",
            extra={"original_length": len(content), "text_length": len(text)},
        )

        return text

    def _parse_markdown(self, content: bytes) -> str:
        """Parse markdown content to plain text."""
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("latin-1", errors="replace")

        # Apply markdown stripping patterns
        for pattern, replacement in self.MARKDOWN_PATTERNS:
            text = re.sub(pattern, replacement, text, flags=re.MULTILINE)

        text = self._clean_text(text)

        logger.debug(
            "Parsed markdown document",
            extra={"original_length": len(content), "text_length": len(text)},
        )

        return text

    def _parse_html(self, content: bytes) -> str:
        """Parse HTML content to extract text."""
        try:
            html = content.decode("utf-8")
        except UnicodeDecodeError:
            html = content.decode("latin-1", errors="replace")

        # Use custom HTML parser
        parser = HTMLTextExtractor()
        try:
            parser.feed(html)
            text = parser.get_text()
        except Exception as e:
            logger.warning(
                "HTML parsing error, falling back to regex",
                extra={"error": str(e)},
            )
            # Fallback: strip all tags with regex
            text = re.sub(r"<[^>]+>", " ", html)

        text = self._clean_text(text)

        logger.debug(
            "Parsed HTML document",
            extra={"original_length": len(content), "text_length": len(text)},
        )

        return text

    def _clean_text(self, text: str) -> str:
        """Clean and normalize extracted text."""
        # Normalize whitespace
        text = re.sub(r"[ \t]+", " ", text)
        # Normalize line breaks (max 2 consecutive)
        text = re.sub(r"\n{3,}", "\n\n", text)
        # Strip leading/trailing whitespace
        text = text.strip()
        return text


# Singleton instance
_document_parser: DocumentParser | None = None


def get_document_parser() -> DocumentParser:
    """
    Get or create the singleton DocumentParser instance.

    Returns:
        DocumentParser instance
    """
    global _document_parser
    if _document_parser is None:
        _document_parser = DocumentParser()
    return _document_parser
