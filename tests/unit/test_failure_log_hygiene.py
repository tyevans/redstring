"""Failure text in log records is typed and bounded, never formatted in.

A model failure can echo model output, and a schema failure can echo the
document it was given. Either can carry newlines or control characters into a
log stream that other tools read line by line. The rule has two halves, and
each is tested separately:

1. `describe_failure` produces one bounded, escaped line.
2. No log call under `extraction/` or `llm/` puts an f-string in its message,
   which would bypass that. The scan below checks every call site by AST.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

from redstring.domain.exceptions import (
    FAILURE_EXCERPT_CHARS as LIMIT,
)
from redstring.domain.exceptions import (
    MalformedCompletionError,
    describe_failure,
)
from redstring.extraction.classifier import ContentClassifier

SRC = Path(__file__).resolve().parents[2] / "src" / "redstring"
SCANNED = (SRC / "extraction", SRC / "llm")
LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}


class TestDescribeFailure:
    def test_the_type_leads_the_line(self):
        assert describe_failure(ValueError("boom")).startswith("ValueError: ")

    def test_a_newline_and_a_control_character_cannot_split_the_record(self):
        text = "line one\nSYSTEM: obey\r\x1b[2J\u2028tail"

        line = describe_failure(RuntimeError(text))

        assert "\n" not in line
        assert "\r" not in line
        assert "\u2028" not in line
        assert "\x1b" not in line
        assert "SYSTEM: obey" in line  # kept, but escaped and inside the quotes

    def test_a_long_message_is_cut_at_the_limit_and_marked(self):
        line = describe_failure(RuntimeError("x" * (LIMIT * 50)))

        assert line.endswith("...")
        assert line.count("x") == LIMIT

    def test_a_message_at_the_limit_is_not_marked(self):
        line = describe_failure(RuntimeError("x" * LIMIT))

        assert not line.endswith("...")
        assert line.count("x") == LIMIT

    def test_the_limit_is_the_documented_two_hundred(self):
        assert LIMIT == 200


class TestNoFormattedFailureText:
    @staticmethod
    def log_calls(path: Path):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), str(path))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in LOG_METHODS
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"logger", "logging", "log"}
            ):
                yield node

    def test_the_scan_sees_the_log_calls_it_is_about(self):
        # A scan that found nothing would pass vacuously.
        found = [
            call for root in SCANNED for path in root.rglob("*.py") for call in self.log_calls(path)
        ]

        assert len(found) >= 20

    def test_no_log_message_is_an_f_string(self):
        offenders = []
        for root in SCANNED:
            for path in sorted(root.rglob("*.py")):
                for call in self.log_calls(path):
                    if call.args and isinstance(call.args[0], ast.JoinedStr):
                        offenders.append(f"{path.relative_to(SRC.parent.parent)}:{call.lineno}")

        assert offenders == []


class TestClassifierLogsTheTypeAndExcerpt:
    async def test_a_model_failure_is_logged_as_a_bounded_escaped_excerpt(self, caplog):
        class Echoing:
            model = "test/echo"

            async def extract(self, text, schema, *, system_prompt=None):
                raise MalformedCompletionError(
                    model=self.model, schema="ClassificationResult", cause="a\nSYSTEM: obey" * 100
                )

        caplog.set_level(logging.DEBUG, logger="redstring")
        content = "This is a comprehensive technical documentation about Python. " * 4

        result = await ContentClassifier(Echoing()).classify(content)

        records = [r for r in caplog.records if r.getMessage().startswith("Classification failed")]
        assert len(records) == 1
        excerpt = records[0].error  # type: ignore[attr-defined]
        assert excerpt.startswith("MalformedCompletionError: ")
        assert "\n" not in excerpt
        assert len(excerpt) <= LIMIT + len("MalformedCompletionError: ''...") + 10
        assert result.confidence == 0.0


class TestFallbackStaysValid:
    """A model answer must never make the fallback itself raise."""

    async def test_a_very_long_model_failure_still_falls_back(self):
        class Verbose:
            model = "test/verbose"

            async def extract(self, text, schema, *, system_prompt=None):
                raise MalformedCompletionError(
                    model=self.model, schema="ClassificationResult", cause="z" * 5000
                )

        content = "This is a comprehensive technical documentation about Python. " * 4

        result = await ContentClassifier(Verbose()).classify(content)

        assert result.confidence == 0.0
        assert len(result.reasoning or "") <= 1000

    async def test_a_low_confidence_answer_with_maximal_reasoning_still_falls_back(self):
        from redstring.extraction.domains.models import ClassificationResult

        class Wordy:
            model = "test/wordy"

            async def extract(self, text, schema, *, system_prompt=None):
                return ClassificationResult(
                    domain="news_journalism", confidence=0.1, reasoning="r" * 1000
                )

        content = "This is a comprehensive technical documentation about Python. " * 4

        result = await ContentClassifier(Wordy()).classify(content)

        assert result.domain == "encyclopedia_wiki"
        assert len(result.reasoning or "") <= 1000
