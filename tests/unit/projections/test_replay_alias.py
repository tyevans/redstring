"""`replay` is `project`, for callers whose own vocabulary has a *project*.

Reported downstream: a knowledge-graph consumer plausibly has a "project"
noun, and ours lands in the same twelve-line function. Renaming would break
every caller for a cosmetic gain, so the surface carries both names.

The assertion is *identity*, not "both work". Two names bound to two
separately-defined functions would pass a behavioural test and then drift the
first time one of them gained an argument -- which is the failure this exists
to prevent, since a caller choosing the alias would be choosing the stale one.
"""

from __future__ import annotations

import redstring
from redstring import projections


class TestTheAliasIsTheSameFunction:
    def test_the_package_exports_both_names_for_one_object(self) -> None:
        assert redstring.replay is redstring.project

    def test_the_subpackage_does_too(self) -> None:
        assert projections.replay is projections.project

    def test_both_names_are_promised(self) -> None:
        assert {"project", "replay"} <= set(redstring.__all__)
        assert {"project", "replay"} <= set(projections.__all__)
