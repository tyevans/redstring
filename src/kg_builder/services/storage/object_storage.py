"""
Object Storage Service for MinIO/S3-compatible storage.

Provides methods for uploading, downloading, and managing files
in MinIO object storage. Used primarily for document uploads.
"""

import logging
from functools import lru_cache
from uuid import UUID

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from kg_builder.config import settings

logger = logging.getLogger(__name__)


class ObjectStorageError(Exception):
    """Base exception for object storage errors."""

    pass


class ObjectStorageService:
    """
    Service for interacting with MinIO/S3-compatible object storage.

    Provides methods to:
    - Upload files to buckets
    - Download file content
    - Delete files
    - Generate presigned URLs for direct access
    - Ensure buckets exist
    """

    def __init__(
        self,
        endpoint: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        secure: bool | None = None,
    ):
        """
        Initialize the object storage service.

        Args:
            endpoint: MinIO/S3 endpoint (default: from settings)
            access_key: Access key/username (default: from settings)
            secret_key: Secret key/password (default: from settings)
            secure: Use HTTPS (default: from settings)
        """
        self.endpoint = endpoint or settings.MINIO_ENDPOINT
        self.access_key = access_key or settings.MINIO_ACCESS_KEY
        self.secret_key = secret_key or settings.MINIO_SECRET_KEY
        self.secure = secure if secure is not None else settings.MINIO_SECURE

        # Build endpoint URL
        protocol = "https" if self.secure else "http"
        endpoint_url = f"{protocol}://{self.endpoint}"

        # Create boto3 S3 client
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
            ),
        )

        logger.info(
            "ObjectStorageService initialized",
            extra={
                "endpoint": endpoint_url,
                "secure": self.secure,
            },
        )

    def ensure_bucket_exists(self, bucket: str) -> bool:
        """
        Ensure a bucket exists, creating it if necessary.

        Args:
            bucket: Bucket name

        Returns:
            True if bucket exists or was created, False on error

        Raises:
            ObjectStorageError: If bucket creation fails
        """
        try:
            self._client.head_bucket(Bucket=bucket)
            logger.debug("Bucket exists", extra={"bucket": bucket})
            return True
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "404":
                # Bucket doesn't exist, create it
                try:
                    self._client.create_bucket(Bucket=bucket)
                    logger.info("Created bucket", extra={"bucket": bucket})
                    return True
                except ClientError as create_error:
                    logger.error(
                        "Failed to create bucket",
                        extra={
                            "bucket": bucket,
                            "error": str(create_error),
                        },
                    )
                    raise ObjectStorageError(
                        f"Failed to create bucket {bucket}: {create_error}"
                    ) from create_error
            else:
                logger.error(
                    "Failed to check bucket",
                    extra={
                        "bucket": bucket,
                        "error": str(e),
                    },
                )
                raise ObjectStorageError(
                    f"Failed to check bucket {bucket}: {e}"
                ) from e

    def upload_file(
        self,
        bucket: str,
        key: str,
        content: bytes,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> str:
        """
        Upload file content to object storage.

        Args:
            bucket: Target bucket name
            key: Object key (path within bucket)
            content: File content as bytes
            content_type: MIME type of the content
            metadata: Optional metadata to attach to the object

        Returns:
            The object key (for reference)

        Raises:
            ObjectStorageError: If upload fails
        """
        try:
            # Ensure bucket exists
            self.ensure_bucket_exists(bucket)

            # Prepare extra args
            extra_args = {"ContentType": content_type}
            if metadata:
                extra_args["Metadata"] = metadata

            # Upload
            self._client.put_object(
                Bucket=bucket,
                Key=key,
                Body=content,
                ContentType=content_type,
                Metadata=metadata or {},
            )

            logger.info(
                "Uploaded file to object storage",
                extra={
                    "bucket": bucket,
                    "key": key,
                    "content_type": content_type,
                    "size_bytes": len(content),
                },
            )

            return key

        except ClientError as e:
            logger.error(
                "Failed to upload file",
                extra={
                    "bucket": bucket,
                    "key": key,
                    "error": str(e),
                },
            )
            raise ObjectStorageError(f"Failed to upload to {bucket}/{key}: {e}") from e

    def download_file(self, bucket: str, key: str) -> bytes:
        """
        Download file content from object storage.

        Args:
            bucket: Source bucket name
            key: Object key (path within bucket)

        Returns:
            File content as bytes

        Raises:
            ObjectStorageError: If download fails or object not found
        """
        try:
            response = self._client.get_object(Bucket=bucket, Key=key)
            content = response["Body"].read()

            logger.debug(
                "Downloaded file from object storage",
                extra={
                    "bucket": bucket,
                    "key": key,
                    "size_bytes": len(content),
                },
            )

            return content

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "NoSuchKey":
                logger.warning(
                    "Object not found",
                    extra={"bucket": bucket, "key": key},
                )
                raise ObjectStorageError(f"Object not found: {bucket}/{key}") from e
            logger.error(
                "Failed to download file",
                extra={
                    "bucket": bucket,
                    "key": key,
                    "error": str(e),
                },
            )
            raise ObjectStorageError(
                f"Failed to download from {bucket}/{key}: {e}"
            ) from e

    def delete_file(self, bucket: str, key: str) -> bool:
        """
        Delete a file from object storage.

        Args:
            bucket: Source bucket name
            key: Object key (path within bucket)

        Returns:
            True if deleted (or didn't exist), False on error

        Raises:
            ObjectStorageError: If deletion fails
        """
        try:
            self._client.delete_object(Bucket=bucket, Key=key)

            logger.info(
                "Deleted file from object storage",
                extra={
                    "bucket": bucket,
                    "key": key,
                },
            )

            return True

        except ClientError as e:
            logger.error(
                "Failed to delete file",
                extra={
                    "bucket": bucket,
                    "key": key,
                    "error": str(e),
                },
            )
            raise ObjectStorageError(f"Failed to delete {bucket}/{key}: {e}") from e

    def get_presigned_url(
        self,
        bucket: str,
        key: str,
        expires_in: int = 3600,
        method: str = "get_object",
    ) -> str:
        """
        Generate a presigned URL for direct access to an object.

        Args:
            bucket: Bucket name
            key: Object key
            expires_in: URL expiration time in seconds (default: 1 hour)
            method: S3 method (get_object for download, put_object for upload)

        Returns:
            Presigned URL string

        Raises:
            ObjectStorageError: If URL generation fails
        """
        try:
            url = self._client.generate_presigned_url(
                method,
                Params={"Bucket": bucket, "Key": key},
                ExpiresIn=expires_in,
            )

            logger.debug(
                "Generated presigned URL",
                extra={
                    "bucket": bucket,
                    "key": key,
                    "expires_in": expires_in,
                    "method": method,
                },
            )

            return url

        except ClientError as e:
            logger.error(
                "Failed to generate presigned URL",
                extra={
                    "bucket": bucket,
                    "key": key,
                    "error": str(e),
                },
            )
            raise ObjectStorageError(
                f"Failed to generate URL for {bucket}/{key}: {e}"
            ) from e

    def file_exists(self, bucket: str, key: str) -> bool:
        """
        Check if a file exists in object storage.

        Args:
            bucket: Bucket name
            key: Object key

        Returns:
            True if file exists, False otherwise
        """
        try:
            self._client.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "404":
                return False
            raise ObjectStorageError(f"Failed to check {bucket}/{key}: {e}") from e

    def get_file_info(self, bucket: str, key: str) -> dict:
        """
        Get metadata about a file in object storage.

        Args:
            bucket: Bucket name
            key: Object key

        Returns:
            Dict with content_type, size_bytes, last_modified, metadata

        Raises:
            ObjectStorageError: If file not found or error occurs
        """
        try:
            response = self._client.head_object(Bucket=bucket, Key=key)
            return {
                "content_type": response.get("ContentType", "application/octet-stream"),
                "size_bytes": response.get("ContentLength", 0),
                "last_modified": response.get("LastModified"),
                "metadata": response.get("Metadata", {}),
            }
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "404":
                raise ObjectStorageError(f"Object not found: {bucket}/{key}") from e
            raise ObjectStorageError(
                f"Failed to get info for {bucket}/{key}: {e}"
            ) from e

    def generate_document_key(
        self,
        tenant_id: UUID,
        project_id: UUID,
        document_id: UUID,
        filename: str,
    ) -> str:
        """
        Generate a storage key for a document upload.

        Format: {tenant_id}/{project_id}/{document_id}/{filename}

        Args:
            tenant_id: Tenant UUID
            project_id: Project UUID
            document_id: Document UUID
            filename: Original filename

        Returns:
            Storage key string
        """
        return f"{tenant_id}/{project_id}/{document_id}/{filename}"


# Singleton instance
_object_storage_service: ObjectStorageService | None = None


def get_object_storage_service() -> ObjectStorageService:
    """
    Get or create the singleton ObjectStorageService instance.

    Returns:
        ObjectStorageService instance
    """
    global _object_storage_service
    if _object_storage_service is None:
        _object_storage_service = ObjectStorageService()
    return _object_storage_service
