"""Type aliases for identifiers used throughout the domain model."""

from __future__ import annotations

from typing import NewType
from uuid import UUID

EntityId = NewType("EntityId", UUID)
RelationshipId = NewType("RelationshipId", UUID)
TenantId = NewType("TenantId", UUID)
SourceId = NewType("SourceId", str)
