"""Stability is agreement between repeats. It is not accuracy, and the test
that says so loudest is the one where a pipeline extracting one wrong entity
every time scores a perfect 1.0."""

from __future__ import annotations

from bench.stability import stability_of


def test_identical_runs_agree_completely() -> None:
    assert stability_of([["a", "b"], ["a", "b"]]).jaccard == 1.0


def test_a_consistently_wrong_pipeline_also_scores_one() -> None:
    """The metric's own limit, pinned so nobody reads it as correctness.

    A pipeline that finds one entity in a document naming forty, every single
    time, is perfectly stable. That is why the field is called stability and
    why accuracy is scored separately against the graded corpus.
    """
    assert stability_of([["wrong"], ["wrong"], ["wrong"]]).jaccard == 1.0


def test_disjoint_runs_agree_not_at_all() -> None:
    assert stability_of([["a"], ["b"]]).jaccard == 0.0


def test_partial_agreement_is_intersection_over_union() -> None:
    """Three runs: 'a' in all three, 'b' in two, 'c' in one.

    Union is 3, intersection is 1, so 1/3. Written as a literal rather than
    as a formula over the inputs.
    """
    result = stability_of([["a", "b"], ["a", "b", "c"], ["a"]])

    assert result.jaccard == 1 / 3
    assert result.always == 1
    assert result.sometimes == 2
    assert result.runs == 3


def test_a_repeated_name_within_one_run_does_not_inflate_agreement() -> None:
    """Runs are compared as sets. A run listing 'a' twice agrees with a run
    listing it once, and an implementation counting occurrences does not."""
    assert stability_of([["a", "a"], ["a"]]).jaccard == 1.0


def test_one_run_is_no_stability_rather_than_perfect_stability() -> None:
    """A single run agrees with itself trivially, and reporting 1.0 for it
    would make a misconfigured `repeats: 1` sweep look maximally stable."""
    assert stability_of([["a", "b"]]) is None


def test_no_runs_is_no_stability() -> None:
    assert stability_of([]) is None


def test_two_empty_runs_are_no_stability_rather_than_perfect_agreement() -> None:
    """Two runs that extracted nothing agree on nothing, and 0/0 must not be
    reported as 1.0 -- that is the exact number a dead endpoint produces."""
    assert stability_of([[], []]) is None
