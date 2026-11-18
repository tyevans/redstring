"""Limits on what a model may return, and on what a prompt may carry back.

A completion is untrusted data. The model may return a name of any length,
any number of entities, and names that contain line breaks. Three rules
apply, and each one is named here so a reader does not have to infer it:

1. **Clip, do not reject.** A name longer than `MAX_NAME_CHARS` is cut to
   that length. Rejecting it would make one long name discard the whole
   chunk's extraction, because `LlmProvider.extract` raises on a failed
   validation. Clipping keeps the rest of the answer.
2. **Drop, do not reject, past a count.** Entities and relationships beyond
   the count are dropped in the order the model gave them. The first ones
   are kept.
3. **Flatten before a prompt.** A name that reaches a later prompt has its
   control characters replaced by one space each run, and is then clipped.
   A name with a line break must not start a new line in the system prompt.

The limits are generous. They are not tuned to a model. A real entity name
is a few words, and a real chunk holds a few dozen entities.
"""

from __future__ import annotations

import re

#: Longest entity or relationship endpoint name kept, in characters.
MAX_NAME_CHARS = 200

#: Longest `entity_type` or `relationship_type` kept, in characters.
MAX_TYPE_CHARS = 100

#: Longest `description` kept, in characters.
MAX_DESCRIPTION_CHARS = 1000

#: Most property keys kept on one entity or relationship.
MAX_PROPERTY_KEYS = 50

#: Longest JSON text of one property value. A longer value is dropped whole,
#: not clipped: a clipped JSON value is not a value.
MAX_PROPERTY_VALUE_CHARS = 1000

#: Most entities kept from one completion. Chunks in this library are a few
#: thousand characters, and far fewer than this many entities fit in one.
MAX_ENTITIES_PER_EXTRACTION = 500

#: Most relationships kept from one completion.
MAX_RELATIONSHIPS_PER_EXTRACTION = 1000

# Control characters, the C1 range, and the two Unicode line separators. A
# model reads U+2028 and U+2029 as line breaks, so they are flattened too.
_CONTROL_RUN = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]+")


def clip(text: str, limit: int) -> str:
    """`text` cut to at most `limit` characters.

    Args:
        text: The text to cut.
        limit: The most characters to keep.

    Returns:
        `text` unchanged when it fits, otherwise its first `limit` characters.
    """
    return text[:limit]


def prompt_safe(text: str, limit: int) -> str:
    """`text` as one line of at most `limit` characters, fit for a prompt.

    Every run of control characters, including `\\n`, `\\r` and `\\t`, becomes
    a single space. The result is stripped and then clipped. The order matters:
    flattening first means the limit counts the characters the model reads.

    Args:
        text: A name or type that will be written into a prompt.
        limit: The most characters to keep.

    Returns:
        A string with no control characters and no leading or trailing space.
    """
    return clip(_CONTROL_RUN.sub(" ", text).strip(), limit)
