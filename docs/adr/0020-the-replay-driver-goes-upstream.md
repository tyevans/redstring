# ADR 0020: The replay driver goes upstream, and is not re-exported back

## Status

Accepted. **Supersedes [ADR 0018](0018-a-replay-report-carries-its-failures.md)**
in its entirety: everything 0018 decided is now decided the same way, by
`eventsource-py` 0.12.0, in `eventsource`'s code rather than ours. 0018 stays
in the tree as the record of *why* the shape is what it is — the reasoning was
the deliverable, and it is the reasoning upstream adopted.

[ADR 0006](0006-the-public-surface-is-gated.md) **stands**, and this removes
five names from `__all__` (`project`, `replay`, `ReplayReport`,
`ReplayFailure`, `ReplayFailedError`) without touching the mechanism.
[ADR 0007](0007-composition-is-the-only-top-layer.md) **stands** — nothing
moved between layers; a module left the tree.
[ADR 0001](0001-event-log-schema-and-granularity.md) and
[ADR 0004](0004-consolidation-emits-events.md) **stand**: no event payload
changed and the log is neither read nor written differently by anything here.

## Context

This project wrote two things because the library underneath it did not offer
them:

- `projections/replay.py` — `project`/`replay`, `ReplayReport`,
  `ReplayFailure`, `ReplayFailedError`. `eventsource`'s `ProjectionCoordinator`
  polls on a timer for live catch-up; nothing drove a *rebuild*, and the
  difference is load-bearing rather than cosmetic
  (`CheckpointTrackingProjection.handle` re-raises, which is correct for a
  subscription and wrong for a rebuild).
- `projections/base.py` — `StoreProjection`, a `DeclarativeProjection` holding
  one store and restating its parent's full constructor so a subclass could
  not silently narrow it.

Both were reported upstream. Both shipped in `eventsource-py` 0.12.0, as its
ADR 0054 and ADR 0055, alongside `FeedReadOptions.aggregate_type` (its ADR
0052), which is the other half of `BACKLOG.md` B68.

So the question this record settles is not whether to adopt them — a
downstream copy of a dependency's feature is debt by definition. It is what
happens to the five names on our public surface.

## Decision

**Delete both modules and take the upstream implementations. Do not
re-export.** `from eventsource import replay`.

### Why not re-export

`redstring.__init__` already argues the general case, about types rather than
functions: *"Re-exporting them under our own name would be worse than
depending on them openly."* A re-export costs the surface five names it does
not own, and buys a caller one import line. The prices are not comparable:
every name in `__all__` is a promise this package has to keep across its own
releases, and four of these five would be promises about someone else's
release cadence.

The alias `project` dies with it, and that is the part worth saying out loud,
because it was added *for* a downstream consumer whose vocabulary has a
*project* noun. The collision is still real and the answer is now
`from eventsource import replay as replay_log` — an import-site rename, which
is where a naming collision is best solved anyway, and which no longer
obliges this package to carry a second name for one function forever.

### What the upstream version does that ours did not

Not a transcription. Five differences, and two of them are defects ours had:

| | ours | upstream 0.12.0 |
|---|---|---|
| read scoping | `tenant_id` | `tenant_id` **and** `aggregate_type`, both pushed into the adapter's query |
| failure list | unbounded, pinning live tracebacks | capped by `max_failures`, with `failures_truncated` reporting what the cap dropped |
| streaming failures | none | `on_failure=`, fired for every failure retained or not, and before a `strict=True` raise |
| `ReplayReport.failed` | distinct **positions** | distinct **event ids** |
| `ReplayFailure.event_type` | `type(event).__name__` | the event's own registered `event_type` |
| `ReplayFailedError` root | `RedstringError` | `eventsource`'s `ProjectionError` |

The third row is `BACKLOG.md` B73, which argued exactly those two shapes — a
cap that says it truncated, or a callback — and deferred choosing between them
until someone had a replay failing at that scale. Upstream took both, which is
the right answer to a two-good-options question when neither costs the other.

The fourth row is a latent defect, not a preference. `position` is
`Position | None` by contract; a store with no feed sets it on nothing, and
our `failed` would then have folded an entire failed rebuild into a count of
one. No test here could have caught it, because both adapters in this
repository always set a position — the failure-shape table in `CLAUDE.md` has
a row for exactly this reasoning ("a contract two implementations satisfy by
accident is not a contract").

The sixth row is a correction. `ReplayFailedError` deriving from
`RedstringError` said the failure belonged to this library; what actually
happened is that a projection failed to process an event, which is
`ProjectionError`. `except ProjectionError` should catch a strict rebuild's
stop for the same reason it catches a live projection's.

### The consequence for `StoreProjection`

`GraphProjection` and `VectorProjection` now inherit
`StoreProjection.__init__(store, **options: Unpack[ProjectionOptions])`.
**All six parent options become keyword-only**, where three of them
(`checkpoint_repo`, `dlq_repo`, `enable_tracing`) had been
positional-or-keyword in our hand-written version. `GraphProjection(store,
checkpoints)` now raises `TypeError`. That is a break with no shim and it is
an improvement: passing those positionally is how you get `enable_tracing`
where you meant `retry_policy`.

## Consequences

**`DOCUMENTED_FOREIGN_TYPES` loses eight entries, and that is the gate
working.** `GlobalEventFeed`, `EventSubscriber` and `Position` were there for
`project`'s signature; `RetryPolicy`, `Tracer`, `TenantFilter`,
`ProjectionCheckpoints` and `DLQRepository` were there for our
`StoreProjection.__init__`. No signature of ours mentions any of them now, so
`test_no_documented_foreign_type_is_stale` struck them —
[ADR 0014](0014-exemption-lists-are-empty-and-must-stay-falsifiable.md)'s rule
firing on a list that describes rather than exempts.

The five constructor names are not undocumented as a result; they are
documented by `ProjectionOptions`, which is upstream's own name for that
option set. `redstring.projections` says so, reasoned about rather than
measured — the same standing exception the module docstring already records
for `document_stream` and the constructor a class inherits from a foreign
base.

**The floor moves to `>=0.12.0` and the cap to `<0.13`.** Unlike the previous
two bumps, the floor moves *because something here needs it*:
`redstring.projections` imports `StoreProjection` from
`eventsource.application.projections`, which does not exist before 0.12.0.
`tests/integration/test_declared_floors_work.py` measures that rather than
asserting it.

**The tests that covered `replay` stay, and change what they are for.** They
now exercise this package's projections *through* the upstream driver — a real
`MissingEntityError` from `GraphProjection`, not an injected exception — which
is the integration nobody upstream can write. `test_replay_alias.py` is
deleted outright: it pinned an alias that no longer exists.

**A future upstreaming should look like this one.** What made it cheap was
that the reasoning had been written down at the time — ADR 0018 and BACKLOG
B68/B73 are what the upstream ADRs are arguing with, and B73's "here are the
two honest shapes and here is why neither is worth choosing yet" is
recognisably what got built. Deferred work recorded properly is a design
document that has already survived a use.
