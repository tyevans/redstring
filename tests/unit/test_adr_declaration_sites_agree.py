"""An ADR is declared in four places, and nothing made them agree.

`docs/adr/NNNN-*.md` is the decision. `docs/adr/index.md` is the table a
reader browses. `mkdocs.yml`'s nav is what the site publishes. And
`.claude/rules/definition-of-done.md` carries the table a spec author is
required to check their plan against. Four declaration sites for one fact,
which is `.claude/rules/recurring-defects.md` §2 -- and until this module every
one of them drifted silently.

**Both known drifts had already happened, in opposite directions**, which is
what makes them worth naming here rather than merely fixing:

- ADR 0024 shipped with **no index row**. `mkdocs.yml`'s nav made the page
  reachable, so `mkdocs build --strict` was silent -- it checks links, and
  nothing linked to a row that did not exist.
- ADR 0042 shipped with **no nav entry**. `mkdocs build` says so, but as an
  `INFO` line that `--strict` does not fail on, in a list seven entries long
  for the `superpowers/specs/` pages nobody intends to publish. The one line
  that meant something was camouflaged by seven that did not.
- ADRs 0040 and 0041 were in the nav and not the index. Found by this module.

So the two mechanisms that already existed (`--strict`, and reading the files
side by side in review) each caught the drift the other missed, and neither
caught both. A set comparison catches every direction at once, which is the
argument for doing it this way rather than adding another link.

The `definition-of-done.md` table is the fourth site and the one that had
decayed furthest: it stopped at 0019 while the tree reached 0042, so
twenty-three decisions were invisible to the rule that exists to make specs
account for existing decisions -- in a file loaded into every session. That is
BACKLOG B-ADR-TABLE; the index/nav halves are B96.

**Row *content* is not gated and cannot be.** This module asserts that every
ADR is listed everywhere, not that any summary of it is true. A wrong summary
in the definition-of-done table is worse than a missing row, because it will be
trusted -- the only defence there is reading the ADR while writing the row.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
ADR_DIR = ROOT / "docs" / "adr"
INDEX = ADR_DIR / "index.md"
MKDOCS = ROOT / "mkdocs.yml"
DEFINITION_OF_DONE = ROOT / ".claude" / "rules" / "definition-of-done.md"

#: An ADR number as it appears in a filename: four digits, then a hyphen.
_FILENAME = re.compile(r"^(\d{4})-.+\.md$")

#: A row in `docs/adr/index.md`: `| [0024 · Title](0024-title.md) | ... |`.
#: The number is taken from the *link target* rather than the link text,
#: because the target is what has to resolve.
_INDEX_ROW = re.compile(r"^\|\s*\[[^\]]*\]\((\d{4})-[^)]*\.md\)", re.MULTILINE)

#: A row in the definition-of-done table, whose links are repo-relative and
#: therefore differently shaped from the index's.
_RULE_ROW = re.compile(r"\(\.\./\.\./docs/adr/(\d{4})-[^)]*\.md\)")


def _adr_numbers_on_disk() -> set[str]:
    numbers = set()
    for path in ADR_DIR.iterdir():
        match = _FILENAME.match(path.name)
        if match is not None:
            numbers.add(match.group(1))
    return numbers


def _nav_entries(node: object) -> list[str]:
    """Every document path in `mkdocs.yml`'s nav, at any nesting depth.

    The nav is a list of single-key mappings whose values are either a path
    or another such list, so this cannot assume a fixed shape.
    """
    if isinstance(node, str):
        return [node]
    if isinstance(node, list):
        return [path for item in node for path in _nav_entries(item)]
    if isinstance(node, dict):
        return [path for value in node.values() for path in _nav_entries(value)]
    return []


@pytest.fixture(scope="module")
def on_disk() -> set[str]:
    numbers = _adr_numbers_on_disk()
    # A gate over an empty set passes vacuously, which is the failure this
    # project has recorded against exemption lists and inert checks alike.
    assert len(numbers) > 20, f"only {len(numbers)} ADRs found under {ADR_DIR}"
    return numbers


class TestEveryAdrIsListedEverywhereItHasToBe:
    def test_the_index_table_names_every_adr(self, on_disk: set[str]) -> None:
        listed = set(_INDEX_ROW.findall(INDEX.read_text(encoding="utf-8")))
        assert listed == on_disk, (
            f"docs/adr/index.md and docs/adr/ disagree. "
            f"Files with no index row: {sorted(on_disk - listed)}. "
            f"Index rows with no file: {sorted(listed - on_disk)}."
        )

    def test_the_mkdocs_nav_names_every_adr(self, on_disk: set[str]) -> None:
        # `mkdocs.yml` uses `!!python/name:` tags that a safe loader rejects,
        # so the nav is read with a loader that ignores unknown tags rather
        # than by enabling arbitrary construction.
        class _IgnoreTags(yaml.SafeLoader):
            pass

        _IgnoreTags.add_multi_constructor("", lambda loader, suffix, node: None)
        config = yaml.load(MKDOCS.read_text(encoding="utf-8"), Loader=_IgnoreTags)

        navigated = {
            match.group(1)
            for path in _nav_entries(config.get("nav", []))
            for match in [re.fullmatch(r"adr/(\d{4})-.+\.md", path)]
            if match is not None
        }
        assert navigated == on_disk, (
            f"mkdocs.yml's nav and docs/adr/ disagree. "
            f"Files with no nav entry: {sorted(on_disk - navigated)} "
            f"(these surface only as an INFO line `--strict` does not fail on). "
            f"Nav entries with no file: {sorted(navigated - on_disk)}."
        )

    def test_the_definition_of_done_table_names_every_adr(self, on_disk: set[str]) -> None:
        listed = set(_RULE_ROW.findall(DEFINITION_OF_DONE.read_text(encoding="utf-8")))
        assert listed == on_disk, (
            f".claude/rules/definition-of-done.md's ADR table and docs/adr/ "
            f"disagree. Missing rows: {sorted(on_disk - listed)}. "
            f"Rows naming no file: {sorted(listed - on_disk)}. "
            f"Read the ADR before writing its row -- a wrong summary in this "
            f"table is worse than a missing one, because it gets trusted."
        )


class TestTheGateCannotPassVacuously:
    """Each assertion above compares two sets, and a regex that matched
    nothing would make both sides empty on the listing side only -- which
    fails. What would *not* fail is a regex matching nothing on **both**
    sides, so the fixture asserts the disk side is populated. These pin the
    two parsers themselves, since a silently-broken one would make its
    assertion compare an empty set against a full one and fail loudly, but a
    parser that drifted to matching a *superset* would not.
    """

    def test_the_index_parser_finds_rows(self) -> None:
        assert len(set(_INDEX_ROW.findall(INDEX.read_text(encoding="utf-8")))) > 20

    def test_the_rule_table_parser_finds_rows(self) -> None:
        text = DEFINITION_OF_DONE.read_text(encoding="utf-8")
        assert len(set(_RULE_ROW.findall(text))) > 20

    def test_the_nav_walker_descends_into_nested_sections(self) -> None:
        nested = [{"A": [{"B": "one.md"}, {"C": [{"D": "two.md"}]}]}, {"E": "three.md"}]
        assert _nav_entries(nested) == ["one.md", "two.md", "three.md"]
