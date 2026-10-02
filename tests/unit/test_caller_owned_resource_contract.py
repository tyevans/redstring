"""Verification for CallerOwnedResourceContract (B119).

Verifies resource identity and closedness assertions, and proves that
the negative mutant (closing a caller-owned resource on adapter close)
fails the test suite immediately.
"""

from __future__ import annotations

import pytest

from redstring.testing.lifetime import CallerOwnedResourceContract


class DummyResource:
    def __init__(self) -> None:
        self._closed = False

    def close(self) -> None:
        self._closed = True


class DummyStore:
    def __init__(self, resource: object, *, owns: bool = False) -> None:
        self._pool = resource
        self._owns_pool = owns

    async def close(self) -> None:
        if self._owns_pool and hasattr(self._pool, "close"):
            self._pool.close()


class TestCallerOwnedResourceContract:
    def test_assert_resource_identity_passes_for_identical_resource(self) -> None:
        resource = DummyResource()
        store = DummyStore(resource)
        CallerOwnedResourceContract.assert_resource_identity(store, resource)
        CallerOwnedResourceContract.assert_resource_identity(store, resource, "_pool")

    def test_assert_resource_identity_fails_when_store_swapped_resource(self) -> None:
        resource1 = DummyResource()
        resource2 = DummyResource()
        store = DummyStore(resource1)
        with pytest.raises(AssertionError, match="not identical"):
            CallerOwnedResourceContract.assert_resource_identity(store, resource2)

    def test_assert_resource_not_closed_passes_when_open(self) -> None:
        resource = DummyResource()
        CallerOwnedResourceContract.assert_resource_not_closed(resource)

    def test_assert_resource_not_closed_fails_when_closed(self) -> None:
        resource = DummyResource()
        resource.close()
        with pytest.raises(AssertionError, match="was closed"):
            CallerOwnedResourceContract.assert_resource_not_closed(resource)

    def test_assert_resource_closed_passes_when_closed(self) -> None:
        resource = DummyResource()
        resource.close()
        CallerOwnedResourceContract.assert_resource_closed(resource)

    def test_assert_resource_closed_fails_when_open(self) -> None:
        resource = DummyResource()
        with pytest.raises(AssertionError, match="was not closed"):
            CallerOwnedResourceContract.assert_resource_closed(resource)

    async def test_negative_mutant_fails_when_adapter_erroneously_closes_caller_resource(
        self,
    ) -> None:
        """Negative mutant verification (DoD criterion 2).

        If an adapter mistakenly claims ownership of a caller-provided resource,
        or unconditionally calls `close()` on the underlying resource,
        the assertion must fail immediately.
        """
        caller_pool = DummyResource()
        # Mutated store: erroneously configured to own and close the caller's pool
        mutant_store = DummyStore(caller_pool, owns=True)

        CallerOwnedResourceContract.assert_resource_identity(mutant_store, caller_pool)
        await mutant_store.close()

        # The contract must catch the mutant by raising AssertionError
        with pytest.raises(AssertionError, match="was closed"):
            CallerOwnedResourceContract.assert_resource_not_closed(caller_pool)
