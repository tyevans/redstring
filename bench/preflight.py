"""Refuse to start, rather than produce a plausible number.

`scripts/mutation.py` exists because an environment lying about the code is
undetectable from the output. A benchmark has the same hazard with the sign
flipped: a broken run is not slow, it is *fast*. A pipeline that extracts
nothing from a 100k-character document finishes in seconds and wins every
grid it appears in, and the reading is "the new chunk size is a huge win".

Four checks, each of which the three others cannot see:

- **Both model ids are listed.** Not "the endpoint answers": llama-swap lists
  every model it is configured for, and serving one of two produces a run
  that half works.
- **A real completion comes back non-empty.** A listed model whose weights
  will not load answers with nothing. BACKLOG B12 is this repository's
  standing example of trusting a model listing.
- **An embedding is the configured width.** A different embedding model
  behind the same id is a silent dimension change.
- **A warm-up extraction produces at least one entity.** Everything above can
  pass while extraction returns an empty graph.

Probes are injected so each refusal is unit-tested without a network. A gate
whose happy path is "the endpoint answered" has to be watched failing before
it is believed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from bench.config import BenchConfig


class PreflightError(Exception):
    """The endpoint cannot produce a measurement worth recording."""


@dataclass(frozen=True, slots=True)
class Probes:
    """The four questions asked of the endpoint before anything is timed."""

    list_models: Callable[[], Sequence[str]]
    complete: Callable[[], str]
    embed: Callable[[], Sequence[float]]
    warm_up_entities: Callable[[], int]


def _attempt[T](what: str, endpoint: str, probe: Callable[[], T]) -> T:
    try:
        return probe()
    except PreflightError:
        raise
    except Exception as error:
        raise PreflightError(f"{what} failed against {endpoint}: {error!r}") from error


def preflight(config: BenchConfig, probes: Probes) -> None:
    """Check the endpoint can produce a measurement, or raise saying why.

    Raises:
        PreflightError: Naming which check failed and what it saw.
    """
    served = list(_attempt("model listing", config.endpoint, probes.list_models))
    for model in (config.extraction_model, config.embedding_model):
        if model not in served:
            raise PreflightError(
                f"{config.endpoint} does not serve {model}; it lists {sorted(served)}"
            )

    completion = _attempt("completion probe", config.endpoint, probes.complete)
    if not completion.strip():
        raise PreflightError(
            f"{config.extraction_model} is listed but returned an empty completion; "
            "a listed model whose weights will not load looks exactly like this"
        )

    vector = _attempt("embedding probe", config.endpoint, probes.embed)
    if len(vector) != config.embedding_dimensions:
        raise PreflightError(
            f"{config.embedding_model} returned {len(vector)} dimensions, "
            f"config expects {config.embedding_dimensions}"
        )

    if _attempt("warm-up extraction", config.endpoint, probes.warm_up_entities) < 1:
        raise PreflightError(
            "the warm-up extraction produced no entities; every timing below would "
            "measure a pipeline that extracts nothing, which is the fastest run there is"
        )
