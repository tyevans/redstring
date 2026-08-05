# Harden model calls

This guide wraps a working `LlmProvider` in the three pieces `redstring`
ships for surviving a flaky or overloaded model server: retry with backoff, a
per-tenant rate limit, and a circuit breaker. The result is a class that still
satisfies the `LlmProvider` port, so `build_graph` takes it unchanged.

## Before you start

You need two things.

**A working provider.** Anything satisfying the exported `LlmProvider` port
will do, but usually it is

```python
from redstring.llm.adapters.langchain import LangChainLlmProvider

inner = LangChainLlmProvider.openai_compatible(
    base_url="http://192.168.1.14:8080/v1",  # the /v1 root
    model="qwen3.6-27b-mtp",                 # the server's model id
)
```

which covers llama.cpp, llama-swap, vLLM, Ollama's OpenAI shim and OpenAI
itself — they agree on the wire format that matters here. It needs the `llm`
extra (`uv add 'redstring[llm]'`); without `langchain-openai` installed the
constructor raises `ImportError` naming the extra. `api_key` defaults to
`"not-needed"` because local servers ignore it and the OpenAI client refuses
to start without one; pass a real key for a hosted endpoint. The provenance
string recorded on every extracted entity is `f"{provider}/{model}"`, so the
provider above reports `openai-compatible/qwen3.6-27b-mtp`.

**Get an extraction working *without* any of this hardening first.** Every
failure mode below — the retry that never succeeds, the breaker that opens
immediately — looks exactly like a misconfigured `base_url` or a model id the
server does not have.

**A tenant id.** `TenantId` is a `UUID` (see
[Domain value types](../reference/domain-value-types.md)). The rate limiter's
allowance is keyed per tenant, so you must have the same id in hand at the
point of the model call that you pass to `build_graph(..., tenant_id=...)` —
if the wrapper limits against a different id than the one the pipeline writes
under, the limit is real but it is not the tenant's.

Everything here is `async`, including `Cache`, `RateLimiter` and
`CircuitBreaker` — there is no synchronous entry point to any of it.

## These pieces are not on the public surface

`redstring.__all__` is the whole promise (see
[ADR 0006](../adr/0006-the-public-surface-is-gated.md)). None of the three
hardening pieces is in it, nor the `Cache` port they store state behind, so
you import all of them by dotted path:

```python
from redstring.llm.retry import ExtractionRetryPolicy, RetryExhausted, with_retry
from redstring.llm.rate_limiter import RateLimitExceeded, RateLimiter
from redstring.llm.circuit_breaker import CircuitBreaker, CircuitOpen, CircuitState
from redstring.llm.cache import MemoryCache
from redstring.llm.cache.redis import RedisCache
from redstring.ports.cache import Cache
```

Two details of that block are not arbitrary:

- `MemoryCache` comes from the package (`redstring.llm.cache`); `RedisCache`
  is **only** reachable from `redstring.llm.cache.redis`. That is deliberate:
  the Redis adapter's client import is deferred so the package `__init__` does
  not drag `redis` in for callers who never asked for it. Importing it from
  the package will fail.
- `CircuitState` is what `await breaker.state()` returns, so you need it for
  the assertions in [Verifying your wiring](#verifying-your-wiring) even
  though you never construct one.

**What "internal" means here.** A dotted path may change without notice,
including in a patch release — renamed, moved between modules, or given a
different signature — and no test in this repo will warn you, because the
surface tests only check what `__all__` claims. Two of the three exceptions
below are not exported either, so even your `except` clauses depend on a
dotted import.

Practical consequences, in the order they will bite you:

1. **Pin the version you tested against.** A compatible-release constraint
   (`redstring~=X.Y`) is not enough; these pieces sit outside the promise
   that constraint expresses.
2. **Keep the wrapper in one module of your own.** Every dotted import in this
   guide belongs in that file, so an upgrade that renames something is one
   file to fix and one place to read before upgrading.
3. **Do not re-export them from your own package.** Handing your callers
   `yourapp.RateLimiter` makes an internal path part of *your* public surface,
   and you inherit the churn.

What *is* stable is the boundary you wrap. `LlmProvider` is exported, your
wrapper is written against it, and `build_graph` takes it because it satisfies
that port and for no other reason. If the hardening pieces move, the wrapper's
inside changes and nothing outside it does — which is the whole reason this
guide builds a class rather than sprinkling `acquire` calls through a
pipeline.

The three exceptions and their modules are tabulated in
[Reference: the exceptions](#reference-the-exceptions). If you want to own
this state instead of importing it, the ports are the seam:
[Implement a store adapter](implement-a-store-adapter.md) covers writing your
own `Cache` and proving it against the compliance suite, and
[ADR 0008: the two non-store ports](../adr/0008-the-two-non-store-ports.md)
says why `Cache` is as small as it is.

## Write a hardened provider

The whole of the hardening lives in one class: it holds an inner
`LlmProvider`, satisfies the same port, and is therefore accepted anywhere the
inner one was. Start from this skeleton and fill in `extract` as you work
through the steps below.

```python
from uuid import UUID

from pydantic import BaseModel

from redstring import LlmProvider


class HardenedLlmProvider:
    """An `LlmProvider` that survives a flaky model server."""

    def __init__(self, inner: LlmProvider, *, tenant_id: UUID) -> None:
        self._inner = inner
        self._tenant_id = tenant_id

    @property
    def model(self) -> str:
        return self._inner.model

    async def extract[S: BaseModel](
        self, text: str, schema: type[S], *, system_prompt: str | None = None
    ) -> S:
        return await self._inner.extract(text, schema, system_prompt=system_prompt)
```

That is already a conforming provider — it just hardens nothing yet. Four
things about it are load-bearing.

**There is nothing to inherit and nothing to register.** `LlmProvider` is a
`runtime_checkable` `Protocol` (`redstring.ports.llm_provider`), so
structural conformance is the entire requirement: a `model` property and an
async `extract`. Subclassing it would work too, but it buys nothing and drags
the port into your class's MRO. Note the limit of `runtime_checkable`:
`isinstance(x, LlmProvider)` checks that the *members exist*, not that their
signatures match — a wrapper with the wrong `extract` signature passes
`isinstance` and fails at the call. Type-check the module (`mypy`, `pyright`)
rather than relying on the runtime check.

**Delegate `model`; never answer with your own name.** `model` is provenance,
not configuration: its value is copied onto `Entity.model` and lands in a
durable event log, where "re-extract everything the old model touched" has to
stay answerable. A wrapper that returns `"hardened"` — or a fixed string it
was constructed with — writes a model identity into that log that no model
ever produced. Make it a property that reads through, so swapping the inner
provider cannot leave it stale.

**Keep `extract`'s signature exactly.** `text` and `schema` positional,
`system_prompt` keyword-only and defaulting to `None`, generic in
`S: BaseModel` so the return type is the schema the caller passed rather than
`BaseModel`. Pass `system_prompt` through untouched — the port is explicit
that providers supply no default prompt of their own, because two callers
passing the same text would then get different answers for a reason neither
could see. A wrapper quietly injecting one is the same defect wearing a
different hat.

**Hold the tenant id on the instance.** It is a `TenantId`, which is a `UUID`
([Domain value types](../reference/domain-value-types.md)). The port has
nowhere to put it — `extract` takes text and a schema, and that narrowness is
deliberate — so the rate limiter's key has to come from construction. One
wrapper instance therefore serves one tenant. For a multi-tenant process,
build a wrapper per tenant around a *shared* limiter and breaker (they key by
tenant and by prefix respectively, so sharing is what you want), or move the
id into a `contextvars.ContextVar` your `extract` reads. Do not reach for a
mutable attribute you set before each call: concurrent extractions interleave,
and the failure is one tenant's traffic being counted against another's
allowance — silent, and only visible as a limit that fires too early.

As you add the layers, everything goes *inside* `extract`, between the
signature above and the delegating call:

```python
    async def extract[S: BaseModel](
        self, text: str, schema: type[S], *, system_prompt: str | None = None
    ) -> S:
        # breaker: should we call at all?
        # limiter: are we allowed to right now?
        # retry:   did the wire drop?
        return await self._inner.extract(text, schema, system_prompt=system_prompt)
```

The [full example](#full-example) at the end of Step 3 is this class with all
three filled in.

## Step 1 — Retry the failures the provider may answer differently

`with_retry` is a decorator for **coroutine functions**. Apply it to the call
you want retried and it re-invokes that call on the exception types you name,
sleeping between attempts:

```python
from redstring.llm.retry import ExtractionRetryPolicy, RetryExhausted, with_retry


@with_retry(retryable_exceptions=(ConnectionError, TimeoutError))
async def call_the_model(text: str) -> Extraction:
    return await inner.extract(text, Extraction)
```

The default policy makes `max_retries` = 3 retries **after** the first
attempt, so four calls in total. Delay before retry N (0-indexed) is
`min(initial_delay * multiplier ** N, max_delay)`, then jittered — 1s, 2s, 4s
by default, each ±10%.

Inside a wrapper class, decorate at construction rather than at class-body
scope, so the policy stays a constructor argument:

```python
self._call = with_retry(
    retryable_exceptions=retryable,
    policy=ExtractionRetryPolicy(max_retries=3, initial_delay=1.0),
)(self._inner.extract)
```

`with_retry` is typed as taking `Callable[P, Awaitable[T]]`, not
`Callable[P, T]`. That is deliberate and worth respecting: applied to a
*synchronous* function the retry loop's `await` blows up on the first
retryable failure rather than at the call site, which is a long way from where
you would look. Decorate `async def` only, and let the type checker enforce it.

### Choose your own retryable exceptions

**The module names no retryable exceptions, on purpose.** Which failures are
worth a second attempt depends on the HTTP client underneath your provider,
and only you know what it raises — `httpx.ConnectError`, `openai.APIError`,
a bare `TimeoutError`. The parameter *does* have a default, `(Exception,)`,
and it retries **everything**. Treat that default as a placeholder and always
pass an explicit tuple; nothing warns you if you do not.

Retry is only safe for a failure the provider may answer differently next
time: a dropped connection, a timeout, a 503. A too-wide tuple does not make a
permanent failure recoverable — it makes it arrive `max_retries` times slower.
A malformed request retried on the default policy sleeps 1s, 2s and 4s and
then delivers the same error roughly seven seconds later, wrapped in
`RetryExhausted` so the type your caller was matching on is now one level
down. Under the rate limiter of Step 2 it has also spent four slots of that
tenant's allowance to learn nothing, and under the breaker of Step 3 it takes
four times as long to reach the `record_failure()` that would have shed load.

The widest tuple is the worst version of this, because `(Exception,)` catches
this library's own `LlmProviderError` family — `MalformedCompletionError`,
`EmptyCompletionError`, `RefusedCompletionError`, every one of them a case
where the server answered and will answer identically. Those are the three
listed under [Never retry these](#never-retry-these), and the default tuple
retries all of them.

Anything *not* in the tuple propagates immediately and unwrapped, on the first
attempt — no sleep, no `RetryExhausted`, the original traceback. That
propagation is the whole mechanism behind "never retry these": you exclude an
exception by *not naming it*, and there is no deny-list to maintain.

Two things the tuple does not have to handle. `asyncio.CancelledError` derives
from `BaseException`, so even `(Exception,)` leaves cancellation alone and a
timed-out task still unwinds. And a tuple naming a base class covers its
subclasses in the ordinary way, so `(httpx.TransportError,)` is usually a
better-judged line than enumerating six connection errors.

### Tune the backoff

`ExtractionRetryPolicy` is where the waiting is configured. Construct one and
pass it to `with_retry`; with no `policy=` you get `ExtractionRetryPolicy()`.

```python
@with_retry(
    retryable_exceptions=(ConnectionError,),
    policy=ExtractionRetryPolicy(max_retries=5, initial_delay=2.0, max_delay=30.0),
)
async def call_it_more_patiently(text: str) -> Extraction: ...
```

| Parameter | Default | What it does |
|---|---|---|
| `max_retries` | 3 (`DEFAULT_MAX_RETRIES`) | attempts **after** the first |
| `initial_delay` | 1.0s | the first retry's base delay |
| `multiplier` | 2.0 | growth per retry |
| `max_delay` | 60.0s | cap on the base delay |
| `jitter` | 0.1 | ±10% random variation |

The delay before retry N (0-indexed) is

```
base  = min(initial_delay * multiplier ** N, max_delay)
delay = max(0.0, base + random.uniform(-base * jitter, base * jitter))
```

so the defaults give roughly 1s, 2s, 4s, each ±10%, and four calls in total.
`policy.get_delay(N)` computes it directly if you want to see the schedule for
a policy before committing to it.

Two consequences of that formula are easy to get wrong:

- **`max_delay` caps the base, not the jittered result.** At the cap with
  `jitter=0.1` the sleeps still vary within ±10% of `max_delay`, which is the
  point — a fleet pinned at the cap is exactly the fleet you do not want
  synchronised.
- **`multiplier=1.0` is legal and means constant delay**, not "no backoff
  applied to a growing number". Every retry then waits `initial_delay`.

**Total worst-case wait is the sum of the schedule, and it grows fast.** The
defaults spend ~7 seconds before raising; `max_retries=5, initial_delay=2.0`
spends 2+4+8+16+30 = 60 seconds under the `max_delay=30.0` above. Pick numbers
against the deadline the caller actually has — under the rate limiter of
Step 2 each attempt also consumes a slot of the tenant's allowance, and under
the breaker of Step 3 nothing is reported as a failure until the whole
schedule has run.

The constructor validates, so a bad policy raises `ValueError` at construction
rather than at the first retry — hours later, in production, on the failure
path:

| Rejected | Rule |
|---|---|
| `max_retries=-1` | must be non-negative |
| `initial_delay=-1.0`, `max_delay=-1.0` | must be non-negative |
| `multiplier=0.5` | must be at least 1.0 (a shrinking backoff is not a backoff) |
| `jitter=1.5`, `jitter=-0.1` | must be between 0.0 and 1.0 |

Three edges are legal and mean what they say:

- `max_retries=0` — exactly one attempt, no sleeping, and still
  `RetryExhausted` on failure. Use it when you want the exhaustion signal and
  the `__cause__` chaining without the waiting.
- `max_retries=None` — the same as omitting it; you get
  `DEFAULT_MAX_RETRIES`. This is a plain module constant, deliberately not
  read from a settings object, so the schedule depends on nothing outside the
  call you can see.
- `jitter=0.0` — exact, deterministic delays. Reach for it in tests, where
  asserting on a sleep length is otherwise flaky, and leave it alone in
  production.

**Keep some jitter in production.** It is what stops every worker that failed
on the same outage from retrying in lockstep and re-creating the spike that
knocked the model server over. Local models are single-GPU often enough that
this is not hypothetical.

Jitter is drawn from `random`, unseeded and not injectable. If you need
deterministic timing in a test, set `jitter=0.0` rather than seeding the
global RNG — `pytest-randomly` reseeds it per test, and a test that depends
on that seed is the order-dependent kind this repo treats as a bug.

### Handle `RetryExhausted`

When every attempt has failed on a *retryable* exception, `with_retry` raises
`RetryExhausted`. Three things about it will catch you out.

**It is a plain `Exception`, not a `RedstringError`.** An
`except RedstringError` around your pipeline — the clause that catches
everything else this library raises — does not catch it. If you have one
handler for the library, add `RetryExhausted` to it explicitly. (See
[Reference: the exceptions](#reference-the-exceptions).)

**Its message does not contain the real error.** The message is built from the
policy and the function name only:

```
Exhausted 3 retries for extract
```

That is `max_retries` and `func.__name__` — nothing about the refused TLS
handshake, the connection reset, or the 503 that actually happened. The
failure from the *last* attempt is chained as `__cause__`, and reading it is
the whole point of catching this exception. Since the decorator uses
`functools.wraps`, the name in the message is your wrapped function's, so
decorating `self._inner.extract` gives `"... for extract"` and decorating a
helper called `call_the_model` gives that.

**`raise ... from last_exception` sets `__cause__`, not `__context__`.** Log
it explicitly; a bare `logger.exception()` inside the handler prints the
`RetryExhausted` traceback with the cause appended as "The above exception was
the direct cause", which is fine at a terminal and useless once a log
aggregator has taken the first line.

```python
try:
    result = await call_the_model(text)
except RetryExhausted as exhausted:
    logger.error(
        "gave up after %d attempts",
        exhausted.attempts,
        exc_info=exhausted.__cause__,
    )
    raise
```

`exc_info` accepts an exception instance, so passing `exhausted.__cause__`
puts the *original* traceback in the log record. Then `raise` — re-raise
rather than swallow, so the layers above (the breaker in
[Step 3](#step-3--stop-hammering-a-dead-model-with-circuitbreaker)) still see
that the call failed.

### `attempts` counts calls, the message counts retries

`attempts` is the **total number of calls made, including the first**, while
the message reports `max_retries`. They differ by one, always, and both are
correct:

| Policy | Calls made | `attempts` | Message |
|---|---|---|---|
| default (`max_retries=3`) | 4 | `4` | `Exhausted 3 retries for …` |
| `max_retries=2` | 3 | `3` | `Exhausted 2 retries for …` |
| `max_retries=0` | 1 | `1` | `Exhausted 0 retries for …` |

Assert on `attempts` rather than parsing the message if you test this, and be
aware of the off-by-one when you report it to a human: "gave up after 4
attempts" and "exhausted 3 retries" describe the same event.

The last row is the one to notice. **`max_retries=0` still raises
`RetryExhausted`** after its single call — the exception means "the retry
wrapper gave up", not "we retried and it did not help". A caller that treats
`RetryExhausted` as evidence of a transient, repeatedly-observed failure is
wrong about a zero-retry policy, and `__cause__` is again the thing to read.

### Narrowing on the cause

Because the real type is only on `__cause__`, dispatching on *what* failed
means inspecting it:

```python
except RetryExhausted as exhausted:
    if isinstance(exhausted.__cause__, TimeoutError):
        ...  # the model server is slow, not gone
    raise
```

`__cause__` is typed `BaseException | None` and can in principle be `None`, so
`isinstance` (which is `False` for `None`) is the safe form and `except`-style
narrowing on a possibly-absent cause is not. In practice the decorator always
chains the last retryable exception, and it can only reach the raise after
catching one.

Do not reach for this to re-classify a failure as permanent — if a type is not
worth retrying, keep it out of `retryable_exceptions` in the first place and
it will reach you unwrapped, on the first attempt, with its own traceback.
That is the subject of the next subsection.

### Never retry these

Three exceptions share one property: **the server answered.** There was no
dropped connection and no timeout — a completion came back and this library
rejected it, or the model declined to produce one. A second identical request
gets an identical answer, so retrying only spends tokens, latency and rate-limit
slots to reach the same failure.

All three are `LlmProviderError` subclasses and all three are exported from
`redstring`, so *not* naming them is one import away from being reviewable:

```python
from redstring import (
    EmptyCompletionError,
    MalformedCompletionError,
    RefusedCompletionError,
)
```

You exclude them by **leaving them out of `retryable_exceptions`** — there is
no deny-list parameter, and none is needed: anything outside the tuple
propagates on the first attempt, unwrapped, with its own traceback. The one
tuple that gets this wrong is the default. `(Exception,)` catches all three,
because `LlmProviderError` derives from `RedstringError` which derives from
`Exception`. So does any tuple written as `(LlmProviderError,)` in the hope of
catching "model problems" — that spelling names exactly the three failures
that cannot be helped and none of the transport failures that can.

| Exception | Raised when | Useful attributes | Do this instead |
|---|---|---|---|
| `EmptyCompletionError` | no usable content came back | `model`, `finish_reason` | raise `max_tokens`, or fix the prompt |
| `MalformedCompletionError` | content failed schema validation | `model`, `schema`, `cause` | simplify the schema, or ask the server for strict structured output |
| `RefusedCompletionError` | the model's safety layer declined | `model` | record the document as un-extractable and move on |

**`MalformedCompletionError`** means content arrived and
`schema.model_validate_json` rejected it. `cause` carries the pydantic
validation error and `schema` the schema's name, which is what you want in the
log — the request or the schema shape is the problem, and it will fail
identically on every attempt. The `LangChainLlmProvider` adapter already asks
the server for strict structured output, so reaching this path usually means
the server ignored that request; retrying does not change the server's mind.

**`EmptyCompletionError`** is the one people are most tempted to retry, and
the temptation is understandable: it occasionally works. The reference
deployment reaches it with a reasoning model that spends its whole token
budget on `reasoning_content` before `content` begins, answering HTTP 200
throughout — and a retry that happens to think less does succeed. That is a
lottery, not a fix. **Read `finish_reason` first:** `"length"` means the budget
was too small and the answer is a larger `max_tokens` on the provider,
`"stop"` means the model chose to say nothing and the answer is in the prompt.
No number of retries buys a bigger budget.

**`RefusedCompletionError`** is a permanent property of *this content*. Every
retry sends the same text to the same safety layer for the same refusal.

That last pair are deliberately **siblings rather than one class**, because
they call for opposite responses — bigger budget versus never send this
content again — and that distinction is exactly what a caller extracting from
clinical or legal text needs. Catching `LlmProviderError` as one retryable
type throws it away.

### A refused key is not `RefusedCompletionError`

"Refused" covers two different failures, and only one of them is this
library's:

- **A refused *request*** — the content filter — is
  `RefusedCompletionError`, above.
- **A refused *key*** — a wrong, expired or unauthorised API key — never
  reaches this library at all. It surfaces as your HTTP client's own auth
  exception (`openai.AuthenticationError` and its kin), raised inside the
  adapter and propagated unchanged.

Both are permanent, and the second is the one a broad tuple silently swallows:
`(Exception,)`, or a `(httpx.HTTPStatusError,)` written for 503s, will retry a
401 on the full backoff schedule and then report `RetryExhausted` — so a
deployment with a typo'd key looks like a slow, flaky model server instead of
a one-line configuration fix. Keep authentication and authorisation errors out
of the tuple explicitly, and prefer naming the transport exceptions you mean
over naming a base class that spans both.

One more non-retryable to know about: `extract` raises a plain `ValueError`
for blank `text`, before any network call. It is a caller bug, it is not an
`LlmProviderError`, and — like everything else here — the way to keep it out
of the retry loop is to not name it.

### What this costs you downstream

Excluded exceptions skip the retry loop entirely, which has a consequence in
the [full example](#full-example) worth being deliberate about: they propagate
past the `except RetryExhausted` clause, so they never reach
`breaker.record_failure()` and **do not count toward opening the circuit.**

That is right for the first two — a schema the model cannot satisfy is not
evidence the server is unhealthy, and a breaker that opens on them takes a
working model offline. It is a judgement call for `RefusedCompletionError`: if
a refusal in your deployment means a misconfigured endpoint rather than
genuinely objectionable content, add an explicit `record_failure()` for it.
Decide, rather than inheriting the answer from which clause happens to catch
what.


## Step 2 — Rate-limit per tenant with `RateLimiter`

Retry protects you from a model server that drops calls. The rate limiter
protects the *server* from you — and, because the allowance is per tenant,
protects each of your tenants from the others.

```python
from redstring.llm.rate_limiter import RateLimitExceeded, RateLimiter

limiter = RateLimiter(rpm=60)
```

Every argument is keyword-only: `rpm` (required), `window_seconds` (60.0),
`cache` (a `MemoryCache` when omitted — [Step 4](#step-4--choose-the-cache-backend)),
and `key_prefix` (`"kg:ratelimit"`). Keys are `f"{key_prefix}:{tenant_id}"`,
so two limiters sharing a cache share an allowance unless you give them
different prefixes — which is how you run one limit for extraction and a
separate one for embeddings against the same Redis.

### Why `rpm` is required, and why `rpm=0` raises

`rpm` has no default because a library that silently limits you to a number
you never chose is worse than one that asks. There is no correct guess here:
60 is generous for a single-GPU llama.cpp and absurdly low for a hosted
endpoint.

It is also not read from a settings object. The number that limits your model
calls is visible at the construction site, in the code you are reading, rather
than in an environment the reader has to go and find.

`rpm <= 0` raises `ValueError` at construction —
`rpm must be positive, got 0` — and the `0` case is the interesting one. It is
*representable* in a way that `-1` is not: "an allowance of zero" is a
coherent sentence. But it means refuse every call forever, and no caller means
that. Somebody who wants no model calls does not build a limiter and wire it
into a pipeline; a `0` in that argument came from an unset environment
variable, an integer division, or a config default that never got filled in.

Honouring it would produce the worst available failure: a pipeline that makes
no progress, raising `RateLimitExceeded` from the first call with a
`retry_after` that never helps, because no slot ever frees for it to count
down to. That reads as an overloaded model server. The `ValueError` naming the
argument at startup is a much shorter debugging session, and it fires before
any document has been read.

`window_seconds <= 0` is rejected on the same reasoning and with the same
shape of message (`window_seconds must be positive, got 0`): a window of zero
width contains no hits, so the limit would silently not exist — the opposite
failure, and the more dangerous one, since nothing appears wrong until the
model server falls over.

Both checks run in `__init__`, before anything is stored, so a rejected
limiter cannot be half-built and cannot be used.

### Call `acquire(tenant_id)` before the model call

`acquire` is the only thing that grants a slot. It either returns `None` (a
hit was recorded) or raises `RateLimitExceeded`. There is no boolean form, no
context manager, and nothing to release afterwards — the hit ages out of the
window on its own.

Call it **immediately before the model call**, after the breaker has said the
call is worth making and outside the retry decorator:

```python
import asyncio

from redstring.llm.rate_limiter import RateLimitExceeded

try:
    await limiter.acquire(tenant_id)
except RateLimitExceeded as limited:
    await asyncio.sleep(limited.retry_after)
    await limiter.acquire(tenant_id)

result = await call_the_model(text)
```

**Sleep on `retry_after`; do not retry immediately.** The wait is not a
guess, a constant, or a backoff schedule. The limiter asks the cache for the
oldest hit still inside the window and computes

```
retry_after = (oldest_hit + window_seconds) - now
```

clamped at `0.0` — the exact moment that hit falls out of the window and a
slot frees. A caller that sleeps for it and retries succeeds. A caller that
retries immediately is refused again, and again, for the whole remainder of
the window: nothing about the window changed in the microsecond between the
two calls. Under `rpm=60` that is a spin loop of up to sixty seconds
producing nothing but log lines, which is the same failure shape as retrying
inside an open breaker.

The message carries the same number for a human: `tenant <uuid> has used 60
calls in 60s; retry in 0.42s`. Sleep on the attribute, never on a number
parsed out of the message.

Two properties of the refusal path make the sleep-and-retry above safe:

- **A refused call is not recorded.** The `raise` happens before
  `record_hit`, so nothing is written to the cache. Asking costs you nothing.
  Were refusals counted, a client in a retry loop would push the window
  forward with every attempt and lock itself out indefinitely while making no
  successful calls at all — the limiter would punish the one client behaving
  correctly.
- **`retry_after` does not grow as you ask again.** Because refusals are not
  recorded, each successive refusal counts down toward the *same* aging-out
  hit and reports a wait no longer than the last. This is asserted directly in
  the unit tests; it is a property you can rely on, not an artefact.

One edge to know: if the window is full but the cache reports no oldest hit,
`retry_after` falls back to the full `window_seconds`. That happens when hits
expire out from under the count between two cache calls, so it is a
conservative answer to a race rather than a wait you should expect to see.

### Do not route `RateLimitExceeded` through `with_retry`

Leave `RateLimitExceeded` out of your `retryable_exceptions` tuple, and note
that the default `(Exception,)` catches it. Three reasons, in increasing order
of how much they will cost you:

1. **The schedules are unrelated.** `with_retry` sleeps 1s, 2s, 4s regardless
   of when a slot frees; the limiter has already computed the exact number.
   Sleeping the wrong amount either wastes the tenant's window or fails again.
2. **Every attempt consumes a slot.** If retry sits *outside* the limiter,
   one logical call takes up to four slots of the tenant's allowance to make
   one request — the limit still holds, but it counts something other than
   model calls. This is why the ordering below puts the limiter outside retry.
3. **Refusals become `RetryExhausted`.** The `retry_after` you needed is then
   two levels down, on `__cause__`, and the caller sees an exception whose
   name says nothing about rate limits.

### Bound the retry, or shed instead

The single retry above is the simple form and it is right for a caller with
one request in flight. It is not a loop, deliberately. Under sustained
overload — several workers, or one tenant genuinely over its allowance — a
caller that always sleeps and retries never sheds load, and the second
`acquire` can itself be refused when another worker takes the freed slot
first.

If you loop, bound it:

```python
for _ in range(3):
    try:
        await limiter.acquire(tenant_id)
        break
    except RateLimitExceeded as limited:
        await asyncio.sleep(limited.retry_after)
else:
    raise TooBusy(f"no slot for {tenant_id} after 3 waits")
```

An unbounded `while True` here is the shape this repo treats as a bug: its
exit depends on data another process controls, so it does not fail, it hangs —
and a hang in a worker reads as infrastructure trouble and gets restarted
rather than investigated. Better still, decide before you start:
`remaining(tenant_id)` lets you queue or drop the document without building a
prompt you cannot send.

### Backpressure with `remaining(tenant_id)`

`acquire` tells you *after the fact* that you were over the line. `remaining`
tells you before you have spent anything:

```python
if await limiter.remaining(tenant_id) == 0:
    await queue_for_later(document)
    return

await limiter.acquire(tenant_id)
result = await call_the_model(text)
```

It returns `max(0, rpm - hits_in_the_current_window)` — an `int`, and **never
negative**. A fresh tenant gets the whole allowance back; two `acquire` calls
against `rpm=5` leave `3`.

That clamp is not cosmetic. Several workers sharing one cache can race past
the limit together — check the count, all pass, all record — and a raw
negative would then read as an allowance to anything that adds to it
(`remaining + 1`, a queue depth, a metric averaged across tenants). Zero is
the honest answer to "how many may I send", whether you are one over or five.

Three properties to build on:

- **It takes no slot.** It only counts; nothing is recorded. Calling it on
  every document costs a cache read.
- **It is a snapshot, not a reservation.** It is true at the microsecond it
  returns. Another worker — or another coroutine in this one — can take the
  last slot before your `acquire`, so a positive `remaining` is not a promise
  the next call succeeds.
- **It is therefore backpressure, never a permission check.** `acquire` is the
  only thing that grants a slot, and it still has to be called and its
  `RateLimitExceeded` still has to be handled. Using `remaining` *instead* of
  the try/except gives you a limiter that mostly works and lets bursts
  through, which is the failure the sliding window exists to prevent.

What it is good for is the decision `acquire` cannot express: whether to wait
at all. Shedding, deferring to a queue, ordering a batch so tenants with room
go first, or exporting a per-tenant gauge — all of them want a number rather
than an exception, and all of them want it before you build a prompt and pay
to tokenize it.

### Keep `window_seconds` at 60 unless you mean something else

`window_seconds` defaults to 60.0 and is **independent of `rpm`**. The name is
for the common case, not a constraint: the two arguments together say "`rpm`
calls per `window_seconds` seconds", so

```python
RateLimiter(rpm=10, window_seconds=1.0)     # ten per second
RateLimiter(rpm=600, window_seconds=3600)   # six hundred an hour
```

are both legitimate, and neither renames `rpm`. If you change the window,
expect to re-read every `rpm` at a construction site as "per window" — the
argument keeps its name and stops meaning per minute, which is the one real
cost of touching this.

Averages that match are not the same limit. Ten per second, six hundred a
minute and thirty-six thousand an hour all average identically, and they
permit completely different bursts: the hour-wide window lets a tenant spend
its entire allowance in the first second and then starve for the rest of the
hour. **Pick the window that matches what the server actually falls over on**
— concurrent decode slots on a single-GPU model, usually — rather than the one
whose arithmetic is tidy. A wider window is more permissive about bursts even
when it looks stricter on paper.

The narrow direction has its own limit: at `window_seconds=1.0` you are
limiting to a resolution finer than a model call takes to return, and the
window will usually be empty by the time the previous call finishes. That is
not wrong, it just is not doing anything.

`window_seconds <= 0` raises `ValueError` at construction, for the reason in
[Why `rpm` is required](#why-rpm-is-required-and-why-rpm0-raises): a
zero-width window contains no hits, so the limit would silently not exist.

Two knock-on effects of the number you choose:

- **`retry_after` is bounded by it.** The wait handed to a refused caller is
  at most one window, so an hour-wide window can produce a `retry_after` of
  most of an hour. Check the value against your request deadline before
  sleeping on it blindly.
- **Key lifetime is twice it.** A tenant's hit series is kept for
  `2 × window_seconds` after its last hit, so idle tenants expire on their own
  and a shared Redis does not accumulate one key per tenant forever. Twice
  rather than exactly once, so a series can never expire while hits still
  inside the window depend on it — an expiry at the window edge would drop
  hits that still count and silently raise the effective limit.

### Sliding window, not fixed buckets

The window is **the last `window_seconds` measured from now**, not the current
clock-minute. Every `acquire` computes `since = now - window_seconds` and asks
the cache how many hits were recorded at or after that instant; the window
moves with the call rather than resetting on a boundary.

A fixed-window counter is the obvious cheaper design — one counter per
minute-bucket, `Cache.increment` and an expiry, one round trip instead of the
count/oldest/record this does. It was rejected for one reason: **it lets
through twice the limit across a boundary.** With `rpm=60`, sixty calls at
11:59:59.9 and sixty more at 12:00:00.1 are each legal in their own bucket —
120 calls in 200 milliseconds, from a limiter that reports itself as obeying
sixty a minute, with nothing in its logs to say anything unusual happened.

Twice the limit in a burst is precisely what knocks over a single-GPU local
model, which is the case this exists for. Averaged over any minute you care to
measure it looks compliant, so the failure shows up as an occasional
model-server death and never as a rate-limit metric.

The sliding window has no boundary to straddle. At 12:00:00.1 the sixty calls
from 11:59:59.9 are still inside the window, so the next call is refused, and
it stays refused until those hits are genuinely `window_seconds` old.

Two things you get from this that a bucket counter cannot give you:

- **`retry_after` is a real instant, not a guess.** Because the limiter keeps
  the individual hit timestamps, it can ask for the *oldest hit still in the
  window* and hand you `(oldest + window_seconds) - now`. A fixed-window
  counter knows only when its bucket ends, which for a caller refused early in
  a full bucket is a much longer wait than it actually has to serve.
- **Recovery is gradual.** Slots free one at a time as individual hits age
  out, so a tenant that has been at its limit trickles back to full rate. A
  bucket reset hands the whole allowance back at once, which is exactly the
  synchronised burst the jitter in [Step 1](#tune-the-backoff) exists to avoid.

The cost is honest: two cache reads and a write per `acquire` instead of one
increment, and a stored series per tenant rather than a counter. That series is
what expires after `2 × window_seconds` of idleness (see
[Keep `window_seconds` at 60](#keep-window_seconds-at-60-unless-you-mean-something-else)).

One implementation detail to know if you ever read a clock alongside this: the
limiter reads `datetime.now(UTC)` **once per `acquire`** and passes that same
epoch float into every cache call it makes — counting the window, finding the
oldest hit, and stamping the new one. That is why `Cache`'s window methods take
`at`/`since` rather than reading a clock themselves. Two independent reads
could disagree by microseconds, and the disagreement would surface as a
`retry_after` computed against an instant slightly after the one used for the
count. It is clamped at `0.0`, so the visible symptom would not be a negative
number but a caller sleeping for nothing and being refused again — a spin loop
that appears only under load.

## Step 3 — Stop hammering a dead model with `CircuitBreaker`

Retry handles a call that failed. The breaker handles a model that has
*stopped answering* — the case where every call is going to fail, retry turns
each one into four, and the only useful thing to do is stop calling.

```python
from redstring.llm.circuit_breaker import CircuitBreaker, CircuitOpen, CircuitState

breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60.0)
```

Every argument is keyword-only: `failure_threshold` (5), `recovery_timeout`
(60.0), `half_open_max_calls` (1), `cache` (a `MemoryCache` when omitted —
[Step 4](#step-4--choose-the-cache-backend)) and `key_prefix`
(`"kg:circuit"`). Keys are `f"{key_prefix}:{name}"` for `state`, `failures`,
`opened_at` and `half_open_calls`, so **two breakers sharing a cache and a
prefix are one breaker** — which is what you want across workers, and is why
two breakers guarding *different* model servers need different prefixes.

There is no tenant here, deliberately. The rate limiter's allowance is per
tenant because the allowance is a fairness question; the breaker's subject is
the model server, and it is down or up for everyone.

### The three states

- **`CLOSED`** — normal. Calls go through and failures are counted.
- **`OPEN`** — refusing immediately, without calling the model at all.
- **`HALF_OPEN`** — letting a *strictly limited* number of probes through to
  find out whether the model came back.

`HALF_OPEN` is the state that earns the pattern. Going straight from `OPEN` to
`CLOSED` when the timeout elapses sends the full backlog at a service that has
just finished restarting and knocks it over again — so recovery is probed by
at most `half_open_max_calls` requests while everything else keeps being
refused, and the circuit only closes when one of them succeeds. A single
failed probe reopens the circuit immediately; it does not have to reach
`failure_threshold` again.

The transitions, all of which happen inside the three calls of the next
subsection:

| From | Trigger | To |
|---|---|---|
| `CLOSED` | `record_failure()` reaching `failure_threshold` | `OPEN` |
| `CLOSED` | `record_success()` | `CLOSED`, failure count cleared |
| `OPEN` | `allow_request()` after `recovery_timeout` | `HALF_OPEN` |
| `HALF_OPEN` | `record_success()` | `CLOSED`, everything cleared |
| `HALF_OPEN` | `record_failure()` | `OPEN`, timeout restarted |

`await breaker.state()` reads the current one as a `CircuitState`, and
`await breaker.reset()` forces it back to `CLOSED` with the counters cleared —
for an operator who has fixed the server and does not want to wait out the
timeout, and for tests.

### It fails toward calling the model

Two decisions in here go the same way, and knowing which way saves you a
confusing incident:

- **An unreadable stored state reads as `CLOSED`, with a warning.** A corrupt
  cache entry raising would turn one bad key into a total outage — the exact
  thing the breaker exists to prevent.
- **A missing `opened_at` counts as recovery being due**, so the circuit
  probes rather than staying open. A state entry that outlived its timestamp
  would otherwise be an outage nothing recovers from.

Both mean the failure mode of a damaged cache is *calling a model that may be
down*, which retry and the rate limiter already survive, rather than refusing
work forever. If you are debugging a circuit that will not stay open, look at
the cache before the breaker.

### The call sequence

**`allow_request()` → make the call → `record_success()` or
`record_failure()`.** All three are required, every time. The breaker does not
wrap your call and cannot observe it: it knows only what you tell it, so a
path that skips the reporting step leaves the state machine frozen wherever it
was.

```python
breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60.0)

if not await breaker.allow_request():
    raise CircuitOpen(
        f"{provider.model} is unavailable",
        retry_after=await breaker.retry_after(),
    )

try:
    result = await call_the_model(text)
except Exception:
    await breaker.record_failure()
    raise
else:
    await breaker.record_success()
    return result
```

`try/except/else` rather than `try/except` with the success call at the end of
the `try` block: in the `else` form, an exception raised by
`record_success()` itself cannot be mistaken for a failure of the model call.

### What each call does

**`allow_request()` returns a `bool` and raises nothing.** It is a decision,
not a guard — it does not stop you calling the model if you ignore it, and
there is no context-manager form that would. It also *drives* a transition:
when the circuit is `OPEN` and `recovery_timeout` has elapsed, this is the
call that moves it to `HALF_OPEN`. Which means the transitioning call is
itself one of the probes — it falls through into the probe-counting branch
rather than returning `True` early, so `half_open_max_calls=2` admits exactly
two requests and not three. That off-by-one is pinned by
`test_only_the_permitted_number_of_probes_get_through` in
`tests/unit/llm/test_circuit_breaker.py`.

A consequence worth internalising: **`allow_request()` has side effects.**
Calling it "just to check" consumes a probe slot while half-open. When you
want to *look* at the breaker without touching it, use `await breaker.state()`
— that is a pure read.

**`record_failure()` means different things in different states, and you do
not have to care.** From `CLOSED` it increments the failure count and opens
the circuit if the count reaches `failure_threshold`. From `HALF_OPEN` it
reopens immediately, without waiting for the threshold again — a probe that
fails has already answered the only question the probe asked. From `OPEN` it
does nothing, which is what makes the sequence safe under concurrency: several
workers reporting the failure of an already-open circuit is a no-op, not a
race that pushes the recovery timeout out repeatedly.

**`record_success()` is not optional bookkeeping.** From `HALF_OPEN` it is the
*only* thing that closes the circuit — omit it and the breaker stays half-open
with its probe allowance spent, refusing every subsequent call against a model
that has been healthy for hours. From `CLOSED` it clears the accumulated
failure count, which is what stops a long-lived healthy process from
eventually accumulating `failure_threshold` unrelated failures and opening a
circuit in front of a working server.

### Report the outcome of the model call, and nothing else

The `except Exception` above is deliberately broad, and it is broad over a
narrow scope: exactly one call. Two ways to get this wrong:

- **Reporting failures from outside the call.** If schema construction, a
  cache lookup, or your own logging raises inside the `try`, the breaker
  records a failure the model server had nothing to do with. Keep the `try`
  around the model call alone.
- **Reporting an exception that says nothing about the server.** In the
  [full example](#full-example) the clause is `except RetryExhausted`, not
  `except Exception`, so `MalformedCompletionError` and friends propagate
  without counting — see
  [What this costs you downstream](#what-this-costs-you-downstream). A
  breaker that opens because a schema is too hard for the model takes a
  working server offline.

Note also where the reporting sits relative to retry: with retry innermost,
one `record_failure()` follows a whole exhausted retry schedule. That is the
intent — `failure_threshold=5` then means five *failed calls*, not five failed
attempts — but it means the breaker learns slowly. Five defaults-policy
exhaustions is around 35 seconds of backoff before the circuit opens. If you
want it to react faster, lower `failure_threshold` rather than moving the
reporting inside the retry loop.

### Forgetting a call, and how it looks

Because the breaker is told rather than wrapping, each omission has its own
symptom:

| Omitted | What you see |
|---|---|
| `allow_request()` | the breaker never refuses anything; state transitions still happen, and `state()` reads `OPEN` while traffic sails through |
| `record_failure()` | the circuit never opens; an outage looks exactly like slow calls |
| `record_success()` | the circuit opens normally and never closes again — the worst of the three, because it takes `recovery_timeout` to become visible |

The third is why [Verifying your wiring](#verifying-your-wiring) checks
recovery and not just opening. A breaker that opens is easy to demonstrate; a
breaker that *closes* is the half people ship broken.

Two escape hatches for operators, neither part of the per-call sequence:
`await breaker.state()` reads the current `CircuitState` without touching
anything, and `await breaker.reset()` forces `CLOSED` and clears the failure
count, the open timestamp and the probe count — for someone who has just fixed
the server and does not want to wait out `recovery_timeout`, and for tests.

### Handle `CircuitOpen` and its estimated `retry_after`

**The breaker never raises `CircuitOpen`. You do.** `allow_request()` returns
a `bool` and nothing else; the exception exists so that your wrapper can
report a refusal to its own caller in the same shape everything else in the
pipeline uses. That is why the raise is written out in full in
[the call sequence](#the-call-sequence) rather than hidden inside the breaker:

```python
from redstring.llm.circuit_breaker import CircuitOpen

if not await breaker.allow_request():
    raise CircuitOpen(
        f"{provider.model} is unavailable",
        retry_after=await breaker.retry_after(),
    )
```

`CircuitOpen(message, retry_after=0.0)` takes a required message and an
optional wait, and exposes both as attributes — `.message` (as well as the
usual `str(error)`) and `.retry_after`. Pass the number; the default of `0.0`
means "no idea", and a caller that sleeps on it spins.

It derives from `RedstringError`, so a single `except RedstringError` around
your pipeline catches it — along with `RateLimitExceeded` and the
`LlmProviderError` family, but **not** `RetryExhausted`, which is the one
outlier ([reference](#reference-the-exceptions)).

### Catching it

The two facts a caller wants are already separate attributes, so there is
nothing to parse:

```python
try:
    result = await hardened.extract(text, Extraction)
except CircuitOpen as refused:
    if refused.retry_after > deadline_remaining:
        await park_for_later(document)     # no point waiting
    else:
        await asyncio.sleep(refused.retry_after)
        result = await hardened.extract(text, Extraction)
```

Distinguish it from `RateLimitExceeded` even though both carry a
`retry_after`. A rate-limit refusal means *you* asked too fast and the work is
fine; a `CircuitOpen` means the model server is believed to be down and every
tenant is affected. Deferring a document is usually right for the second and
usually wasteful for the first.

Nothing is recorded when you raise it: the refused call never reached the
model, so there is no `record_failure()` to make and making one would keep the
circuit open on the strength of calls that were never attempted.

### `retry_after()` is an estimate, and here is exactly how much

`await breaker.retry_after()` reads the stored open timestamp and returns

```
max(0.0, recovery_timeout - (now - opened_at))
```

— seconds until an `allow_request()` will admit a probe. Never negative. It is
a *read*: unlike `allow_request()`, it takes no probe slot and drives no
transition, so calling it in a log line or a metric is safe.

Three ways the number can be wrong, all benign, none worth defending against:

- **Another worker probes first.** With a shared `RedisCache` the first worker
  past the timeout closes the circuit on success, so the wait you were handed
  was longer than the wait you needed.
- **A probe fails and the timeout restarts.** `record_failure()` from
  `HALF_OPEN` rewrites `opened_at`, so a caller that sleeps for exactly
  `retry_after` and retries can be refused again with a fresh full wait.
  Sleeping on it is still right; expecting it to be the *last* wait is not.
- **The clock is the process's own.** `datetime.now(UTC)` on each worker,
  compared against a timestamp another worker wrote. Skew across a fleet moves
  the estimate by the skew.

Treat it as a hint to schedule against, exactly as you would an HTTP
`Retry-After` header, and re-read it rather than counting down a copy.

### `0.0` means two different things

`retry_after()` returns `0.0` when there is **no stored `opened_at`** — which
covers the healthy case (the circuit has never opened, or `reset()` cleared
it) and the damaged one (the state key outlived its timestamp). The breaker
treats a missing timestamp as recovery being due, so both readings agree with
what `allow_request()` will do next: admit a probe. See
[It fails toward calling the model](#it-fails-toward-calling-the-model).

The practical consequence is that **`retry_after == 0.0` is not evidence the
circuit is closed.** If you want to know the state, ask for it:
`await breaker.state()` returns a `CircuitState` and touches nothing. A caller
that reads `0.0` as "healthy" and one that reads it as "retry immediately"
both end up doing the same correct thing, which is why the clamp is safe — but
only the second is reasoning about what the value means.

Note also that `opened_at` survives the move to `HALF_OPEN` (only `state` and
the probe count change), so a half-open circuit reports a `retry_after` of
`0.0` once the timeout has elapsed. Probing is due; some other worker is
already doing it.

### Sizing `failure_threshold`, `recovery_timeout` and `half_open_max_calls`

The defaults — `CircuitBreaker()` is `failure_threshold=5`,
`recovery_timeout=60.0`, `half_open_max_calls=1` — are a reasonable starting
point for one local model server behind a handful of workers. Change them when
you can say what you are trading.

| Parameter | Default | Raise it to… | Lower it to… |
|---|---|---|---|
| `failure_threshold` | 5 | tolerate a noisy server without going offline | shed load sooner in a real outage |
| `recovery_timeout` | 60.0s | give a restarting server room, and widen the failure window | rediscover a recovered server faster |
| `half_open_max_calls` | 1 | probe faster after a long outage | — 1 is the floor |

**All three raise `ValueError` if non-positive**, with the argument named in
the message (`failure_threshold must be positive, got 0`), in `__init__`
before anything is stored. Zero is rejected rather than honoured for the
reason `rpm=0` is: a threshold of zero would mean "open before any failure",
a timeout of zero "probe instantly", a probe count of zero "never recover" —
none of which is what a caller means, and each of which came from an unset
config value.

**`failure_threshold` counts *reported* failures, not attempts.** With retry
innermost ([the ordering](#ordering-the-three)) one `record_failure()` follows
an entire exhausted retry schedule, so five on the default policy is roughly
35 seconds of backoff before the circuit opens. Budget the two together: the
time to open is `failure_threshold` × (your worst-case retry schedule +
timeout), and that is how long every request pays full price during an outage.
Lower the threshold rather than moving the reporting inside the retry loop.

Set it too low and one bad minute — a single GC pause, one connection reset —
takes a healthy model offline for `recovery_timeout`. Two things soften that
in practice: `record_success()` from `CLOSED` clears the accumulated count, so
a threshold is only reached by failures with no success between them; and the
count decays anyway ([next subsection](#why-the-failure-count-decays)). A
threshold of 1 is legal and appears throughout the unit tests, but in
production it means the first hiccup opens the circuit.

**`recovery_timeout` is two settings wearing one name.** It is the wait before
an open circuit probes, *and* it is the TTL on the failure counter — so
raising it to be gentler with a restarting server also widens the window over
which failures accumulate, making the circuit open more easily. They pull the
same way (a longer timeout is more conservative on both counts), but notice
that you cannot tune one without the other. It also bounds `retry_after()`,
which is what your callers sleep on, so an hour-long timeout hands out
hour-long waits.

Match it to how long the thing you are protecting takes to come back: a
llama-swap model reload is seconds, a container restart tens of seconds, a
node replacement minutes. Shorter than the recovery means probes that fail and
reopen the circuit with a fresh `opened_at` — which costs little, because a
failed probe is one call, not a stampede.

**`half_open_max_calls` should stay at 1 unless you have measured otherwise.**
The state exists to send *less* than normal load at a service that has just
come back; probing with ten calls is how you knock it over a second time.
Raise it only when a single probe is too slow a signal — a long
`recovery_timeout` and a fleet where one unlucky probe against a
still-warming-up server costs everyone another full timeout.

Two details if you do raise it. The transitioning call counts as a probe (see
[the call sequence](#the-call-sequence)), so the number is exact:
`half_open_max_calls=2` admits two requests, not three. And probes are counted,
not resolved — the allowance is spent by *admitting* calls, so with a value
above 1 the extra probes are in flight concurrently against the server you are
being careful with. Any one success closes the circuit; any one failure
reopens it.

**Sizing is per breaker, and a breaker is a `key_prefix`.** Two breakers
sharing a cache and the default `"kg:circuit"` prefix are one breaker with two
sets of numbers pointed at the same keys — a configuration that reads as two
policies and behaves as neither. Guarding two model servers means two prefixes:

```python
extraction = CircuitBreaker(failure_threshold=5, cache=cache, key_prefix="kg:extract")
embedding = CircuitBreaker(failure_threshold=20, cache=cache, key_prefix="kg:embed")
```

Sharing *is* what you want across workers hitting one server — that is the
point of a `RedisCache` here ([Step 4](#step-4--choose-the-cache-backend)) —
and it changes what the threshold means: five failures across the fleet, not
five per worker. With `MemoryCache` each process counts alone, so a
twenty-worker deployment sends twenty times the threshold at a dying server
before any of them stops.

### Why the failure count decays with `recovery_timeout`, and increments do not refresh the TTL

`failure_threshold` is described above as "consecutive-ish" failures. This
subsection is the "ish". The count is of failures **within roughly one
`recovery_timeout`**, not of failures ever, and the mechanism is a TTL that is
set once and never renewed.

`record_failure()` from `CLOSED` does exactly one thing before checking the
threshold:

```python
failures = await self._cache.increment(
    self._key("failures"), ttl_seconds=self.recovery_timeout
)
```

and the `Cache` port is explicit that `ttl_seconds` is applied when the key is
**created**, not on every increment. Both adapters implement that promise, and
`tests/compliance/cache.py` pins it for any third one — `MemoryCache.increment`
keeps the existing deadline when the key is already live, and `RedisCache`
issues its `PEXPIRE` only when the `INCR` returns `1`.

So the counter is a **tumbling window anchored at the first failure**: the
first failure after a clear creates the key with a `recovery_timeout` life,
subsequent failures add to it without extending it, and when it expires the
count is gone. Only failures inside that one window can add up to
`failure_threshold`.

### Why the refresh would be wrong

A TTL refreshed on every increment is a counter that **never expires under
load** — precisely when it needs to. Failures arriving more often than
`recovery_timeout` would keep pushing the deadline out, so the count would
grow without bound and the only question would be when it eventually crossed
the threshold.

That turns the breaker into something else entirely. Without the decay, five
failures spread over an hour open the circuit exactly as five failures in one
second do — and the first is a healthy service having a bad day while the
second is an outage. A long-lived worker would then accumulate unrelated
failures until it took a working model server offline, and the incident would
be unreadable: nothing near the moment of opening went wrong.

Note that this is the same reasoning as the sliding window in
[Step 2](#sliding-window-not-fixed-buckets), reaching the opposite
implementation. The rate limiter needs individual timestamps because it must
never let a burst through; the breaker only needs "were there N failures
recently", and a counter with a TTL answers that in one round trip. Trading
boundary precision for a single `INCR` is the right trade here — a threshold
crossed a moment early or late costs one call.

### The count also clears on success

Decay is the *second* way the count goes away. `record_success()` from
`CLOSED` deletes the `failures` key outright, so a threshold is only ever
reached by failures with **no success between them** — which is what makes
"consecutive-ish" nearly true. `reset()` deletes it too, as does closing the
circuit from `HALF_OPEN`.

Between them, the two clearing paths mean the count you have to reason about
is: failures since the last success, and within one `recovery_timeout` of the
first of them.

### What this means when you pick `recovery_timeout`

**`recovery_timeout` is two settings sharing one name** (see
[Sizing the three parameters](#sizing-failure_threshold-recovery_timeout-and-half_open_max_calls)),
and this is the second one. Raising it to be gentler with a slow-restarting
server also widens the window over which failures accumulate, so the circuit
opens **more** easily. They pull the same way — a longer timeout is more
conservative on both counts — but you cannot tune one without moving the
other, and there is no separate knob.

Two consequences worth checking against your own numbers:

- **A failure rate below `failure_threshold` per `recovery_timeout` can never
  open the circuit.** With the defaults that is fewer than five failures a
  minute — sustainable indefinitely, by design, because a model answering most
  calls is not one you want to stop calling.
- **Retry sits inside, so the arithmetic is per *call*, not per attempt.** One
  `record_failure()` follows an entire exhausted retry schedule (~7s on the
  default policy), so five of them take ~35 seconds — comfortably inside a
  60-second window, but not inside a 10-second one. If you shorten
  `recovery_timeout` below your worst-case retry schedule times
  `failure_threshold`, the count expires before it can reach the threshold and
  the circuit never opens at all.

That last failure mode is silent: the breaker works, the state machine is
fine, and the circuit simply stays `CLOSED` through an outage. If you are
debugging a breaker that never opens, compute
`failure_threshold × (retry schedule + timeout)` and compare it to
`recovery_timeout` before looking anywhere else.

Finally, the decay is per breaker key, so it is per `key_prefix` and per
cache. Under `MemoryCache` each process has its own window; under a shared
`RedisCache` the fleet has one, and "five failures in a minute" means across
all workers ([Step 4](#step-4--choose-the-cache-backend)).

## Ordering the three

**Breaker outermost, rate limiter next, retry innermost.**

```
breaker.allow_request()
    limiter.acquire(tenant_id)
        with_retry(...)   →  inner.extract(...)
    ↑
breaker.record_success() / record_failure()
```

Retrying *inside* an open breaker is the wrong nesting in both directions.
Put retry outside the breaker and every retry pays a `CircuitOpen` round trip
for a call the breaker already decided not to make — you have built a busy
loop whose only output is log lines. Put the limiter outside the breaker and
a refused-without-calling request consumes a tenant's allowance, so the
allowance drains while no work happens.

The nesting above has each layer answering the question the next one down
should not be asked: *should we call at all* (breaker), *are we allowed to
right now* (limiter), *did the wire drop* (retry).

## Full example

```python
import asyncio
import logging
from uuid import UUID

from pydantic import BaseModel

from redstring import InMemoryGraphStore, LlmProvider, SourceDocument, build_graph
from redstring.llm.adapters.langchain import LangChainLlmProvider
from redstring.llm.circuit_breaker import CircuitBreaker, CircuitOpen
from redstring.llm.rate_limiter import RateLimitExceeded, RateLimiter
from redstring.llm.retry import ExtractionRetryPolicy, RetryExhausted, with_retry

logger = logging.getLogger(__name__)


class HardenedLlmProvider:
    """An `LlmProvider` that survives a flaky model server."""

    def __init__(
        self,
        inner: LlmProvider,
        *,
        tenant_id: UUID,
        limiter: RateLimiter,
        breaker: CircuitBreaker,
        retryable: tuple[type[Exception], ...],
    ) -> None:
        self._inner = inner
        self._tenant_id = tenant_id
        self._limiter = limiter
        self._breaker = breaker
        self._call = with_retry(
            retryable_exceptions=retryable,
            policy=ExtractionRetryPolicy(max_retries=3, initial_delay=1.0),
        )(self._inner.extract)

    @property
    def model(self) -> str:
        return self._inner.model

    async def extract[S: BaseModel](
        self, text: str, schema: type[S], *, system_prompt: str | None = None
    ) -> S:
        if not await self._breaker.allow_request():
            raise CircuitOpen(
                f"{self.model} is unavailable",
                retry_after=await self._breaker.retry_after(),
            )

        try:
            await self._limiter.acquire(self._tenant_id)
        except RateLimitExceeded as limited:
            await asyncio.sleep(limited.retry_after)
            await self._limiter.acquire(self._tenant_id)

        try:
            result = await self._call(text, schema, system_prompt=system_prompt)
        except RetryExhausted as exhausted:
            await self._breaker.record_failure()
            logger.error(
                "extraction failed after %d attempts",
                exhausted.attempts,
                exc_info=exhausted.__cause__,
            )
            raise
        else:
            await self._breaker.record_success()
            return result


async def main() -> None:
    tenant_id = UUID("00000000-0000-0000-0000-000000000001")
    provider = HardenedLlmProvider(
        LangChainLlmProvider.openai_compatible(
            base_url="http://192.168.1.14:8080/v1", model="qwen3.6-27b-mtp"
        ),
        tenant_id=tenant_id,
        limiter=RateLimiter(rpm=60),
        breaker=CircuitBreaker(failure_threshold=5, recovery_timeout=60.0),
        retryable=(ConnectionError, TimeoutError),
    )

    report = await build_graph(
        SourceDocument(...),
        provider=provider,
        store=InMemoryGraphStore(),
        tenant_id=tenant_id,
    )
    print(report)
```

Note what is *not* wrapped: `EmptyCompletionError`, `MalformedCompletionError`
and `RefusedCompletionError` are not in `retryable`, so they propagate out of
`_call` unretried — and past the `except RetryExhausted` clause, which means
they also do not count as breaker failures. That is deliberate for the first
two (the server answered; the request is the problem) and a judgement call for
the third — add `record_failure()` for `RefusedCompletionError` if a bad key
should take the circuit down.

## Step 4 — Choose the cache backend

Both the limiter and the breaker keep their state behind the `Cache` port, and
both default to `MemoryCache` when you pass no `cache=`. See
[ADR 0013: resilience behind the cache port](../adr/0013-resilience-behind-the-cache-port.md)
for why the state went behind a port at all.

### `MemoryCache` is the default and genuinely limits

It is a dict, single-process, no I/O, no dependencies. It is not a test
double: with one worker, `RateLimiter(rpm=60)` over `MemoryCache` enforces 60
calls a minute correctly, and the breaker's state machine is fully functional.
Its one limitation is that **processes cannot agree** — nothing is shared
across them.

### Swap to `RedisCache` when workers must share

Use Redis when several workers must draw on one allowance or trip together.
Without it each worker gets its own window, so the effective limit is `rpm` ×
worker count, and a failing model is discovered separately by every one of
them.

```python
from redstring.llm.cache.redis import RedisCache

cache = RedisCache.from_url("redis://localhost:6379/0")
limiter = RateLimiter(rpm=60, cache=cache)
breaker = CircuitBreaker(cache=cache)
```

`from_url` needs the `redis` package and raises `ImportError` without it. It
builds the client, sets `decode_responses=True`, and marks the cache as owning
it.

### Bringing your own client

```python
import redis.asyncio

client = redis.asyncio.from_url(url, decode_responses=True)  # required
cache = RedisCache(client, owns_client=False)
```

**`decode_responses=True` is mandatory.** The port says `get` returns
`str | None`; a client left at its default returns `bytes`, and the breaker's
state comparison then silently never matches — passing every test against
`MemoryCache` and failing only in the deployment that has Redis. That is the
whole failure mode: no exception, just a circuit that never opens.

`owns_client` defaults to `False`, so `close()` leaves your client alone. A
shared client closed by whichever component finished first is a bug that only
appears under shutdown.

### One cache or two, and shutting down

The limiter and the breaker use different key prefixes (`"kg:ratelimit"` and
`"kg:circuit"`), so one cache instance serves both safely. Separate caches are
also fine — use them when you want the two to live in different Redis
databases.

`RateLimiter.close()` and `CircuitBreaker.close()` both call through to the
cache's `close()`. That is only meaningful for a cache the object created
itself: **if you pass one cache to both and call `close()` on each, the second
call closes an already-closed cache.** Own the lifecycle yourself instead —
construct the cache, pass it in, and close the cache at shutdown.

## Verifying your wiring

Do not assume the layers are connected. Prove each one can fire:

1. **Force the breaker open.** Point the provider at a dead port and extract
   in a loop. After `failure_threshold` failures,
   `await breaker.state()` should read `CircuitState.OPEN` and the next
   `allow_request()` should return `False` *without* a connection attempt —
   which you can see as the call returning far faster than the connection
   timeout.
2. **Confirm the breaker recovers.** Bring the server back and wait
   `recovery_timeout`. The next call is admitted as a probe; on success,
   `state()` reads `CLOSED` again.
3. **Confirm the limiter is per tenant.** Exhaust tenant A's window until
   `acquire` raises `RateLimitExceeded`, then call `acquire` for tenant B in
   the same process. It must succeed, and `await limiter.remaining(b)` must
   still be near `rpm`. If B is blocked too, your key prefix or your tenant id
   is wrong.
4. **Confirm retry is not swallowing a permanent error.** Feed the provider a
   request that produces `MalformedCompletionError` and time it — it must fail
   immediately, not after the full backoff schedule.

## Reference: the exceptions

| Exception | Module | Base | Extra attributes |
|---|---|---|---|
| `RetryExhausted` | `redstring.llm.retry` | `Exception` | `message`, `attempts`; real error on `__cause__` |
| `RateLimitExceeded` | `redstring.llm.rate_limiter` | `RedstringError` | `retry_after` (seconds until a slot frees) |
| `CircuitOpen` | `redstring.llm.circuit_breaker` | `RedstringError` | `message`, `retry_after` (estimated) |

`RetryExhausted` is the odd one: it derives from `Exception`, not
`RedstringError`, so `except RedstringError` does not catch it. None of the
three is exported from `redstring` — all three are reached by dotted path, as
above.

## Related reading

- [ADR 0013: resilience behind the cache port](../adr/0013-resilience-behind-the-cache-port.md)
  — why the limiter and breaker store state through `Cache`.
- [ADR 0008: the two non-store ports](../adr/0008-the-two-non-store-ports.md) —
  why `Cache` and `LlmProvider` are as small as they are.
- [Implement a store adapter](implement-a-store-adapter.md) — for writing your
  own `Cache` or `LlmProvider` and proving it against the compliance suite.
- [Domain value types](../reference/domain-value-types.md) — `TenantId` and the
  rest of the id vocabulary.
- [README](https://github.com/tyevans/redstring/blob/main/README.md) — the public surface these pieces sit behind.
