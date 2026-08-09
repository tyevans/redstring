# ADR 0031: Extraction does not think

## Status

Accepted. **Amends
[`0008` the two non-store ports](0008-the-two-non-store-ports.md) in its
consequences only**: the port is untouched, and this is an instance of the
rule 0008 already states -- the adapter absorbs the awkwardness of its
backend, and "this model reasons before answering" is exactly that kind of
awkwardness. [`0013` resilience behind the cache
port](0013-resilience-behind-the-cache-port.md) stands; this is a property of
one request, not of calling a model over a network.

## Context

`LangChainLlmProvider.openai_compatible` built a `ChatOpenAI` and sent
whatever the server's default behaviour was. On the reference deployment that
default is a *reasoning* model: `qwen3.6-27b-mtp` emits chain of thought
before its answer.

Three costs, and only the first was known.

**Latency.** Extraction was slow in a way that read as "the model is big".
Measured on one graded corpus document, identical prompt, `temperature=0.0`:
2789 completion tokens and 40.7 s of generation, of which the answer was about
260 tokens. The rest was thinking.

**Failure.** `DEFAULT_MAX_TOKENS` is 8192 and a reasoning trace can spend it
before `content` starts, which surfaces as `EmptyCompletionError` with
`finish_reason="length"`. That is the failure this repository already
documented in `ports/llm_provider.py` and had been treating as a budget
problem. It is a *thinking* problem with a budget symptom, and a call that
exhausts its budget is simultaneously the slowest call the model can make and
the one that returns nothing.

**Precision, which was the surprise.** Thinking did not make extraction more
accurate. It made it *less* accurate, by inventing entities the text does not
state.

## Decision

### `openai_compatible` sends `enable_thinking: false` by default

`NO_THINKING = {"chat_template_kwargs": {"enable_thinking": False}}`, passed as
`extra_body`. `thinking=True` restores the server's own behaviour.

The measurement, whole graded corpus, both arms in one run:

| | wall clock | entity tp/fp/fn | relationship tp/fp/fn |
|---|---|---|---|
| thinking | 155.1 s | 12 / 9 / 0 | 5 / 11 / 1 |
| no thinking | 27.3 s | 12 / **3** / 0 | 5 / **6** / 1 |

Recall is identical and perfect in both arms. Precision improves on both
entities and relationships, and the run is 5.7x faster. This is the only
change measured in this repository that improved every axis at once, which is
itself worth being suspicious of -- it was run against the same corpus, scorer
and floors as the constrained-decoding measurement that came out negative, so
the instrument is not one that flatters changes.

**Why the win is plausible rather than lucky.** Extraction asks for entities
the text *states*. A reasoning model given room to deliberate uses it to infer
— to decide that a chief executive is also a `person` and a `source` and a
`claim`, that a plant closure implies an `event`, that a company has a
`location`. Every one of those is a false positive under grading rule 1, which
grades what the text states and not what is true. The same mechanism that
makes reasoning valuable elsewhere is the mechanism that hurts here.

**`thinking=True` restores the server default rather than asserting
`enable_thinking: true`.** A backend with no chat template to pass kwargs to
rejects the field whichever value it carries, so the escape hatch has to be an
*absent* field. Sending the inverted flag would satisfy a test that only
checked the flag flipped, and would still fail against OpenAI.

**Rejected: a generic `extra_body` passthrough.** The constructor's docstring
already declines to become a second, worse copy of `ChatOpenAI`'s signature,
and that reasoning stands. `thinking` is one named field with a measurement
behind it; a caller needing anything else builds the chat model itself and
uses `__init__`, which is how this was measured before it was added.

**Rejected: raising `DEFAULT_MAX_TOKENS` instead.** It treats the symptom.
A bigger budget buys a longer reasoning trace and a slower call, and the
`EmptyCompletionError` returns for any document long enough.

**Rejected: leaving the default alone and documenting the flag.** The default
is what almost every caller gets, and a default that is 5.7x slower and less
precise is not a neutral choice merely because it is the server's.

### `__init__` is untouched

A caller who builds their own chat model gets exactly what they configured.
The escape hatch has to stay an escape hatch.

## Consequences

**Every caller of `openai_compatible` changes behaviour on upgrade**, without
changing a line. That is the point, and it is also the risk: a caller relying
on a reasoning model's deliberation for something other than extraction gets a
different answer. The mitigation is that this adapter serves one purpose --
`LlmProvider.extract` -- and that purpose is the one measured above.

**Backends with no chat template will reject the field**, OpenAI's own API
being the one to expect, with a 400 on the first call. `BACKLOG.md` B118
carries what is untested and what would close it. The failure is loud and
immediate, which is the property that made this default acceptable.

**Thinking-off is steadier, not deterministic.** The two thinking-off calls
above agreed where the thinking-on pair did not, and that is the whole of the
evidence for it: later corpus runs with thinking off returned 3 and then 4
entity false positives on the same documents. Reproducibility improved; it did
not arrive. Any comparison here still needs both arms in one run.

**Two earlier findings in this repository were measured with thinking on and
are weakened.** ADR 0030's conclusion that constrained decoding hurts
precision was measured against a noisier baseline (8-9 entity false positives,
where the library's baseline is now 3), and the carryover measurement recorded
under B115 assumed a zero noise floor on the strength of byte-identical
repeats. Neither assumption survives: two identical requests at temperature
zero returned 2789 and 2239 completion tokens and disagreed on entity count.
Both entries now say so. Re-running either is now cheap, which is the
underrated consequence of this change -- **the accuracy suite went from 15
minutes to 30 seconds, and a measurement nobody can afford to repeat is a
measurement nobody checks.**
