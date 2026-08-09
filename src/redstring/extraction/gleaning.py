"""Asking a second time, having shown the model its own first answer.

A model reading a dense paragraph names the entities it notices and stops.
It does not stop because the paragraph is exhausted; it stops because the
answer feels complete. Shown that answer and asked what is missing, the same
model on the same text reliably finds more -- which is Microsoft GraphRAG's
"gleaning" and, in a different dress, the reflexion step Graphiti runs after
its first extraction pass.

The effect is largest exactly where this library is weakest: a long chunk with
many entities, where recall falls off toward the end of the text.

## What this costs, and why it is therefore off by default

One extra model call per chunk per pass. On the reference deployment -- a
single-GPU llama.cpp server processing one request at a time -- that is a
doubling of wall-clock for the whole document, and the pipeline is already
sequential over chunks for the same reason. Recall is worth paying for and it
is not worth paying for *by accident*, so `ExtractionPipeline` takes
`gleanings=0` and a caller asks.

## Re-listing is free here, and that is why the prompt is mild

`carryover.py` has to forbid the model from repeating what it is shown,
because a repeat there becomes an entity attributed to a chunk that never
mentioned it. Here the opposite holds: a glean pass that repeats the whole
first answer is *merged with it*, and `map_extraction` deduplicates by derived
id, so a repeat costs tokens and changes nothing. The instruction can
therefore ask plainly for what was missed without having to defend against
being obeyed too literally -- and an over-cautious prompt is the failure mode
that matters, since a model told sternly not to repeat itself also declines to
mention the entity it now realises it mis-typed.

## Combining happens before mapping, not after

`combine` returns an `Extraction` -- the wire shape -- rather than merging two
`MappedExtraction`s. That is not a stylistic choice about where to put a fold:
`_map_relationships` resolves an endpoint name against the entities in *the
same answer*, so an edge the glean pass states between one entity it found and
one the first pass found is resolvable only if the two answers are a single
`Extraction` by the time the mapper sees them. Merged afterwards, that edge is
counted `unresolved` and dropped -- and edges spanning the two passes are a
large fraction of what a second pass is for.
"""

from __future__ import annotations

from redstring.extraction.schema import Extraction

#: How many already-found entities are named back to the model.
#:
#: Larger than `carryover`'s bound, because this list is doing a different
#: job: the carryover is a spelling aid and can be sampled, while this is the
#: thing the model is being asked to find gaps in. A truncated list invites it
#: to "find" entities it already reported, which is harmless but wasteful. One
#: chunk that genuinely holds more than this many entities is past the point
#: where a second pass is the right tool.
MAX_LISTED = 100

_INSTRUCTION = (
    "You have already extracted the following from this text:\n"
    "{found}\n"
    "\n"
    "Read the text again. Some entities and relationships it states were "
    "missed -- minor people, organisations, places, works and events "
    "mentioned in passing are the usual omissions, as is anything stated "
    "near the end of the text.\n"
    "\n"
    "Return what was missed, in the same format. It is not a problem to "
    "repeat something already listed above. Still extract only what the text "
    "says: if nothing was missed, return empty lists."
)

_NOTHING = "(nothing)"


def gleaning_prompt(base_prompt: str, found: Extraction) -> str:
    """The system prompt for a second pass over text already extracted.

    Args:
        base_prompt: What the first pass was told. Kept in front, so the rules
            that shaped the first answer -- a domain's vocabulary above all --
            still apply to the second. A gleaning prompt that replaced it
            would extract the same chunk under two different specifications
            and merge the results.
        found: The first pass's answer, named back to the model.

    Returns:
        A prompt. `base_prompt` unchanged, then the instruction and the list.
    """
    return f"{base_prompt}\n\n{_INSTRUCTION.format(found=_describe(found))}"


def _describe(found: Extraction) -> str:
    """The first answer as a list, entities then relationships.

    Names and types only -- not descriptions, properties or confidences. The
    model is being asked "what is missing", and a verbose recitation of what
    is present crowds out the text it is supposed to be re-reading. Both lists
    are bounded for the same reason.
    """
    lines = [f"- {entity.name} ({entity.entity_type})" for entity in found.entities[:MAX_LISTED]]
    lines += [
        f"- {edge.source_name} -{edge.relationship_type}-> {edge.target_name}"
        for edge in found.relationships[:MAX_LISTED]
    ]
    return "\n".join(lines) if lines else _NOTHING


def combine(first: Extraction, extra: Extraction) -> Extraction:
    """One `Extraction` holding both answers, first pass first.

    Order is load-bearing rather than incidental. `map_extraction` resolves a
    tie between two mentions of one entity with `domain.preference`, which is
    a *total* order -- so the result does not depend on this order at all, and
    stating that is the point: a glean pass that re-reports an entity with a
    lower confidence cannot demote the first pass's mention, and one that
    reports it with a higher confidence promotes it whichever way round they
    are folded.

    Nothing is deduplicated here. The mapper's dedup is by derived id, which
    is the only definition of "the same entity" this library will accept, and
    a second one applied first could only disagree with it.
    """
    return Extraction(
        entities=[*first.entities, *extra.entities],
        relationships=[*first.relationships, *extra.relationships],
    )


def found_nothing(extraction: Extraction) -> bool:
    """True when a pass reported no entities and no relationships.

    The pipeline's stop condition, and it is checked on the *pass* rather than
    on the combined result -- which is the difference between "this pass added
    nothing, stop asking" and "there is nothing here", the second of which is
    false as soon as the first pass found anything.
    """
    return not extraction.entities and not extraction.relationships
