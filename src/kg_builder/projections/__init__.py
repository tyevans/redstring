"""Read models derived from the event log.

The event log is the write model; a `GraphStore` and a `VectorStore` are
projections of it -- derived, disposable, and rebuildable by replay. That
claim is only worth as much as the test that proves it, which is
`tests/unit/projections/test_replay_equivalence.py`.
"""

from kg_builder.projections.graph import GraphProjection
from kg_builder.projections.replay import ReplayReport, project
from kg_builder.projections.vector import VectorProjection

__all__ = [
    "GraphProjection",
    "ReplayReport",
    "VectorProjection",
    "project",
]
