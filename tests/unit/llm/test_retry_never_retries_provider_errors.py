"""`with_retry` with default arguments never retries a provider refusal.

A refusal or a malformed completion is answered identically on every attempt,
so retrying it spends tokens and time for nothing. The defaults used to be
`(Exception,)` with nothing excluded, so each of these was called four times.
"""

from __future__ import annotations

import pytest

from redstring.domain.exceptions import (
    EmptyCompletionError,
    MalformedCompletionError,
    RefusedCompletionError,
)
from redstring.llm.retry import LlmRetryPolicy, RetryExhausted, with_retry


async def no_sleep(_delay: float) -> None:
    return None


def refusing(error: Exception):
    calls: list[int] = []

    @with_retry(
        policy=LlmRetryPolicy(max_retries=3, initial_delay=0.0, jitter=0.0), sleeper=no_sleep
    )
    async def call_model() -> str:
        calls.append(1)
        raise error

    return call_model, calls


@pytest.mark.parametrize(
    "error",
    [
        RefusedCompletionError(model="m"),
        EmptyCompletionError(model="m", finish_reason="length"),
        MalformedCompletionError(model="m", schema="S", cause="bad"),
    ],
    ids=["refusal", "empty", "malformed"],
)
async def test_a_provider_error_is_called_once_with_default_arguments(error):
    call_model, calls = refusing(error)

    with pytest.raises(type(error)) as raised:
        await call_model()

    assert raised.value is error
    assert len(calls) == 1


async def test_a_transient_failure_is_still_retried_with_default_arguments():
    calls: list[int] = []

    @with_retry(
        policy=LlmRetryPolicy(max_retries=3, initial_delay=0.0, jitter=0.0), sleeper=no_sleep
    )
    async def flaky() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError("dropped")
        return "ok"

    assert await flaky() == "ok"
    assert len(calls) == 3


async def test_the_exclusion_can_be_switched_off_by_the_caller():
    call_model, calls = refusing(RefusedCompletionError(model="m"))
    retrying = with_retry(
        policy=LlmRetryPolicy(max_retries=3, initial_delay=0.0, jitter=0.0),
        sleeper=no_sleep,
        never_retry=(),
    )(call_model.__wrapped__)

    with pytest.raises(RetryExhausted):
        await retrying()

    assert len(calls) == 4
