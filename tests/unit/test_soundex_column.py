"""Unit tests for soundex column functionality.

These tests verify the jellyfish soundex implementation that will be used
as a reference for the PostgreSQL soundex function.
"""

import pytest

try:
    import jellyfish
    JELLYFISH_AVAILABLE = True
except ImportError:
    JELLYFISH_AVAILABLE = False


@pytest.mark.skipif(not JELLYFISH_AVAILABLE, reason="jellyfish not installed")
class TestSoundexEncoding:
    """Tests for soundex phonetic encoding."""

    def test_soundex_basic_encoding(self):
        """Test basic soundex encoding produces expected results."""
        # Standard soundex examples
        assert jellyfish.soundex("Robert") == "R163"
        assert jellyfish.soundex("Rupert") == "R163"  # Same as Robert

    def test_soundex_technical_terms(self):
        """Test soundex with technical/domain terms."""
        # DomainEvent variations
        domain_event = jellyfish.soundex("DomainEvent")
        assert len(domain_event) == 4
        assert domain_event[0] == "D"  # First letter preserved

        # Similar sounding terms should have same soundex
        assert jellyfish.soundex("Smith") == jellyfish.soundex("Smyth")

    def test_soundex_case_insensitive(self):
        """Test soundex is case-insensitive."""
        assert jellyfish.soundex("ROBERT") == jellyfish.soundex("robert")
        assert jellyfish.soundex("DomainEvent") == jellyfish.soundex("domainevent")

    def test_soundex_with_spaces(self):
        """Test soundex with spaces in input."""
        # Soundex processes first word/continuous letters
        without_space = jellyfish.soundex("DomainEvent")
        with_space = jellyfish.soundex("Domain Event")
        # Note: These may differ because soundex stops at first space in some implementations
        # or treats space-separated as different words
        assert len(without_space) == 4
        assert len(with_space) == 4

    def test_soundex_with_numbers(self):
        """Test soundex handles strings with numbers."""
        # Soundex typically ignores numbers
        result = jellyfish.soundex("Entity123")
        assert len(result) == 4
        assert result[0] == "E"

    def test_soundex_empty_string(self):
        """Test soundex with empty string."""
        # Empty string should return empty or all zeros
        result = jellyfish.soundex("")
        assert isinstance(result, str)

    def test_soundex_single_character(self):
        """Test soundex with single character."""
        result = jellyfish.soundex("A")
        assert result[0] == "A"
        assert len(result) == 4  # Padded with zeros

    def test_soundex_same_phonetics_different_spelling(self):
        """Test that phonetically similar names have same soundex."""
        # Classic soundex examples
        pairs = [
            ("Robert", "Rupert"),
            ("Smith", "Smyth"),
            ("Tymczak", "Tymczak"),  # Same word
        ]
        for word1, word2 in pairs:
            assert jellyfish.soundex(word1) == jellyfish.soundex(word2), \
                f"{word1} and {word2} should have same soundex"

    def test_soundex_different_first_letter(self):
        """Test that different first letters always produce different soundex."""
        words = ["Apple", "Banana", "Cherry", "Date"]
        soundexes = [jellyfish.soundex(w) for w in words]

        # First character should match first letter of word
        for word, sx in zip(words, soundexes):
            assert sx[0] == word[0].upper()

    def test_soundex_output_format(self):
        """Test soundex output is always 4 characters."""
        test_words = [
            "A",
            "Ab",
            "Abc",
            "Abcdefghijklmnop",
            "DomainEvent",
            "ExtractedEntity",
            "Knowledge",
        ]
        for word in test_words:
            result = jellyfish.soundex(word)
            assert len(result) == 4, f"Soundex of '{word}' should be 4 chars, got {len(result)}"

    def test_soundex_preserves_first_letter(self):
        """Test that soundex always preserves the first letter."""
        test_cases = [
            ("Apple", "A"),
            ("banana", "B"),
            ("cherry", "C"),
            ("domain", "D"),
            ("entity", "E"),
        ]
        for word, expected_first in test_cases:
            result = jellyfish.soundex(word)
            assert result[0] == expected_first.upper(), \
                f"First char of soundex('{word}') should be '{expected_first.upper()}'"
