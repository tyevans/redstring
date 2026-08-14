"""Naming drift, counted as pairs rather than inferred from a total.

Entity count cannot see drift: measured, changing the chunk size moves the
entity set no more than re-running the same configuration does (jaccard 0.587
against 0.601-0.667). This metric exists because deliverable C's whole risk is
drift, and the metric it would otherwise be judged by is blind to it.
"""

from __future__ import annotations

from bench.drift import variant_pairs, variant_pairs_detail


def test_a_first_name_beside_its_full_name_is_one_pair() -> None:
    """The exact shape the pipeline manufactures at a chunk boundary."""
    assert variant_pairs(["dudley", "dudley dursley"]) == 1


def test_unrelated_names_are_not_a_pair() -> None:
    assert variant_pairs(["harry potter", "albus dumbledore"]) == 0


def test_a_shared_word_is_not_enough() -> None:
    """`the philosopher's stone` and `the goblet of fire` share a token and
    are different things. Only a strict subset counts."""
    assert variant_pairs(["the philosophers stone", "the goblet of fire"]) == 0


def test_three_spellings_of_one_name_are_three_pairs() -> None:
    """Every pair is counted, not every cluster.

    A cluster count would report 1 here and 1 for a two-name cluster, hiding
    the difference between mild and severe drift on one entity.
    """
    assert variant_pairs(["harry", "harry potter", "harry james potter"]) == 3


def test_identical_names_are_not_a_pair() -> None:
    """A strict subset, not any subset -- a name is not a variant of itself,
    and a duplicated list entry must not manufacture drift."""
    assert variant_pairs(["harry potter", "harry potter"]) == 0


def test_equal_token_sets_with_different_order_are_not_a_pair() -> None:
    """Transposition is not a subset. Same tokens in different order do not
    make a strict subset, so they are not counted as a variant pair.

    Note: this is currently correct-but-arguable -- equal token sets in a
    different order are almost certainly the same entity, and B-BENCH-4 names
    transposition as a known blind spot that could be improved.
    """
    assert variant_pairs(["dudley dursley", "dursley dudley"]) == 0


def test_possessives_and_hyphens_are_normalised_before_comparing() -> None:
    """`harry's` and `harry` are one name spelled two ways.

    Each half needs the punctuation actually present and differing between the
    two names -- otherwise the plain token-subset rule carries the assertion
    and the normalisation is untested.
    """
    assert variant_pairs(["harry's wand", "harry wand extra"]) == 1
    assert variant_pairs(["half-blood prince", "the half blood prince"]) == 1


def test_the_detail_lists_the_pairs_it_counted() -> None:
    """The count is for the report; the pairs are for the human deciding
    whether a rise is real drift or an artefact of the heuristic."""
    assert variant_pairs_detail(["dudley", "dudley dursley"]) == [("dudley", "dudley dursley")]


def test_the_order_of_the_input_does_not_change_the_count() -> None:
    names = ["harry", "harry potter", "albus dumbledore"]

    assert variant_pairs(names) == variant_pairs(list(reversed(names)))


def test_an_empty_run_has_no_pairs() -> None:
    assert variant_pairs([]) == 0
