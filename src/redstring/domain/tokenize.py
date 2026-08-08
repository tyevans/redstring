"""What counts as a term, decided here and nowhere else.

## Why this is not the database's tokenizer

Postgres has a perfectly good `english` text search configuration, and using
it would be the obvious implementation. It is rejected because the in-memory
adapter cannot run it: the two stores would then disagree about what a *term*
is, and every ranking they produced would differ no matter how pure the
scorer was. Tokenization is upstream of every number BM25 computes, so it
belongs in the one place both adapters can share.

## No stemming

"running" does not match "run", and that cost is real. A stemmer is a
language model -- English-only, and a dependency -- and two implementations of
"the Porter stemmer" differ at the edges, which is the divergence this module
exists to prevent, reintroduced. Adding one later means adding a single
domain-owned implementation, never a per-adapter one. Filed as a backlog entry
saying exactly that.
"""

from __future__ import annotations

import re
import unicodedata

#: Words dropped before they reach the index. They appear in nearly every
#: passage, so their inverse document frequency is near zero and they cost a
#: candidate scan for no ranking signal.
#:
#: Deliberately small and English-only. A large list starts discarding terms
#: that carry meaning in some corpus ("can" in a manufacturing corpus), and a
#: per-language list is the stemming argument in the module docstring again.
STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "for",
        "from",
        "has",
        "have",
        "he",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "she",
        "that",
        "the",
        "they",
        "this",
        "to",
        "was",
        "were",
        "will",
        "with",
    }
)

#: One or more alphanumerics. `\w` would keep the underscore, making
#: `source_id` a single term no reader would search for.
_TOKEN = re.compile(r"[^\W_]+")


def tokenize(text: str) -> list[str]:
    """The terms of `text`, in order, with repeats kept.

    Repeats are the caller's business: term *frequency* is what separates
    BM25 from counting distinct matches, and a tokenizer returning a set
    would silently make every frequency 1.

    NFKC first, then casefold. The order matters -- normalising after folding
    leaves compatibility forms that fold to something else unmatched.
    """
    normalised = unicodedata.normalize("NFKC", text).casefold()
    return [token for token in _TOKEN.findall(normalised) if token not in STOPWORDS]
