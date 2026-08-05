"""Temporal inference and query, over the store ports.

`domain.temporal_parsing` and `domain.interval` hold the pure logic; this
package is where it meets a `GraphStore`. A sibling of `extraction` and
`consolidation` in the layered contract rather than above them, because it
needs nothing from either: it reads entities through the port and computes.
"""
