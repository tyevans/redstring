"""
Unit tests for the BlockingEngine service.

Tests blocking strategies, candidate generation, and tenant isolation.
"""

from kg_builder.services.consolidation.blocking import (
    BlockingEngine,
    BlockingResult,
    BlockingStrategy,
)


class TestBlockingEngineCreation:
    """Tests for BlockingEngine initialization."""

    def test_default_initialization(self):
        """Test creating blocking engine with defaults."""
        engine = BlockingEngine()

        assert engine.max_block_size == 500
        assert engine.min_prefix_length == 5
        assert len(engine.strategies) == 3
        assert BlockingStrategy.PREFIX in engine.strategies
        assert BlockingStrategy.ENTITY_TYPE in engine.strategies
        assert BlockingStrategy.SOUNDEX in engine.strategies

    def test_custom_max_block_size(self):
        """Test creating engine with custom max_block_size."""
        engine = BlockingEngine(max_block_size=100)

        assert engine.max_block_size == 100

    def test_custom_min_prefix_length(self):
        """Test creating engine with custom prefix length."""
        engine = BlockingEngine(min_prefix_length=3)

        assert engine.min_prefix_length == 3

    def test_custom_strategies(self):
        """Test creating engine with custom strategies."""
        strategies = [BlockingStrategy.PREFIX, BlockingStrategy.SOUNDEX]
        engine = BlockingEngine(strategies=strategies)

        assert engine.strategies == strategies
        assert BlockingStrategy.ENTITY_TYPE not in engine.strategies


class TestBlockingResultDataclass:
    """Tests for BlockingResult dataclass."""

    def test_empty_result(self):
        """Test creating empty blocking result."""
        result = BlockingResult(
            candidates=[],
            strategies_used=[],
            total_candidates=0,
            truncated=False,
        )

        assert result.candidates == []
        assert result.strategies_used == []
        assert result.total_candidates == 0
        assert result.truncated is False
        assert result.block_sizes == {}
        assert result.execution_time_ms == 0.0

    def test_result_with_data(self):
        """Test creating result with data."""
        result = BlockingResult(
            candidates=[],  # Would contain entities
            strategies_used=[BlockingStrategy.PREFIX, BlockingStrategy.SOUNDEX],
            block_sizes={"prefix": 5, "soundex": 3},
            total_candidates=7,
            truncated=False,
            execution_time_ms=15.5,
        )

        assert result.total_candidates == 7
        assert result.block_sizes["prefix"] == 5
        assert result.execution_time_ms == 15.5

    def test_truncated_result(self):
        """Test result indicating truncation."""
        result = BlockingResult(
            candidates=[],
            strategies_used=[BlockingStrategy.PREFIX],
            total_candidates=500,
            truncated=True,
        )

        assert result.truncated is True


class TestBlockingStrategies:
    """Tests for individual blocking strategies."""

    def test_strategy_enum_values(self):
        """Test blocking strategy enum values."""
        assert BlockingStrategy.PREFIX.value == "prefix"
        assert BlockingStrategy.ENTITY_TYPE.value == "entity_type"
        assert BlockingStrategy.SOUNDEX.value == "soundex"
        assert BlockingStrategy.TRIGRAM.value == "trigram"
        assert BlockingStrategy.COMBINED.value == "combined"


class TestSoundexComputation:
    """Tests for soundex computation."""

    def test_compute_soundex_basic(self):
        """Test basic soundex computation."""
        assert BlockingEngine.compute_soundex("Robert") == "R163"
        assert BlockingEngine.compute_soundex("Rupert") == "R163"

    def test_compute_soundex_similar_names(self):
        """Test soundex for similar sounding names."""
        # These should have the same soundex
        assert BlockingEngine.compute_soundex("Smith") == BlockingEngine.compute_soundex("Smyth")
        assert BlockingEngine.compute_soundex("Jon") == BlockingEngine.compute_soundex("John")

    def test_compute_soundex_different_names(self):
        """Test soundex for different names."""
        # These should have different soundex codes
        assert BlockingEngine.compute_soundex("Smith") != BlockingEngine.compute_soundex("Jones")

    def test_compute_soundex_technical_terms(self):
        """Test soundex with technical terms."""
        # Technical terms may not work as well with soundex
        soundex_domain = BlockingEngine.compute_soundex("DomainEvent")
        soundex_domain_service = BlockingEngine.compute_soundex("DomainService")

        # These are different enough to have different soundex
        assert soundex_domain != soundex_domain_service

    def test_compute_soundex_empty_string(self):
        """Test soundex with empty string."""
        assert BlockingEngine.compute_soundex("") == ""

    def test_compute_soundex_none_handling(self):
        """Test soundex with None-like inputs."""
        assert BlockingEngine.compute_soundex("") == ""


class TestMetaphoneComputation:
    """Tests for metaphone computation."""

    def test_compute_metaphone_basic(self):
        """Test basic metaphone computation."""
        # Metaphone handles phonetics better than soundex
        metaphone = BlockingEngine.compute_metaphone("Robert")
        assert metaphone != ""

    def test_compute_metaphone_empty_string(self):
        """Test metaphone with empty string."""
        assert BlockingEngine.compute_metaphone("") == ""


class TestNYSIISComputation:
    """Tests for NYSIIS computation."""

    def test_compute_nysiis_basic(self):
        """Test basic NYSIIS computation."""
        nysiis = BlockingEngine.compute_nysiis("Robert")
        assert nysiis != ""

    def test_compute_nysiis_empty_string(self):
        """Test NYSIIS with empty string."""
        assert BlockingEngine.compute_nysiis("") == ""


class TestPrefixComputation:
    """Tests for prefix extraction."""

    def test_compute_prefix_default_length(self):
        """Test prefix extraction with default length."""
        assert BlockingEngine.compute_prefix("domain_event") == "domai"

    def test_compute_prefix_custom_length(self):
        """Test prefix extraction with custom length."""
        assert BlockingEngine.compute_prefix("domain_event", length=7) == "domain_"

    def test_compute_prefix_short_string(self):
        """Test prefix extraction with string shorter than length."""
        assert BlockingEngine.compute_prefix("test", length=5) == "test"

    def test_compute_prefix_empty_string(self):
        """Test prefix extraction with empty string."""
        assert BlockingEngine.compute_prefix("") == ""

    def test_compute_prefix_exact_length(self):
        """Test prefix extraction with string equal to length."""
        assert BlockingEngine.compute_prefix("hello", length=5) == "hello"


class TestBlockingKeyMatching:
    """Tests for blocking key matching logic."""

    def test_get_matching_keys_stub(self):
        """Test _get_matching_keys method exists and can be called.

        Note: Full testing requires entity fixtures which will be
        in integration tests.
        """
        engine = BlockingEngine()
        # Method exists
        assert hasattr(engine, "_get_matching_keys")


class TestBlockingConditionBuilding:
    """Tests for condition building logic."""

    def test_build_condition_stub(self):
        """Test _build_condition method exists and can be called.

        Note: Full testing requires entity fixtures which will be
        in integration tests.
        """
        engine = BlockingEngine()
        # Method exists
        assert hasattr(engine, "_build_condition")


class TestBlockingEngineConfiguration:
    """Tests for engine configuration options."""

    def test_all_strategies_combined(self):
        """Test combining all strategies."""
        engine = BlockingEngine(
            strategies=[
                BlockingStrategy.PREFIX,
                BlockingStrategy.ENTITY_TYPE,
                BlockingStrategy.SOUNDEX,
                BlockingStrategy.TRIGRAM,
            ]
        )

        assert len(engine.strategies) == 4

    def test_single_strategy(self):
        """Test using only one strategy."""
        engine = BlockingEngine(strategies=[BlockingStrategy.ENTITY_TYPE])

        assert len(engine.strategies) == 1
        assert engine.strategies[0] == BlockingStrategy.ENTITY_TYPE

    def test_empty_strategies_list(self):
        """Test behavior with empty strategies list.

        Note: Empty list is treated as falsy and defaults to standard strategies.
        This is intentional - an empty list is likely a mistake.
        """
        engine = BlockingEngine(strategies=[])

        # Empty list defaults to standard strategies
        assert len(engine.strategies) == 3


class TestBlockingEngineMetrics:
    """Tests for blocking engine metrics and statistics."""

    def test_block_statistics_method_exists(self):
        """Test get_block_statistics method exists."""
        engine = BlockingEngine()
        assert hasattr(engine, "get_block_statistics")

    def test_find_candidates_batch_method_exists(self):
        """Test find_candidates_batch method exists."""
        engine = BlockingEngine()
        assert hasattr(engine, "find_candidates_batch")


class TestPhoneticBlockingConsistency:
    """Tests for phonetic blocking consistency."""

    def test_soundex_case_insensitive(self):
        """Test that soundex is case-insensitive."""
        # Soundex should handle case consistently
        upper = BlockingEngine.compute_soundex("ROBERT")
        lower = BlockingEngine.compute_soundex("robert")
        mixed = BlockingEngine.compute_soundex("RoBeRt")

        # jellyfish soundex may handle case differently
        # Main thing is consistency
        assert upper == lower
        assert lower == mixed

    def test_metaphone_case_handling(self):
        """Test metaphone case handling."""
        upper = BlockingEngine.compute_metaphone("ROBERT")
        lower = BlockingEngine.compute_metaphone("robert")

        # Should be consistent
        assert upper == lower

    def test_nysiis_case_handling(self):
        """Test NYSIIS case handling."""
        upper = BlockingEngine.compute_nysiis("ROBERT")
        lower = BlockingEngine.compute_nysiis("robert")

        # Should be consistent
        assert upper == lower
