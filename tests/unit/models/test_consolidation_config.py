"""Unit tests for ConsolidationConfig model."""

from uuid import uuid4

import pytest

from kg_builder.models.consolidation_config import (
    DEFAULT_AUTO_MERGE_THRESHOLD,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_FEATURE_WEIGHTS,
    DEFAULT_MAX_BLOCK_SIZE,
    DEFAULT_REVIEW_THRESHOLD,
    ConsolidationConfig,
)


class TestConsolidationConfigDefaults:
    """Tests for ConsolidationConfig default values."""

    def test_default_thresholds(self):
        """Test default threshold values."""
        config = ConsolidationConfig(tenant_id=uuid4())
        assert config.auto_merge_threshold == DEFAULT_AUTO_MERGE_THRESHOLD
        assert config.review_threshold == DEFAULT_REVIEW_THRESHOLD

    def test_default_max_block_size(self):
        """Test default max_block_size."""
        config = ConsolidationConfig(tenant_id=uuid4())
        assert config.max_block_size == DEFAULT_MAX_BLOCK_SIZE

    def test_default_feature_toggles(self):
        """Test default feature toggle values."""
        config = ConsolidationConfig(tenant_id=uuid4())
        assert config.enable_embedding_similarity is True
        assert config.enable_graph_similarity is True
        assert config.enable_auto_consolidation is True

    def test_default_embedding_model(self):
        """Test default embedding model."""
        config = ConsolidationConfig(tenant_id=uuid4())
        assert config.embedding_model == DEFAULT_EMBEDDING_MODEL

    def test_default_feature_weights(self):
        """Test default feature weights."""
        config = ConsolidationConfig(tenant_id=uuid4())
        assert config.feature_weights == DEFAULT_FEATURE_WEIGHTS


class TestConsolidationConfigGetDefaults:
    """Tests for get_defaults class method."""

    def test_get_defaults_returns_complete_config(self):
        """Test get_defaults returns all expected keys."""
        defaults = ConsolidationConfig.get_defaults()

        assert "auto_merge_threshold" in defaults
        assert "review_threshold" in defaults
        assert "max_block_size" in defaults
        assert "enable_embedding_similarity" in defaults
        assert "enable_graph_similarity" in defaults
        assert "enable_auto_consolidation" in defaults
        assert "embedding_model" in defaults
        assert "feature_weights" in defaults

    def test_get_defaults_values(self):
        """Test get_defaults returns correct values."""
        defaults = ConsolidationConfig.get_defaults()

        assert defaults["auto_merge_threshold"] == 0.90
        assert defaults["review_threshold"] == 0.50
        assert defaults["max_block_size"] == 500

    def test_get_defaults_feature_weights_is_copy(self):
        """Test that feature_weights is a copy, not reference."""
        defaults1 = ConsolidationConfig.get_defaults()
        defaults2 = ConsolidationConfig.get_defaults()

        defaults1["feature_weights"]["custom"] = 1.0
        assert "custom" not in defaults2["feature_weights"]


class TestConsolidationConfigGetWeight:
    """Tests for get_weight method."""

    def test_get_weight_existing(self):
        """Test getting weight for existing feature."""
        config = ConsolidationConfig(
            tenant_id=uuid4(),
            feature_weights={"jaro_winkler": 0.5, "custom": 0.8},
        )
        assert config.get_weight("jaro_winkler") == 0.5
        assert config.get_weight("custom") == 0.8

    def test_get_weight_missing_with_default(self):
        """Test fallback to default for missing feature."""
        config = ConsolidationConfig(
            tenant_id=uuid4(),
            feature_weights={},
        )
        # Should fallback to DEFAULT_FEATURE_WEIGHTS
        assert config.get_weight("jaro_winkler") == DEFAULT_FEATURE_WEIGHTS["jaro_winkler"]

    def test_get_weight_missing_no_default(self):
        """Test fallback to 0.0 for unknown feature."""
        config = ConsolidationConfig(
            tenant_id=uuid4(),
            feature_weights={},
        )
        assert config.get_weight("unknown_feature") == 0.0


class TestConsolidationConfigSetWeight:
    """Tests for set_weight method."""

    def test_set_weight_valid(self):
        """Test setting weight with valid value."""
        config = ConsolidationConfig(tenant_id=uuid4())
        config.set_weight("custom", 0.75)
        assert config.feature_weights["custom"] == 0.75

    def test_set_weight_at_boundaries(self):
        """Test setting weights at boundary values."""
        config = ConsolidationConfig(tenant_id=uuid4())
        config.set_weight("min_weight", 0.0)
        config.set_weight("max_weight", 1.0)
        assert config.feature_weights["min_weight"] == 0.0
        assert config.feature_weights["max_weight"] == 1.0

    def test_set_weight_invalid_too_high(self):
        """Test that weight > 1.0 raises ValueError."""
        config = ConsolidationConfig(tenant_id=uuid4())
        with pytest.raises(ValueError) as exc_info:
            config.set_weight("invalid", 1.5)
        assert "between 0.0 and 1.0" in str(exc_info.value)

    def test_set_weight_invalid_negative(self):
        """Test that negative weight raises ValueError."""
        config = ConsolidationConfig(tenant_id=uuid4())
        with pytest.raises(ValueError) as exc_info:
            config.set_weight("invalid", -0.1)
        assert "between 0.0 and 1.0" in str(exc_info.value)


class TestConsolidationConfigIsValid:
    """Tests for is_valid property."""

    def test_is_valid_default_config(self):
        """Test default config is valid."""
        config = ConsolidationConfig(tenant_id=uuid4())
        assert config.is_valid is True

    def test_is_valid_custom_valid_config(self):
        """Test custom valid config."""
        config = ConsolidationConfig(
            tenant_id=uuid4(),
            auto_merge_threshold=0.85,
            review_threshold=0.40,
            max_block_size=1000,
        )
        assert config.is_valid is True

    def test_is_valid_invalid_auto_threshold_high(self):
        """Test invalid when auto_merge_threshold > 1.0."""
        config = ConsolidationConfig(
            tenant_id=uuid4(),
            auto_merge_threshold=1.5,
        )
        assert config.is_valid is False

    def test_is_valid_invalid_auto_threshold_negative(self):
        """Test invalid when auto_merge_threshold < 0.0."""
        config = ConsolidationConfig(
            tenant_id=uuid4(),
            auto_merge_threshold=-0.1,
        )
        assert config.is_valid is False

    def test_is_valid_invalid_review_threshold_high(self):
        """Test invalid when review_threshold > 1.0."""
        config = ConsolidationConfig(
            tenant_id=uuid4(),
            review_threshold=1.5,
        )
        assert config.is_valid is False

    def test_is_valid_invalid_review_gte_auto(self):
        """Test invalid when review_threshold >= auto_merge_threshold."""
        config = ConsolidationConfig(
            tenant_id=uuid4(),
            auto_merge_threshold=0.80,
            review_threshold=0.85,  # Greater than auto
        )
        assert config.is_valid is False

    def test_is_valid_invalid_review_equal_auto(self):
        """Test invalid when review_threshold == auto_merge_threshold."""
        config = ConsolidationConfig(
            tenant_id=uuid4(),
            auto_merge_threshold=0.80,
            review_threshold=0.80,  # Equal to auto
        )
        assert config.is_valid is False

    def test_is_valid_invalid_max_block_size_zero(self):
        """Test invalid when max_block_size is zero."""
        config = ConsolidationConfig(
            tenant_id=uuid4(),
            max_block_size=0,
        )
        assert config.is_valid is False

    def test_is_valid_invalid_max_block_size_negative(self):
        """Test invalid when max_block_size is negative."""
        config = ConsolidationConfig(
            tenant_id=uuid4(),
            max_block_size=-100,
        )
        assert config.is_valid is False


class TestConsolidationConfigFeatureWeightsIndependence:
    """Tests for feature weights instance independence."""

    def test_feature_weights_not_shared(self):
        """Test that feature weights are not shared between instances."""
        config1 = ConsolidationConfig(tenant_id=uuid4())
        config2 = ConsolidationConfig(tenant_id=uuid4())

        config1.feature_weights["custom"] = 1.0
        assert "custom" not in config2.feature_weights

    def test_feature_weights_not_affected_by_default(self):
        """Test that modifying instance doesn't affect DEFAULT_FEATURE_WEIGHTS."""
        config = ConsolidationConfig(tenant_id=uuid4())
        original_jaro = DEFAULT_FEATURE_WEIGHTS["jaro_winkler"]

        config.feature_weights["jaro_winkler"] = 0.99
        assert DEFAULT_FEATURE_WEIGHTS["jaro_winkler"] == original_jaro


class TestConsolidationConfigRepr:
    """Tests for ConsolidationConfig string representation."""

    def test_repr_contains_tenant_id(self):
        """Test repr contains tenant_id."""
        tenant_id = uuid4()
        config = ConsolidationConfig(tenant_id=tenant_id)
        repr_str = repr(config)
        assert str(tenant_id) in repr_str

    def test_repr_contains_thresholds(self):
        """Test repr contains threshold values."""
        config = ConsolidationConfig(
            tenant_id=uuid4(),
            auto_merge_threshold=0.85,
            review_threshold=0.45,
        )
        repr_str = repr(config)
        assert "0.85" in repr_str
        assert "0.45" in repr_str
