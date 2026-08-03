"""
Storage services for object storage (MinIO/S3) integration.
"""

from kg_builder.services.storage.object_storage import (
    ObjectStorageService,
    get_object_storage_service,
)

__all__ = [
    "ObjectStorageService",
    "get_object_storage_service",
]
