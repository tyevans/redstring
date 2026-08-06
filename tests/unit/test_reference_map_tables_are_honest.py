"""A reference page's map table names sections the page actually has.

**This is the gate B65 was missing.** `reference/domain-value-types.md` opened
with a table listing fourteen sections, of which nine had never been written.
Nothing failed: the table is prose, the links to the absent sections were the
only trace, and repairing those links made the gap invisible again. A page can
therefore promise a structure it does not have, indefinitely, and the only
reader who finds out is one who goes looking for a specific section.

`mkdocs build --strict` catches the *link* half — an anchor that resolves
nowhere is a build failure. It cannot catch this half, because a table row is
not a link, and it certainly cannot catch a row whose link was quietly
repointed at some other section to make the build pass.

Deliberately narrow: it checks that every row names a real heading, and that
the table's order matches the page's. It does **not** check the reverse —
a section absent from the table is a documentation nit rather than a broken
promise, and requiring every heading to appear would make the table a second
declaration of the page's structure rather than an index into it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[2]

#: Pages carrying a "Section | Module" map table, and the heading level their
#: sections use. One entry today; add a row when a page grows such a table,
#: which is cheaper than discovering it did not have one.
MAPPED_PAGES = [
    (PROJECT / "docs" / "reference" / "domain-value-types.md", 2),
]

#: The map table is the one under a `| Section | Module |` header. Anchoring on
#: that header rather than on a row shape is what keeps the many other
#: two-column tables these pages carry -- fields, members, defaults -- out of
#: the result. A pattern loose enough to match "a row of two cells" matched
#: those too, on the first run of this module.
_TABLE = re.compile(r"^\| Section \| Module \|\n\|[-| ]+\|\n(?P<rows>(?:\|.*\n)+)", re.M)


def _table_sections(text: str) -> list[str]:
    """Section names from the leading map table, in the order it lists them."""
    match = _TABLE.search(text)
    if match is None:
        return []
    return [
        line.split("|")[1].strip()
        for line in match.group("rows").splitlines()
        if line.startswith("|")
    ]


def _headings(text: str, level: int) -> list[str]:
    return re.findall(rf"^{'#' * level} (.+)$", text, re.M)


@pytest.mark.parametrize(
    ("page", "level"), MAPPED_PAGES, ids=lambda value: getattr(value, "name", str(value))
)
class TestTheMapTableMatchesThePage:
    def test_the_detector_finds_a_table(self, page: Path, level: int):
        """A checker over an empty set passes vacuously. If the row pattern
        stops matching — a reformat, a moved table — every assertion below
        goes quiet while looking exactly as green."""
        assert len(_table_sections(page.read_text())) >= 10

    def test_every_row_names_a_section_that_exists(self, page: Path, level: int):
        text = page.read_text()
        headings = set(_headings(text, level))
        missing = [row for row in _table_sections(text) if row not in headings]

        assert not missing, (
            f"{page.name}'s map table promises sections the page does not have: "
            f"{missing}. Write them, or take the row out — a table row is a "
            f"claim about the page and nothing else checks it."
        )

    def test_the_table_is_in_page_order(self, page: Path, level: int):
        """The table says it is in page order, so it has to be.

        Weaker than it looks and deliberately so: this compares the *table's*
        sequence against the same sections' sequence in the page, ignoring any
        heading the table does not mention. Reordering the page without
        reordering the table is the realistic drift, and it is what this
        catches.
        """
        text = page.read_text()
        rows = _table_sections(text)
        in_page = [heading for heading in _headings(text, level) if heading in set(rows)]

        assert rows == in_page
