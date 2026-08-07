"""Passage storage: the `ChunkStore` adapters.

Sibling of `redstring.graph` and `redstring.vector` in the layered contract,
for the same reason each of those is a sibling of the other: it holds the
adapters for one port, needs nothing from either, and neither needs anything
from it. A caller joining a chunk to its entities holds both ports.
"""
