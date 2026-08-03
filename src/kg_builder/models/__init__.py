"""
Database models.

This module exports all database models for easy import throughout the
application. All models inherit from Base and include common audit columns
(id, created_at, updated_at).

Models:
    - Tenant: Organization or tenant in the multi-tenant system
    - User: User within a tenant, authenticated via OAuth
    - OAuthProvider: OAuth provider configuration for a tenant
    - ProviderType: Enum of supported OAuth provider types
    - UserTenantMembership: User membership in a tenant (multi-tenant support)
    - MembershipRole: Enum of membership roles
    - ScrapingJob: Web scraping job configuration and status
    - JobStatus: Enum of scraping job states
    - ScrapedPage: Scraped web page content and metadata
    - ExtractedEntity: Entity extracted from scraped content
    - EntityType: Enum of entity types
    - ExtractionMethod: Enum of extraction methods
    - EntityRelationship: Relationship between entities
    - ExtractionProvider: Extraction provider configuration (OpenAI, Ollama, etc.)
    - ExtractionProviderType: Enum of extraction provider types
    - InferenceProvider: LLM inference provider configuration
    - InferenceProviderType: Enum of inference provider types
    - InferenceRequest: Inference request history (projection)
    - InferenceStatus: Enum of inference request statuses
"""

from kg_builder.models.consolidation_config import (
    ConsolidationConfig,
    DEFAULT_AUTO_MERGE_THRESHOLD,
    DEFAULT_FEATURE_WEIGHTS,
    DEFAULT_REVIEW_THRESHOLD,
)
from kg_builder.models.entity_alias import EntityAlias
from kg_builder.models.extracted_entity import (
    EntityRelationship,
    EntityType,
    ExtractedEntity,
    ExtractionMethod,
)
from kg_builder.models.merge_history import MergeEventType, MergeHistory
from kg_builder.models.merge_review_queue import MergeReviewItem, MergeReviewStatus
from kg_builder.models.extraction_provider import (
    ExtractionProvider,
    ExtractionProviderType,
)
from kg_builder.models.inference_provider import (
    InferenceProvider,
    ProviderType as InferenceProviderType,
)
from kg_builder.models.inference_request import InferenceRequest, InferenceStatus
from kg_builder.models.oauth_provider import OAuthProvider, ProviderType
from kg_builder.models.scraped_page import ScrapedPage
from kg_builder.models.scraping_job import JobStatus, ScrapingJob
from kg_builder.models.tenant import Tenant
from kg_builder.models.user import User
from kg_builder.models.user_tenant_membership import MembershipRole, UserTenantMembership

__all__ = [
    # Ownership models (tenants/users) — kept so SQLAlchemy can resolve
    # relationships declared on the knowledge-graph models.
    "Tenant",
    "User",
    "OAuthProvider",
    "ProviderType",
    "UserTenantMembership",
    "MembershipRole",
    # Scraping models
    "ScrapingJob",
    "JobStatus",
    "ScrapedPage",
    "ExtractedEntity",
    "EntityType",
    "ExtractionMethod",
    "EntityRelationship",
    # Consolidation models
    "ConsolidationConfig",
    "DEFAULT_AUTO_MERGE_THRESHOLD",
    "DEFAULT_FEATURE_WEIGHTS",
    "DEFAULT_REVIEW_THRESHOLD",
    "EntityAlias",
    "MergeEventType",
    "MergeHistory",
    "MergeReviewItem",
    "MergeReviewStatus",
    # Extraction provider models
    "ExtractionProvider",
    "ExtractionProviderType",
    # Inference models
    "InferenceProvider",
    "InferenceProviderType",
    "InferenceRequest",
    "InferenceStatus",
]
