"""Verification for CallerOwnedResourceContract and NoOpLifetimeMixin (B119).

Verifies resource identity and closedness assertions across supported resource types,
and proves that the negative mutant (closing a caller-owned resource on adapter close)
fails the test suite immediately.
"""

from __future__ import annotations

import pytest

from redstring.testing.lifetime import CallerOwnedResourceContract, NoOpLifetime


class DummyResource:
    def __init__(self) -> None:
        self._closed = False

    def close(self) -> None:
        self._closed = True


class DummyStore:
    def __init__(self, resource: object, *, owns: bool = False, attr: str = "_pool") -> None:
        setattr(self, attr, resource)
        self._owns_pool = owns
        self._resource = resource

    async def close(self) -> None:
        if self._owns_pool and hasattr(self._resource, "close"):
            self._resource.close()


class CallableClosedResource:
    def __init__(self, closed: bool) -> None:
        self._val = closed

    def is_closed(self) -> bool:
        return self._val


class PropertyClosedResource:
    def __init__(self, closed: bool) -> None:
        self.is_closed = closed


class AttributeClosedResource:
    def __init__(self, closed: bool) -> None:
        self.closed = closed


class ClosesCounterResource:
    def __init__(self, closes: int) -> None:
        self.closes = closes


class EmptyDouble(NoOpLifetime):
    pass


class TestNoOpLifetimeMixin:
    async def test_context_manager_and_close(self) -> None:
        double = EmptyDouble()
        async with double as entered:
            assert entered is double
        await double.close()


class TestCallerOwnedResourceContract:
    def test_assert_resource_identity_passes_for_identical_resource(self) -> None:
        resource = DummyResource()
        store = DummyStore(resource)
        CallerOwnedResourceContract.assert_resource_identity(store, resource)
        CallerOwnedResourceContract.assert_resource_identity(store, resource, "_pool")

    def test_assert_resource_identity_recognizes_driver_client_cache(self) -> None:
        resource = DummyResource()
        for attr in ("_driver", "_client", "_cache"):
            store = DummyStore(resource, attr=attr)
            CallerOwnedResourceContract.assert_resource_identity(store, resource)

    def test_assert_resource_identity_fails_when_store_swapped_resource(self) -> None:
        resource1 = DummyResource()
        resource2 = DummyResource()
        store = DummyStore(resource1)
        with pytest.raises(AssertionError, match="not identical"):
            CallerOwnedResourceContract.assert_resource_identity(store, resource2)

    def test_assert_resource_identity_fails_when_no_known_attribute(self) -> None:
        with pytest.raises(AssertionError, match="does not hold any known resource attribute"):
            CallerOwnedResourceContract.assert_resource_identity(object(), DummyResource())

    def test_assert_resource_not_closed_variants(self) -> None:
        # _closed attribute
        r1 = DummyResource()
        CallerOwnedResourceContract.assert_resource_not_closed(r1)
        r1.close()
        with pytest.raises(AssertionError, match="was closed"):
            CallerOwnedResourceContract.assert_resource_not_closed(r1)

        # is_closed callable
        r2_open = CallableClosedResource(False)
        CallerOwnedResourceContract.assert_resource_not_closed(r2_open)
        r2_closed = CallableClosedResource(True)
        with pytest.raises(AssertionError, match="was closed"):
            CallerOwnedResourceContract.assert_resource_not_closed(r2_closed)

        # is_closed property/value
        r3_open = PropertyClosedResource(False)
        CallerOwnedResourceContract.assert_resource_not_closed(r3_open)
        r3_closed = PropertyClosedResource(True)
        with pytest.raises(AssertionError, match="was closed"):
            CallerOwnedResourceContract.assert_resource_not_closed(r3_closed)

        # closed attribute
        r4_open = AttributeClosedResource(False)
        CallerOwnedResourceContract.assert_resource_not_closed(r4_open)
        r4_closed = AttributeClosedResource(True)
        with pytest.raises(AssertionError, match="was closed"):
            CallerOwnedResourceContract.assert_resource_not_closed(r4_closed)

        # closes count
        r5_open = ClosesCounterResource(0)
        CallerOwnedResourceContract.assert_resource_not_closed(r5_open)
        r5_closed = ClosesCounterResource(1)
        with pytest.raises(AssertionError, match="was closed"):
            CallerOwnedResourceContract.assert_resource_not_closed(r5_closed)

        # unknown type raises TypeError
        with pytest.raises(TypeError, match="does not expose a known closedness attribute"):
            CallerOwnedResourceContract.assert_resource_not_closed(object())

    def test_assert_resource_closed_variants(self) -> None:
        # _closed attribute
        r1 = DummyResource()
        with pytest.raises(AssertionError, match="was not closed"):
            CallerOwnedResourceContract.assert_resource_closed(r1)
        r1.close()
        CallerOwnedResourceContract.assert_resource_closed(r1)

        # is_closed callable
        r2_open = CallableClosedResource(False)
        with pytest.raises(AssertionError, match="was not closed"):
            CallerOwnedResourceContract.assert_resource_closed(r2_open)
        r2_closed = CallableClosedResource(True)
        CallerOwnedResourceContract.assert_resource_closed(r2_closed)

        # is_closed property/value
        r3_open = PropertyClosedResource(False)
        with pytest.raises(AssertionError, match="was not closed"):
            CallerOwnedResourceContract.assert_resource_closed(r3_open)
        r3_closed = PropertyClosedResource(True)
        CallerOwnedResourceContract.assert_resource_closed(r3_closed)

        # closed attribute
        r4_open = AttributeClosedResource(False)
        with pytest.raises(AssertionError, match="was not closed"):
            CallerOwnedResourceContract.assert_resource_closed(r4_open)
        r4_closed = AttributeClosedResource(True)
        CallerOwnedResourceContract.assert_resource_closed(r4_closed)

        # closes count
        r5_open = ClosesCounterResource(0)
        with pytest.raises(AssertionError, match="was not closed"):
            CallerOwnedResourceContract.assert_resource_closed(r5_open)
        r5_closed = ClosesCounterResource(2)
        CallerOwnedResourceContract.assert_resource_closed(r5_closed)

        # unknown type raises TypeError
        with pytest.raises(TypeError, match="does not expose a known closedness attribute"):
            CallerOwnedResourceContract.assert_resource_closed(object())

    async def test_negative_mutant_fails_when_adapter_erroneously_closes_caller_resource(
        self,
    ) -> None:
        """Negative mutant verification (DoD criterion 2).

        If an adapter mistakenly claims ownership of a caller-provided resource,
        or unconditionally calls `close()` on the underlying resource,
        the assertion must fail immediately.
        """
        caller_pool = DummyResource()
        mutant_store = DummyStore(caller_pool, owns=True)

        CallerOwnedResourceContract.assert_resource_identity(mutant_store, caller_pool)
        await mutant_store.close()

        with pytest.raises(AssertionError, match="was closed"):
            CallerOwnedResourceContract.assert_resource_not_closed(caller_pool)
