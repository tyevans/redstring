"""Injection points for host applications.

The library does not own a task queue. A host application registers its own
dispatcher (e.g. a Celery task's ``.delay``) and the scraping pipelines call it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

logger = logging.getLogger(__name__)


class ExtractionDispatcher(Protocol):
    def __call__(self, page_id: str, tenant_id: str) -> None: ...


def _noop(page_id: str, tenant_id: str) -> None:
    logger.debug(
        "No extraction dispatcher registered; dropping extraction request",
        extra={"page_id": page_id, "tenant_id": tenant_id},
    )


_dispatcher: Callable[[str, str], None] = _noop


def set_extraction_dispatcher(dispatcher: Callable[[str, str], None]) -> None:
    """Register the callable used to queue entity extraction for a page."""
    global _dispatcher
    _dispatcher = dispatcher


def get_extraction_dispatcher() -> Callable[[str, str], None]:
    return _dispatcher
