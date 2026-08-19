# Mutation and measurement runs, August 2026 (historical)

> **This is a record of runs that are over, not a description of open work.**
> Every session, table and survivor classification below was finished; what is
> still to be run, and the invocation to run it with, stayed in `BACKLOG.md`
> under the entry each block came from. Read it when a number here is cited and
> you want to know how it was obtained, or before re-running a region so you
> know what the last run found.
>
> Moved verbatim out of `BACKLOG.md` on 2026-08-18, because a file about open
> work had accumulated 250 lines of completed run reports. Nothing here was
> rewritten in the move. `.claude/rules/commits.md` says counts, file tables
> and survivor lists belong in a commit message for exactly this reason — these
> predate that habit, so they get a page instead.

## `temporal_parsing.py` — the four completed cosmic-ray sessions

From `BACKLOG.md` B54, which keeps the region table, the wrapper invocation,
the timeout finding and what remains unrun. Of the module's 850 mutants these
four sessions account for the 391 counted as verified.

**The range run: 268 mutants, 22 survivors, all classified.**

- **16 equivalent by construction** -- `_Parsed | None` in two return
  annotations, rewritten as `+`, `%`, `^`, `**` and so on. PEP 563 makes
  annotations strings that are never evaluated; unkillable here and anywhere.
- **1 equivalent** -- `name != "September"` as `is not`, over month names that
  are module-level literals and therefore interned. It is CLAUDE.md's row-one
  trap sitting in the tree, equivalent only because every operand is a
  literal, and it would stop being equivalent the moment a spelling arrived
  from anywhere else.
- **4 test gaps, now closed** -- a year range and a month range with *equal*
  endpoints (`end < start` widened to `<=` returned `None` and nothing
  noticed), and a quarter range *starting* at Q3 or Q4. The last is the
  instructive one: `(first - 1) * 3 + 1` and `(first >> 1) * 3 + 1` agree for
  Q1 and Q2 and differ for Q3 and Q4, and the existing cases were `Q1-Q2` and
  `Q2-Q4` -- so the range's *end* was covered at Q4 while its *start* was
  blind, which reading the parameters does not reveal.
- **1 real defect, fixed** -- see below.

**The defect: `_MONTH_NUMBERS` carried a spelling `_MONTH` could not produce.**
The spelling table has exactly one conditional, whose entire purpose is to add
"Sept" for September. The `_MONTH` pattern accepted `Sep(?:tember)?` and not
"Sept", so that entry was **unreachable** -- "Sept 2024" fell through every
pattern to `dateparser`, resolved differently against the two probe dates, and
raised `AmbiguousReferenceDateError` instead of parsing. Two declarations of
one fact with nothing failing while they disagreed, and it could only have
been found this way: mutating the branch changed nothing observable, because
no input reached it.

Fixed in the pattern rather than by deleting the entry -- "Sept" is ordinary
text and the table's intent was plainly to accept it -- with
`test_every_spelling_the_table_maps_is_one_the_pattern_accepts` as the gate,
proved red by reverting the pattern.

**The period/century run: 176 mutants, 28 survivors, all classified.**

- **11 equivalent by construction** -- the `_Parsed | None` return annotation
  again.
- **2 equivalent, and worth understanding rather than pattern-matching** --
  `base + 1` rewritten as `base | 1` and `base ^ 1`. `base` is
  `(century - 1) * 100`, always a multiple of 100 and therefore always even,
  so bit 0 is clear and all three spellings agree for *every* century. Not
  "equivalent on the inputs we test": equivalent, full stop.
- **15 test gaps, now closed.** Four on the `century < 1` guard and eleven on
  the portion arithmetic.

**The century arithmetic could not be tested at the 19th century at all**, and
that is the finding worth carrying. `(19 - 1) * 100` is 1800, which shares no
set bit with 1, 33, 34, 66, 67 or 100 -- so `base + k`, `base | k` and
`base ^ k` are *the same number* for every constant in the table. And
`century - 1` equals `century ^ 1` for any odd century. Every existing case
used the 19th century, which is the natural example for a library that reads
historical text, and it made eleven mutants unkillable. The 20th century
(base 1900) breaks every one of those coincidences.

The guard needed its own boundary: `century < 1` widened to `< 2` rejects
"early 1st century", and the first version of that test used plain
"1st century" -- which `_CENTURY` matches and which never reaches the guard at
all, so it passed against the mutant. **A boundary test has to reach the
branch the boundary is in.**

**The render run: 159 mutants, 7 survivors, all classified.** The smallest
survivor count of the four, and the classification is most of the value.

- **4 equivalent by construction** -- the `str | None` return annotation on
  `render_temporal`, the PEP 563 shape again.
- **1 equivalent, and provably so** -- `start != datetime(start.year,
  start.month, start.day, ...)` rewritten as `>`. The two differ only where
  `start` is *below* the midnight of its own date, which no datetime is.
- **1 equivalent for the declared type** -- `precision is DatePrecision.YEAR`
  as `==`. Enum identity and equality agree; the two would part only for an
  argument the annotation forbids.
- **1 test gap, now closed** -- and it is CLAUDE.md's row about intervals
  whose bounds never coincide, second instance, in a different module.

**The gap: `end.year <= start.year` survived being rewritten as `is`.**
`TestRenderDeclines` *does* carry a year-range case, `2023-01-01` to
`2023-06-01` -- but June 1 is not the first of its year, so the *first* clause
of the `or` answers and the comparison is never what decides. Identity is
false for every distinct `int` object, so under the mutant `"2023-2023"`
rendered as a range the parser then refuses to read back. Closed with a
coincident-endpoint case, proved by hand-applying the mutant under
`PYTHONDONTWRITEBYTECODE=1`.

Note what is *not* testable there: `TemporalExtent` rejects `end < start` at
construction, so the `<` half of `<=` is unreachable and a coincident case is
the whole of what an assertion can reach.


## The constrained-decoding measurement that was retracted

From `BACKLOG.md` B57, which keeps the current (identical-in-both-arms) result
and the two limits of the instrument. This is the *first* run, which read a
confounder as a mechanism: it was made with the model thinking, and the false
positives it invented a "checklist" story to explain turned out to be the
reasoning trace, not the constraint.

**What follows is the first measurement, kept because being wrong this way is
the lesson.** It ran with the model thinking, and read a confounder as a
mechanism.

Graded corpus, `qwen3.6-27b-mtp`, `temperature=0.0`, **thinking on**:

| | entity tp | entity fp | entity fn | rel tp | rel fp | rel fn |
|---|---|---|---|---|---|---|
| unconstrained | 12 | **8** | 0 | 5 | **6** | 1 |
| constrained | 12 | **13** | 0 | 5 | **7** | 1 |

Recall is identical and perfect in both arms. Precision is worse constrained.

**The mechanism is the part worth keeping, because it is the opposite of the
intuition.** An enum does not only forbid the types outside it -- it *advertises*
the types inside it, and the model treats the list as a checklist. On
`newsroom-event`, an 81-character sentence about a summit, the unconstrained
run emitted four entity types and the constrained run emitted **all nine the
`news_journalism` schema declares** -- inventing a `claim`, a `date`, a
`quote`, a `source` and a `statistic` the text does not contain. The entire
5-point rise in false positives is that one document.

So the trade is not "coverage for consistency" as ADR 0030 and 0011 both
describe it. It is that, *plus* a hallucination pressure proportional to how
many types the schema declares and how few of them the document contains.
A nine-type schema against a one-sentence document is the worst case, and it
is not a rare one.

**And that mechanism explains nothing, which is the correction.** The false
positives it was invented to account for were the *reasoning trace* inventing
entities; they vanished when thinking was turned off, not when the constraint
was removed. The "checklist" story was persuasive enough to reach an ADR, this
entry and a documentation warning before the confounder surfaced a day later.

**A mechanism inferred from one measurement is a hypothesis, however well the
story fits.** When a result comes with a satisfying explanation, the
explanation is the part to distrust -- it is what stops you looking for the
variable you did not control.

## The off-corpus carryover A/B

From `BACKLOG.md` B115, which keeps what a graded multi-chunk document has to
satisfy. This measurement is why that entry knows what to ask for — and it is
also the run that turned out to be a control mistaken for a measurement, since
the document was one chunk.

Worth keeping, because whoever writes the graded document should know what to
expect and what the trap is. A 2831-character Lovelace/Babbage passage,
chunked at 900/100 into three chunks, `qwen3.6-27b-mtp`, each arm run twice:

| | entities | relationships | fragment pairs | one name under two types |
|---|---|---|---|---|
| `carryover_entities=0` | 53 | 54 | 8 | 4 |
| `carryover_entities=32` | 51 | 58 | 7 | 0 |

Both repeats of each arm were **byte-identical**. That is not a suspicious
result here — `LangChainLlmProvider.openai_compatible` defaults to
`temperature=0.0`, so identical prompts give identical completions — and it
settles something useful: the run-to-run noise floor on this rig is *zero*, so
the whole of the difference above is attributable to the carryover and none of
it to sampling.

**It does not follow that this generalises.** Zero variance means repeating
the run tells you nothing new; it does not turn one document into a sample.
The direction matches what the mechanism predicts, and that is all it is.

The clearest signal is the last column, not the first. The off arm produced
the *same name under two different entity types* four times — `Analytical
Engine`, `Engine`, `1871` and `funding` each appearing twice with different
types, which is two ids for one thing — and the on arm produced none. That is
the defect a `(name, entity_type)` carryover is shaped to fix, and it is worth
grading for explicitly: **a graded document should contain at least one entity
whose type a later chunk would plausibly assign differently.**

The off arm also emitted `ine`, a truncated fragment, and
`article on the Analytical Engine` as an entity in its own right. Neither is
about the carryover; both are worth remembering when grading, because a corpus
that never sees them cannot measure whether anything fixed them.
