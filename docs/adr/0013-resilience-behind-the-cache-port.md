# ADR 0013: Resilience lives in `llm/`, over the `Cache` port

**Status:** accepted, slice 6 of the ring migration.

Retry, rate limiting and circuit breaking are transport concerns. They sit in
`redstring.llm` — beside the provider adapters, a *sibling* of `extraction`
rather than a layer beneath it — and the state they need to survive a process
boundary lives behind `redstring.ports.cache.Cache`, whose default
implementation is in-memory. Redis is an upgrade a caller chooses when several
workers must trip and throttle together, never the price of getting extraction
to run.

**Why this is an ADR:** the reasoning exists only in the docstrings of
`llm/retry.py`, `llm/rate_limiter.py`, `llm/circuit_breaker.py` and
`ports/cache.py`, and the layer placement is asserted by `CLAUDE.md`'s
architecture contract but argued nowhere. Those docstrings carry the decisions
that are expensive to rediscover — why a sliding window rather than a bucket,
why the failure counter decays by TTL, why the port says `record_hit` instead
of `zadd`, why the `Ollama*` prefixes are gone — and a docstring is read by
whoever is already editing the file, which is the one person who does not need
convincing. This document is for the reader deciding whether to move a
resilience decorator into `extraction/`, add a Redis dependency to the `llm`
extra, or widen `Cache` into a general-purpose cache. Each of those has been
considered and rejected here, with reasons that outlive the code they were
written about.

Related: [ADR 0002: two store ports](0002-two-store-ports.md) and
[ADR 0008: the two non-store ports](0008-the-two-non-store-ports.md) cover the
other port boundaries; [Harden model calls](../how-to/harden-model-calls.md) is
the task-shaped version of everything below;
[Implement a store adapter](../how-to/implement-a-store-adapter.md) is the same
exercise for the store side; the [README](https://github.com/tyevans/redstring/blob/main/README.md) states the
no-infrastructure default this ADR is the argument for.

## Context

Extraction calls a model over a network, and the calls fail in the ways
network calls fail: transiently, in bursts, and sometimes because the thing on
the other end has fallen over and every further call is making it worse. The
library needs retry, rate limiting and circuit breaking for that reason alone.
The question this ADR settles is not whether to have them but where they live
and what they are allowed to require of a caller.

### What moved: `extraction/rate_limiter.py` and `extraction/circuit_breaker.py` spoke Redis directly

Before slice 6 both modules sat in `extraction/` and imported
`redis.asyncio` at module scope, taking their configuration from a global
`redstring.config.settings`. The rate limiter kept its sliding window in a
Redis sorted set and the circuit breaker kept `state`, a failure count, an
`opened_at` timestamp and a half-open call count in four Redis keys under an
`ollama_circuit` prefix. Neither had an in-memory path: importing either module
required the `redis` package, and a caller who passed no `redis_url` did not
get a local fallback — the constructor silently substituted
`settings.REDIS_URL`, so the first call went to whatever that pointed at.

Two consequences followed from that shape, and both are the reason the modules
moved rather than merely being tidied. The first is architectural: a resilience
module inside `extraction/` is a network concern living in the layer that is
supposed to be about entities and documents, and it dragged a third-party
client in with it. The second is testing: with the clock and the store both
inside the implementation, every test of a window or a recovery timeout was
either a `sleep` or a Redis fixture.

The classes were also named `OllamaRateLimiter` and `OllamaCircuitBreaker`,
after the only caller they happened to have. Nothing in either was
Ollama-specific.

### What a library may not require: a caller with no Redis must still get extraction

`redstring` is a library, and the deciding constraint is what a caller must
*stand up* before anything works. A caller who wants extraction in one process
should get correct rate limiting and a working circuit breaker with no server
to run — the coordination Redis provides is only needed when several workers
must trip and throttle *together*, which is a deployment property, not a
property of extraction. The old shape failed that test in the quietest
possible way: it did not raise on a missing `redis_url`, it fell back to a
global setting, so the first model call reached for a host the caller had
never been asked about.

So the resilience state had to move behind a port whose default implementation
is in-process, and Redis had to become an adapter a caller *constructs* rather
than a module-scope import anyone inherits.
`redstring.ports.cache.Cache` is that port,
`redstring.llm.cache.memory.MemoryCache` is that default, and
`redstring.llm.cache.redis.RedisCache` is the upgrade. Both the rate limiter
and the circuit breaker take `cache: Cache | None = None` and substitute
`MemoryCache()` when it is None, so the no-infrastructure path is the one you
get by not deciding. `RedisCache` takes a `redis.asyncio.Redis` client the
caller has already made — it invents no URL — and defers its `redis` import to
the one classmethod that builds a client for you, so `import redstring` never
touches the driver.

Note the boundary this draws, because it is easy to misread: the `redis`
package is a plain dependency of the distribution, so the *import* is always
available. What a caller is never required to have is a running Redis. This
ADR is about the second, and every decision below follows from those two
constraints — transport concerns belong beside the transport, and
infrastructure is never the price of entry.

## Decision 1: retry, rate limiting and circuit breaking are transport concerns, so they sit in `llm/`

`redstring.llm.retry`, `redstring.llm.rate_limiter` and
`redstring.llm.circuit_breaker` live beside the provider adapters, in the
package the layered contract makes a *sibling* of `extraction`. Nothing
outside `llm/` refers to them: `with_retry`, `RateLimiter` and
`CircuitBreaker` appear nowhere else under `src/`, and the only first-party
name `extraction/` imports from the model side is
`redstring.domain.exceptions.LlmProviderError`.

The test each of the three passes is the same one, and it is worth stating in
the form that decides future cases: **would this concern still exist if the
model were called some other way, and would it exist if extraction were
replaced by something else?** Retry, throttling and breaking are all yes to
the first and no to the second. They exist because a call crosses a network to
a service with finite capacity that can fall over. Swap extraction for
summarisation, classification, or anything else that calls a model, and all
three are needed unchanged; swap the network call for an in-process model and
all three evaporate. That is what makes them properties of the transport
rather than of turning prose into entities — which is exactly the argument
`llm/__init__.py` makes, and which the old placement in `extraction/` got
backwards.

Placing them here buys three things that the subsections below take in turn.
Extraction is left knowing only `ports.llm_provider`, so it cannot acquire a
dependency on how a model call is made. A second caller of the transport gets
the resilience for free rather than reaching sideways into `extraction/` for
it. And the three modules become testable in isolation, because none of them
has any reason to know what it is wrapping — `with_retry` is a decorator over
an arbitrary awaitable and names no exception type at all, which is the next
decision after this one.

### Why not `extraction/`: what is being retried is a network call

`llm/retry.py` states the whole argument in one line — *what is being retried
is a network call, and extraction should not know that one happened.* A retry
is not a second attempt at extracting entities. It is a second attempt at
reaching a host, made because the first attempt failed in a way the host may
answer differently. The unit of work that gets repeated is the request, and
the request is the transport's object, not extraction's.

Follow that through and the same holds for the other two. A rate limiter's
budget is requests per minute against a server with finite capacity; it is
measured in calls, not in documents or chunks or entities. A circuit breaker's
`OPEN` state is a claim about a *host* — that it is failing and further
traffic makes it worse — and its `HALF_OPEN` probe is a question put to that
host. None of the three has a concept in it that extraction owns. Put them in
`extraction/` and every one of them is a module whose entire vocabulary comes
from the layer next door.

The concrete cost of the old placement was that extraction knew about
`redis.asyncio`. That is the shape the rule exists to prevent, and it is worth
naming precisely, because it does not look like a layering mistake while you
are making it: a resilience module needs somewhere to keep a failure count, the
nearest durable store is Redis, and now the package that is supposed to be
about entities and documents imports a network client. The dependency did not
arrive because anyone decided extraction should talk to Redis. It arrived as
the second-order consequence of putting a transport concern in the wrong
package, which is why the placement is worth arguing rather than assuming.

The inverse test is the one to apply to the next candidate. Chunking looks
superficially similar — it exists partly because of a context limit, which is a
property of the model — but what it operates on is a `SourceDocument` and what
it produces is text that will become entities. It survives replacing the
network call with an in-process model; retry, throttling and breaking do not.
That is the line: **a concern belongs in `llm/` when removing the network
removes the concern.**

### Why `llm` is a *sibling* of `extraction`, not a layer beneath it

The previous section argues the three modules belong in `llm/`. This one
argues about the shape of the line they sit on, which is a separate decision
and the more easily lost of the two. `pyproject.toml` declares:

```
extraction : consolidation : temporal : graph : vector : llm
```

The colons make those six packages *siblings*, and in an import-linter layers
contract siblings are forbidden to import each other in **either** direction.
Had `llm` been given its own line below `extraction`, the contract would still
have been satisfied by everything the library does today — extraction is
higher, so extraction importing `llm` would be legal. That is precisely the
outcome the sibling line exists to make impossible: `extraction/pipeline.py`
could then `from redstring.llm.adapters.langchain import ...` and the
`LlmProvider` port would become advisory. Sibling placement is what converts
"extraction talks to the port" from a convention into a gate.

What extraction is left with is visible in `pipeline.py`: its only names from
the model side are `redstring.ports.llm_provider.LlmProvider`, imported under
`TYPE_CHECKING`, and `redstring.domain.exceptions.LlmProviderError`. A port
and an exception, both from layers *below* both siblings — which is the
general form of how two siblings are allowed to meet. Shared vocabulary moves
down, never sideways. `consolidation` and `temporal` are on the same line for
the same reason and settled it the same way: the tie-break that consolidation
and extraction both needed moved down to `domain.preference` rather than one
importing the other.

The asymmetry is worth stating explicitly, because "sibling" sounds like a
weaker claim than "beneath" and is in fact a stronger one. Beneath would
constrain `llm` and leave `extraction` free. Siblings constrain both, and the
direction that matters is the one a layers contract would otherwise permit.

Two things follow that a lower placement would not give. `llm` cannot grow a
dependency on extraction to reach the resilience decorators the other way —
there is no arrangement in which `circuit_breaker.py` learns what a chunk or a
`SourceDocument` is. And a second caller of the transport, one that summarises
or classifies rather than extracting, sits on this same line and gets retry,
throttling and breaking without reaching into `extraction/` for them. Under
the alternative, resilience would have been reachable only from above, and the
second caller's honest options would be to import extraction or to copy the
three modules.

One limit, and it is the reason a test exists next to the contract:
**`lint-imports` sees first-party imports only.** It cannot tell you that
`langchain_core` has appeared in `extraction/pipeline.py`, because the layers
contract is declared over `redstring` with `include_external_packages =
false`. The sibling line stops extraction reaching the adapter *through*
`redstring.llm`; nothing in it stops extraction importing LangChain directly.
`tests/unit/llm/test_port_does_not_leak.py` is the other half — it parses
every module under `src/` and fails on any `langchain*` import outside
`llm/adapters/`, by source text rather than by importing, so a lazy
function-level import cannot slip through. Read the two together: the contract
keeps the port from being bypassed from the inside, the test keeps it from
being bypassed from the outside, and either alone leaves the guarantee half
made.

### The consequence for `extraction`: it reaches `ports.llm_provider` and never an adapter

The two sections above are about where resilience lives. This one is about
what the arrangement leaves extraction *able to say*, because that is the
property worth protecting and it is stated most precisely as an inventory.

Everything `extraction/` knows about calling a model is two names, both from
layers below the sibling line:

```python
from redstring.domain.exceptions import RedstringError, LlmProviderError

...
if TYPE_CHECKING:
    from redstring.ports.llm_provider import LlmProvider
```

`pipeline.py` and `classifier.py` import exactly that pair and nothing else
from the model side. The port comes in under `TYPE_CHECKING`, so at runtime
extraction does not import it at all — a `Protocol` it only annotates against
costs nothing to load. `LlmProvider` never appears in an `isinstance` check
here; the provider arrives as a constructor argument and is used through
`await self._provider.extract(...)` and `self._provider.model`. Two methods.
That is the entire contract between the layer that turns prose into entities
and the layer that talks to a network.

What follows is the part that makes the boundary worth its cost. Extraction
cannot tell whether the provider it holds is hardened. `LangChainLlmProvider`,
`FakeLlmProvider`, and a caller's own wrapper that retries and throttles are
indistinguishable from inside `pipeline.py`, because all three are just
something with `extract` and `model`. Retry is not a parameter it passes, a
setting it reads, or an exception type it catches specially — a call that
failed twice and succeeded on the third attempt is, to extraction, a call that
succeeded. `with_retry`, `RateLimiter` and `CircuitBreaker` appear in no module
outside `llm/`; grep for them across `src/` and the sibling package is the only
hit.

That invisibility is what makes the hardening *composable*. Nothing in the
library wires the three pieces onto a provider automatically: no adapter
applies them, `build_graph` does not, and there is no configuration flag that
turns them on. The caller wraps its own provider and passes the wrapper in,
which is exactly what [Harden model calls](../how-to/harden-model-calls.md)
walks through. Because the wrapper satisfies `LlmProvider`, `build_graph`
accepts it for that reason and no other — and a caller who wants a different
retry policy, a token-bucket limiter, or no hardening at all changes their
wrapper and touches nothing in this library. Had resilience been applied
inside the pipeline, every one of those choices would have been a parameter on
`ExtractionPipeline` and a branch in its body.

The failure vocabulary follows the same rule. The one thing extraction *does*
know is that a model call can fail, and it knows it as `LlmProviderError` from
`domain.exceptions` — a domain type, not a transport one. `pipeline.py` catches
it to implement `skip_failed_chunks`; `classifier.py` catches it to fall back
to a default domain. Neither has ever seen a `ConnectionError`, an HTTP status,
or a `CircuitBreakerOpenError`. The adapter is what translates the network's
vocabulary into the port's, and that translation is the port's real work — a
port that let `httpx` exceptions through would have moved the coupling rather
than removed it.

The check on all of this is a negative one, and negative properties rot
quietly. Two gates hold it: the sibling line in the layers contract, which
makes `from redstring.llm...` in `extraction/` a build failure rather than a
review comment, and `tests/unit/llm/test_port_does_not_leak.py`, which catches
the third-party half the contract cannot see. Between them, the sentence "the
pipeline reaches the port and never an adapter" is enforced rather than
merely true today.

### Retryable exceptions stay the caller's decision — the module names none

`with_retry` takes `retryable_exceptions` as its first parameter and there is
no list of exception types anywhere in `llm/retry.py`. That is deliberate, and
it is the decision most likely to be undone by someone trying to be helpful,
so it is worth writing down what the helpful version would cost.

The rule the module enforces is stated in its own docstring: *retrying is only
safe for a failure the provider may answer differently next time* — a dropped
connection, a timeout, a 503 — and never for one it will answer identically,
such as a malformed request or a refused key. Which exceptions fall on which
side depends on the client library the caller's provider actually uses.
`langchain-openai` raises `openai.APIConnectionError`; an `httpx`-based
provider raises `httpx.ConnectError`; a caller's own adapter may raise
whatever it likes. A built-in list would have to name third-party exception
types, which means either importing them — putting a vendor dependency in a
module whose whole purpose is to be vendor-neutral — or matching on class
names as strings, which is worse.

The failure mode of getting it wrong is not a crash but a delay. Passing a
tuple that is too wide turns a permanent failure into the same permanent
failure `max_retries` times slower, with the backoff slept in between. A
malformed prompt that would have failed in 200ms fails in eight seconds
instead, and every one of the extra attempts spends the caller's quota
producing the identical error. Nothing raises; the symptom is a pipeline that
got slow.

Note the default, because it is the widest possible tuple:
`retryable_exceptions: tuple[type[Exception], ...] = (Exception,)`. Applying
`@with_retry()` with no arguments retries *everything*, including
`MalformedCompletionError` and `RefusedCompletionError` — the two cases the
adapter goes out of its way to distinguish precisely so a caller need not
retry them. The default is convenience for a decorator that must have one, not
a recommendation. [Harden model calls](../how-to/harden-model-calls.md) always
passes the tuple explicitly, and so should you.

The same reasoning explains why the module does not translate what it catches.
`RetryExhausted` is raised when the attempts run out, carrying the last real
exception as `__cause__` — it does not wrap the failure in an
`LlmProviderError`, because it does not know that what it retried was a model
call. `with_retry` is a decorator over an arbitrary awaitable; the only thing
it is entitled to say is "this ran out of attempts", and the thing that failed
stays intact underneath for the caller to inspect. Translation into the
domain's vocabulary is the *adapter's* job, and `llm/adapters/langchain.py`
does it: `EmptyCompletionError`, `MalformedCompletionError`,
`RefusedCompletionError`, all `LlmProviderError` subclasses. That split is the
reason a caller can retry on the vendor's connection error and still hand
`extraction/` nothing but domain exceptions.

The general form, and it applies to the next resilience primitive as much as
this one: **a module that would have to name a vendor's exception types to be
useful should take them as an argument instead.** The knowledge of which
failures are transient lives with whoever chose the provider. Moving it into
the library would make `llm/retry.py` depend on the set of providers that
existed when it was written.

## Decision 2: resilience state lives behind the `Cache` port, not behind Redis

The rate limiter and the circuit breaker both need state that outlives a
single call, and both take it as `cache: Cache | None = None`, substituting
`MemoryCache()` when it is None. `redstring.ports.cache.Cache` is a
`runtime_checkable` `Protocol` with eight methods and no dependency on
anything; `redstring.llm.cache.memory.MemoryCache` implements it in a dict,
and `redstring.llm.cache.redis.RedisCache` implements it over a Redis client
the caller owns. Neither resilience module imports `redis`, and neither
mentions a URL.

The reason this is a decision rather than tidiness is what the state
*actually is*. A sliding window of request timestamps and a decaying failure
count are, viewed one way, obviously Redis: a sorted set and a counter with a
TTL, four commands, done. That reading is how the old modules ended up
importing `redis.asyncio` in `extraction/` — not by anyone deciding
extraction should talk to Redis, but by the state having an obvious home and
nobody asking what the caller was thereby required to run. Viewed the other
way, the state is small, per-tenant, per-circuit, and only needs to be *shared*
when more than one process is doing the limiting. Sharing is a deployment
property. Making it a precondition of the library is the mistake.

So the port is drawn at the place where those two readings diverge. Everything
above the port — window arithmetic, `retry_after`, the state machine, the
decay — is behaviour the library owns and tests without infrastructure.
Everything below it is "keep this, expiring, possibly shared", which is the
only part Redis was ever needed for. `MemoryCache` is not a stub for tests: a
limiter backed by it genuinely limits and a breaker backed by it genuinely
opens. The single thing it cannot do is let two processes agree.

Two properties of the port follow from that, and the subsections below take
them in turn. It is *small* and phrased in the caller's vocabulary —
`record_hit`, `count_hits`, `oldest_hit`, not `zadd`, `zcount`,
`zremrangebyscore` — because a port named after one adapter's commands is that
adapter wearing a different name, and no in-memory implementation could
satisfy it without reimplementing Redis. And it *takes the time as an
argument* rather than reading a clock, which is what makes both a drift-free
Redis adapter and a sleep-free test possible at once.

The general form, for the next port this library grows: **draw the boundary at
what the caller needs done, not at what the leading implementation makes
easy.** The second test is the one that decides it — write the in-memory
adapter first, and if it turns out to be a re-implementation of the
infrastructure, the port has the infrastructure's shape rather than the
caller's.

### `MemoryCache` is the default, not an option; `RedisCache` is the upgrade for multi-worker coordination

Neither resilience class asks for a cache. `RateLimiter` and `CircuitBreaker`
both declare `cache: Cache | None = None` and both resolve it the same way:

```python
self._cache: Cache = cache if cache is not None else MemoryCache()
```

That line is the decision in its entirety. A caller who never thinks about
where the state lives gets an in-process dict, and gets it by *not deciding* —
which is the only default a library is entitled to have. The old shape decided
the other way in the quietest possible manner: a missing `redis_url` fell back
to `settings.REDIS_URL`, so the caller's first model call reached for a host
nobody had asked them about.

`MemoryCache` is not a test double that happens to be shipped. Its own
docstring is blunt about it — *it is not a stand-in that "works well enough for
tests": a rate limiter backed by this genuinely limits, and a circuit breaker
backed by this genuinely opens.* The window arithmetic is real (`bisect.insort`
on write, `bisect_left` on read, pruning in place), the TTLs are real, and
`increment` refuses to refresh an existing deadline for exactly the reason
Redis's `INCR` does not. A single-process caller is not running a degraded
mode. They are running the whole thing.

**What `RedisCache` adds is one property, and it is worth naming precisely:
several processes see one view of the state.** Nothing else. Not durability —
both are expiring caches and neither promises a hit survives anything. Not
speed — a dict beats a network round trip. Not correctness of the algorithm —
that lives above the port. The single missing property matters only when the
limit or the breaker is supposed to be a property of the *deployment* rather
than of a worker, and `tests/unit/llm/test_rate_limiter.py::TestSharing` is
where the difference is pinned:

```python
cache = MemoryCache()
first = RateLimiter(rpm=1, cache=cache)
second = RateLimiter(rpm=1, cache=cache)

await first.acquire(tenant)
with pytest.raises(RateLimitExceeded):
    await second.acquire(tenant)
```

…paired with a second test in which two limiters on *separate* caches both
succeed. The pair is the point. The first test alone would pass against an
implementation that always raised on the second call, and the docstring on the
second says so: *"the other half of the claim, so the test above is not
vacuous."* Together they state the failure the upgrade prevents — a
four-worker deployment permitting four times the configured rate, silently,
and only in production.

Note what those tests do *not* need: Redis. Sharing is a property of one cache
instance being passed to two limiters, so the multi-worker behaviour is
demonstrable in-process, in milliseconds, with no fixture. That is a
side-effect of drawing the port at "keep this, expiring, possibly shared"
rather than at Redis's command set, and it is the strongest practical argument
for having done so.

The two adapters are held to one behaviour by `tests/compliance/cache.py`,
which both must pass — the package docstring names it as *what stops them
drifting*, on the grounds that an in-memory reference more forgiving than the
real backend lets a caller pass its tests on behaviour production does not
have. Two places where they were nearly allowed to differ are already fixed
and are the shape to watch for: `RedisCache` sets `decode_responses=True`
because the port says `get` returns `str | None` and a default client returns
`bytes` — a caller comparing against a `str` literal would match under
`MemoryCache` and never match under Redis. And `set` uses `px` rather than
`ex`, because the port's TTL is a float and `ex` truncates, turning a 0.5s TTL
into no TTL at all. Both are invisible to any test run against one adapter.

Only the import graph, not the dependency list, is what "no infrastructure"
means here. `redis[hiredis]` is a plain dependency of the distribution, so the
package is always importable; what a caller is never required to have is a
*running* Redis. `redstring.llm.cache.__init__` exports `MemoryCache` and
only `MemoryCache`, and `RedisCache.from_url` defers its `import
redis.asyncio` into the classmethod body, so `import redstring` touches the
driver nowhere. Choosing the upgrade is an explicit reach for
`redstring.llm.cache.redis`, which is the level of deliberateness the choice
deserves.

`RedisCache` also takes a client rather than inventing one:
`RedisCache(client, owns_client=False)` wraps something the caller already
owns, and `owns_client` defaults to False so `close()` does not shut a shared
client out from under whatever else is using it — a bug that only ever appears
during shutdown. `from_url` is the convenience path, and it is the one case
where the cache owns what it closes.

The general rule, and it is the one to apply to the next port with an
infrastructure-backed adapter: **the default implementation must be the one
that requires nothing, and the upgrade must be nameable in a single sentence.**
If the in-memory default is only usable in tests, the port is in the wrong
place; if you cannot say in one line what the infrastructure buys, you do not
yet know whether it is needed. Here the sentence is "several processes agree",
and everything else about `RedisCache` is bookkeeping in service of it.
[Harden model calls](../how-to/harden-model-calls.md) is the task-shaped
version — Step 4 is choosing the backend, and it is the only step a
single-process caller can skip.

### The port is deliberately small: exactly what a limiter and a breaker need

`Cache` is a `runtime_checkable` `Protocol` with eight methods — `get`, `set`,
`increment`, `delete`, `record_hit`, `count_hits`, `oldest_hit`, `close` — and
its own docstring states the constraint the size comes from: *this is not a
general-purpose cache abstraction; it is exactly what a rate limiter and a
circuit breaker need in order to coordinate several processes without either of
them naming Redis.*

"Exactly" is meant literally, and it is checkable. Every method has a caller in
this library, and the two callers between them use all of them:

| Method | Used by | For |
|---|---|---|
| `get` | `CircuitBreaker` | the state name, `opened_at` |
| `set` | `CircuitBreaker` | writing `state`, `opened_at` |
| `increment` | `CircuitBreaker` | the failure count, the half-open probe count |
| `delete` | `CircuitBreaker` | clearing `failures`, `opened_at`, `half_open_calls` |
| `record_hit` | `RateLimiter` | one request against the window |
| `count_hits` | `RateLimiter` | how much of the budget is spent |
| `oldest_hit` | `RateLimiter` | what `retry_after` is |
| `close` | both | releasing whatever the adapter holds |

There is nothing in the port neither of them calls, and — the half of the claim
that is easier to lose — nothing either of them needs that is missing, so
neither reaches around it. Note that the two halves of the table do not
overlap: the breaker touches none of the window methods and the limiter touches
none of the key/value ones. That is the subject of the next section.

The size is a *cost* decision, not an aesthetic one. Every method on a port is
something each adapter must implement correctly and something the compliance
suite must pin; `tests/compliance/cache.py` is run against both `MemoryCache`
and `RedisCache`, so a ninth method is a ninth pair of behaviours that can
silently disagree. The two disagreements this port has already had — `bytes`
versus `str` from `get`, and `ex` truncating a float TTL to nothing — were both
found on methods that exist because something calls them. A method added
speculatively gets the same failure modes and no test pressure to expose them,
because nothing in the library exercises it.

What was deliberately left out is the more useful list, since each omission is a
thing a general-purpose cache would have and this one refuses:

- **No `get_many`/`set_many`.** Neither caller ever reads two keys as a unit.
- **No `expire`/`ttl`/`persist`.** TTL is set where a key is created and never
  adjusted afterwards; `increment`'s docstring makes the refusal explicit —
  `ttl_seconds` applies when the key is *created*, because a TTL refreshed on
  every hit is a counter that never expires under load, which is exactly when
  it needs to.
- **No `decrement`.** The breaker's failure count does not come back down; it
  decays by TTL or is deleted wholesale on a reset. That is the subject of a
  later section, and it is why the absence is a design statement rather than an
  omission.
- **No compare-and-set, no locks, no transactions.** The breaker's writes are
  last-writer-wins, and adding atomicity here would be adding it for a race
  nobody has yet argued is real.
- **No serialisation.** Values are `str`, so the port never has to say whose
  JSON or pickle it speaks.
- **No cache-aside `get_or_set`, no key iteration, no `flush`.** These are the
  operations a *caching* abstraction has. This port is not for caching
  anything; it holds resilience state that happens to expire.

Each of those is addable the day a caller needs it, and adding one then is a
small, argued change. Shipping them now would mean two adapters implementing
behaviour with no test that can distinguish right from wrong.

The general form is the same instruction as the previous section, applied to
method count instead of vocabulary: **a port should be the intersection of what
its callers use, not the union of what its adapters offer.** Redis offers a few
hundred commands and `MemoryCache` could offer anything a dict can do; the
useful port is the small overlap the library actually needs. When someone
proposes a ninth method, the question that decides it is not "would this be
handy" but "which existing caller is currently working around its absence" —
and if the answer is none, the method is a guess that two adapters will have to
keep in step forever. [Implement a store adapter](../how-to/implement-a-store-adapter.md)
makes the same argument from the adapter author's side, where the cost of a
wide port is paid.

### Two capabilities, not one: key/value for the breaker, hit-window for the limiter

The eight methods are not one interface used two ways. They are two disjoint
sets, and the split is exact — the previous section's table has no row where
both callers appear:

- **Key/value — `get`, `set`, `increment`, `delete`.** The circuit breaker's
  whole world. Four keys under `kg:circuit`: `state`, `opened_at`, `failures`,
  `half_open_calls`. A name, a timestamp, and two counters.
- **Hit window — `record_hit`, `count_hits`, `oldest_hit`.** The rate
  limiter's whole world. One series per tenant under `kg:ratelimit`, and the
  three questions a sliding window asks of it: add this call, how many are
  still inside the window, when does the oldest one leave.

`close` is the only method both call, and it is not state at all.

The reason this is worth writing down is that the two halves look mergeable
and are not. A window is obviously "just" a list, and a list is obviously "just"
a value — so a port with `get`/`set` and nothing else would appear to support
both, with the limiter reading its series, appending, and writing it back. That
version is wrong for two separate reasons, and only the first is obvious.
Read-modify-write over a shared store is a lost update whenever two workers
call at once, which is exactly the deployment the shared store exists for. And
it moves the pruning into the limiter, so every adapter that could prune
cheaply — Redis with `ZREMRANGEBYSCORE`, `MemoryCache` with a slice — is
prevented from doing so by a port that hands it an opaque string.

The converse merge fails just as cleanly. Nothing the breaker keeps is a
series. Its failure count is a scalar that decays by TTL and is deleted
wholesale on a success; the state name is a single value that the last writer
wins. Expressing those as hits in a window would mean asking "how many
failures since T" — which is a *different algorithm* from the one the breaker
implements, and one with no `HALF_OPEN` in it.

So the port carries both vocabularies rather than one general one, and each
caller uses only its own half. That asymmetry is a feature worth defending
when someone proposes unifying them: an adapter author implementing `Cache`
is writing two small, independent things — a dict with deadlines and a sorted
series with a window query — neither of which has to be expressed in the
other's terms. `MemoryCache` keeps them in two attributes (`_values` and
`_hits`) for precisely that reason, and the only place they meet is `delete`,
which clears both so that one key name cannot half-survive.

One consequence to know before writing a third caller: **the two halves share a
key namespace but not a value space.** `MemoryCache.increment` raises on a
non-numeric value rather than resetting the count, specifically because Redis
raises there too and silent divergence between the adapters is the failure this
port has already had twice. A caller that `set`s a key and later `increment`s
it gets an error from both adapters, which is the intended answer. What is
undefined is mixing halves on one key — `set` and `record_hit` on the same
string touch different maps in `MemoryCache` — so give the two kinds of state
different prefixes, the way the two callers already do.

The general rule this instance illustrates: **when a port's callers turn out to
use disjoint subsets of it, that is evidence the vocabulary is right, not that
the port should be split or merged.** The alternative shapes both fail the same
test — a general "store a value" port makes the limiter reimplement Redis in
the caller, and a general "count events" port makes the breaker a different
algorithm. Two named capabilities, each stated in the terms of the thing that
needs it, is what lets `MemoryCache` be forty lines rather than a re-implementation
of the infrastructure. [Harden model calls](../how-to/harden-model-calls.md)
shows the two capabilities from the caller's side; the compliance suite in
`tests/compliance/cache.py` exercises both halves against both adapters, which
is what keeps them from drifting apart independently.

### Why the window half is not `zadd`/`zcard`/`zremrangebyscore`

The three window methods are named for what a sliding-window limiter wants to
know — record this call, how many are still inside the window, when does the
oldest one leave — and not for the four Redis commands that implement them.
The port's own docstring states the reason: *a port that said
`zadd`/`zcard`/`zremrangebyscore` would be a Redis port wearing a different
name, and no in-memory implementation could satisfy it without reimplementing
Redis.*

The mapping is one-to-one, which is what makes the temptation real.
`RedisCache.record_hit` is a `ZADD` with the epoch time as the score,
`count_hits` is `ZREMRANGEBYSCORE` then `ZCOUNT` in a transaction, and
`oldest_hit` is `ZRANGEBYSCORE ... LIMIT 0 1 WITHSCORES`. Nothing is lost in
translation, so the command-shaped port would have been *shorter* to write and
would have looked more general — the limiter could then keep any series it
liked, and a future caller could use the sorted set for something else.

Three things go wrong the moment the port is spelled that way, and the first
is the decisive one.

**The in-memory adapter becomes a re-implementation of Redis.** `zadd` obliges
`MemoryCache` to hold a member-to-score map with `NX`/`XX`/`GT`/`CH` semantics
and ordering by `(score, member)` lexicographic tie-break; `zcard` obliges a
count that ignores the window entirely, so pruning has to be exposed
separately and correctly. What the library actually needs from that structure
is a list and a binary search — `bisect.insort` on write, `bisect_left` on
read, twenty lines. The general rule from the previous sections lands here
concretely: **write the in-memory adapter first, and if it turns out to be a
re-implementation of the infrastructure, the port has the infrastructure's
shape rather than the caller's.**

**Pruning stops being the adapter's business.** `count_hits` promises only
that it answers "how many at or after `since`", and explicitly permits
discarding older events as a side effect — *callers must not depend on them
surviving.* That licence is what lets each adapter prune the cheap way:
`RedisCache` folds a `ZREMRANGEBYSCORE` into the same transaction as the
count, `MemoryCache` deletes a prefix slice in place, and the module docstring
names that as the one thing lazy expiry cannot bound, because a hit series
grows with *traffic* rather than with the number of tenants. Under a
command-shaped port the limiter would have to issue the prune itself, meaning
every caller of the port re-derives when to prune and an adapter with a
cheaper way of doing it has no way to use it.

**Read-modify-write reappears, and with it the lost update.** A
`zadd`-flavoured port keeps the round trips separate by construction; the
adapter can no longer put the add and the expiry, or the prune and the count,
in one pipeline. `RedisCache` does both in `transaction=True` pipelines
precisely because two workers calling at the same instant is the deployment
that made a shared cache worth having. A port that hands out primitives makes
atomicity the caller's problem in exactly the case the caller cannot see.

Two details of the Redis adapter are worth reading as evidence that the
translation is where the difficulty belongs. Its `ZADD` member is
`f"{at!r}:{id(self):x}"` rather than the timestamp alone, because a sorted set
is a *set*: two hits recorded at the identical instant would collapse into one
member and undercount exactly when a burst is what the limiter is trying to
catch. And `_hits(key)` namespaces the series apart from the plain value at
the same key, because Redis answers a `GET` against a sorted set with
`WRONGTYPE` — an error `MemoryCache`, which keeps values and hits in two
separate dicts, has no way to reproduce. Both are adapter-local fixes for
adapter-local hazards. Expose the commands and both become semantics the port
has to specify and every adapter has to match.

The test that this is the right line is that the compliance suite can state
window behaviour without naming a data structure. `tests/compliance/cache.py`
asserts that hits outside the window are not counted, that two hits at the
same instant count as two, that the oldest hit in the window is the one
reported, that `delete` clears the series, and that all of it holds
per-tenant — and every one of those runs unchanged against a dict and against
Redis. A suite written in terms of `zadd` would have had to test one adapter's
emulation of the other's semantics, which is a test of the emulation rather
than of the behaviour the limiter depends on.

The general form, and it is the sharper version of "small on purpose":
**name a port's methods after the question the caller is asking, not after the
call the leading adapter would make.** The two are hard to tell apart when
there is one adapter, because the adapter's vocabulary is the only vocabulary
in the room. The distinguishing question is what a *second* adapter would have
to do — and for the window half the answer is the difference between twenty
lines and a sorted-set implementation.

### Time is passed in, never read by the adapter

Every window method on the port takes the instant as an argument:
`record_hit(key, *, at: float, ttl_seconds: float)`,
`count_hits(key, *, since: float)`, `oldest_hit(key, *, since: float)`. No
adapter calls `time.time()` to decide where a hit sits in a window, and
`MemoryCache.__init__` goes as far as accepting a `clock` parameter it
explicitly ignores — kept, its docstring says, "so a reader who expects a
clock parameter finds this note instead of a hidden `time.time()`."

Two things go wrong if the adapter reads the clock, and only one of them is a
testing problem.

**A Redis adapter would read a different clock from the caller's.** The
limiter computes `since = now - window` on the application host and the
adapter would score the hit on the Redis host. Those two machines are as
consistent as their NTP configuration, which on a cluster means several
milliseconds on a good day and unbounded during a step correction. A window
whose lower bound and whose scores come from different clocks silently admits
or refuses calls near the edge, and the discrepancy scales with drift rather
than with anything a caller can see. Passing `at` and `since` from the same
process makes the arithmetic self-consistent regardless of what any adapter's
host thinks the time is — the port's docstring calls this "a real bug on a
cluster with drift, not merely an awkward test", and the ordering there is the
right one.

**And the clock would be inside the thing under test.** With `at` supplied,
`tests/compliance/cache.py` pins a constant — `NOW = 1_700_000_000.0` — and
states window behaviour as arithmetic on it: a hit at `NOW - 600` is outside a
window opened at `NOW - 60` and a hit at `NOW` is inside it, two hits at the
identical instant count as two, the oldest hit at or after `since` is the one
reported. Every one of those runs in microseconds, against both adapters,
with no `sleep` and no monkeypatched clock. The old
`extraction/rate_limiter.py` could not express any of them without waiting for
real seconds to pass, which is why it had so few of them.

The discipline continues one level up. `RateLimiter.acquire` reads
`datetime.now(UTC).timestamp()` **once** and hands that same instant to
`count_hits`, to `oldest_hit`, and to `record_hit`. That is not tidiness:
`retry_after` is computed as `(oldest + self._window) - now`, so a second
clock read between the two calls would make the subtraction inconsistent and
produce an occasional negative wait — the failure the later section on the
sliding window returns to. Reading once and passing down is what makes
"count the window" and "when does the oldest hit expire" answers to the same
question. `CircuitBreaker` follows the same rule from the other direction: it
stores `opened_at` as a string it wrote from its own clock and compares
against its own clock, so the recovery timeout is measured entirely within one
process's frame of reference.

One honest limit, because the section heading overstates it slightly if read
alone: **`ttl_seconds` is a duration, not an instant, and expiry is the
adapter's own business.** `MemoryCache` converts a TTL to a deadline against
`time.monotonic()` at write time; `RedisCache` hands it to `PEXPIRE` and lets
the server hold it. That is deliberate — a monotonic deadline cannot be
skewed by a wall-clock correction, and Redis's own expiry is the thing
`RedisCache` exists to use. The rule is narrower than "adapters never look at
a clock": **the caller owns every time that appears in an answer, and the
adapter owns only the time at which it forgets things.** Nothing the port
returns depends on an adapter's clock, which is why the same compliance suite
can pin exact values against both.

The general form, for the next port that needs a notion of time:
**take the instant as a parameter whenever the answer depends on it.** The
test that decides it is whether two implementations on two hosts could
disagree about the result — if they could, the clock is on the wrong side of
the boundary, and the sleep-free tests you get as a side effect are the
smaller of the two prizes.

## Supporting argument: sliding window over fixed-window bucketing

`RateLimiter` counts the hits recorded in the last `window_seconds` and
refuses when that count reaches `rpm`. The alternative — one counter per
minute-bucket, incremented and expired — was available, simpler, and cheaper,
and it is rejected. The three subsections below take the rejection, the TTL
that makes the window safe, and the single clock read that keeps its
arithmetic honest.

### `Cache.increment` alone would have supported the simpler design

The fixed-window limiter needs nothing the key/value half of the port does not
already provide. `increment(f"{prefix}:{tenant}:{int(now // 60)}", ttl_seconds=60)`
returns the count for the current bucket and expires it when the bucket does;
refuse once the returned count exceeds `rpm`. One round trip, one integer per
tenant per minute, and no series to prune — `MemoryCache` would grow with the
number of tenants rather than with traffic, which is the growth `memory.py`
singles out as *the one thing lazy expiry cannot bound*.

`increment`'s TTL rule fits that design exactly, which is part of why it is
tempting: `ttl_seconds` applies when the key is *created* and is not refreshed
on later increments, so a bucket key expires a fixed 60 seconds after its
first call regardless of how many follow. A bucket wants precisely that. (The
same rule is load-bearing for a different reason on the breaker's failure
count, which the decay section below takes up.)

It is worth being clear that this is a real cost the sliding window pays and
not a strawman. Three of the eight port methods — `record_hit`, `count_hits`,
`oldest_hit` — exist solely for the sliding window; delete the requirement and
the port loses its entire second capability, `RedisCache` loses two pipelines
and its `_hits` namespacing, and `MemoryCache` loses `bisect` and its second
dict. The design under discussion is the more expensive one on every axis
except the one that decides it.

### Why it was rejected: twice the limit across a bucket boundary is exactly the failure being prevented

A bucket counter permits `rpm` calls at 11:59:59.9 and `rpm` more at
12:00:00.1. Over any 60-second span that straddles a boundary the caller has
sent twice the configured rate, and it is not a rare edge: a client that
backs off to the top of the minute and retries lands there deliberately, and
every worker doing so lands there together.

What makes this disqualifying rather than merely imprecise is what the limiter
is for. `rate_limiter.py` states it directly — *the reason to rate-limit a
single-GPU local model is precisely that twice the limit is what knocks it
over.* The limit is not a billing quota where a 2× overshoot costs money and
gets reconciled later; it is a guess at what the model host survives. A
limiter whose worst case is the failure mode it exists to prevent has not
reduced the risk, it has made it periodic — and periodic at the most likely
instant.

The sliding window has no such instant. `count_hits(key, since=now - window)`
asks about the trailing window from wherever `now` happens to fall, so there
is no boundary to straddle and the guarantee holds over *every* 60-second
span rather than over a privileged set of them. That is the whole of the
argument, and it is why the three extra port methods are worth their cost.

`tests/unit/llm/test_rate_limiter.py` pins the sliding half with a 0.15s
window — short enough to watch a hit age out and free a slot against the real
clock — and its docstring notes that a fixed-window counter would pass a
sloppier version of the same test. The exact-boundary claims are made in
`tests/compliance/cache.py` against a frozen `NOW`, where a hit at `NOW - 600`
is outside a window opened at `NOW - 60` and a hit at `NOW` is inside it, and
both adapters must agree.

### The series TTL is a multiple of the window, not the window

`record_hit` is called with `ttl_seconds=self._window * _SERIES_TTL_MULTIPLE`,
and `_SERIES_TTL_MULTIPLE` is 2. The multiple is load-bearing, and the reason
is stated where the constant is defined: a series expiring exactly at the
window edge could drop hits that are *still inside* it, which silently raises
the effective limit.

The mechanism is that a TTL bounds the series as a whole while the window
selects within it. Both adapters refresh the deadline on every `record_hit` —
`MemoryCache` rewrites the entry's expiry alongside the `bisect.insort`, and
`RedisCache` pipelines a `PEXPIRE` with the `ZADD` — so the series lives `ttl`
past the *newest* hit. But the window asks about the oldest, and at
`ttl == window` a hit at the far edge of the
window can sit exactly at the expiry of a series whose last write was one
instant ago. Expire the series and `count_hits` returns 0 for a tenant who has
just spent their allowance: not an error, not a log line, simply a limiter
that lets the next `rpm` calls through. The port states the requirement as a
precondition on the caller — `ttl_seconds` *must exceed the widest window the
caller will ask for* — and this constant is the limiter satisfying it with
room to spare rather than to the millimetre.

Doubling is a margin, not a derivation; nothing in the algorithm needs exactly
2. The cost of being generous is bounded and known — a series for a tenant who
has gone quiet is kept one extra window before it is forgotten, and
`count_hits` prunes the aged-out prefix in place on every read regardless. The
cost of being exact is a wrong answer at the boundary that no assertion about
a returned value can see. When a TTL and a query window are set from the same
quantity, **make the TTL the larger and say why in the constant's name.**

### The clock is read once per call and handed down, so `retry_after` cannot go negative

`acquire` reads `datetime.now(UTC).timestamp()` once, into `now`, and every
subsequent value derives from it: `since = now - self._window`, the
`count_hits` and `oldest_hit` calls both take that `since`, `retry_after` is
`(oldest + self._window) - now`, and a permitted call is recorded with
`at=now`. Nothing in the method reads a clock a second time.

That is what keeps the subtraction consistent. `oldest` is by construction at
or after `since`, so `oldest + window >= now` holds — but only while `now` is
the same number both times. Re-read the clock between the two calls and the
second is later, the inequality breaks by the elapsed time, and the caller is
handed a negative wait: a `retry_after` of -0.003 that a caller sleeping on it
treats as "go now", straight back into the limit. The failure is small,
intermittent, load-dependent, and invisible in any test that does not race.
`llm/rate_limiter.py` names it as the reason for the discipline, and the port
is what makes the discipline possible — the window methods take `at` and
`since` as parameters precisely so the caller can supply one instant to all of
them.

There is one branch where no clock arithmetic happens at all: `oldest_hit` can
return `None` for a key whose count was at the limit an instant earlier —
another worker's series aged out, or the whole series expired between the two
calls — and `retry_after` falls back to the full `self._window`. That is the
deliberately pessimistic answer. The alternative, computing a wait from a hit
that is not there, is how a limiter tells a caller to retry immediately into a
window it has not left.

`max(retry_after, 0.0)` appears twice in the raise, in the message and in the
attribute, and it is a floor rather than the fix. It bounds the damage from
any arithmetic that still goes negative; it does not make the answer right,
and reading it as the guard would be reading the belt as the trousers.

The other half of the guarantee is that a refused call records nothing. The
`record_hit` sits after the check and is skipped on the raising path, because
counting refused calls would let a client in a retry loop extend the window it
is waiting on and lock itself out indefinitely.
`test_a_refused_call_is_not_itself_counted` asserts exactly that: the
`retry_after` from a later refusal is no larger than the first. Note the shape
of that assertion — it compares two observed waits rather than either against
the constant, so it cannot be satisfied by an implementation that happens to
return `window` every time.

The general form, and it applies to any check-then-act over a port that takes
time as an argument: **read the clock once, pass it to everything, and let the
port's signature be what enforces it.** An adapter that reads its own clock
takes that option away, which is the argument the previous section made from
the other side.

## Supporting argument: the failure counter decays by TTL

`CircuitBreaker.record_failure` increments `kg:circuit:failures` with
`ttl_seconds=self.recovery_timeout` and opens the circuit once the returned
count reaches `failure_threshold`. Nothing ever decrements that key: it is
deleted wholesale on a success or a reset, and otherwise it simply expires.
The threshold is therefore "five failures within a recovery timeout of each
other", not "five failures ever" — which is why the constructor's docstring
calls the count *consecutive-ish*, with the "ish" pointing at this section.

The decay is bought with a single argument to `increment`, and it works only
because of a rule the port states about that argument. The three subsections
below take the rule, the reason the alternative is unacceptable, and the state
the decay does not cover.

### `Cache.increment` does not refresh a TTL on later increments

The port's contract is explicit: `ttl_seconds` *is applied when the key is
created, not on every increment — a TTL refreshed on each hit is a counter
that never expires under load, which is exactly when it needs to.* Both
adapters implement that literally. `RedisCache.increment` issues `PEXPIRE`
only when `INCR` returns 1; `MemoryCache.increment` writes a fresh deadline
when the key is absent and otherwise keeps `self._values[key][1]` unchanged
while replacing the value.

Note that this is the same `increment` the rejected fixed-window limiter would
have used, and the rule serves both callers for opposite-looking reasons: a
bucket wants a key that dies a fixed 60 seconds after the bucket opened, and a
failure count wants a window that dies a fixed `recovery_timeout` after the
*first* failure in it. Both are "expire relative to creation", which is why one
method covers both without a flag.

Getting this wrong is invisible from inside the breaker. A refreshed TTL still
counts correctly, still opens at the threshold, and still resets on success —
it is only the *decay* that stops happening, and the decay has no return value
to assert against. So the property is pinned at the port instead, in
`tests/compliance/cache.py::test_a_counters_ttl_is_not_refreshed_by_later_increments`,
which increments with a 0.12s TTL, sleeps 0.06s, increments again, sleeps
0.10s, and requires the key to be gone. Under a refreshing implementation the
second increment moves the deadline out and the key survives; the assertion is
on the key's *absence*, which is the only observable the behaviour has. Both
adapters run it, so the two cannot drift here — which matters more than usual,
because a Redis adapter that called `PEXPIRE` unconditionally would look
correct in every breaker test and only misbehave in production, under load.

### Five failures in an hour is a bad day; five in a second is an outage

That sentence is the whole justification, and the module docstring states it
in those terms: without the decay, *five failures spread over an hour would
open the circuit exactly as five failures in a second do — and the first is a
healthy service having a bad day while the second is an outage.*

The distinction is not stylistic. A breaker exists to stop traffic reaching a
host that is failing *now*; the failure count is its evidence for that claim,
and an undecaying count is evidence with no expiry date. On a service running
at any real volume, a handful of failures per hour is normal weather —
timeouts, a restarted pod, a truncated response. Count those cumulatively and
the circuit opens after a period that depends only on total uptime, at which
point a healthy service is refused traffic for `recovery_timeout` for reasons
nobody can reconstruct. Worse, the failure is self-perpetuating in the reading
it invites: what an operator sees is a circuit tripping against a host with
green dashboards, which reads as flapping infrastructure rather than as a
counter that never decays.

Two mechanisms keep the count honest and they cover different cases, which is
worth separating because either alone leaves a hole.

- **A success deletes the count.** `record_success` in `CLOSED` calls
  `delete(self._key("failures"))` — not a decrement, a delete.
  `test_a_success_clears_the_accumulated_failures` is the one that pins it,
  and its docstring names the failure it prevents: *two failures now plus two
  failures an hour from now would open a circuit in front of a service that
  was working the whole time.* This is the mechanism that handles interleaved
  traffic, where successes and failures alternate.
- **The TTL handles the case with no successes to observe.** A low-traffic
  caller may make one call every few minutes and have several of them fail
  without a success in between to clear anything. Nothing calls
  `record_success`, so nothing deletes the key; the decay is the only thing
  standing between that caller and an open circuit assembled from failures
  minutes apart.

Tying the TTL to `recovery_timeout` rather than to a separate setting is a
deliberate economy, and its justification is that the two quantities answer the
same question. `recovery_timeout` is already the caller's statement of the
timescale on which this service's health is judged — how long a failure stays
relevant before a probe is worth making. A failure older than that has been
superseded by the breaker's own recovery cycle, so counting it towards the
next trip would be double-counting evidence the breaker has already acted on.
One knob, not two, and no way for the two to be set inconsistently.

### `HALF_OPEN` and why the breaker is not a timeout

The decay above is about the `CLOSED` state, and it is only half of what stops
this being a plain timeout. The other half is that the failure count does not
govern the path back. `record_failure` in `HALF_OPEN` does not increment
anything: it reopens immediately, and
`test_one_failed_probe_reopens_without_needing_the_threshold_again` is the
test that requires it. One failed probe is conclusive evidence — the caller
asked the host a direct question and got an answer — so making it accumulate
towards a threshold again would be treating a measurement as a rumour. The
symmetric case is `record_success` in `HALF_OPEN`, which closes the circuit
outright rather than merely clearing a count.

`HALF_OPEN` is what earns the pattern over a timeout, and the module docstring
puts the argument plainly: *going straight from `OPEN` back to `CLOSED` sends
the full load at a service that has just come back, which knocks it over
again.* A timeout only knows how long to wait. It cannot ask whether waiting
worked, so its recovery is a guess enforced against every caller at once, and
its failure mode is a thundering herd arriving at a host in exactly the state
least able to absorb one. `HALF_OPEN` replaces the guess with a measurement
taken by `half_open_max_calls` requests — one, by default, because *the point
of the state is to send less than normal load at a service that has just
returned*.

Three details of that probing path are load-bearing, and each was learned from
a way it can go wrong.

- **The transition falls through rather than returning.** When
  `allow_request` finds the recovery due it calls `_to_half_open()` and then
  drops into the half-open branch, so the transitioning call is counted as a
  probe. Returning `True` directly would admit it *uncounted*, and
  `half_open_max_calls=2` would let three requests through — caught by
  `test_only_the_permitted_number_of_probes_get_through`, and inherited from
  the Redis implementation this replaced.
- **The probe count is cleared before the state changes**, not after.
  `_to_half_open` deletes `half_open_calls` and only then writes the state.
  The other order leaves a window in which the circuit is half-open holding
  the previous attempt's exhausted counter, so every probe is refused and the
  circuit never recovers — the one bug in a breaker that no amount of waiting
  fixes. `test_reopening_and_probing_again_gets_a_fresh_probe_allowance` is
  the guard.
- **A missing `opened_at` counts as due.** `_recovery_is_due` returns True
  when the timestamp is absent, because a state entry that outlived its
  timestamp is a cache that lost a key, and the safe reading is to probe:
  staying open forever on a lost key is an outage nothing would recover from.
  `test_a_lost_opened_at_probes_rather_than_staying_open_forever` deletes the
  key directly to prove it.

That last one is the general shape of every default in this module, and it is
the note to end on: **where the stored state is ambiguous, the breaker resolves
towards letting traffic through.** An unrecognised `state` value reads as
`CLOSED` with a warning rather than raising, for the same stated reason —
raising would turn one corrupt cache entry into a total outage, which is the
opposite of what a breaker is for. The state lives in an *expiring* cache by
design, so losing it is a normal event rather than an exceptional one, and a
breaker whose failure mode on lost state is "refuse everything" would be a
worse liability than the failures it was installed to contain.
[Harden model calls](../how-to/harden-model-calls.md) shows the breaker in
place around a provider; the `retry_after` on `CircuitOpen` is an estimate for
exactly the reason this section describes — another worker may probe first and
either close the circuit early or push the timeout out by failing.
