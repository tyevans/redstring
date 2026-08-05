"""Read models derived from the event log.

The event log is the write model; a `GraphStore` and a `VectorStore` are
projections of it -- derived, disposable, and rebuildable by replay. That
claim is only worth as much as the test that proves it, which is
`tests/unit/projections/test_replay_equivalence.py`.
"""

from redstring.projections.graph import GraphProjection
from redstring.projections.replay import (
    ReplayFailedError,
    ReplayFailure,
    ReplayReport,
    project,
    replay,
)
from redstring.projections.vector import VectorProjection

__all__ = [
    "GraphProjection",
    "ReplayFailedError",
    "ReplayFailure",
    "ReplayReport",
    "VectorProjection",
    "project",
    "replay",
]
