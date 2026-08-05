"""Does extraction find the right things? — the only suite that asks.

Every other tree here checks that the library is **correct**: invariants hold,
adapters agree on their ports, events replay to the same graph. Correct and
accurate are different properties, and extraction can satisfy every invariant
in `tests/unit/` while returning entities that are simply wrong. This package
is the measurement that would notice.

## Three parts, and the split is the point

| Module | Needs | Runs |
|---|---|---|
| `scoring.py` | nothing | commit gate, via `tests/unit/accuracy/test_scoring.py` |
| `corpus.py` + `corpus.yaml` | nothing | commit gate, via `tests/unit/accuracy/test_harness.py` |
| `test_extraction_accuracy.py` | a live model | `-m accuracy`, deliberately |

B12 sat open for eleven slices because "measure extraction accuracy" reads as
one job needing a model, a corpus and a metric at once. It is two jobs: deciding
whether a predicted entity *is* an expected one, which needs nothing and is
where a wrong answer is silent, and getting predictions, which needs everything.
Only the metric can be tested cheaply, and only the metric has to be right for
any of the numbers to mean anything.

**So the harness is proved before it is believed.** Both silent failures of an
accuracy suite produce a plausible number — measuring nothing gives F1 = 0.0 and
reads as a bad model; comparing the corpus against itself gives 1.0 and reads as
a good one. `tests/unit/accuracy/test_harness.py` runs the whole pipeline
against `FakeLlmProvider` and pins both directions, in the commit gate, with no
endpoint. That is the same rule the mutation runbook states as "a zero-survivor
run means the harness is broken".

## The corpus is a starter, not a benchmark

Five short hand-graded documents. Enough to answer "is extraction working"; not
enough to answer "how well". `corpus.yaml` carries the grading rules, and the
floors in `test_extraction_accuracy.py` are set where a regression trips them
rather than where a good model sits — a floor tuned to the current endpoint is
a test of that endpoint.

## Running it

    KG_LLM_BASE_URL=http://host:8080/v1 uv run pytest -m accuracy tests/accuracy/

The probe runs a real extraction and requires a real entity back. Its
predecessor probed a *model list*, the model was listed, the weights would not
load, and eight tests failed instead of skipping — which is the origin of the
rule now stated for every integration suite in this repository.
"""
