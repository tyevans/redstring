"""A real `LlmProvider` with canned answers. Not a mock.

The distinction matters and is the reason this file exists rather than
`unittest.mock.AsyncMock`. A mock returns the object the test handed it, so
an extraction test built on one asserts that extraction passes its own
fixture through. This provider takes **payload dicts** and validates them
against the caller's schema through the same gate the LangChain adapter uses,
so:

- a test cannot smuggle a pre-validated schema instance past validation;
- a payload the schema rejects raises `MalformedCompletionError` here for the
  same reason and with the same type as a real model's bad JSON does;
- "the transport gave us nothing" (`EMPTY`) and "the model found nothing"
  (a payload that validates to an empty result) stay distinguishable, which
  they must, because the first is a bug and the second is an answer.

Two ways to program it, and the choice is not cosmetic:

- `script=[...]` answers positionally. Use it when the call *count* is the
  thing under test.
- `by_substring={...}` answers according to the text it was given. Use it for
  anything about chunking or merging: with a positional script, permuting the
  chunks permutes which answer each chunk receives, so an order-independence
  test would pass against a merge that is not order-independent at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from pydantic import ValidationError

from kg_builder.domain.exceptions import EmptyCompletionError, MalformedCompletionError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pydantic import BaseModel


class _Empty:
    """Sentinel: this call returns no usable content at all."""

    def __repr__(self) -> str:
        return "EMPTY"


#: Script entry standing for a completion with no content -- the reasoning
#: model spending its whole budget before `content` starts, or a truncation.
EMPTY: Final = _Empty()

#: What a script entry may be: a payload to validate, or `EMPTY`.
type Response = Mapping[str, Any] | _Empty


class FakeLlmProvider:
    """Canned structured output, validated like the real thing."""

    def __init__(
        self,
        *,
        model: str = "fake/canned-v1",
        script: Sequence[Response] | None = None,
        by_substring: Mapping[str, Response] | None = None,
        default: Response | None = None,
    ) -> None:
        """Build a provider that answers from a script or from the text given.

        Args:
            model: Reported as `LlmProvider.model`, so extracted entities
                carry a provenance string that is visibly a fake.
            script: Payloads answered in call order. Exhausting it is an
                error, not a repeat of the last entry.
            by_substring: Payload per substring; the first key contained in
                the text wins, in insertion order.
            default: Answer for text matching no `by_substring` key. Defaults
                to `{}`, which validates to a result with nothing in it for
                any schema whose fields all have defaults -- the encoding of
                "the model found nothing here", which is not a failure.

        Raises:
            ValueError: Unless exactly one of `script`/`by_substring` is given.
        """
        if (script is None) == (by_substring is None):
            raise ValueError("a FakeLlmProvider needs exactly one of `script` or `by_substring`")
        if script is not None and default is not None:
            raise ValueError("`default` applies to `by_substring` only; a script has no misses")
        self._model = model
        self._script = list(script) if script is not None else None
        self._by_substring = dict(by_substring) if by_substring is not None else None
        self._default: Response = {} if default is None else default
        self._calls = 0

    @property
    def model(self) -> str:
        return self._model

    async def extract[S: BaseModel](
        self,
        text: str,
        schema: type[S],
        *,
        system_prompt: str | None = None,
    ) -> S:
        """Answer per the script or the text, then validate against `schema`.

        `system_prompt` is accepted and deliberately ignored: a fake that
        varied its answer by prompt would make prompt wording load-bearing in
        tests that are about extraction, and there is no real model behind it
        for the prompt to mean anything to.
        """
        response = self._next_response(text)
        if isinstance(response, _Empty):
            raise EmptyCompletionError(model=self._model)
        try:
            return schema.model_validate(dict(response))
        except ValidationError as error:
            raise MalformedCompletionError(
                model=self._model, schema=schema.__name__, cause=str(error)
            ) from error

    def _next_response(self, text: str) -> Response:
        if self._script is not None:
            index, self._calls = self._calls, self._calls + 1
            # An assertion rather than a domain error: this is a defect in the
            # test, not a condition the library can be in.
            assert index < len(self._script), (
                f"FakeLlmProvider was given a script of {len(self._script)} but called "
                f"{index + 1} times; script the call or use `by_substring`"
            )
            return self._script[index]
        assert self._by_substring is not None
        for needle, response in self._by_substring.items():
            if needle in text:
                return response
        return self._default
