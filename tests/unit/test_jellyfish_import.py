"""
Test jellyfish package availability and basic functionality.

This test verifies that the jellyfish library is properly installed
and its core functions are accessible for entity consolidation.
"""

import pytest


@pytest.mark.unit
def test_jellyfish_import():
    """Verify jellyfish package is available."""
    import jellyfish

    # Test Jaro-Winkler similarity
    score = jellyfish.jaro_winkler_similarity("DomainEvent", "Domain Event")
    assert 0.0 <= score <= 1.0
    assert score > 0.9  # These are very similar

    # Test Soundex
    soundex = jellyfish.soundex("test")
    assert len(soundex) == 4
    assert soundex[0].isalpha()

    # Test Levenshtein distance
    distance = jellyfish.levenshtein_distance("kitten", "sitting")
    assert distance == 3


@pytest.mark.unit
def test_jaro_winkler_similarity_range():
    """Test Jaro-Winkler similarity returns values in valid range."""
    import jellyfish

    # Identical strings should have similarity of 1.0
    assert jellyfish.jaro_winkler_similarity("test", "test") == 1.0

    # Completely different strings should have low similarity
    score = jellyfish.jaro_winkler_similarity("abc", "xyz")
    assert 0.0 <= score < 0.5

    # Similar strings should have high similarity
    score = jellyfish.jaro_winkler_similarity("Microsoft", "Microsft")
    assert score > 0.9


@pytest.mark.unit
def test_soundex_encoding():
    """Test Soundex phonetic encoding."""
    import jellyfish

    # Test standard Soundex behavior
    assert jellyfish.soundex("Robert") == "R163"
    assert jellyfish.soundex("Rupert") == "R163"  # Should be same as Robert

    # Smith variations should have same Soundex
    assert jellyfish.soundex("Smith") == jellyfish.soundex("Smyth")


@pytest.mark.unit
def test_levenshtein_distance():
    """Test Levenshtein edit distance."""
    import jellyfish

    # Identical strings have distance 0
    assert jellyfish.levenshtein_distance("test", "test") == 0

    # Single character difference
    assert jellyfish.levenshtein_distance("test", "tent") == 1

    # Complete replacement
    assert jellyfish.levenshtein_distance("abc", "xyz") == 3


@pytest.mark.unit
def test_damerau_levenshtein_distance():
    """Test Damerau-Levenshtein distance (allows transpositions)."""
    import jellyfish

    # Transposition should be distance 1 (not 2 like regular Levenshtein)
    assert jellyfish.damerau_levenshtein_distance("ab", "ba") == 1

    # Compare with regular Levenshtein
    assert jellyfish.levenshtein_distance("ab", "ba") == 2


@pytest.mark.unit
def test_metaphone_encoding():
    """Test Metaphone phonetic encoding."""
    import jellyfish

    # Metaphone should handle similar-sounding words
    metaphone1 = jellyfish.metaphone("Smith")
    metaphone2 = jellyfish.metaphone("Smyth")
    assert metaphone1 == metaphone2


@pytest.mark.unit
def test_nysiis_encoding():
    """Test NYSIIS phonetic encoding."""
    import jellyfish

    # NYSIIS encoding works
    nysiis = jellyfish.nysiis("Macintosh")
    assert isinstance(nysiis, str)
    assert len(nysiis) > 0
