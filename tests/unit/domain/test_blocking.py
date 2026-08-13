"""Blocking keys are pure, total, and namespaced."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from redstring.domain.blocking import (
    DEFAULT_STRATEGIES,
    PREFIX_LENGTH,
    BlockingKeyStrategy,
    blocking_keys_for,
    entity_type_key,
    prefix_key,
    prefix_key_for_name,
    query_blocking_keys,
    soundex_key,
    soundex_key_for_name,
)
from redstring.domain.entity import Entity
from redstring.domain.provenance import ExtractionMethod, Provenance

#: A fixed observation instant. Never `datetime.now(UTC)`: a fixture that
#: varies per run makes any comparison on `observed_at` non-deterministic.
OBSERVED = datetime(2026, 2, 13, 11, 7, tzinfo=UTC)


def _entity(name: str, entity_type: str = "person") -> Entity:
    return Entity(
        id=uuid4(),
        tenant_id=uuid4(),
        name=name,
        normalized_name=name.lower(),
        entity_type=entity_type,
        provenance=Provenance(
            observed_at=OBSERVED,
            extraction_method=ExtractionMethod.MANUAL,
            confidence=1.0,
        ),
    )


class TestKeysAreNamespaced:
    def test_a_type_and_a_prefix_that_read_alike_are_different_keys(self):
        """Without namespacing, "Person Of Interest" blocks with every person.

        This is the case that motivates the prefixes, so it is pinned by
        example rather than left to the property below: the type name and the
        five-character prefix are the *same string* here, and only the
        namespace separates them.
        """
        typed = _entity("Ada Lovelace", entity_type="perso")
        named = _entity("Perso Ipsum", entity_type="concept")

        assert entity_type_key(typed) == "t:perso"
        assert prefix_key(named) == "p:perso"
        assert entity_type_key(typed) != prefix_key(named)

    def test_the_three_namespaces_are_distinct(self):
        entity = _entity("Ada Lovelace")
        keys = {prefix_key(entity), entity_type_key(entity), soundex_key(entity)}

        assert len(keys) == 3
        assert {key[:2] for key in keys} == {"p:", "t:", "s:"}


class TestPrefix:
    def test_it_takes_the_first_characters_of_the_normalized_name(self):
        assert prefix_key(_entity("Ada Lovelace")) == "p:ada l"

    def test_it_normalizes_rather_than_trusting_the_stored_name(self):
        """Two extractors writing different `normalized_name`s for one name
        must still block together, so the key function normalizes itself."""
        loose = _entity("  ADA   Lovelace ")
        loose.normalized_name = "whatever the extractor felt like"

        assert prefix_key(loose) == prefix_key(_entity("Ada Lovelace"))

    def test_a_name_shorter_than_the_window_is_its_own_prefix(self):
        assert prefix_key(_entity("Ada")) == "p:ada"

    def test_a_name_that_could_normalize_to_nothing_cannot_be_built(self):
        """The reason `prefix_key` has no empty-name branch.

        `Entity` refuses a name whose `strip()` is falsy, and `normalize_name`
        strips with the same function -- so the only input that would reach a
        guard cannot be constructed. Asserted here rather than left implicit,
        because if `Entity` ever relaxes that rule this test is what says
        `prefix_key` now needs the branch.
        """
        with pytest.raises(ValidationError):
            _entity("   ")


class TestSoundex:
    def test_spelling_variants_share_a_code(self):
        """ "Robert" and "Rupert", the canonical soundex pair. Note that
        "Catherine"/"Katherine" do *not* -- soundex keeps the first letter
        verbatim -- which is a limitation of the algorithm rather than of this
        wrapper, and the reason the prefix and type keys exist alongside it."""
        assert soundex_key(_entity("Robert")) == soundex_key(_entity("Rupert"))
        assert soundex_key(_entity("Catherine")) != soundex_key(_entity("Katherine"))

    def test_it_codes_the_whole_name_and_not_the_first_token(self):
        """Coding only "Ada" would block "Ada Lovelace" with every Adams in
        the tenant -- the block-too-large failure. Pinned with a pair that a
        first-token implementation would collapse and a whole-name one does
        not."""
        assert soundex_key(_entity("Ada Lovelace")) != soundex_key(_entity("Ada Byron"))

    def test_a_name_with_no_ascii_letters_has_no_code(self):
        """`jellyfish.soundex` refuses nothing -- it codes `"2024"` as
        `"2000"` and `"2007"` as `"2000"` too. A key that lumps every year
        together is the oversized block this module exists to avoid."""
        assert soundex_key(_entity("2024")) is None
        assert soundex_key(_entity("\u4e2d\u6587")) is None

    def test_digits_and_punctuation_do_not_reach_the_coder(self):
        """The correction, stated as a difference. Coding the raw name gives
        `"2200"` for "2024-Q3" -- a code led by a digit, sharing a block with
        every other digit-led name. Reducing to letters first gives a code for
        the only word in it."""
        assert soundex_key(_entity("2024-Q3")) == soundex_key(_entity("Q"))

    def test_a_name_with_some_letters_is_coded(self):
        assert soundex_key(_entity("Q3 2024 Review")) is not None

    def test_accents_are_folded_rather_than_discarded(self):
        """The case that matters is an accented **consonant**.

        Discarding the character instead of folding it loses a coded letter:
        "Mu\u00f1oz" reduces to "muoz" and codes `M200`, while "Munoz" codes
        `M520` -- so the two spellings of one name never share a block, which
        is the miss blocking exists to prevent. Folding through NFKD makes
        both `M520`.

        An accented *vowel* hides this: soundex ignores vowels after the first
        letter, so "Ren\u00e9e" and "Renee" both code `R500` either way. A test
        written with a vowel would pass against the broken implementation --
        the `CLAUDE.md` shape, in a spelling nobody would think to look for.
        """
        assert soundex_key(_entity("Mu\u00f1oz")) == soundex_key(_entity("Munoz"))
        assert soundex_key(_entity("\u00c5ngstr\u00f6m")) == soundex_key(_entity("Angstrom"))

    def test_an_accented_first_letter_folds_too(self):
        """soundex keeps the first letter verbatim, so this is the position
        where a stray accent does the most damage."""
        assert soundex_key(_entity("\u00e9ada")) == soundex_key(_entity("eada"))


class TestTheTypeKey:
    def test_it_normalizes_like_the_prefix_does(self):
        """`prefix_key`'s docstring gives the argument and it applies here
        equally: two extractors writing "Person" and "person" for one type
        must not land in different blocks."""
        assert entity_type_key(_entity("Ada", entity_type="Person")) == entity_type_key(
            _entity("Ada", entity_type="  person ")
        )

    def test_internal_whitespace_collapses(self):
        assert entity_type_key(_entity("Ada", entity_type="legal   entity")) == entity_type_key(
            _entity("Ada", entity_type="Legal Entity")
        )

    def test_it_is_still_always_present(self):
        """The property that makes every entity blockable. Normalizing must
        not introduce a way for the type key to vanish -- `Entity` does not
        reject a whitespace-only `entity_type`, so this is reachable."""
        key = entity_type_key(_entity("Ada", entity_type="   "))

        assert key == "t:"


class TestTheKeySet:
    def test_the_default_produces_one_key_per_strategy(self):
        keys = blocking_keys_for(_entity("Ada Lovelace"))

        assert keys == frozenset({"p:ada l", "t:person", soundex_key(_entity("Ada Lovelace"))})

    def test_absent_keys_are_dropped_not_represented(self):
        assert blocking_keys_for(_entity("2024")) == frozenset({"p:2024", "t:person"})

    def test_a_caller_can_choose_fewer_strategies(self):
        entity = _entity("Ada Lovelace")

        assert blocking_keys_for(entity, [BlockingKeyStrategy.ENTITY_TYPE]) == frozenset(
            {"t:person"}
        )

    def test_only_the_soundex_strategy_can_yield_nothing(self):
        """Stated as a test because the module docstring makes the claim, and
        an unstated caveat is one nobody knows to check for."""
        numeric = _entity("2024")

        assert blocking_keys_for(numeric, [BlockingKeyStrategy.SOUNDEX]) == frozenset()
        assert blocking_keys_for(numeric, [BlockingKeyStrategy.PREFIX]) == frozenset({"p:2024"})


class TestProperties:
    """Every property here skips names `Entity` itself refuses.

    Blocking is asked about entities that exist, so a name that cannot become
    one is not a case these functions have to survive -- and filtering in the
    strategy would silently narrow what is generated.
    """

    @given(name=st.text(min_size=1, max_size=40), entity_type=st.text(min_size=1, max_size=20))
    def test_key_computation_never_raises(self, name, entity_type):
        """Blocking keys are computed on every entity extraction, so a name
        that crashes the key function stops a whole document."""
        try:
            entity = _entity(name, entity_type=entity_type)
        except ValidationError:
            return
        blocking_keys_for(entity)

    @given(name=st.text(min_size=1, max_size=40))
    def test_keys_are_deterministic(self, name):
        """Two entities with the same name and type block together, whatever
        else differs. If this were false, a re-extraction would land an entity
        in a different block from its own earlier copy."""
        try:
            first, second = _entity(name), _entity(name)
        except ValidationError:
            return

        assert blocking_keys_for(first) == blocking_keys_for(second)

    @given(name=st.text(min_size=1, max_size=40))
    def test_every_key_is_namespaced(self, name):
        try:
            entity = _entity(name)
        except ValidationError:
            return

        for key in blocking_keys_for(entity):
            assert key[:2] in {"p:", "t:", "s:"}, key

    @given(name=st.text(min_size=1, max_size=40))
    def test_no_key_is_only_its_namespace(self, name):
        """An empty payload behind a namespace is the degenerate key this
        module refuses to emit: it would match every entity that also failed
        to produce one."""
        try:
            entity = _entity(name)
        except ValidationError:
            return

        for key in blocking_keys_for(entity):
            assert len(key) > 2, key

    @given(
        name=st.text(
            # `Entity` refuses a NUL in any free-form field, because a JSON
            # column cannot hold one (`domain/json_safety.py`). Excluding it in
            # the alphabet rather than catching `ValidationError` the way the
            # property above does: this one is about the *prefix window*, and a
            # rejected construction would silently stop testing that.
            alphabet=st.characters(codec="utf-8", exclude_characters="\x00"),
            min_size=PREFIX_LENGTH + 1,
            max_size=40,
        ).filter(str.strip)
    )
    def test_the_prefix_never_exceeds_its_window(self, name):
        assert len(prefix_key(_entity(name))) <= len("p:") + PREFIX_LENGTH

    def test_the_default_strategies_are_all_of_them(self):
        """A strategy added to the enum and forgotten in the default tuple
        would be dead code that looks live."""
        assert set(DEFAULT_STRATEGIES) == set(BlockingKeyStrategy)


class TestUnknownStrategy:
    def test_a_strategy_with_no_key_function_fails_loudly(self):
        """Guards the `_KEY_FUNCTIONS` table against a new enum member being
        added without one -- which would otherwise be a `KeyError` at
        extraction time on some tenant's document."""
        with pytest.raises(KeyError):
            blocking_keys_for(_entity("Ada"), ["not-a-strategy"])  # type: ignore[list-item]


class TestQueryBlockingKeys:
    def test_prefix_key_for_name_agrees_with_the_entity_form(self) -> None:
        """The two spellings are one function; a copy is how they drift."""
        entity = _entity("Ada Lovelace")
        assert prefix_key_for_name("Ada Lovelace") == prefix_key(entity)

    def test_soundex_key_for_name_agrees_with_the_entity_form(self) -> None:
        entity = _entity("Ada Lovelace")
        assert soundex_key_for_name("Ada Lovelace") == soundex_key(entity)

    def test_soundex_key_for_name_is_none_when_nothing_can_be_coded(self) -> None:
        """Same empty case as the entity form: digits code to nothing."""
        assert soundex_key_for_name("12345") is None

    def test_query_blocking_keys_carries_prefix_and_soundex(self) -> None:
        keys = query_blocking_keys("Ada Lovelace")
        assert prefix_key_for_name("Ada Lovelace") in keys
        assert soundex_key_for_name("Ada Lovelace") in keys

    def test_query_blocking_keys_never_carries_a_type_key(self) -> None:
        """A type key blocks an entire type -- often the whole tenant.

        The type key exists so an entity is never unblockable. As a *query*
        key it is the opposite: it matches every entity of that type, so
        candidate generation would degrade into a full scan the moment a
        query happened to share a type. `entity_types` is applied as a
        filter, not as a key.
        """
        keys = query_blocking_keys("Ada Lovelace")
        assert not any(key.startswith("t:") for key in keys)

    def test_query_blocking_keys_drops_an_absent_soundex(self) -> None:
        """A query that codes to nothing yields the prefix key alone, not a
        None."""
        assert query_blocking_keys("12345") == [prefix_key_for_name("12345")]

    def test_query_blocking_keys_has_no_duplicates(self) -> None:
        """`find_by_blocking_keys` keys its result by key; a repeat is a
        wasted query."""
        keys = query_blocking_keys("Ada Lovelace")
        assert len(keys) == len(set(keys))
