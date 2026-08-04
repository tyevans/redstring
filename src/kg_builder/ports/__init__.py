"""Ports: the interfaces the library depends on, defined in domain vocabulary.

A port sits directly above `domain` and below every adapter. Nothing here
imports an adapter, a driver, or a query language. In particular the
`GraphStore` port must never leak Cypher.
"""
