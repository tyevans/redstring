"""Refusing fan-out parameters at the composition entry points.

`concurrency` and `gleanings` multiply the model calls one document costs, so
both are refused before the first call is made. The entry points share one
check so that `build_graph`, `build_graph_batch` and `build_graph_stream`
cannot disagree about the range.
"""

from __future__ import annotations

from redstring.domain.limiter import check_concurrency
from redstring.extraction.gleaning import check_gleanings

__all__ = ["check_build_limits"]


def check_build_limits(concurrency: int, gleanings: int) -> None:
    """Refuse fan-out values before any model call is made.

    Checked at the entry points, not only in `ExtractionPipeline`. With
    `domain=AUTO` a call to the pipeline's constructor comes after the
    classifier has already been called, so a refusal there would be a
    refusal after a paid call.

    Raises:
        ValueError: `concurrency` is outside `1..MAX_CONCURRENCY`, or
            `gleanings` is outside `0..MAX_GLEANINGS` (`check_gleanings`).
    """
    check_concurrency("concurrency", concurrency)
    check_gleanings(gleanings)
