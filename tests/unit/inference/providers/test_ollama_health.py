"""Tests for OllamaProvider.health_check timestamp handling."""

from datetime import UTC
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from kg_builder.inference.providers.ollama import OllamaProvider


@pytest.mark.unit
@pytest.mark.asyncio
async def test_health_check_checked_at_is_timezone_aware():
    """ProviderHealth.checked_at must be tz-aware; datetime.utcnow() is naive."""
    provider = OllamaProvider(base_url="http://localhost:11434")

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {"models": []}

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.return_value = mock_response

    with patch.object(provider, "_get_client", return_value=mock_client):
        result = await provider.health_check()

    assert result.checked_at.tzinfo is not None
    assert result.checked_at.tzinfo == UTC or result.checked_at.utcoffset() is not None
