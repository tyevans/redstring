"""`Cache` adapters. `MemoryCache` is the default; `RedisCache` is the upgrade.

Both must pass `tests/compliance/cache.py`, which is what stops them drifting
-- an in-memory reference that is more forgiving than the real backend lets a
caller pass its tests on behaviour production does not have.
"""

from kg_builder.llm.cache.memory import MemoryCache

__all__ = ["MemoryCache"]
