"""Every event in `KG_EVENT_TYPES` has a section on the events reference page.

`docs/reference/events.md` documents each event in a `## <Name>` section
running to a few hundred lines. `DocumentChunked` had none -- it was
registered, in the tuple, folded by a projection, and absent from the page
except for one row in a summary table.

**That it survived is the point.** The per-event sections are hand-written
prose with nothing tying them to the tuple, which is the same shape
`KG_EVENT_TYPES` itself exists to prevent: `redstring/events/__init__.py` says
the schema is a tuple rather than prose precisely so that a new event cannot
be added without something noticing. The documentation had no equivalent, so
the next undocumented event would have arrived the same silent way.

Deliberately a *heading* check and not a word count. What this can enforce is
that a section exists; whether it says anything useful is review's job. The
alternative -- asserting some minimum length -- would be satisfied by a stub
and would have to be retuned every time a section was edited, so it would
measure prose volume while claiming to measure coverage.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from redstring.events import KG_EVENT_TYPES

EVENTS_PAGE = Path(__file__).resolve().parents[3] / "docs" / "reference" / "events.md"

#: `## \\`Name\\`` -- the page's own convention for an event section. Matched
#: with the backticks, because `## DocumentChunked` written bare would render
#: differently from every other section and should fail rather than pass.
_SECTION = re.compile(r"^## `(\w+)`$", re.MULTILINE)


def documented_events() -> set[str]:
    return set(_SECTION.findall(EVENTS_PAGE.read_text()))


def test_the_page_exists_and_has_sections() -> None:
    """Guard the guard: a regex that matches nothing passes vacuously.

    If the page moves or the heading convention changes, every assertion below
    goes quiet while continuing to report success -- the failure shape
    `recurring-defects.md` section 3 is about, and the reason the compliance
    coverage gates each assert their own detector finds something.
    """
    assert EVENTS_PAGE.exists(), f"{EVENTS_PAGE} is gone; this gate is measuring nothing"
    assert len(documented_events()) >= len(KG_EVENT_TYPES)


@pytest.mark.parametrize("event", KG_EVENT_TYPES, ids=lambda e: e.__name__)
def test_every_event_type_has_a_section(event: type) -> None:
    assert event.__name__ in documented_events(), (
        f"{event.__name__} is in KG_EVENT_TYPES and has no `## \\`{event.__name__}\\`` "
        f"section in docs/reference/events.md. Every other event has one: a "
        f"field table, what the payload means, and what its validator refuses. "
        f"An event nobody documented is one a consumer has to read the source "
        f"to fold."
    )


def test_no_section_documents_an_event_that_is_gone() -> None:
    """The other direction, which is the one that rots quietly.

    A removed event leaves its section behind, and a section describing a
    payload that no longer exists is worse than a missing one -- it is
    confidently wrong, and a reader has no way to tell. Checked in both
    directions for the same reason the compliance registries are.

    Only headings that name a real event class are considered: the page has
    other backticked `##` sections (payload types, helpers), so this compares
    against the names of things that once were events rather than against
    every heading.
    """
    known = {event.__name__ for event in KG_EVENT_TYPES}
    event_shaped = {
        name
        for name in documented_events()
        if name.endswith(("Extracted", "Chunked", "Embedded", "Merged", "Undone"))
    }
    stale = event_shaped - known
    assert not stale, (
        f"docs/reference/events.md documents events that are not in "
        f"KG_EVENT_TYPES: {sorted(stale)}. Either the event was removed and "
        f"its section should go, or it was renamed and the section should "
        f"follow."
    )
