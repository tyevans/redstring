"""
Neo4j knowledge graph integration for Knowledge Mapper.

This package provides:
- Neo4j client for database operations
- Entity and relationship synchronization
- Graph queries with tenant isolation
"""

from kg_builder.graph.client import Neo4jClient, get_neo4j_client

__all__ = [
    "Neo4jClient",
    "get_neo4j_client",
]
