"""Blocking keys: which entities are worth comparing at all.

Comparing every pair of a tenant's entities is quadratic and unaffordable past
a few thousand. Blocking cuts it down: each entity carries a small set of
**keys**, and only entities sharing a key are ever scored against each other.
A key is a cheap, deliberately lossy summary -- a name prefix, a type, a
phonetic code -- chosen so that two records for the same real thing very
probably share one.

## The store computes nothing

Keys are computed here, at extraction time, and stored on the entity.
`GraphStore.find_by_blocking_key` only looks them up. That split is what makes
this pure domain logic rather than four backend-specific strategies:

| Was | Really is | Is now |
|---|---|---|
| `PREFIX` | a key function | `prefix_key` |
| `ENTITY_TYPE` | a key function | `entity_type_key` |
| `SOUNDEX` | a key function | `soundex_key` |
| `TRIGRAM` | a similarity search | `VectorStore.search` |

The fourth was never a key function, which is why it is not here. Approximate
matching belongs where approximate matching lives; an adapter with a native
fuzzy index may serve it faster, but nothing may depend on it having one.

## Keys are namespaced, and that is not decoration

`"person"` as an entity-type key and `"person"` as a five-character name prefix
are different claims about an entity, and an un-namespaced scheme would block
"Personal Data" together with every person in the tenant -- a block big enough
to undo the point of blocking. The prefix is `"p:"`, the type `"t:"`, the
soundex `"s:"`.

## Why a soundex key can be absent

`soundex_key` returns `None` for a name it cannot code: no ASCII letters at
all. That is not defensiveness, it is a correction --
`jellyfish.soundex` does not require letters and does not refuse anything, it
just produces nonsense:

```
soundex("2024")    -> "2000"
soundex("2007")    -> "2000"     the same block as "2024"
soundex("  ada")   -> " 000"     every leading-space name, together
soundex("\u4e2d\u6587")   -> "\u4e2d000"     every CJK name starting the same way
```

Each of those is a key that matches far too much, which is the one failure
blocking cannot survive: an oversized block puts the quadratic back. So the
name is reduced to its ASCII letters first, and a name with none gets no
soundex key rather than a junk one.

**Accents are folded, not discarded**, and the difference is the whole point
of the reduction. Simply dropping non-ASCII characters loses a *coded letter*
whenever the accent is on a consonant: "Mu\u00f1oz" becomes "muoz" and codes
`M200`, while "Munoz" codes `M520` -- two spellings of one name that can never
share a block. NFKD splits the character into its base letter plus a combining
mark, so the letter survives and only the mark is dropped. An accented *vowel*
hides this, since soundex ignores vowels after the first letter: "Ren\u00e9e" and
"Renee" both code `R500` either way.

`prefix_key` and `entity_type_key` are always present -- `Entity` refuses a
blank name, and every entity has a type -- so no entity is ever unblockable.

## Both textual keys normalize, and neither trusts a stored field

`prefix_key` and `entity_type_key` run `normalize_name` themselves. Two
extractors writing "Person" and "person" for one type would otherwise land in
different `t:` blocks, which is the same miss `prefix_key` normalizes to
avoid. Note `entity_id_for` does **not** normalize the type, so those two are
genuinely different entities -- which is exactly why they need to block
together: telling them apart is consolidation's judgement to make, and it
cannot make it if they never meet.
"""

from __future__ import annotations

import unicodedata
from enum import StrEnum
from typing import TYPE_CHECKING

import jellyfish

from redstring.domain.normalization import normalize_name

if TYPE_CHECKING:
    from collections.abc import Iterable

    from redstring.domain.entity import Entity

#: How many characters of the normalized name `prefix_key` keeps.
#:
#: Five is the inherited value, and it is a real trade rather than a default:
#: shorter blocks more together (more candidate pairs, more compute, fewer
#: missed matches), longer blocks less. It is a module constant rather than a
#: parameter because the value is baked into stored keys -- changing it means
#: recomputing every entity's keys, which is a migration and not a call-site
#: decision.
PREFIX_LENGTH = 5

_PREFIX = "p:"
_TYPE = "t:"
_SOUNDEX = "s:"


class BlockingKeyStrategy(StrEnum):
    """The three key functions. Each maps an entity to at most one key."""

    PREFIX = "prefix"
    ENTITY_TYPE = "entity_type"
    SOUNDEX = "soundex"


#: What `blocking_keys_for` uses when the caller does not choose.
#:
#: All three, because they fail differently: a prefix catches "Ada Lovelace"
#: against "Ada Lovelace, Countess" and misses "A. Lovelace"; soundex catches
#: spelling variants and misses abbreviations; the type key catches nothing on
#: its own and is what stops an entity from being unblockable.
DEFAULT_STRATEGIES = (
    BlockingKeyStrategy.PREFIX,
    BlockingKeyStrategy.ENTITY_TYPE,
    BlockingKeyStrategy.SOUNDEX,
)


def prefix_key(entity: Entity) -> str:
    """The first `PREFIX_LENGTH` characters of the normalized name.

    Normalized here rather than trusting `Entity.normalized_name`: that field
    is whatever the extractor put there, and a key function that produced a
    different answer for the same name depending on which extractor ran would
    make blocking silently miss matches across sources.

    Total, with no empty-name branch. `Entity` rejects a name whose `strip()`
    is falsy and `normalize_name` strips with the same function, so a valid
    entity cannot normalize to nothing. A guard here would be a branch no
    input reaches, which is worse than none: it describes a situation that
    cannot arise, so a reader reasons about the wrong invariant.
    """
    return _PREFIX + normalize_name(entity.name)[:PREFIX_LENGTH]


def entity_type_key(entity: Entity) -> str:
    """The entity's type, normalized. Always present -- see the docstring.

    Total, and deliberately without a blank-type guard. `Entity` does not
    reject a whitespace-only `entity_type`, so `"t:"` is reachable -- and it
    is the *right* answer: those entities share a type, vacuous as it is, and
    a key of `None` would leave them with one fewer way to be found. This is
    the opposite of `soundex_key`'s empty case, where the shared value would
    be a phonetic claim that was never made.
    """
    return _TYPE + normalize_name(entity.entity_type)


def soundex_key(entity: Entity) -> str | None:
    """A phonetic code for the name, or `None` when there is nothing to code.

    The name is NFKD-normalized and reduced to its ASCII letters first.
    `jellyfish.soundex` accepts anything and codes digits, spaces and CJK into
    keys that collide far too widely -- see the module docstring for the four
    measured cases, and for why the normalization has to come *before* the
    filter rather than instead of it.

    The **whole** name is coded, not the first token. Coding "Ada Lovelace" as
    if it were "Ada" would block it with every Adam and Adams in the tenant,
    which is the block-too-large failure this file's docstring warns about.
    """
    # NFKD first, so an accented letter becomes base + combining mark and the
    # base survives the ASCII filter. Filtering without it drops the letter.
    decomposed = unicodedata.normalize("NFKD", normalize_name(entity.name))
    letters = "".join(
        character for character in decomposed if character.isascii() and character.isalpha()
    )
    if not letters:
        return None
    return _SOUNDEX + jellyfish.soundex(letters)


_KEY_FUNCTIONS = {
    BlockingKeyStrategy.PREFIX: prefix_key,
    BlockingKeyStrategy.ENTITY_TYPE: entity_type_key,
    BlockingKeyStrategy.SOUNDEX: soundex_key,
}


def blocking_keys_for(
    entity: Entity, strategies: Iterable[BlockingKeyStrategy] = DEFAULT_STRATEGIES
) -> frozenset[str]:
    """Every key `entity` should be findable by.

    A `frozenset`, matching `Entity.blocking_keys`: the keys are a set of
    claims and their order means nothing. Absent keys are dropped rather than
    represented, so the result can be empty -- but only if `ENTITY_TYPE` was
    not among the strategies, since that one always produces a key.
    """
    keys = (_KEY_FUNCTIONS[strategy](entity) for strategy in strategies)
    return frozenset(key for key in keys if key is not None)
