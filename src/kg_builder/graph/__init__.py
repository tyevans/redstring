"""Graph storage: the `GraphStore` adapters.

Sibling of `kg_builder.vector` in the layered contract: each holds the
adapters for one port and neither may import the other.

Nothing is re-exported here on purpose. `client.py` and `queries.py` -- the
pre-migration `Neo4jClient` singleton and a module of loose Cypher constants
-- were deleted in slice 9. Importing an adapter is a deliberate act at the
composition root (`kg_builder.graph.adapters.neo4j`,
`kg_builder.graph.adapters.memory`); a package-level re-export would make
`import kg_builder.graph` pull the `neo4j` driver in, and that is an optional
extra.
"""
