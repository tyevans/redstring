"""Tests for ProviderHealth.checked_at's default_factory."""

import pytest

from kg_builder.inference.providers.base import ProviderHealth, ProviderStatus


@pytest.mark.unit
def test_checked_at_default_is_timezone_aware():
    """The default_factory path (checked_at omitted) must produce a tz-aware
    datetime; datetime.utcnow is naive and deprecated since Python 3.12.
    """
    health = ProviderHealth(status=ProviderStatus.HEALTHY)

    assert health.checked_at.tzinfo is not None
