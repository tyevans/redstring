# Reference

Lookup material: what a thing is and what it guarantees, without a task
wrapped around it.

| Page | Covers |
|---|---|
| [Events](events.md) | `DocumentExtracted`, `EntitiesMerged`, `MergeUndone` — the whole persisted schema, field by field, and what each one commits the log to |
| [Aggregates](aggregates.md) | The write model: which aggregate owns which event, and how a merge rehydrates its own history to undo itself |
| [Domain value types](domain-value-types.md) | `Entity`, `Relationship`, `Alias`, `TemporalExtent`, the id newtypes, and the invariants each enforces at construction |
| [Domain schema YAML](domain-schema-yaml.md) | Every key a schema file may carry, and what each does to the generated prompt |
| [Neo4j graph store](neo4j-graph-store.md) | The adapter's Cypher, its node and relationship layout, and the constraints it expects |
| [Quality gates](quality-gates.md) | What each pre-commit hook checks, the configuration it reads, and why running one by hand answers a different question than the configured run |

## The public surface

The authoritative list is `redstring.__all__`, and the module docstring is its
reference — including what is deliberately left out. That is not duplicated
here on purpose: a second copy of an API list is a second thing to go stale,
and this one is enforced by tests rather than by prose. See
[ADR 0006](../adr/0006-the-public-surface-is-gated.md).

```python
import redstring
print(redstring.__all__)
```

Anything reached through a dotted path — `redstring.consolidation.service`,
`redstring.llm.retry` — is internal. It is real and it is tested; what it does
not have is a promise, so a rename or a changed signature there is not a
breaking change.
