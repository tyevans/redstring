"""
Timeline Export Service for exporting timeline data to various formats.

This service provides export functionality for timeline data to:
- JSON: Structured data with metadata
- CSV: Tabular format with formula injection protection
- ICS: iCalendar format (RFC 5545) for calendar applications

Security:
- CSV exports implement formula injection prevention
- All exports include watermarking/metadata

See ADR-025 for design decisions.
"""

import csv
import io
import logging
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from kg_builder.schemas.timeline import (
    DatePrecision,
    TemporalRelationship,
    TimelineEvent,
    UncertaintyMarker,
)

logger = logging.getLogger(__name__)

# Characters that can trigger formula execution in spreadsheet applications
# Prefix with single quote to prevent execution
FORMULA_INJECTION_CHARS = frozenset({"=", "+", "-", "@", "\t", "\r"})

# Maximum events before requiring streaming export
STREAMING_THRESHOLD = 1000


class TimelineExportService:
    """
    Service for exporting timeline data to various formats.

    Supports JSON, CSV, and ICS (iCalendar) export formats with
    proper security measures and metadata watermarking.
    """

    def __init__(self):
        """Initialize the timeline export service."""
        pass

    def export_json(
        self,
        events: list[TimelineEvent],
        relationships: list[TemporalRelationship] | None = None,
        *,
        include_meta: bool = True,
        include_relationships: bool = True,
        job_id: UUID | None = None,
        user_id: str | None = None,
        filters_applied: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Export timeline events to JSON format.

        Args:
            events: List of TimelineEvent objects to export
            relationships: Optional list of temporal relationships
            include_meta: Whether to include export metadata (default: True)
            include_relationships: Whether to include relationships (default: True)
            job_id: Job ID for metadata
            user_id: User ID for metadata
            filters_applied: Dict of filters applied for metadata

        Returns:
            Dictionary containing exported data and metadata
        """
        logger.info(
            "Exporting timeline to JSON",
            extra={
                "event_count": len(events),
                "include_meta": include_meta,
                "include_relationships": include_relationships,
            },
        )

        result: dict[str, Any] = {
            "events": [self._serialize_event_json(event) for event in events],
        }

        if include_relationships and relationships:
            result["relationships"] = [
                self._serialize_relationship_json(rel) for rel in relationships
            ]

        if include_meta:
            result["_metadata"] = self._generate_export_metadata(
                format_type="json",
                event_count=len(events),
                relationship_count=len(relationships) if relationships else 0,
                job_id=job_id,
                user_id=user_id,
                filters_applied=filters_applied,
            )

        return result

    def _serialize_event_json(self, event: TimelineEvent) -> dict[str, Any]:
        """Serialize a TimelineEvent to a JSON-compatible dict."""
        return {
            "id": str(event.id),
            "name": event.name,
            "description": event.description,
            "entity_type": event.entity_type,
            "start_date": event.start_date.isoformat() if event.start_date else None,
            "end_date": event.end_date.isoformat() if event.end_date else None,
            "precision": event.precision.value if event.precision else None,
            "uncertainty": event.uncertainty.value if event.uncertainty else None,
            "original_text": event.original_text,
            "sequence_position": event.sequence_position,
            "source_page_id": str(event.source_page_id),
            "source_url": event.source_url,
        }

    def _serialize_relationship_json(
        self, rel: TemporalRelationship
    ) -> dict[str, Any]:
        """Serialize a TemporalRelationship to a JSON-compatible dict."""
        return {
            "id": str(rel.id),
            "source_event_id": str(rel.source_event_id),
            "target_event_id": str(rel.target_event_id),
            "source_event_name": rel.source_event_name,
            "target_event_name": rel.target_event_name,
            "relationship_type": rel.relationship_type,
            "confidence": rel.confidence,
            "evidence": rel.evidence,
            "is_inferred": rel.is_inferred,
        }

    def export_csv(
        self,
        events: list[TimelineEvent],
        *,
        date_format: str = "iso",
        job_id: UUID | None = None,
        user_id: str | None = None,
        filters_applied: dict[str, Any] | None = None,
    ) -> str:
        """
        Export timeline events to CSV format.

        Implements CSV formula injection prevention by prefixing
        dangerous characters with a single quote.

        Args:
            events: List of TimelineEvent objects to export
            date_format: Date format - "iso" for ISO 8601 or "human" for readable
            job_id: Job ID for header metadata
            user_id: User ID for header metadata
            filters_applied: Dict of filters applied for header metadata

        Returns:
            CSV string with header comments and data rows
        """
        logger.info(
            "Exporting timeline to CSV",
            extra={
                "event_count": len(events),
                "date_format": date_format,
            },
        )

        output = io.StringIO()

        # Add metadata as header comments
        metadata = self._generate_export_metadata(
            format_type="csv",
            event_count=len(events),
            relationship_count=0,
            job_id=job_id,
            user_id=user_id,
            filters_applied=filters_applied,
        )

        # Write metadata comments
        output.write("# Timeline Export\n")
        output.write(f"# Exported at: {metadata['exported_at']}\n")
        if job_id:
            output.write(f"# Job ID: {job_id}\n")
        if user_id:
            output.write(f"# User: {user_id}\n")
        output.write(f"# Total events: {len(events)}\n")
        output.write("# WARNING: Cells starting with =, +, -, @, TAB, or CR have been sanitized for security.\n")
        output.write("#\n")

        # Write CSV data
        writer = csv.writer(output)

        # Header row
        writer.writerow([
            "ID",
            "Name",
            "Description",
            "Entity Type",
            "Start Date",
            "End Date",
            "Precision",
            "Uncertainty",
            "Original Text",
            "Sequence Position",
            "Source URL",
        ])

        # Data rows
        for event in events:
            writer.writerow([
                str(event.id),
                self._sanitize_csv_cell(event.name),
                self._sanitize_csv_cell(event.description or ""),
                self._sanitize_csv_cell(event.entity_type),
                self._format_date(event.start_date, date_format),
                self._format_date(event.end_date, date_format),
                event.precision.value if event.precision else "",
                event.uncertainty.value if event.uncertainty else "",
                self._sanitize_csv_cell(event.original_text or ""),
                str(event.sequence_position) if event.sequence_position is not None else "",
                self._sanitize_csv_cell(event.source_url),
            ])

        return output.getvalue()

    async def export_csv_streaming(
        self,
        events_generator: AsyncGenerator[TimelineEvent],
        *,
        date_format: str = "iso",
        job_id: UUID | None = None,
        user_id: str | None = None,
        filters_applied: dict[str, Any] | None = None,
        chunk_size: int = 100,
    ) -> AsyncGenerator[str]:
        """
        Export timeline events to CSV format with streaming.

        Yields chunks of CSV data for large exports.

        Args:
            events_generator: Async generator of TimelineEvent objects
            date_format: Date format - "iso" or "human"
            job_id: Job ID for header metadata
            user_id: User ID for header metadata
            filters_applied: Dict of filters applied
            chunk_size: Number of events per chunk (default: 100)

        Yields:
            CSV string chunks
        """
        logger.info(
            "Starting streaming CSV export",
            extra={
                "date_format": date_format,
                "chunk_size": chunk_size,
            },
        )

        # Yield header
        header_output = io.StringIO()
        header_output.write("# Timeline Export (Streaming)\n")
        header_output.write(f"# Exported at: {datetime.now(UTC).isoformat()}\n")
        if job_id:
            header_output.write(f"# Job ID: {job_id}\n")
        if user_id:
            header_output.write(f"# User: {user_id}\n")
        header_output.write("# WARNING: Cells starting with =, +, -, @, TAB, or CR have been sanitized for security.\n")
        header_output.write("#\n")

        writer = csv.writer(header_output)
        writer.writerow([
            "ID",
            "Name",
            "Description",
            "Entity Type",
            "Start Date",
            "End Date",
            "Precision",
            "Uncertainty",
            "Original Text",
            "Sequence Position",
            "Source URL",
        ])

        yield header_output.getvalue()

        # Yield data in chunks
        chunk_output = io.StringIO()
        chunk_writer = csv.writer(chunk_output)
        event_count = 0

        async for event in events_generator:
            chunk_writer.writerow([
                str(event.id),
                self._sanitize_csv_cell(event.name),
                self._sanitize_csv_cell(event.description or ""),
                self._sanitize_csv_cell(event.entity_type),
                self._format_date(event.start_date, date_format),
                self._format_date(event.end_date, date_format),
                event.precision.value if event.precision else "",
                event.uncertainty.value if event.uncertainty else "",
                self._sanitize_csv_cell(event.original_text or ""),
                str(event.sequence_position) if event.sequence_position is not None else "",
                self._sanitize_csv_cell(event.source_url),
            ])
            event_count += 1

            if event_count % chunk_size == 0:
                yield chunk_output.getvalue()
                chunk_output = io.StringIO()
                chunk_writer = csv.writer(chunk_output)

        # Yield remaining events
        remaining = chunk_output.getvalue()
        if remaining:
            yield remaining

        logger.info(
            "Completed streaming CSV export",
            extra={"total_events": event_count},
        )

    def _sanitize_csv_cell(self, value: str) -> str:
        """
        Sanitize a CSV cell value to prevent formula injection.

        Prefixes cells starting with dangerous characters with a single quote.
        This prevents spreadsheet applications from interpreting the cell
        as a formula.

        Args:
            value: Cell value to sanitize

        Returns:
            Sanitized cell value
        """
        if not value:
            return value

        # Check if first character could trigger formula execution
        if value[0] in FORMULA_INJECTION_CHARS:
            return f"'{value}"

        return value

    def _format_date(
        self,
        dt: datetime | None,
        date_format: str,
    ) -> str:
        """
        Format a datetime for CSV output.

        Args:
            dt: Datetime to format (or None)
            date_format: "iso" for ISO 8601, "human" for readable format

        Returns:
            Formatted date string or empty string if None
        """
        if dt is None:
            return ""

        if date_format == "human":
            return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        else:
            return dt.isoformat()

    def export_ics(
        self,
        events: list[TimelineEvent],
        *,
        calendar_name: str = "Timeline Export",
        job_id: UUID | None = None,
        user_id: str | None = None,
    ) -> str:
        """
        Export timeline events to iCalendar (ICS) format.

        Follows RFC 5545 iCalendar specification.

        Args:
            events: List of TimelineEvent objects to export
            calendar_name: Name for the calendar (default: "Timeline Export")
            job_id: Job ID for X-EXPORTED-* properties
            user_id: User ID for X-EXPORTED-* properties

        Returns:
            ICS calendar string
        """
        logger.info(
            "Exporting timeline to ICS",
            extra={
                "event_count": len(events),
                "calendar_name": calendar_name,
            },
        )

        lines = []

        # Calendar header
        lines.append("BEGIN:VCALENDAR")
        lines.append("VERSION:2.0")
        lines.append("PRODID:-//Knowledge Mapper//Timeline Export//EN")
        lines.append(f"X-WR-CALNAME:{self._escape_ics_text(calendar_name)}")
        lines.append("CALSCALE:GREGORIAN")
        lines.append("METHOD:PUBLISH")

        # Export metadata as custom properties
        lines.append(f"X-EXPORTED-AT:{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}")
        if job_id:
            lines.append(f"X-EXPORTED-JOB-ID:{job_id}")
        if user_id:
            lines.append(f"X-EXPORTED-USER:{self._escape_ics_text(user_id)}")
        lines.append(f"X-EXPORTED-EVENT-COUNT:{len(events)}")

        # Add events
        for event in events:
            event_lines = self._create_vevent(event)
            lines.extend(event_lines)

        # Calendar footer
        lines.append("END:VCALENDAR")

        return "\r\n".join(lines) + "\r\n"

    def _create_vevent(self, event: TimelineEvent) -> list[str]:
        """
        Create a VEVENT component for an ICS calendar.

        Args:
            event: TimelineEvent to convert

        Returns:
            List of ICS lines for the VEVENT
        """
        lines = []
        lines.append("BEGIN:VEVENT")

        # Required properties
        lines.append(f"UID:{event.id}@knowledge-mapper")
        lines.append(f"DTSTAMP:{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}")

        # Date handling based on precision
        if event.start_date:
            if event.precision in (DatePrecision.HOUR, DatePrecision.MINUTE):
                # Use DATE-TIME format for precise times
                lines.append(f"DTSTART:{event.start_date.strftime('%Y%m%dT%H%M%SZ')}")
                if event.end_date:
                    lines.append(f"DTEND:{event.end_date.strftime('%Y%m%dT%H%M%SZ')}")
            else:
                # Use DATE format for day/month/year precision
                lines.append(f"DTSTART;VALUE=DATE:{event.start_date.strftime('%Y%m%d')}")
                if event.end_date:
                    lines.append(f"DTEND;VALUE=DATE:{event.end_date.strftime('%Y%m%d')}")
        elif event.sequence_position is not None:
            # For sequence-only events, use a placeholder date
            # Add sequence position to summary
            lines.append(f"DTSTART;VALUE=DATE:{datetime.now(UTC).strftime('%Y%m%d')}")

        # Summary (title)
        summary = self._escape_ics_text(event.name)
        lines.append(f"SUMMARY:{summary}")

        # Description (includes uncertainty information)
        description_parts = []
        if event.description:
            description_parts.append(event.description)

        if event.uncertainty:
            uncertainty_text = self._get_uncertainty_text(event.uncertainty)
            if uncertainty_text:
                description_parts.append(f"Date uncertainty: {uncertainty_text}")

        if event.original_text:
            description_parts.append(f"Original text: {event.original_text}")

        if event.sequence_position is not None:
            description_parts.append(f"Sequence position: {event.sequence_position}")

        if description_parts:
            description = self._escape_ics_text("\\n\\n".join(description_parts))
            lines.append(f"DESCRIPTION:{description}")

        # Categories
        lines.append(f"CATEGORIES:{event.entity_type.upper()}")

        # URL
        if event.source_url:
            lines.append(f"URL:{event.source_url}")

        # Custom properties for precision and uncertainty
        if event.precision:
            lines.append(f"X-DATE-PRECISION:{event.precision.value}")
        if event.uncertainty:
            lines.append(f"X-UNCERTAINTY:{event.uncertainty.value}")

        lines.append("END:VEVENT")
        return lines

    def _escape_ics_text(self, text: str) -> str:
        """
        Escape text for ICS format per RFC 5545.

        Args:
            text: Text to escape

        Returns:
            Escaped text safe for ICS
        """
        # Escape backslashes first
        text = text.replace("\\", "\\\\")
        # Escape semicolons
        text = text.replace(";", "\\;")
        # Escape commas
        text = text.replace(",", "\\,")
        # Escape newlines
        text = text.replace("\n", "\\n")
        text = text.replace("\r", "")
        return text

    def _get_uncertainty_text(self, uncertainty: UncertaintyMarker) -> str:
        """
        Get human-readable text for uncertainty marker.

        Args:
            uncertainty: UncertaintyMarker enum value

        Returns:
            Human-readable uncertainty description
        """
        uncertainty_descriptions = {
            UncertaintyMarker.EXACT: "Exact date",
            UncertaintyMarker.APPROXIMATE: "Approximate date",
            UncertaintyMarker.CIRCA: "Circa (around this date)",
            UncertaintyMarker.BEFORE: "Before this date",
            UncertaintyMarker.AFTER: "After this date",
            UncertaintyMarker.INFERRED: "Date inferred from context",
        }
        return uncertainty_descriptions.get(uncertainty, "")

    def _generate_export_metadata(
        self,
        format_type: str,
        event_count: int,
        relationship_count: int,
        job_id: UUID | None = None,
        user_id: str | None = None,
        filters_applied: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Generate export metadata for watermarking.

        Args:
            format_type: Export format (json, csv, ics)
            event_count: Number of events exported
            relationship_count: Number of relationships exported
            job_id: Job ID
            user_id: User ID
            filters_applied: Filters that were applied to the export

        Returns:
            Dictionary with export metadata
        """
        metadata: dict[str, Any] = {
            "exported_at": datetime.now(UTC).isoformat(),
            "format": format_type,
            "event_count": event_count,
            "relationship_count": relationship_count,
            "generator": "Knowledge Mapper Timeline Export",
            "version": "1.0",
        }

        if job_id:
            metadata["job_id"] = str(job_id)

        if user_id:
            metadata["exported_by"] = user_id

        if filters_applied:
            # Serialize filters, converting any special types
            serialized_filters = {}
            for key, value in filters_applied.items():
                if isinstance(value, datetime):
                    serialized_filters[key] = value.isoformat()
                elif isinstance(value, UUID):
                    serialized_filters[key] = str(value)
                else:
                    serialized_filters[key] = value
            metadata["filters_applied"] = serialized_filters

        return metadata


# Singleton instance
_export_service: TimelineExportService | None = None


def get_timeline_export_service() -> TimelineExportService:
    """
    Get singleton TimelineExportService instance.

    Returns:
        TimelineExportService instance
    """
    global _export_service

    if _export_service is None:
        _export_service = TimelineExportService()

    return _export_service
