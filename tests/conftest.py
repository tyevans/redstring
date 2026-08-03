"""Test configuration for kg-builder.

These modules reference symbols that do not exist in the extracted source —
and did not exist in knowledge-mapper either. They belong to in-progress
temporal/strategy-router work that was uncommitted at the time of extraction
(`TemporalEventProperties`, `DatePrecision`, `get_strategy_router`). They are
carried over verbatim and skipped at collection until the corresponding
implementation lands.
"""

collect_ignore = [
    "unit/extraction/test_strategy_router.py",
    "unit/extraction/test_temporal_schemas.py",
    "unit/models/test_extracted_entity_temporal.py",
    "unit/services/extraction/test_temporal_enrichment.py",
]
