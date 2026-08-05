# ADR 0008: The two non-store ports, `Cache` and `LlmProvider`

**Status:** accepted; amended by
[ADR 0017](0017-the-embedding-provider-port.md).

> **Amendment (ADR 0017).** There are now **three** non-store ports:
> `EmbeddingProvider` joined `Cache` and `LlmProvider`. The title and the
> "four ports, two ADRs" framing below are a record of the state when this was
> decided, not a description of the tree.
>
> The reasoning here is unchanged and was what the new port was designed
> against — a provider port stays narrow, absorbs its backend's awkwardness,
> and never lets a client library reach the layers above it. What ADR 0017
> adds is a question this page did not have to answer, because neither `Cache`
> nor `LlmProvider` has a counterpart it must agree with: an embedding
> provider and a `VectorStore` share a dimension, and *something* has to
> reject a mismatch. That decision is there, not here.

**Why this is an ADR:** ports are expensive to change (adapters + compliance
suites move with them), and [ADR 0002](0002-two-store-ports.md) covers only
`GraphStore` and `VectorStore`.

## Context: four ports, two ADRs

`redstring.ports` holds four Protocols. Two of them are stores and are argued
in ADR 0002. The other two — `Cache` and `LlmProvider` — are the subject here.
They are grouped into one decision because they are the same decision made
twice: each stands between the library's own vocabulary and a piece of
external transport, and each is shaped so that the transport's vocabulary
stops at the boundary.

### What sits behind each port today

| Port | Adapters |
|---|---|
| `Cache` | `llm/cache/memory.py` (`MemoryCache`), `llm/cache/redis.py` (`RedisCache`) |
| `LlmProvider` | `llm/adapters/langchain.py` (`LangChainLlmProvider`), `llm/adapters/fake.py` (`FakeLlmProvider`) |

`MemoryCache` is a dict plus a sorted list of hit times, with lazy expiry and
no background sweep — a library must not start a task on a caller's event
loop uninvited. `RedisCache` maps the hit window onto `ZADD`/`ZCOUNT`/`ZRANGE`
with epoch time as the score, and prunes with `ZREMRANGEBYSCORE` on read.

`LangChainLlmProvider` wraps any LangChain `BaseChatModel` and is the only
module in `src/` permitted to import `langchain*`. `FakeLlmProvider` is a real
adapter, not a mock: it takes payload *dicts* and validates them against the
caller's schema through the same gate, so a test cannot smuggle a
pre-validated instance past validation, and a bad payload raises
`MalformedCompletionError` for the same reason a real model's bad JSON does.

### Why these two are grouped

Both exist to keep an external transport out of `extraction` and `domain`.
Redis and LangChain are the two dependencies whose types would otherwise
propagate into signatures the rest of the library reads and writes, and both
are dependencies a caller may legitimately not have. The architecture contract
places `llm` *beside* `extraction` rather than beneath it for exactly this
reason: extraction can reach `ports.llm_provider` and never the adapter.

## Decision: `Cache` is the transport's shared state, not a general-purpose cache

The port is exactly what a rate limiter and a circuit breaker need in order to
coordinate several processes without either of them naming Redis. It is not an
attempt at a cache abstraction, and nothing in the library uses it to cache
anything.

### It is two capabilities, not one

`get`, `set`, `increment`, `delete` are the key/value half, and they are what
`CircuitBreaker` needs: a state name (`closed`/`open`/`half_open`), a failure
count, an `opened_at` timestamp, a half-open probe counter.

`record_hit`, `count_hits`, `oldest_hit` are the hit-window half, and they are
what a *sliding-window* `RateLimiter` needs. `oldest_hit` is what turns "you
are over the limit" into "try again in 0.4 seconds": the oldest hit in the
window is the one whose expiry frees a slot.

### A hit-window vocabulary, not Redis sorted-set operations

A port that said `zadd`/`zcard`/`zremrangebyscore` would be a Redis port
wearing a different name, and no in-memory implementation could satisfy it
without reimplementing Redis. Phrased as "events in a time window", the
in-memory implementation is a list and a binary search: `MemoryCache` keeps a
sorted `list[float]` per key, inserts with `bisect.insort`, and answers
`count_hits`/`oldest_hit` from a `bisect_left` at `since`.

The Redis mapping still exists — it just lives on the far side of the
boundary. `record_hit`/`count_hits`/`oldest_hit` become
`ZADD`/`ZCOUNT`/`ZRANGEBYSCORE` with the epoch time as the score, and
`ZREMRANGEBYSCORE` prunes in the same pipeline as the count. What the port's
vocabulary buys is that the two adapters can reach that shape by different
routes and still be obliged to the same promises. Two of those promises are
things a sorted-set-shaped port would have made an adapter's private business:

- **Two hits at the same instant are two hits.** A sorted set is a *set*, so
  `RedisCache` writes members like `f"{at!r}:{id(self):x}"` rather than the
  timestamp alone; identical members would collapse into one, under-counting
  exactly when a burst is what the caller is trying to detect. `MemoryCache`
  gets this for free from a list. The compliance suite makes it a requirement
  of the port rather than a property of one adapter.
- **A window and a value may share a key.** `RedisCache` namespaces the
  sorted set as `f"{key}:hits"`, because Redis would reject a `ZADD` against a
  string key with `WRONGTYPE` — an error `MemoryCache` has no way to
  reproduce, and therefore an asymmetry no caller should be able to observe.
  `delete` clears both halves in one call for the same reason.

Both are visible only because the port is written in the caller's terms; in
Redis's terms they are not decisions at all.

### Rejected: a fixed-window counter built on `increment` alone

`increment` alone would support a limiter, and it is much the cheaper design:
one counter per minute-bucket, `increment` with a `ttl_seconds`, refuse when
the count exceeds `rpm`, let the key expire. No `record_hit`, no `count_hits`,
no `oldest_hit` — three of the port's seven methods, and the only two an
in-memory adapter needs a sorted structure for, would not exist.

It is rejected because a fixed window lets through **twice the limit across a
bucket boundary**: `rpm` calls at 11:59:59.9 and `rpm` more at 12:00:00.1 are
`2 × rpm` calls inside one tenth of a second, and every one of them is legal
under the counter's own rules. The reason this library rate-limits at all is a
single-GPU local model, and twice the limit arriving at once is the exact
failure the limiter exists to prevent. A limiter that is correct except during
a burst has no remaining purpose — bursts are the only condition under which
anything is limited.

The second cost is that a fixed-window counter cannot answer *when to retry*.
It knows how many calls fell in the current bucket and nothing about when they
happened, so the only honest advice it can give is "wait for the bucket to
roll", which is wrong in both directions: too long just after a boundary, and
still too short for a caller whose slot frees earlier. `oldest_hit` exists so
`RateLimitExceeded.retry_after` is the wait until a slot genuinely frees
(`(oldest + window) - now`), and a caller that sleeps for it and retries
succeeds. That guarantee is unavailable to a design built on `increment`.

What the rejection buys is paid for in the port's surface — the hit-window
half exists solely to serve it — and that trade is the reason `Cache` is
argued here rather than assumed. `increment` survives anyway: `CircuitBreaker`
counts failures with it, and a failure count genuinely is a fixed-window
quantity.

### Time is passed in, never read from a clock inside the adapter

Every hit-window method takes the instant it is about as a caller-supplied
epoch float: `record_hit(key, at=...)`, `count_hits(key, since=...)`,
`oldest_hit(key, since=...)`. No adapter calls `time.time()` to answer any of
them.

Two things go wrong if an adapter reads its own clock. The small one is
testing: the clock would sit inside the thing under test, so every window
assertion would have to be bought with a real sleep. As it is, the compliance
suite's window tests are written against a fixed epoch constant and run at
full speed while still asserting the real boundary conditions — that a hit
*exactly* at `since` is inside the window, that a hit before it is not, that
two hits at the same instant are two. Those are the assertions a sleep-based
suite gets least reliably, because they are the ones that need an exact
instant rather than an approximate one.

The larger one is correctness in production. `RedisCache` runs on a different
machine from its caller. If it scored hits by the clock Redis happened to
read, the window would be measured against a clock the caller cannot see, and
on a cluster with drift the limiter would let through more or fewer calls than
its configuration says — intermittently, by an amount nobody can reproduce.
Passing the instant in makes the caller's clock the only clock, so both
adapters answer the same question.

The obligation this creates lands on the caller, and `RateLimiter` discharges
it explicitly: it reads `datetime.now(UTC).timestamp()` **once per call** and
hands that same value to `count_hits`, to `oldest_hit` and to `record_hit`.
Reading it twice would let "how full is the window" and "when does the oldest
hit expire" disagree, and the disagreement surfaces as an occasional negative
`retry_after` — a caller told to wait a negative interval retries immediately
and is refused again.

`MemoryCache.__init__` keeps a `clock` parameter that it ignores entirely.
That is not vestigial: a reader who expects an in-memory cache to take a clock
goes looking for one, and finding the parameter with a docstring explaining
that there is no clock to inject is cheaper than finding nothing and assuming
`time.time()` is hidden somewhere in the file.

**The scope of this decision is window time, not TTL.** `ttl_seconds` is a
*duration*, and an adapter is free to measure it however it likes —
`MemoryCache` converts it to a deadline against `time.monotonic()` at write
time, `RedisCache` hands it to Redis. That is deliberate: a duration is
meaningful without a shared clock, and monotonic time is the right thing to
measure one against precisely because it cannot be dragged backwards by an
NTP correction. So the TTL tests in the compliance suite do sleep, and are
written sub-second to stay fast. Window time is an *instant* and has to be the
caller's; TTL is an interval and does not.

### `get`/`set` traffic in `str`, not `bytes`

`get` returns `str | None` and `set` takes `str`. The port fixes the type
because this is the one place the two adapters disagree *by default*: a
`redis-py` client left at its defaults returns `bytes`, and `MemoryCache`
stores whatever it was handed. A caller comparing the result against a string
literal would therefore match in every in-memory test and never match in the
deployment that has Redis — a difference that is invisible in review, silent
at runtime, and only observable where nobody is running the suite.

`RedisCache.from_url` builds its client with `decode_responses=True` for this
reason. That covers the constructor most callers use, but not the other one:
`RedisCache(client)` takes a client the caller already owns and cannot make it
decode after the fact, so the requirement is stated in the argument's
docstring and in the module docstring. The enforcement lives in the compliance
suite instead, which is the right place for it —
`test_a_value_comes_back_as_str_not_bytes` asserts `isinstance(await
cache.get(...), str)`, so it pins the *promise* rather than the setting. Any
adapter reaching a non-decoding transport by any route fails it, including
future ones whose transport has no flag by that name.

The same choice is what makes counters coherent across the two halves of the
port. `increment` returns an `int`, but the value it leaves behind is readable
through `get`, and the compliance suite requires it to read back as its
decimal string (`test_a_counter_reads_back_as_its_decimal_string`) and to
continue from a value written directly with `set`
(`test_a_counter_set_directly_continues_from_there`). Redis behaves this way
because its strings are its integers; `MemoryCache` stores `"1"` and
`str(total)` explicitly so it agrees. Had the port trafficked in `bytes`, that
correspondence would have been an encoding question for each adapter to answer
separately, which is exactly the class of disagreement this decision removes.

### `MemoryCache` is the default, so the library runs with no Redis

`cache` is optional on both callers, and both fall back to the same
expression: `self._cache: Cache = cache if cache is not None else
MemoryCache()` (`llm/rate_limiter.py`, `llm/circuit_breaker.py`). A caller who
wants extraction and has no Redis *server* still gets extraction, with no
configuration step to discover.

What matters is that the fallback is a real adapter and not a stand-in. A
rate limiter backed by `MemoryCache` genuinely limits — same sliding window,
same `retry_after` — and a circuit breaker backed by it genuinely opens,
half-opens and closes. The single thing it cannot do is let two *processes*
agree, so a limiter configured at `rpm` in four workers admits `4 × rpm`. That
is the whole of what `RedisCache` adds, and it is why the choice is a
deployment property rather than a correctness one: Redis is the upgrade a
caller makes when the deployment stops being one process.

Being the default is what forces two of `MemoryCache`'s properties. It must
start nothing: expiry is lazy, checked when a key is next read, because a
library that quietly starts a sweep task on a caller's event loop then owns a
shutdown path callers forget. And `close` must be safe and meaningful even
though there is nothing to release — it clears both maps, because the port
promises `close` and a default that made it a no-op would let a `RedisCache`
leak the connection its `_owns_client` flag exists to manage.

The one thing this decision does *not* buy is a smaller install. `redis` is a
hard dependency of the project, not an extra, so importing `redstring` pulls
the client library either way; `langchain` is the dependency behind an extra.
"Runs with no Redis" means no server to stand up, not no package to install.

### Consequence: `ttl_seconds` on `increment` applies at key creation, and `ttl_seconds` on `record_hit` must exceed the widest window asked for

Both halves of the port take a `ttl_seconds`, they mean opposite things, and
the port pins both because an adapter left to choose would choose
differently.

**On `increment`, the TTL is applied when the key is created and never
refreshed.** `RedisCache` calls `pexpire` only when `incr` returns `1`;
`MemoryCache` computes a deadline only on the branch that creates the key and
otherwise carries the existing one forward. The alternative — expiring `n`
seconds after the *last* increment — is a counter that never expires under
continuous load, which is precisely the load it exists to measure.
`CircuitBreaker` is what makes this concrete: it counts failures with
`ttl_seconds=recovery_timeout`, and the decay is the difference between five
failures in a second and five failures spread over an hour. Without it the
first indistinguishably becomes the second, and the breaker opens on a healthy
service having a bad day.

This is a promise about the *port*, not an implementation detail of either
adapter, so `CacheCompliance` pins it directly:
`test_a_counters_ttl_is_not_refreshed_by_later_increments` increments twice
inside one TTL, waits past the first increment's deadline, and requires the
key to be gone. An adapter that refreshes on every call passes every
behavioural test about counting and fails only this one.

**On `record_hit`, the TTL bounds the whole series and *is* refreshed by each
hit.** Its job is garbage collection: a tenant nobody has called for in a
while should not keep a sorted set alive forever. That makes the caller's
obligation the one stated in the port — it must exceed the widest window the
caller will ask for, or hits disappear while they are still inside the window
being counted, and the effective limit silently rises.

The obligation is the caller's because only the caller knows its widest
window; the adapter sees one number per call and cannot tell a generous TTL
from a fatal one. `RateLimiter` discharges it by passing
`window * _SERIES_TTL_MULTIPLE`, with the multiple named as a constant and
carrying the reason: a series expiring exactly at the window edge could drop
hits still inside it. Twice the window is not tuning — it is the smallest
factor that puts expiry strictly outside the region `count_hits` reads.

The asymmetry is the thing to carry away. The same parameter name is
*creation-time* on the counter and *sliding* on the series, because a counter
must decay on its own schedule while a series must survive as long as anyone
is still asking about it. Neither behaviour is inferable from the signature,
which is why both sit in the Protocol's docstrings rather than in an
adapter's.

### Consequence: `count_hits` may discard aged events as a side effect; callers may not rely on their survival

`count_hits` is a read in the caller's vocabulary and a write in the
adapter's. The port says so outright — "implementations may discard older
events as a side effect; callers must not depend on them surviving" — and both
adapters take the licence. `MemoryCache` binary-searches to the first hit at or
after `since` and drops everything before it with `del series[:first]`.
`RedisCache` issues `ZREMRANGEBYSCORE window -inf (since` in the same
transactional pipeline as the `ZCOUNT` that answers the question.

The licence is granted because the alternative is unbounded growth in the one
place lazy expiry cannot reach. Every other structure in `MemoryCache` is
bounded by the number of *keys*, and a key that nobody reads again is collected
the next time it is touched or dropped by its TTL. A hit series is bounded by
*traffic*: one busy tenant's window accumulates a float per call, indefinitely,
and its TTL never fires because the tenant is exactly the one still being asked
about. Pruning on read is what keeps the series proportional to the window
rather than to the uptime. `record_hit` is correspondingly the one `MemoryCache`
method that writes without lazy expiry doing the work.

Pruning on *read* rather than on write is the other half of the choice, and it
follows from the clock decision above: an adapter has no clock of its own, so
write time is not a moment at which it knows what has aged out. `since` arrives
with the read, and the read is therefore the first point at which "old" is
defined. A series nobody asks about is left to its TTL, which is what a TTL is
for.

Two things this does **not** license, both of which the compliance suite pins:

- **`oldest_hit` does not prune.** It performs the same `bisect_left` /
  `ZRANGEBYSCORE` lookup and discards nothing, so the order in which a caller
  makes the two calls cannot change either answer.
  `test_the_oldest_hit_ignores_hits_that_have_aged_out` records a hit 600
  seconds old, asks over a 60-second window, and requires the newer hit back —
  it would pass on an adapter that deleted the old one, which is precisely why
  it is not evidence of pruning in either direction. The invariant that matters
  is that `RateLimiter.acquire` calls `count_hits` and then `oldest_hit` with
  the same `since`, and gets a `retry_after` computed over the same set of hits
  it counted.
- **Nothing at or after `since` may be dropped.** Both prunes are strictly
  half-open below `since` — `del series[:first]` where `first` is a
  `bisect_left`, and Redis's exclusive `(since` bound. A hit landing *exactly*
  on the boundary is inside the window and is counted, which
  `test_a_hit_exactly_at_the_boundary_is_inside_the_window` requires. An off-by-one
  here would be invisible in ordinary use and would silently let one extra call
  through per window.

For a caller, the practical rule is that the hit window answers questions about
the present and keeps no history. There is no way to ask a `Cache` how many
calls a tenant made in an hour that has already elapsed, and no way to widen a
window retroactively: hits older than the narrowest `since` any reader has used
may already be gone. That is a deliberate limit on what the port is —
coordination state for a limiter, not a usage ledger. Anything that needs
history needs to be an event, which is what the projections in this library are
for.

The obligation this puts back on the caller is the one stated in the previous
section from the other end. A `RateLimiter` with a 60-second window and a
second reader interested in five minutes cannot share a key: the 60-second
reader's `count_hits` prunes the five-minute reader's data out from under it,
and the wide reader's numbers fall silently rather than erroring. Give each
window its own key, or read them all at the widest `since` and narrow in the
caller.

## Decision: `LlmProvider` is one method, in domain terms

The whole port is one property and one method:

```python
model: str


async def extract[S: BaseModel](
    self, text: str, schema: type[S], *, system_prompt: str | None = None
) -> S: ...
```

Hand it text and a pydantic schema, get back an instance of that schema.
Messages, roles, tool calls, token budgets, response formats and streaming are
the adapter's business and stop at the boundary. The library calls a model for
exactly one purpose — turning prose into structured data it can validate — so
the port is shaped like that purpose rather than like the transport underneath.

### Rejected: a chat-shaped port

A port shaped like a chat API would put `AIMessage` and friends into every
caller's signature, and LangChain's interfaces move fast enough that a
breaking change would then touch every one. Shaped as it is, such a change
touches `redstring/llm/adapters/` and nothing else.

The same reasoning was applied one level down, inside the adapter, and it is
worth recording because the idiomatic choice lost. `LangChainLlmProvider` uses
**only** `await chat.ainvoke(messages, response_format=...)`;
`with_structured_output` was rejected because it decides the parsing strategy,
swallows the raw message, and turns a truncated completion into an
`openai.LengthFinishReasonError` raised from inside the openai SDK — three
behaviours that would each have to be re-learned on every LangChain minor.
Assembling `response_format` and validating the content by hand costs about a
dozen lines and leaves the failure modes ours to name, which is what makes the
error contract below stateable at all.

### The confinement is enforced, not advised

`tests/unit/llm/test_port_does_not_leak.py` walks the AST of every module
under `src/redstring` and fails on a `langchain*` import anywhere outside
`llm/adapters/`. This is a separate gate because a leaked import is not a test
failure, not a lint finding, and — decisively — not an import-linter
violation: the architecture contract is declared over first-party packages
only, so `lint-imports` cannot see a third-party import at all.

Two details of how it checks are deliberate. It reads *source text* rather
than importing the modules: a lazy `import langchain_core` inside a function
body still leaks those types into the signatures around it, and importing
every module to inspect it would require every optional extra installed.
And it collects imports by walking the tree rather than reading the top of the
file, so an import nested in a function, a class body or a `try` block is
found the same way a top-level one is.

The gate also guards *itself*, and that is the part worth copying. Its
substantive check is a search for something that should not be there, so it
passes trivially when it searches nothing at all: a `SOURCE_ROOT` pointing at
a renamed directory, or an `ADAPTERS` path that excludes nothing, would leave
a green test protecting an empty set. Two companion tests make that
impossible — `test_the_walk_finds_the_library` requires the walk to find more
than fifty files including `ports/llm_provider.py`, and
`test_the_adapter_directory_really_does_import_langchain` requires the one
exempted directory to actually contain the import it exempts. This is the
staleness rule stated elsewhere in these docs, in its second form: an
exemption that matches no file, and a prohibition that scans no file, both
pass silently.

Any dependency the architecture confines to a single module needs this second
kind of check. The contract alone will not do it.

### `model` is exposed as provenance, not as configuration

`model: str` is a read-only property on the port, and it is the only member
besides `extract`. Nothing in the library ever *sets* it, and no method takes
it as an argument: it is not a knob for choosing which model to call. It is an
answer to "who produced this", and the provider is the only object in the
system that knows it.

The reason a port this deliberately narrow spends a member on it at all is
that the value has two durable destinations, and both are permanent.

**`Entity.model`.** Every entity records which artifact produced it, with the
convention stated on the field: provider-qualified and versioned —
`"ollama/qwen3.6-27b-mtp"` or `"anthropic/claude-opus-4-20250514"`, never a
bare family name like `"claude"`. The reason is stated there too, and it is
the whole argument for the property: these values land in a durable log, and
an unversioned name makes "re-extract everything the old model touched"
unanswerable. Provenance you cannot query by is decoration.

**`DocumentExtracted.model_version`.** The same string is what makes a repeat
extraction distinguishable from a new one. `DocumentAggregate` keeps the list
of versions a document has been extracted under and refuses a second
extraction under one already there, so a retry after a crash is idempotent
while an extraction under an upgraded model is a genuinely new event.
`ExtractionPipeline.record_extraction` reads `self._provider.model` to supply
it. Every property that story depends on is a property of the *string*: a
provider reporting a bare family name makes an upgrade look like a duplicate
and silently drops the second extraction.

Making it a property of the provider rather than a parameter is what keeps
those two consistent without anyone maintaining the agreement. `map_extraction`
takes `model` explicitly and its docstring names the source — "from
`LlmProvider.model`" — but the pipeline never types a literal, so the entity's
provenance and the event's `model_version` cannot disagree. The alternative,
each caller passing a string alongside the provider it happens to be holding,
is the redundant-declaration shape: two sites for one fact, no mechanism that
fails when they drift, and the log is exactly where drift cannot be repaired.

Being provenance rather than configuration also puts an obligation on the
adapter, which is why both take `model` separately from the transport.
`LangChainLlmProvider.__init__` accepts it beside the chat model because no
chat model knows which provider is in front of it; `openai_compatible`
composes `f"{provider}/{model}"` from a server's bare model id for the same
reason. Even `FakeLlmProvider` reports one (`"fake/canned-v1"`), so entities
extracted in tests carry provenance shaped like the real thing rather than
`None`.

The consequence worth stating is that `None` is not an escape. `Entity.model`
is `str | None`, but the null is reserved for extractions that invoked no
model, and both `Entity` and `map_extraction` enforce the correspondence in
both directions: a model-bearing `extraction_method` (`LLM`, `HYBRID`) with no
model string is refused because it would put unattributable entities in a
permanent log, and a model string on a method that runs none is refused as a
false attribution. A provider that cannot name itself is therefore not a
provider that logs less — it is one whose extractions will not map.

### No default `system_prompt` — prompts belong to `extraction`

`system_prompt` is keyword-only, typed `str | None`, and defaults to `None`.
The default means *no system turn at all*, not "a sensible one the provider
picks": `LangChainLlmProvider` appends a `("system", ...)` message only when
the argument is not `None`, so with `None` the model sees exactly one turn,
the text. That is asserted directly rather than left to be inferred —
`test_without_a_system_prompt_only_the_text_is_sent` requires the message list
to be `[("human", "Ada Lovelace.")]`, which fails the moment any provider
starts inventing instructions.

The reason is that a prompt is the largest single determinant of what comes
back, and a provider substituting one would make it invisible. Two callers
passing identical text would get different answers, and nothing in either
caller's code would name the difference — it would live in whichever adapter
was constructed, and change under them when that adapter was upgraded. The
same string is also what makes an extraction *reproducible*: re-extracting a
document a year later has to be able to ask the same question, and a question
supplied by a dependency's default is not one anyone recorded. A port that
already refuses to invent an empty result for the same reason cannot
consistently invent a prompt.

So the decision is not "there is no default anywhere" — it is that the
default belongs one layer up, where a caller can see and change it.
`ExtractionPipeline.__init__` takes `system_prompt: str = DEFAULT_SYSTEM_PROMPT`
and exposes it back as a read-only property, precisely so a caller can log
what was asked. The argument is on the constructor rather than on `extract`
because domain schemas supply their own: `build_graph` resolves the prompt
before the pipeline is built, from `domain_system_prompt(...)` when a domain
is named or classified, and from `DEFAULT_SYSTEM_PROMPT` when none is.
Consolidation is the third caller and owns its own module-level prompt for its
adjudication calls. Three callers, three prompts, none of them the provider's.

Two consequences follow for anyone writing a provider. A wrapping adapter must
pass `system_prompt` through untouched — a decorator that dropped it would
silently move every caller onto the empty-prompt behaviour, and the type
signature would still check. And `FakeLlmProvider` accepts the argument and
deliberately *ignores* it, which is the same decision seen from the test side:
a fake whose answer varied by prompt wording would make prompt text
load-bearing in tests that are about extraction, and there is no model behind
it for the wording to mean anything to. Neither adapter has a prompt of its
own to defend, which is the property this decision is protecting.

### Empty output raises rather than returning an empty result

An extraction that returns nothing and an extraction that failed are
indistinguishable downstream, and the first is a legitimate answer while the
second silently erodes a knowledge graph. So `extract` raises
`EmptyCompletionError` (no usable content) or `MalformedCompletionError`
(content that did not validate), and only a successfully parsed schema
instance with no entities in it means "nothing here".

The three shapes are *siblings* under `LlmProviderError`, and the third is the
one the hierarchy was designed around. `RefusedCompletionError` is not a
subclass of `EmptyCompletionError` — both leave you with nothing — because the
two call for opposite responses. A truncation is a configuration problem a
larger token budget fixes, and retrying is right. A refusal is a permanent
property of *this content*: retrying spends tokens to be refused again, and
the useful reaction is to record which document could not be extracted and
move on. Collapsing them would make that distinction unavailable exactly where
a caller extracting from clinical or legal text needs it most. A caller
wanting one `except` still has one, because the common base is what the
pipeline, the strategy classifier and the consolidation policy actually catch.
[Harden model calls](../how-to/harden-model-calls.md) is the caller-side
counterpart to this contract.

### Why this is not hypothetical

The reference deployment serves a reasoning model that emits its chain of
thought into `reasoning_content` and its answer into `content`. Under a tight
token budget the budget is spent before `content` begins, and the server
returns HTTP 200 with `content` empty and `finish_reason` `"length"` — roughly
150 completion tokens were needed for a one-word answer. An adapter that
mapped empty content onto an empty extraction would report "this document
contained nothing" for every document whose extraction merely ran out of
budget. `EmptyCompletionError` therefore carries `finish_reason` when the
transport reported one, because `"length"` and `"stop"` call for different
fixes — raise the budget, or look at the prompt.

The follow-on was found only by running against the real server
(`tests/integration/llm/test_live_endpoint.py`) and is the reason this section
is longer than the decision: asking for a `json_schema` response format routes
`langchain-openai` through the openai SDK's *parsing* path, and that path
raises `openai.LengthFinishReasonError` before returning a message at all. So
the adapter's own empty check never sees the truncation case — the more common
of the two — and it would have escaped as a vendor exception from three frames
down, past every `except LlmProviderError` in the library. The same path
raises `ContentFilterFinishReasonError`, which is what the refusal branch
translates. Both are caught and re-raised in the library's own vocabulary; a
port that names its failures is only as good as the transport exceptions the
adapter remembers to catch.

Catching a vendor exception is itself constrained by the port, in a way worth
recording: `openai` is not installed for a caller wrapping a non-OpenAI chat
model, so importing those two classes eagerly would make constructing
*any* `LangChainLlmProvider` an `ImportError`. The adapter imports them in a
`try` and falls back to a private `_NeverRaised` sentinel, so
`except _TruncatedError` degrades to "this transport cannot truncate that
way" rather than to a hard dependency. `DEFAULT_MAX_TOKENS` (8192) is
deliberately generous for the same reason the errors exist at all: a
too-small budget costs a failed run, a too-large one costs nothing unless the
model uses it.

## Consequences

### For adapter authors

`tests/compliance/cache.py` obliges every `Cache` adapter to pass one suite:
key/value round-trips including `str`-not-`bytes`, counters (first increment
returns 1, a counter reads back as its decimal string, a `set` value continues
from there, a TTL not refreshed by later increments), hit windows (boundary
hits are inside, two hits at the same instant are two hits, windows are kept
apart by key, a window and a value may share a key, `delete` clears both), and
`close` being safe twice. Subclass `CacheCompliance` from the adapter's own
test module and supply a `cache` fixture; the module is named so pytest will
not collect it directly. Window time is supplied by the test as a constant
(`NOW = 1_700_000_000.0`), so "an event 90 seconds ago" is a number rather
than a 90-second test. Only `TestExpiry`'s three cases sleep, because a TTL is
a duration and cannot be handed in; they are written sub-second, and the
sub-second value is itself load-bearing — a Redis adapter passing
`ttl_seconds` to `EX` truncates 0.05 to zero whole seconds and gets *no*
expiry, which the suite catches and a one-second TTL would not.

The suite's stated reason is the one to keep in view when adding a case: an
in-memory reference that is *more forgiving* than the real backend lets an
adapter pass on behaviour production does not have. So the awkward cases —
`increment` on a key `set` wrote, a TTL on an increment that is not the first,
two hits at the same instant — belong here rather than in one adapter's own
file.

The suite is subclassed by both adapters: `tests/unit/llm/test_memory_cache.py`
runs it in the unit tier, and `tests/integration/llm/test_redis_cache.py` runs
the same body against a real server under `-m integration`.

For most of its life it was subclassed by *one*, and what happened when the
second arrived is the argument for the tier existing at all. `RedisCache` had
shipped, been reviewed, and been used; its first compliance run failed
immediately on a defect no reader had caught. `record_hit` built its sorted-set
member from `id(self)`, which is constant for the life of the cache object — so
it distinguished two *instances* rather than two hits, and collapsed a burst
into one entry, which is exactly when a rate limiter is being asked a question
that matters. `MemoryCache` cannot exhibit it: it appends to a list.

The comment above that line already described the requirement correctly. The
code under it did not, and nothing executed the promise against the adapter
that had to keep it.

There is **no equivalent tier for `LlmProvider`**, and that is a gap rather
than a decision. Its two adapters are tested separately — `FakeLlmProvider`
against its scripting behaviour, `LangChainLlmProvider` against a stubbed chat
model and, in the integration suite, a live endpoint — and a third adapter
would get no help from either. The error contract is the part that most needs
one: "empty content raises rather than returning an empty result", and the
sibling split between truncation and refusal, are promises a new adapter has
to re-derive from this ADR instead of inheriting from a suite. See
[harden model calls](../how-to/harden-model-calls.md) for the caller-side
behaviour that depends on that contract, and
[implement a store adapter](../how-to/implement-a-store-adapter.md) for what a
port with a compliance tier looks like from the outside.

### For the public surface

`LlmProvider`, `FakeLlmProvider` (with `Response` and `EMPTY`), and the whole
`LlmProviderError` family — `EmptyCompletionError`, `MalformedCompletionError`,
`RefusedCompletionError` — are exported from `redstring`. **`Cache`,
`MemoryCache` and `RedisCache` are not**, nor are `CircuitBreaker` and
`RateLimiter`: the cache is the transport's internal coordination, and
exporting it would promise a cache abstraction the port is deliberately not.
`LangChainLlmProvider` stays internal too — it is reached through the `llm`
extra. See [ADR 0006](0006-the-public-surface-is-gated.md) for why anything
reached by a dotted path is internal regardless.

## Related

- [ADR 0002: the two store ports](0002-two-store-ports.md) — the other half of
  the port surface, and where the compliance-suite pattern this one borrows
  was set.
- [ADR 0006: the public surface is gated](0006-the-public-surface-is-gated.md)
  — why exporting a name pulls its closure, and why these ports export
  asymmetrically.
- [ADR 0013: resilience behind the `Cache` port](0013-resilience-behind-the-cache-port.md)
  — what `CircuitBreaker` and `RateLimiter` actually do with the two
  capabilities decided here.
