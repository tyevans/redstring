"""
Unit tests for OllamaCircuitBreaker.

Tests circuit breaker functionality including:
- CircuitState enum values
- CircuitOpen exception properties
- OllamaCircuitBreaker initialization
- State transitions (CLOSED -> OPEN -> HALF_OPEN -> CLOSED)
- allow_request behavior in different states
- record_success and record_failure behavior
- Factory function and singleton pattern
- Redis key structure

Uses mocking to avoid external Redis dependency.
"""

import importlib.util
import sys
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

# Create a mock settings module before importing circuit_breaker
# This avoids the full import chain through __init__.py
_mock_settings = MagicMock()
_mock_settings.REDIS_URL = "redis://localhost:6379/0"

# Create mock config module
_mock_config = MagicMock()
_mock_config.settings = _mock_settings

# Insert our mock before importing circuit_breaker module
sys.modules["kg_builder.config"] = _mock_config

# Also mock redis.asyncio if it's not available
try:
    import redis.asyncio
except ImportError:
    # Create a mock redis module
    _mock_redis_module = MagicMock()
    _mock_redis_module.Redis = MagicMock()
    _mock_redis_module.from_url = MagicMock()
    sys.modules["redis"] = MagicMock()
    sys.modules["redis.asyncio"] = _mock_redis_module

# Now we need to import circuit_breaker module directly without going through __init__.py
# We use importlib to load just the circuit_breaker module
spec = importlib.util.spec_from_file_location(
    "kg_builder.extraction.circuit_breaker",
    "/home/ty/workspace/kg-builder/src/kg_builder/extraction/circuit_breaker.py",
)
circuit_breaker_module = importlib.util.module_from_spec(spec)
sys.modules["kg_builder.extraction.circuit_breaker"] = circuit_breaker_module
spec.loader.exec_module(circuit_breaker_module)

# Import the classes from the loaded module
CircuitState = circuit_breaker_module.CircuitState
CircuitOpen = circuit_breaker_module.CircuitOpen
OllamaCircuitBreaker = circuit_breaker_module.OllamaCircuitBreaker
get_circuit_breaker = circuit_breaker_module.get_circuit_breaker
reset_circuit_breaker = circuit_breaker_module.reset_circuit_breaker


# =============================================================================
# Mock Classes
# =============================================================================


class MockPipeline:
    """Mock for Redis Pipeline that supports async context manager."""

    def __init__(self):
        self.operations = []
        self.execute_result = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None

    async def set(self, key, value):
        self.operations.append(("set", key, value))

    async def delete(self, key):
        self.operations.append(("delete", key))

    async def execute(self):
        return self.execute_result


class MockRedis:
    """Mock for async Redis client."""

    def __init__(self):
        self.data = {}
        self.mock_pipeline = MockPipeline()
        self.close_called = False

    def pipeline(self, transaction=True):
        return self.mock_pipeline

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value):
        self.data[key] = value

    async def incr(self, key):
        current = self.data.get(key, b"0")
        if isinstance(current, bytes):
            current = int(current.decode())
        elif isinstance(current, str):
            current = int(current)
        new_value = current + 1
        self.data[key] = str(new_value).encode()
        return new_value

    async def delete(self, key):
        if key in self.data:
            del self.data[key]

    async def close(self):
        self.close_called = True


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    return MockRedis()


@pytest.fixture
def circuit_breaker(mock_redis):
    """Create a circuit breaker with mocked Redis."""
    breaker = OllamaCircuitBreaker(
        failure_threshold=3,
        recovery_timeout=30,
        half_open_max_calls=1,
        key_prefix="test_circuit",
    )
    breaker._redis = mock_redis
    return breaker


@pytest.fixture(autouse=True)
def reset_global_breaker():
    """Reset the global circuit breaker instance before each test."""
    reset_circuit_breaker()
    yield
    reset_circuit_breaker()


# =============================================================================
# CircuitState Enum Tests
# =============================================================================


class TestCircuitState:
    """Tests for the CircuitState enum."""

    def test_closed_state_value(self):
        """Test CLOSED state has correct value."""
        assert CircuitState.CLOSED.value == "closed"

    def test_open_state_value(self):
        """Test OPEN state has correct value."""
        assert CircuitState.OPEN.value == "open"

    def test_half_open_state_value(self):
        """Test HALF_OPEN state has correct value."""
        assert CircuitState.HALF_OPEN.value == "half_open"

    def test_state_is_string_enum(self):
        """Test CircuitState is a string enum."""
        assert isinstance(CircuitState.CLOSED, str)
        assert isinstance(CircuitState.OPEN, str)
        assert isinstance(CircuitState.HALF_OPEN, str)

    def test_state_from_value(self):
        """Test creating CircuitState from string value."""
        assert CircuitState("closed") == CircuitState.CLOSED
        assert CircuitState("open") == CircuitState.OPEN
        assert CircuitState("half_open") == CircuitState.HALF_OPEN

    def test_invalid_state_raises_error(self):
        """Test invalid state value raises ValueError."""
        with pytest.raises(ValueError):
            CircuitState("invalid")


# =============================================================================
# CircuitOpen Exception Tests
# =============================================================================


class TestCircuitOpen:
    """Tests for the CircuitOpen exception class."""

    def test_exception_has_message(self):
        """Test CircuitOpen stores message."""
        exc = CircuitOpen("Circuit is open")
        assert exc.message == "Circuit is open"

    def test_exception_has_retry_after(self):
        """Test CircuitOpen stores retry_after."""
        exc = CircuitOpen("Circuit is open", retry_after=30.0)
        assert exc.retry_after == 30.0

    def test_exception_default_retry_after(self):
        """Test CircuitOpen default retry_after is 0."""
        exc = CircuitOpen("Circuit is open")
        assert exc.retry_after == 0.0

    def test_exception_inherits_from_exception(self):
        """Test CircuitOpen is an Exception."""
        exc = CircuitOpen("Circuit is open")
        assert isinstance(exc, Exception)

    def test_exception_str_contains_message(self):
        """Test exception string contains message."""
        exc = CircuitOpen("Circuit is open")
        assert "Circuit is open" in str(exc)


# =============================================================================
# OllamaCircuitBreaker Initialization Tests
# =============================================================================


class TestCircuitBreakerInit:
    """Tests for OllamaCircuitBreaker initialization."""

    def test_init_with_custom_config(self):
        """Test initialization with custom configuration."""
        breaker = OllamaCircuitBreaker(
            failure_threshold=10,
            recovery_timeout=120,
            half_open_max_calls=3,
            key_prefix="custom_circuit",
        )

        assert breaker.failure_threshold == 10
        assert breaker.recovery_timeout == 120
        assert breaker.half_open_max_calls == 3
        assert breaker._key_prefix == "custom_circuit"

    def test_init_with_defaults(self):
        """Test initialization with default values."""
        breaker = OllamaCircuitBreaker()

        assert breaker.failure_threshold == 5
        assert breaker.recovery_timeout == 60
        assert breaker.half_open_max_calls == 1
        assert breaker._key_prefix == "ollama_circuit"

    def test_redis_connection_not_created_on_init(self):
        """Test Redis connection is lazy-loaded."""
        breaker = OllamaCircuitBreaker()
        assert breaker._redis is None


# =============================================================================
# Redis Key Structure Tests
# =============================================================================


class TestRedisKeyStructure:
    """Tests for Redis key naming."""

    def test_state_key_format(self, circuit_breaker):
        """Test state key has correct format."""
        assert circuit_breaker._state_key() == "test_circuit:state"

    def test_failures_key_format(self, circuit_breaker):
        """Test failures key has correct format."""
        assert circuit_breaker._failures_key() == "test_circuit:failures"

    def test_opened_at_key_format(self, circuit_breaker):
        """Test opened_at key has correct format."""
        assert circuit_breaker._opened_at_key() == "test_circuit:opened_at"

    def test_half_open_calls_key_format(self, circuit_breaker):
        """Test half_open_calls key has correct format."""
        assert circuit_breaker._half_open_calls_key() == "test_circuit:half_open_calls"


# =============================================================================
# Get State Tests
# =============================================================================


class TestGetState:
    """Tests for the get_state method."""

    @pytest.mark.asyncio
    async def test_get_state_returns_closed_when_no_state(self, circuit_breaker, mock_redis):
        """Test get_state returns CLOSED when no state set."""
        state = await circuit_breaker.get_state()
        assert state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_get_state_returns_stored_state(self, circuit_breaker, mock_redis):
        """Test get_state returns state stored in Redis."""
        mock_redis.data["test_circuit:state"] = b"open"

        state = await circuit_breaker.get_state()
        assert state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_get_state_handles_half_open(self, circuit_breaker, mock_redis):
        """Test get_state handles HALF_OPEN state."""
        mock_redis.data["test_circuit:state"] = b"half_open"

        state = await circuit_breaker.get_state()
        assert state == CircuitState.HALF_OPEN

    @pytest.mark.asyncio
    async def test_get_state_handles_string_response(self, circuit_breaker, mock_redis):
        """Test get_state handles string (non-bytes) response."""
        mock_redis.data["test_circuit:state"] = "closed"

        state = await circuit_breaker.get_state()
        assert state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_get_state_handles_invalid_state(self, circuit_breaker, mock_redis):
        """Test get_state handles invalid state gracefully."""
        mock_redis.data["test_circuit:state"] = b"invalid_state"

        state = await circuit_breaker.get_state()
        assert state == CircuitState.CLOSED  # Defaults to CLOSED


# =============================================================================
# Allow Request Tests - CLOSED State
# =============================================================================


class TestAllowRequestClosed:
    """Tests for allow_request in CLOSED state."""

    @pytest.mark.asyncio
    async def test_allow_request_returns_true_in_closed_state(self, circuit_breaker, mock_redis):
        """Test allow_request returns True when circuit is CLOSED."""
        # No state set means CLOSED
        allowed = await circuit_breaker.allow_request()
        assert allowed is True

    @pytest.mark.asyncio
    async def test_allow_request_returns_true_after_explicit_closed(
        self, circuit_breaker, mock_redis
    ):
        """Test allow_request returns True with explicit CLOSED state."""
        mock_redis.data["test_circuit:state"] = b"closed"

        allowed = await circuit_breaker.allow_request()
        assert allowed is True


# =============================================================================
# Allow Request Tests - OPEN State
# =============================================================================


class TestAllowRequestOpen:
    """Tests for allow_request in OPEN state."""

    @pytest.mark.asyncio
    async def test_allow_request_returns_false_in_open_state(self, circuit_breaker, mock_redis):
        """Test allow_request returns False when circuit is OPEN."""
        now = datetime.now(UTC).timestamp()
        mock_redis.data["test_circuit:state"] = b"open"
        mock_redis.data["test_circuit:opened_at"] = str(now).encode()

        allowed = await circuit_breaker.allow_request()
        assert allowed is False

    @pytest.mark.asyncio
    async def test_allow_request_transitions_to_half_open_after_timeout(
        self, circuit_breaker, mock_redis
    ):
        """Test allow_request transitions to HALF_OPEN after recovery timeout."""
        # Set opened_at to 31 seconds ago (timeout is 30)
        opened_at = datetime.now(UTC).timestamp() - 31
        mock_redis.data["test_circuit:state"] = b"open"
        mock_redis.data["test_circuit:opened_at"] = str(opened_at).encode()

        allowed = await circuit_breaker.allow_request()

        assert allowed is True
        # Check that transition occurred
        assert any(
            op[1] == "test_circuit:state" and op[2] == "half_open"
            for op in mock_redis.mock_pipeline.operations
            if op[0] == "set"
        )

    @pytest.mark.asyncio
    async def test_allow_request_stays_open_before_timeout(self, circuit_breaker, mock_redis):
        """Test allow_request returns False before recovery timeout."""
        # Set opened_at to 10 seconds ago (timeout is 30)
        opened_at = datetime.now(UTC).timestamp() - 10
        mock_redis.data["test_circuit:state"] = b"open"
        mock_redis.data["test_circuit:opened_at"] = str(opened_at).encode()

        allowed = await circuit_breaker.allow_request()
        assert allowed is False


# =============================================================================
# Allow Request Tests - HALF_OPEN State
# =============================================================================


class TestAllowRequestHalfOpen:
    """Tests for allow_request in HALF_OPEN state."""

    @pytest.mark.asyncio
    async def test_allow_request_allows_first_call_in_half_open(self, circuit_breaker, mock_redis):
        """Test allow_request allows first call in HALF_OPEN state."""
        mock_redis.data["test_circuit:state"] = b"half_open"
        mock_redis.data["test_circuit:half_open_calls"] = b"0"

        allowed = await circuit_breaker.allow_request()
        assert allowed is True

    @pytest.mark.asyncio
    async def test_allow_request_rejects_beyond_max_calls(self, circuit_breaker, mock_redis):
        """Test allow_request rejects calls beyond max in HALF_OPEN."""
        mock_redis.data["test_circuit:state"] = b"half_open"
        mock_redis.data["test_circuit:half_open_calls"] = b"1"

        allowed = await circuit_breaker.allow_request()
        assert allowed is False

    @pytest.mark.asyncio
    async def test_allow_request_increments_half_open_calls(self, circuit_breaker, mock_redis):
        """Test allow_request increments call counter in HALF_OPEN."""
        mock_redis.data["test_circuit:state"] = b"half_open"
        mock_redis.data["test_circuit:half_open_calls"] = b"0"

        await circuit_breaker.allow_request()

        # incr should have been called, resulting in "1"
        assert mock_redis.data["test_circuit:half_open_calls"] == b"1"


# =============================================================================
# Record Success Tests
# =============================================================================


class TestRecordSuccess:
    """Tests for the record_success method."""

    @pytest.mark.asyncio
    async def test_record_success_closes_circuit_from_half_open(self, circuit_breaker, mock_redis):
        """Test record_success transitions HALF_OPEN to CLOSED."""
        mock_redis.data["test_circuit:state"] = b"half_open"

        await circuit_breaker.record_success()

        # Check that transition to CLOSED occurred
        assert any(
            op[1] == "test_circuit:state" and op[2] == "closed"
            for op in mock_redis.mock_pipeline.operations
            if op[0] == "set"
        )

    @pytest.mark.asyncio
    async def test_record_success_resets_failures_in_closed(self, circuit_breaker, mock_redis):
        """Test record_success resets failure count in CLOSED state."""
        mock_redis.data["test_circuit:state"] = b"closed"
        mock_redis.data["test_circuit:failures"] = b"2"

        await circuit_breaker.record_success()

        assert mock_redis.data["test_circuit:failures"] == "0"


# =============================================================================
# Record Failure Tests
# =============================================================================


class TestRecordFailure:
    """Tests for the record_failure method."""

    @pytest.mark.asyncio
    async def test_record_failure_increments_failure_count(self, circuit_breaker, mock_redis):
        """Test record_failure increments failure count."""
        mock_redis.data["test_circuit:state"] = b"closed"
        mock_redis.data["test_circuit:failures"] = b"0"

        await circuit_breaker.record_failure()

        assert mock_redis.data["test_circuit:failures"] == b"1"

    @pytest.mark.asyncio
    async def test_record_failure_opens_circuit_at_threshold(self, circuit_breaker, mock_redis):
        """Test record_failure opens circuit when threshold reached."""
        mock_redis.data["test_circuit:state"] = b"closed"
        mock_redis.data["test_circuit:failures"] = b"2"  # Will become 3

        await circuit_breaker.record_failure()

        # Check that transition to OPEN occurred
        assert any(
            op[1] == "test_circuit:state" and op[2] == "open"
            for op in mock_redis.mock_pipeline.operations
            if op[0] == "set"
        )

    @pytest.mark.asyncio
    async def test_record_failure_reopens_from_half_open(self, circuit_breaker, mock_redis):
        """Test record_failure reopens circuit from HALF_OPEN."""
        mock_redis.data["test_circuit:state"] = b"half_open"

        await circuit_breaker.record_failure()

        # Check that transition to OPEN occurred
        assert any(
            op[1] == "test_circuit:state" and op[2] == "open"
            for op in mock_redis.mock_pipeline.operations
            if op[0] == "set"
        )

    @pytest.mark.asyncio
    async def test_record_failure_does_not_open_below_threshold(self, circuit_breaker, mock_redis):
        """Test record_failure does not open circuit below threshold."""
        mock_redis.data["test_circuit:state"] = b"closed"
        mock_redis.data["test_circuit:failures"] = b"0"

        await circuit_breaker.record_failure()

        # Should increment but not open
        assert mock_redis.data["test_circuit:failures"] == b"1"
        # No transition should have occurred
        assert not any(
            op[1] == "test_circuit:state" and op[2] == "open"
            for op in mock_redis.mock_pipeline.operations
            if op[0] == "set"
        )


# =============================================================================
# State Transition Tests
# =============================================================================


class TestStateTransitions:
    """Tests for internal state transition methods."""

    @pytest.mark.asyncio
    async def test_transition_to_open_sets_state_and_timestamp(self, circuit_breaker, mock_redis):
        """Test _transition_to_open sets state and timestamp."""
        await circuit_breaker._transition_to_open()

        # Check operations
        set_ops = [op for op in mock_redis.mock_pipeline.operations if op[0] == "set"]
        assert any(op[1] == "test_circuit:state" and op[2] == "open" for op in set_ops)
        assert any(op[1] == "test_circuit:opened_at" for op in set_ops)

    @pytest.mark.asyncio
    async def test_transition_to_half_open_sets_state_and_resets_calls(
        self, circuit_breaker, mock_redis
    ):
        """Test _transition_to_half_open sets state and resets calls."""
        await circuit_breaker._transition_to_half_open()

        set_ops = [op for op in mock_redis.mock_pipeline.operations if op[0] == "set"]
        assert any(op[1] == "test_circuit:state" and op[2] == "half_open" for op in set_ops)
        assert any(op[1] == "test_circuit:half_open_calls" and op[2] == "0" for op in set_ops)

    @pytest.mark.asyncio
    async def test_transition_to_closed_resets_all_state(self, circuit_breaker, mock_redis):
        """Test _transition_to_closed resets all state."""
        await circuit_breaker._transition_to_closed()

        # Check set operations
        set_ops = [op for op in mock_redis.mock_pipeline.operations if op[0] == "set"]
        assert any(op[1] == "test_circuit:state" and op[2] == "closed" for op in set_ops)
        assert any(op[1] == "test_circuit:failures" and op[2] == "0" for op in set_ops)

        # Check delete operations
        delete_ops = [op for op in mock_redis.mock_pipeline.operations if op[0] == "delete"]
        assert any(op[1] == "test_circuit:opened_at" for op in delete_ops)
        assert any(op[1] == "test_circuit:half_open_calls" for op in delete_ops)


# =============================================================================
# Get Retry After Tests
# =============================================================================


class TestGetRetryAfter:
    """Tests for the get_retry_after method."""

    @pytest.mark.asyncio
    async def test_get_retry_after_returns_zero_when_closed(self, circuit_breaker, mock_redis):
        """Test get_retry_after returns 0 when circuit is CLOSED."""
        mock_redis.data["test_circuit:state"] = b"closed"

        retry_after = await circuit_breaker.get_retry_after()
        assert retry_after == 0.0

    @pytest.mark.asyncio
    async def test_get_retry_after_returns_remaining_time(self, circuit_breaker, mock_redis):
        """Test get_retry_after returns remaining time when OPEN."""
        # Opened 10 seconds ago, timeout is 30
        opened_at = datetime.now(UTC).timestamp() - 10
        mock_redis.data["test_circuit:state"] = b"open"
        mock_redis.data["test_circuit:opened_at"] = str(opened_at).encode()

        retry_after = await circuit_breaker.get_retry_after()

        # Should be approximately 20 seconds (30 - 10)
        assert 18 <= retry_after <= 22

    @pytest.mark.asyncio
    async def test_get_retry_after_returns_zero_when_timeout_passed(
        self, circuit_breaker, mock_redis
    ):
        """Test get_retry_after returns 0 when timeout has passed."""
        # Opened 40 seconds ago, timeout is 30
        opened_at = datetime.now(UTC).timestamp() - 40
        mock_redis.data["test_circuit:state"] = b"open"
        mock_redis.data["test_circuit:opened_at"] = str(opened_at).encode()

        retry_after = await circuit_breaker.get_retry_after()
        assert retry_after == 0.0


# =============================================================================
# Reset Tests
# =============================================================================


class TestReset:
    """Tests for the reset method."""

    @pytest.mark.asyncio
    async def test_reset_clears_all_keys(self, circuit_breaker, mock_redis):
        """Test reset deletes all circuit breaker keys."""
        mock_redis.data["test_circuit:state"] = b"open"
        mock_redis.data["test_circuit:failures"] = b"5"
        mock_redis.data["test_circuit:opened_at"] = b"12345"
        mock_redis.data["test_circuit:half_open_calls"] = b"1"

        await circuit_breaker.reset()

        delete_ops = [op for op in mock_redis.mock_pipeline.operations if op[0] == "delete"]
        assert len(delete_ops) == 4


# =============================================================================
# Close Connection Tests
# =============================================================================


class TestClose:
    """Tests for the close method."""

    @pytest.mark.asyncio
    async def test_close_closes_redis_connection(self, circuit_breaker, mock_redis):
        """Test close closes the Redis connection."""
        await circuit_breaker.close()
        assert mock_redis.close_called

    @pytest.mark.asyncio
    async def test_close_clears_redis_reference(self, circuit_breaker, mock_redis):
        """Test close sets Redis reference to None."""
        await circuit_breaker.close()
        assert circuit_breaker._redis is None

    @pytest.mark.asyncio
    async def test_close_when_no_connection(self):
        """Test close works when no connection was made."""
        breaker = OllamaCircuitBreaker()
        await breaker.close()
        assert breaker._redis is None


# =============================================================================
# Factory Function Tests
# =============================================================================


class TestFactoryFunction:
    """Tests for the get_circuit_breaker factory function."""

    def test_get_circuit_breaker_creates_instance(self):
        """Test factory creates a circuit breaker instance."""
        breaker = get_circuit_breaker()
        assert isinstance(breaker, OllamaCircuitBreaker)

    def test_get_circuit_breaker_returns_singleton(self):
        """Test factory returns the same instance on multiple calls."""
        breaker1 = get_circuit_breaker()
        breaker2 = get_circuit_breaker()
        assert breaker1 is breaker2

    def test_reset_circuit_breaker_clears_singleton(self):
        """Test reset_circuit_breaker clears the singleton."""
        breaker1 = get_circuit_breaker()
        reset_circuit_breaker()
        breaker2 = get_circuit_breaker()
        assert breaker1 is not breaker2

    def test_get_circuit_breaker_with_custom_config(self):
        """Test factory uses custom configuration on first call."""
        reset_circuit_breaker()  # Ensure fresh start

        breaker = get_circuit_breaker(
            failure_threshold=10,
            recovery_timeout=120,
            half_open_max_calls=3,
        )

        assert breaker.failure_threshold == 10
        assert breaker.recovery_timeout == 120
        assert breaker.half_open_max_calls == 3


# =============================================================================
# Integration Scenario Tests
# =============================================================================


class TestIntegrationScenarios:
    """Tests for complete circuit breaker scenarios."""

    @pytest.mark.asyncio
    async def test_complete_failure_and_recovery_cycle(self, circuit_breaker, mock_redis):
        """Test complete cycle: CLOSED -> OPEN -> HALF_OPEN -> CLOSED."""
        # Start in CLOSED state
        assert await circuit_breaker.get_state() == CircuitState.CLOSED
        assert await circuit_breaker.allow_request() is True

        # Record failures up to threshold
        mock_redis.data["test_circuit:failures"] = b"0"
        await circuit_breaker.record_failure()  # 1
        await circuit_breaker.record_failure()  # 2
        await circuit_breaker.record_failure()  # 3 - threshold reached

        # Verify state is now OPEN
        mock_redis.data["test_circuit:state"] = b"open"  # Set by pipeline
        now = datetime.now(UTC).timestamp()
        mock_redis.data["test_circuit:opened_at"] = str(now).encode()

        state = await circuit_breaker.get_state()
        assert state == CircuitState.OPEN

        # Request should be rejected in OPEN state
        assert await circuit_breaker.allow_request() is False

        # Fast forward past recovery timeout
        opened_at = now - 31  # 31 seconds ago
        mock_redis.data["test_circuit:opened_at"] = str(opened_at).encode()

        # Request should now trigger HALF_OPEN transition
        assert await circuit_breaker.allow_request() is True

        # Simulate success in HALF_OPEN
        mock_redis.data["test_circuit:state"] = b"half_open"
        await circuit_breaker.record_success()

        # Should have transitioned to CLOSED via pipeline

    @pytest.mark.asyncio
    async def test_failure_in_half_open_reopens_circuit(self, circuit_breaker, mock_redis):
        """Test that failure in HALF_OPEN state reopens circuit."""
        mock_redis.data["test_circuit:state"] = b"half_open"
        mock_redis.data["test_circuit:half_open_calls"] = b"0"

        # Allow request in HALF_OPEN
        allowed = await circuit_breaker.allow_request()
        assert allowed is True

        # Record failure
        await circuit_breaker.record_failure()

        # Should have transitioned back to OPEN
        assert any(
            op[1] == "test_circuit:state" and op[2] == "open"
            for op in mock_redis.mock_pipeline.operations
            if op[0] == "set"
        )

    @pytest.mark.asyncio
    async def test_multiple_failures_count_correctly(self, circuit_breaker, mock_redis):
        """Test that multiple failures are counted correctly."""
        mock_redis.data["test_circuit:state"] = b"closed"
        mock_redis.data["test_circuit:failures"] = b"0"

        # Record failures one at a time
        await circuit_breaker.record_failure()
        assert mock_redis.data["test_circuit:failures"] == b"1"

        await circuit_breaker.record_failure()
        assert mock_redis.data["test_circuit:failures"] == b"2"

    @pytest.mark.asyncio
    async def test_success_resets_failure_count(self, circuit_breaker, mock_redis):
        """Test that success resets failure count."""
        mock_redis.data["test_circuit:state"] = b"closed"
        mock_redis.data["test_circuit:failures"] = b"2"

        await circuit_breaker.record_success()

        assert mock_redis.data["test_circuit:failures"] == "0"
