# ADR 0043: A query is embedded differently from a document

## Status

Accepted.

**Amends [`0017` the embedding provider port](0017-the-embedding-provider-port.md).**
`EmbeddingProvider` gains `embed_query`, and both adapters gain a document and
a query task prefix. 0017's decisions stand — the port is still separate from
`LlmProvider`, still narrow, still declares its dimension on both sides, and
`embed_query` carries the same batch and positional contract `embed` does.

**It also extends 0017's identity argument**, which is the half a reader is
most likely to need: 0017 records that changing embedding model means a new
store, because two models' vectors are not comparable even at equal
dimensions. The *task prefix belongs to that identity*, so changing a document
prefix has the same consequence as changing the model. "A prefixed corpus and
an unprefixed one are not comparable" below states it in the terms a caller
will meet it.

[`0012` no ANN index in a multi-tenant vector store](0012-no-ann-index-in-a-multi-tenant-vector-store.md)
**stands**. [`0038` the chunk's vector lives on the chunk](0038-the-chunks-vector-lives-on-the-chunk.md)
**stands**: chunk vectors are corpus vectors and are unaffected in shape; what
changes is which text is sent to produce them.

**[`0002` two store ports](0002-two-store-ports.md) stands, and is not
amended** — deliberately, because it is the ADR a reader is sent to for this
rule and it does not contain it. See "Where the new-store rule actually lives"
below.

## Context

**Modern embedding models are asymmetric.** They are trained with a task prefix
on the input, and they expect a *different* one depending on whether the text
is being stored or being searched for. `nomic-embed-text-v1.5` wants
`search_document: ` on corpus text and `search_query: ` on a query. The BGE
family wants an instruction line on the query and nothing at all on the
document. E5 uses `passage: ` and `query: `. Which strings a model wants is the
model's business; that it wants two of them is now the common case.

`EmbeddingProvider` was a single `embed(texts)` with no notion of which side it
was serving, **so the port could not express this at all.** The distinction had
to live in the caller: every call site prepends the right string, or does not.

**The failure is silent, and that is the whole of the argument.** A corpus
embedded with no prefix produces vectors that are well-formed, finite, of the
declared width, distinct from one another, and clustered in a way that looks
entirely reasonable. Nothing raises. Every assertion in the compliance suite
passes. Cosine scores come back in a plausible range and the top result is
usually related to the query. The only symptom is retrieval quality somewhere
below what the model can do — which does not read as a defect. It reads as
*"this model is mediocre"*, and the natural next step is to try a different
model, or to tune `k`, or to blame the fusion weights.

This is not hypothetical. A downstream consumer — a STaRK benchmark harness
built on this library — embedded an entire corpus unprefixed and reported
retrieval numbers materially below the model's published figures. The run was
believed, and the time went into everything except the prefix.

## Decision

**`EmbeddingProvider` gains `embed_query(texts) -> list[list[float]]`, with the
same contract as `embed` in every other respect.** Batch, positional, one
vector per input in input order, `len(result) == len(texts)`, each of length
`dimension`, and an empty input makes no request. `embed` is the corpus side;
`embed_query` is the query side. For a symmetric model the two are one
function, which is a legitimate adapter and not a degenerate one.

**Both concrete adapters take keyword-only `document_prefix` and
`query_prefix`, defaulting to empty**, and
`LangChainEmbeddingProvider.openai_compatible` threads them through. Empty
defaults mean this is not a behaviour change for any existing caller. The
factory matters as much as the constructor: most callers reach a model through
it, and a parameter available only on `__init__` would be available to nobody.

**Prefixes are not folded into `model`.** See the section on provenance below.

### Why a port method rather than a wrapper the caller composes

The rejected alternative is a `PrefixedEmbeddingProvider` decorator, applied
twice — once with the document prefix around the indexing path, once with the
query prefix around the retrieval path. It is less code here, it needs no port
change, and it would work.

It would work *when wired correctly*, and correct wiring would be a convention.
The two call-site families are already segregated by role, so nothing enforces
that the retrieval path got the query-flavoured wrapper and the indexing path
got the document-flavoured one; a caller who wired one wrapper into both paths
has a working, silent, worse system. That is
`.claude/rules/recurring-defects.md` §3 exactly — *a rule that holds only
because nobody has broken it is indistinguishable from no rule* — and it is
worse than the usual instance of §3, because the check a reviewer would apply
("is the prefix there?") passes while the check that matters ("is it the *right
one*?") has no observable.

A port method makes the distinction structural. The type says there are two
sides; a call site picks one by name; and the compliance suite can require both
of them to satisfy the contract, which a wrapper composed outside the library
can never be required to do. The cost is real and is paid once: every adapter
gains a method, including adapters written in other people's repositories
against `redstring.testing`.

**The second-order reason is that it puts the prefix where the model is.** The
adapter is the object that already knows which model it is talking to; the
prefix is a property of that model in exactly the way the dimension is, and
0017 already argued the dimension belongs on the adapter rather than in the
caller's head. A wrapper would put half of a model's identity outside the
object that names it.

### Why `embed_query` is batch, and why it does not use `aembed_query`

A query is usually one string, so a `embed_query(text: str) -> list[float]`
signature is tempting. It is refused for two reasons. A caller expanding a
question into paraphrases, or scoring a batch of evaluation queries, pays per
*request* — 0017's whole reason for a batch port applies unchanged. And keeping
the two signatures identical means a decorator over this port — a cache, a
retry budget, a `CallLimiter` — wraps both the same way instead of needing two
shapes.

`LangChainEmbeddingProvider.embed_query` therefore routes through
`aembed_documents`, **not** LangChain's `aembed_query`, which takes a single
string. Using it would turn one batch into one HTTP request per text: the exact
optimisation the port exists to keep reachable, lost to a method name that
looked like it matched. Same client, same endpoint, different prefix. There is
a comment saying so at the call, because the mismatch between the two
`*_query` names is the first thing a reader will question.

## A prefixed corpus and an unprefixed one are not comparable

**This is the consequence most likely to bite, and it is not about code.**

Take one model, one dimension, one store. Embed the corpus with
`search_document: `. Embed the same corpus again without it. The two sets of
vectors have the same width and the same provenance string and they are **not
in the same space** in the way that matters: a query vector that ranks well
against one ranks arbitrarily against the other, and the two sets mixed in one
collection produce scores that are meaningless across the boundary. Nothing
about the vectors themselves says which is which.

This is precisely the situation ADR 0017 describes when it says two models'
vectors are not comparable even at equal dimensions, and the conclusion is
identical: **the prefix is part of the embedding model's identity, so changing
it means a new store, not a re-embed of some rows.**

The operational rule, stated once so a future reader hits it:

> Changing `document_prefix` — including setting it for the first time on a
> populated store, or clearing it — invalidates every vector already stored.
> Treat it as changing the model: build a new store and re-embed, do not mix.

`query_prefix` is different in kind and worth separating, because the symmetry
of the two constructor arguments hides it. Nothing is *stored* from the query
side, so changing `query_prefix` invalidates nothing and takes effect on the
next query. It is still not free: a query prefix that does not match the
document prefix the corpus was built with is the original defect, arriving from
the other direction.

### Where the new-store rule actually lives

**The rule this ADR extends is not in ADR 0002, and following the citation
chain is how anyone finds that out.** 0017 writes "ADR 0002 already records
that changing embedding model means a new store, not an in-place change", and
0002 records no such thing: it is about the two store ports, the absent
`delete_entity`, and the compliance gates over both. The sentence's real home
is `src/redstring/ports/vector_store.py`, restated in
`src/redstring/domain/exceptions.py` and in 0017 itself.

**So 0002 is not amended here**, and the temptation to amend it was real —
this ADR extends a rule, a reader plainly needs to meet that rule, and 0002 is
where a reader is pointed. Amending it would have written a decision about
embedding comparability into the Status of an ADR that never made one, which is
worse than the navigation problem it fixes: the ADRs are a historical record,
and a page acquiring an amendment for a decision it does not contain makes the
record wrong in a way no later reader can unpick.

What is done instead: **0017's Status carries the amendment**, because 0017 is
where the rule is actually argued; and 0002 gets a pointer saying the rule is
not there and where it is, which is a correction to an inbound citation rather
than a change to anything 0002 decided. The port docstring that repeated the
mis-citation now names 0017.

### Provenance: the prefixes stay out of `model`

`EmbeddingProvider.model` is the provenance string recorded next to a vector,
and the obvious move — given everything above — is to fold the prefixes into
it, so that `"ollama/nomic-embed-text"` becomes
`"ollama/nomic-embed-text+search_document: "` and the identity is
self-describing. **Rejected**, and this is the one call in this ADR that could
reasonably have gone the other way.

Three reasons, in the order they matter:

1. **It encodes a string chosen for a model into a value used as a key.** The
   provenance string is compared, grouped by, and written into stores and
   events. Prefixes contain spaces, colons, and — for BGE — an entire English
   instruction sentence. A provenance value that may contain
   `"Represent this sentence for searching relevant passages: "` is not a value
   anyone wants in a `WHERE` clause or an event payload.

2. **It would be asymmetric or wrong.** Only the document prefix affects what
   is stored. Folding both in labels stored vectors with a query-side string
   that had no part in producing them; folding in only the document prefix
   gives a `model` that changes meaning depending on which method was called,
   from a *property* that is supposed to be constant.

3. **It buys detection that is available more directly.** The failure it would
   catch is a corpus built under two different prefixes, and a caller who wants
   that guarded should give the second configuration its own store — which the
   rule above already says — or record the prefix in their own deployment
   metadata, where it is a string they own rather than one this library
   defines the format of.

What is given up: nothing in the library will notice a store populated under
two prefixes. That is documented here, in the port's docstring, and in each
adapter's constructor, and it is the same standard of protection 0017 settled
for on the model itself — the dimension is checked mechanically because it can
be, and the model's identity is documented because it cannot.

## Consequences

**Every `EmbeddingProvider` implementation gains a method**, including any
written outside this repository. `redstring.testing.embedding_provider` is
parametrised over both sides rather than duplicated, so an existing adapter's
compliance subclass picks up the query-side cases with no edit — and fails
until the method exists, which is the intended way to find out.

**The compliance suite deliberately does not assert that the two sides
differ.** A symmetric model is legitimate. That the two prefixes are
distinguishable, and reach the client verbatim, is a claim about a *configured*
adapter, and is tested per adapter with two prefixes that are non-empty and
different from each other. Testing it with equal prefixes — `""` and `""` above
all — would be CLAUDE.md's failure-shape table in its purest form: with the two
strings equal, an implementation that swaps them is the same function.

**Retrieval moves and indexing does not.** `Retriever._semantic` and
`ChunkRetriever._semantic` call `embed_query`; `index_documents` (chunk text)
and `build_graph` (entity names) stay on `embed`. A fifth call site should have
to answer "which side is this?", and the answer is available from what the text
*is*, not from where the code lives.

**A caller pointing at nomic now has to say so**, since the defaults are empty
and the library cannot know which model is behind an OpenAI-compatible URL. The
alternative — defaulting to nomic's prefixes because that is what this project
benchmarks against — would silently corrupt every non-nomic deployment, which
is the same failure in the opposite direction and harder to find.
