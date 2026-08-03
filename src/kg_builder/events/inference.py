"""
Domain events for the inference testing feature.

These events track the lifecycle of inference providers and requests.
They are designed to drive the event-sourced inference architecture.

Provider Events:
- ProviderCreated: New provider configuration added
- ProviderUpdated: Provider configuration modified
- ProviderDeleted: Provider configuration removed
- ProviderTestSucceeded: Provider connectivity test passed
- ProviderTestFailed: Provider connectivity test failed

Request Events:
- InferenceRequested: Inference request initiated
- InferenceStarted: Provider began processing
- InferenceCompleted: Inference completed successfully
- InferenceFailed: Inference failed with error
- InferenceCancelled: User cancelled the request
"""

from datetime import datetime
from uuid import UUID

from eventsource import register_event

from kg_builder.events.base import TenantDomainEvent

# =============================================================================
# Provider Lifecycle Events
# =============================================================================


@register_event
class ProviderCreated(TenantDomainEvent):
    """Emitted when a new provider configuration is created.

    Attributes:
        provider_id: Unique identifier for the provider
        name: Display name for the provider
        provider_type: Type of provider (ollama, openai, etc.)
        config: Provider configuration (sensitive fields encrypted)
        default_model: Default model to use
        default_temperature: Default temperature setting
        default_max_tokens: Default max tokens setting
        created_by: User who created the provider
    """

    event_type: str = "ProviderCreated"
    aggregate_type: str = "InferenceProvider"

    provider_id: UUID
    name: str
    provider_type: str
    config: dict
    default_model: str | None = None
    default_temperature: float = 0.7
    default_max_tokens: int = 1024
    created_by: UUID


@register_event
class ProviderUpdated(TenantDomainEvent):
    """Emitted when a provider configuration is updated.

    Only includes fields that were changed.

    Attributes:
        provider_id: Provider being updated
        name: New name (if changed)
        config: New config (if changed)
        default_model: New default model (if changed)
        default_temperature: New temperature (if changed)
        default_max_tokens: New max tokens (if changed)
        is_active: New active status (if changed)
        updated_by: User who made the update
    """

    event_type: str = "ProviderUpdated"
    aggregate_type: str = "InferenceProvider"

    provider_id: UUID
    name: str | None = None
    config: dict | None = None
    default_model: str | None = None
    default_temperature: float | None = None
    default_max_tokens: int | None = None
    is_active: bool | None = None
    updated_by: UUID


@register_event
class ProviderDeleted(TenantDomainEvent):
    """Emitted when a provider configuration is deleted.

    Attributes:
        provider_id: Provider being deleted
        deleted_by: User who deleted the provider
    """

    event_type: str = "ProviderDeleted"
    aggregate_type: str = "InferenceProvider"

    provider_id: UUID
    deleted_by: UUID


@register_event
class ProviderTestSucceeded(TenantDomainEvent):
    """Emitted when a provider connectivity test passes.

    Attributes:
        provider_id: Provider that was tested
        latency_ms: Health check latency
        available_models: Models available on the provider
        tested_by: User who ran the test
        tested_at: When the test was performed
    """

    event_type: str = "ProviderTestSucceeded"
    aggregate_type: str = "InferenceProvider"

    provider_id: UUID
    latency_ms: float
    available_models: list[str]
    tested_by: UUID
    tested_at: datetime


@register_event
class ProviderTestFailed(TenantDomainEvent):
    """Emitted when a provider connectivity test fails.

    Attributes:
        provider_id: Provider that was tested
        error: Error message from the test
        error_type: Type of error (connection, timeout, auth)
        tested_by: User who ran the test
        tested_at: When the test was performed
    """

    event_type: str = "ProviderTestFailed"
    aggregate_type: str = "InferenceProvider"

    provider_id: UUID
    error: str
    error_type: str
    tested_by: UUID
    tested_at: datetime


# =============================================================================
# Inference Request Events
# =============================================================================


@register_event
class InferenceRequested(TenantDomainEvent):
    """Emitted when an inference request is initiated.

    Attributes:
        request_id: Unique identifier for this request
        provider_id: Provider to use for inference
        model: Model to use
        prompt: Input prompt text
        parameters: Request parameters (temperature, max_tokens, etc.)
        stream: Whether streaming was requested
        requested_by: User who made the request
        requested_at: When the request was made
    """

    event_type: str = "InferenceRequested"
    aggregate_type: str = "InferenceRequest"

    request_id: UUID
    provider_id: UUID
    model: str
    prompt: str
    parameters: dict
    stream: bool = False
    requested_by: UUID
    requested_at: datetime


@register_event
class InferenceStarted(TenantDomainEvent):
    """Emitted when inference processing begins.

    This event marks the transition from queued to in-progress.

    Attributes:
        request_id: Request being processed
        started_at: When processing started
    """

    event_type: str = "InferenceStarted"
    aggregate_type: str = "InferenceRequest"

    request_id: UUID
    started_at: datetime


@register_event
class InferenceCompleted(TenantDomainEvent):
    """Emitted when inference completes successfully.

    Attributes:
        request_id: Request that completed
        response: Generated response text
        prompt_tokens: Tokens in the prompt
        completion_tokens: Tokens in the completion
        total_tokens: Total tokens used
        duration_ms: Processing time in milliseconds
        finish_reason: Why generation stopped (stop, length, etc.)
        completed_at: When inference completed
    """

    event_type: str = "InferenceCompleted"
    aggregate_type: str = "InferenceRequest"

    request_id: UUID
    response: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    duration_ms: int
    finish_reason: str = "stop"
    completed_at: datetime


@register_event
class InferenceFailed(TenantDomainEvent):
    """Emitted when inference fails.

    Attributes:
        request_id: Request that failed
        error: Error message
        error_type: Type of error (connection, timeout, rate_limit, etc.)
        failed_at: When the failure occurred
    """

    event_type: str = "InferenceFailed"
    aggregate_type: str = "InferenceRequest"

    request_id: UUID
    error: str
    error_type: str
    failed_at: datetime


@register_event
class InferenceCancelled(TenantDomainEvent):
    """Emitted when a user cancels an inference request.

    Attributes:
        request_id: Request that was cancelled
        cancelled_by: User who cancelled
        cancelled_at: When cancellation occurred
        partial_response: Any partial response generated before cancellation
    """

    event_type: str = "InferenceCancelled"
    aggregate_type: str = "InferenceRequest"

    request_id: UUID
    cancelled_by: UUID
    cancelled_at: datetime
    partial_response: str | None = None
