---
id: '0151'
title: Missing import provenance is marked, not invented
status: Accepted
target_bc: composition
governing_prds:
- PRD-0008
- PRD-0013
governing_stories:
- US-0020
- US-0030
amends:
- ADR-0135
- ADR-0150
---

# ADR-0151: Missing import provenance is marked, not invented

## Status

Accepted. Amends [ADR-0135](adr-0135-provenance-is-a-value-object.md), which
gains one `ExtractionMethod` value, and decision 5 of
[ADR-0150](adr-0150-attacker-influenced-input-is-bounded-and-inert.md), which
now separates the provenance a file states from the provenance it omits.

## Context

ADR-0150 trusts an import file for provenance and for identity. The importer
went further than that. When a JSON-LD node had no provenance, or a field of
it was absent, the importer filled the gap with
`extraction_method=MANUAL`, `confidence=1.0` and a constant `observed_at`.
GraphML carries no provenance at all, so every GraphML entity got those
values. A relationship with no confidence got `1.0`.

So a file that said nothing about its data imported as hand-curated data of
full confidence. That is the most trustworthy provenance the model can
express, and it was not in the file. Consolidation's preference order
(ADR-0110) reads `confidence`. A merge therefore kept the field values of an
entity with no provenance over the values of a scored extraction. A caller
who filtered on `extraction_method` could not tell an import from a manual
curation. The constant date also put every import on one day, so
`MOST_RECENTLY_OBSERVED` ranked them by an accident.

`ExtractionMethod` values are a wire format. They reach persisted events and
store properties, so a new value needs a record.

## Decision

1. **`ExtractionMethod.IMPORTED` (`"imported"`) names a claim whose method
   is unknown.** It means that an import file supplied the claim and did not
   say how it was derived. It invokes no model, so it is not in
   `MODEL_BEARING_METHODS`.

2. **A stated field is kept. An omitted field gets a value that makes no
   claim.** Each provenance field is decided on its own:
   - an omitted `extractionMethod` becomes `IMPORTED`;
   - an omitted `confidence` becomes `IMPORTED_CONFIDENCE`, the midpoint of
     the scale, so it claims no certainty in either direction;
   - an omitted `observedAt` becomes the import time.

   A relationship with no confidence also gets `IMPORTED_CONFIDENCE`.

3. **The import time comes from the call.** `import_graph`, `parse_json_ld`,
   `parse_graphml` and the client facade take an optional, timezone-aware
   `imported_at`. Without it, the time of the call is used, once per call, so
   every defaulted entity of one import shares one instant. A caller who
   wants a repeatable import passes the value, as with the `observed_at`
   parameter of `build_graph`. `composition/import_graph.py` is therefore a
   second module permitted to read the clock.

4. **A missing provenance is defaulted, not refused.** GraphML has no place
   for provenance, so a refusal would make GraphML import impossible. A
   caller who wants to refuse calls `parse_json_ld` or `parse_graphml`,
   checks for `IMPORTED`, and writes only what it accepts.

## Consequences

- An entity with no stated provenance ranks below a scored claim of higher
  confidence in a merge, and above one of lower confidence. Before, it won
  against every claim except another of full confidence.
- The JSON-LD round trip stays lossless. Export writes every provenance
  field, so a re-import states all of them and none is defaulted. A GraphML
  round trip still loses provenance, as before. It now loses it to
  `IMPORTED` instead of to `MANUAL`.
- A store written by this version can hold `"imported"`. A version before
  this one refuses that value when it reads the entity. A downgrade over such
  a store fails at the read, by name, and does not misread the data.
- Two imports of one GraphML file at different times record different
  `observed_at` values unless the caller passes `imported_at`. An import
  replaces the entity (ADR-0150), so the later time wins.

## Related records

- [ADR-0110](adr-0110-one-total-order-for-preference.md) stands. Its order is
  unchanged. The order now receives honest inputs.
- [ADR-0101](adr-0101-event-log-schema-and-granularity.md) stands. An import
  writes to the stores and emits no event, so no event payload changes shape.
  An extraction event can now carry the new value only if a caller builds one
  with it.
