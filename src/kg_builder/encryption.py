"""
Encryption service for protecting sensitive data at rest.

Provides field-level encryption for sensitive configuration values like API keys,
with per-tenant key derivation for enhanced isolation.

Architecture:
- Master key stored in environment variable (ENCRYPTION_MASTER_KEY)
- Per-tenant keys derived using HKDF (HMAC-based Key Derivation Function)
- Fernet symmetric encryption (AES-128-CBC with HMAC-SHA256)
- Audit logging for all encryption/decryption operations

Usage:
    from kg_builder.encryption import get_encryption_service

    service = get_encryption_service()
    encrypted = service.encrypt("api-key-value", tenant_id)
    decrypted = service.decrypt(encrypted, tenant_id)

Security Considerations:
- Master key must be a valid 32-byte URL-safe base64 Fernet key
- Per-tenant derivation ensures tenant isolation
- Never log decrypted values
- Rotate master key periodically (requires re-encryption of all values)
"""

import base64
import logging
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger(__name__)


class EncryptionError(Exception):
    """Base exception for encryption operations."""

    pass


class EncryptionKeyError(EncryptionError):
    """Raised when the encryption key is invalid or missing."""

    pass


class DecryptionError(EncryptionError):
    """Raised when decryption fails (invalid data or wrong key)."""

    pass


class EncryptionService:
    """
    Service for encrypting and decrypting sensitive field values.

    Uses Fernet symmetric encryption with per-tenant key derivation
    for enhanced isolation between tenants.

    Attributes:
        _master_key: The master encryption key (Fernet format)
        _enabled: Whether encryption is enabled
        _tenant_ciphers: Cache of per-tenant Fernet instances
    """

    # Prefix for encrypted values to identify them
    ENCRYPTED_PREFIX = "enc:v1:"

    def __init__(
        self,
        master_key: str | None = None,
        enabled: bool = True,
    ):
        """
        Initialize the encryption service.

        Args:
            master_key: Base64-encoded Fernet key (32 bytes URL-safe base64)
            enabled: Whether encryption is active (False = passthrough mode)

        Raises:
            EncryptionKeyError: If master_key is invalid and encryption is enabled
        """
        self._enabled = enabled
        self._master_key: bytes | None = None
        self._master_fernet: Fernet | None = None
        self._tenant_ciphers: dict[UUID, Fernet] = {}

        if enabled:
            if not master_key:
                raise EncryptionKeyError(
                    "ENCRYPTION_MASTER_KEY is required when encryption is enabled"
                )
            self._validate_and_set_master_key(master_key)

    def _validate_and_set_master_key(self, key: str) -> None:
        """
        Validate and store the master encryption key.

        Args:
            key: Base64-encoded Fernet key

        Raises:
            EncryptionKeyError: If key is invalid
        """
        # Reject obvious placeholder values
        forbidden_values = [
            "change-me-in-production",
            "your-encryption-key-here",
            "CHANGE_ME",
            "",
        ]
        if key in forbidden_values:
            raise EncryptionKeyError(
                "Master encryption key contains a placeholder value. "
                "Generate a proper key using: python -c "
                '"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            )

        try:
            # Validate it's a proper Fernet key by creating an instance
            key_bytes = key.encode("utf-8")
            self._master_fernet = Fernet(key_bytes)
            self._master_key = key_bytes
            logger.info("Encryption service initialized with valid master key")
        except Exception as e:
            raise EncryptionKeyError(
                f"Invalid master encryption key format: {e}. "
                "Key must be a valid 32-byte URL-safe base64 Fernet key."
            ) from e

    def _derive_tenant_key(self, tenant_id: UUID) -> bytes:
        """
        Derive a tenant-specific encryption key using HKDF.

        This ensures that even if one tenant's derived key is compromised,
        other tenants' data remains protected.

        Args:
            tenant_id: The tenant's unique identifier

        Returns:
            32-byte URL-safe base64 encoded key suitable for Fernet
        """
        if not self._master_key:
            raise EncryptionKeyError("Master key not initialized")

        # Use HKDF to derive a tenant-specific key
        # Salt includes the tenant_id for uniqueness
        salt = f"knowledge-mapper:tenant:{tenant_id}".encode()

        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,  # Fernet requires 32 bytes
            salt=salt,
            info=b"tenant-encryption-key",
        )

        # Derive key from master key
        derived_key = hkdf.derive(self._master_key)

        # Fernet expects URL-safe base64 encoding
        return base64.urlsafe_b64encode(derived_key)

    def _get_tenant_cipher(self, tenant_id: UUID) -> Fernet:
        """
        Get or create a Fernet cipher for a specific tenant.

        Caches cipher instances to avoid repeated key derivation.

        Args:
            tenant_id: The tenant's unique identifier

        Returns:
            Fernet instance for the tenant
        """
        if tenant_id not in self._tenant_ciphers:
            derived_key = self._derive_tenant_key(tenant_id)
            self._tenant_ciphers[tenant_id] = Fernet(derived_key)
            logger.debug(
                "Created cipher for tenant",
                extra={"tenant_id": str(tenant_id)},
            )
        return self._tenant_ciphers[tenant_id]

    def encrypt(
        self,
        plaintext: str,
        tenant_id: UUID,
        field_name: str | None = None,
    ) -> str:
        """
        Encrypt a plaintext value for a specific tenant.

        Args:
            plaintext: The value to encrypt
            tenant_id: The tenant owning this data
            field_name: Optional field name for audit logging

        Returns:
            Encrypted string with version prefix (enc:v1:...)

        Raises:
            EncryptionError: If encryption fails
        """
        if not self._enabled:
            # Passthrough mode - return as-is
            return plaintext

        if not plaintext:
            return plaintext

        try:
            cipher = self._get_tenant_cipher(tenant_id)
            encrypted_bytes = cipher.encrypt(plaintext.encode("utf-8"))
            encrypted_str = encrypted_bytes.decode("utf-8")

            # Prefix with version for future migration support
            result = f"{self.ENCRYPTED_PREFIX}{encrypted_str}"

            logger.info(
                "Encrypted field value",
                extra={
                    "tenant_id": str(tenant_id),
                    "field_name": field_name or "unknown",
                    "encrypted_length": len(result),
                },
            )

            return result

        except Exception as e:
            logger.error(
                "Encryption failed",
                extra={
                    "tenant_id": str(tenant_id),
                    "field_name": field_name or "unknown",
                    "error": str(e),
                },
            )
            raise EncryptionError(f"Failed to encrypt value: {e}") from e

    def decrypt(
        self,
        ciphertext: str,
        tenant_id: UUID,
        field_name: str | None = None,
    ) -> str:
        """
        Decrypt a ciphertext value for a specific tenant.

        Args:
            ciphertext: The encrypted value (with enc:v1: prefix)
            tenant_id: The tenant owning this data
            field_name: Optional field name for audit logging

        Returns:
            Decrypted plaintext string

        Raises:
            DecryptionError: If decryption fails (wrong key, corrupted data)
        """
        if not self._enabled:
            # Passthrough mode - return as-is
            return ciphertext

        if not ciphertext:
            return ciphertext

        # Check if this is actually encrypted
        if not ciphertext.startswith(self.ENCRYPTED_PREFIX):
            # Not encrypted - return as-is (migration support)
            logger.debug(
                "Value not encrypted, returning as-is",
                extra={
                    "tenant_id": str(tenant_id),
                    "field_name": field_name or "unknown",
                },
            )
            return ciphertext

        try:
            # Remove version prefix
            encrypted_data = ciphertext[len(self.ENCRYPTED_PREFIX) :]
            cipher = self._get_tenant_cipher(tenant_id)
            decrypted_bytes = cipher.decrypt(encrypted_data.encode("utf-8"))
            result = decrypted_bytes.decode("utf-8")

            logger.info(
                "Decrypted field value",
                extra={
                    "tenant_id": str(tenant_id),
                    "field_name": field_name or "unknown",
                },
            )

            return result

        except InvalidToken:
            logger.error(
                "Decryption failed - invalid token (wrong key or corrupted data)",
                extra={
                    "tenant_id": str(tenant_id),
                    "field_name": field_name or "unknown",
                },
            )
            raise DecryptionError(
                "Failed to decrypt value - data may be corrupted or encrypted with a different key"
            )
        except Exception as e:
            logger.error(
                "Decryption failed",
                extra={
                    "tenant_id": str(tenant_id),
                    "field_name": field_name or "unknown",
                    "error": str(e),
                },
            )
            raise DecryptionError(f"Failed to decrypt value: {e}") from e

    def is_encrypted(self, value: str) -> bool:
        """
        Check if a value appears to be encrypted.

        Args:
            value: The value to check

        Returns:
            True if value has encryption prefix
        """
        return value.startswith(self.ENCRYPTED_PREFIX) if value else False

    def encrypt_dict_field(
        self,
        data: dict,
        field_path: str,
        tenant_id: UUID,
    ) -> dict:
        """
        Encrypt a specific field within a dictionary.

        Useful for encrypting specific fields in JSON config objects.

        Args:
            data: Dictionary containing the field
            field_path: Dot-separated path to field (e.g., "api_key" or "auth.token")
            tenant_id: The tenant owning this data

        Returns:
            Copy of dictionary with field encrypted
        """
        result = data.copy()
        parts = field_path.split(".")

        # Navigate to parent
        current = result
        for part in parts[:-1]:
            if part in current and isinstance(current[part], dict):
                current[part] = current[part].copy()
                current = current[part]
            else:
                return result  # Path doesn't exist

        # Encrypt the field
        field_name = parts[-1]
        if field_name in current and isinstance(current[field_name], str):
            current[field_name] = self.encrypt(
                current[field_name],
                tenant_id,
                field_name=field_path,
            )

        return result

    def decrypt_dict_field(
        self,
        data: dict,
        field_path: str,
        tenant_id: UUID,
    ) -> dict:
        """
        Decrypt a specific field within a dictionary.

        Args:
            data: Dictionary containing the encrypted field
            field_path: Dot-separated path to field
            tenant_id: The tenant owning this data

        Returns:
            Copy of dictionary with field decrypted
        """
        result = data.copy()
        parts = field_path.split(".")

        # Navigate to parent
        current = result
        for part in parts[:-1]:
            if part in current and isinstance(current[part], dict):
                current[part] = current[part].copy()
                current = current[part]
            else:
                return result  # Path doesn't exist

        # Decrypt the field
        field_name = parts[-1]
        if field_name in current and isinstance(current[field_name], str):
            current[field_name] = self.decrypt(
                current[field_name],
                tenant_id,
                field_name=field_path,
            )

        return result

    def clear_tenant_cache(self, tenant_id: UUID | None = None) -> None:
        """
        Clear cached tenant ciphers.

        Call this if master key is rotated or for memory cleanup.

        Args:
            tenant_id: Specific tenant to clear, or None for all
        """
        if tenant_id:
            self._tenant_ciphers.pop(tenant_id, None)
            logger.debug(
                "Cleared cipher cache for tenant",
                extra={"tenant_id": str(tenant_id)},
            )
        else:
            self._tenant_ciphers.clear()
            logger.info("Cleared all tenant cipher caches")

    @staticmethod
    def generate_key() -> str:
        """
        Generate a new Fernet-compatible encryption key.

        Use this to generate the ENCRYPTION_MASTER_KEY value.

        Returns:
            URL-safe base64 encoded 32-byte key
        """
        return Fernet.generate_key().decode("utf-8")

    @staticmethod
    def mask_value(value: str, visible_chars: int = 4) -> str:
        """
        Mask a sensitive value for display.

        Args:
            value: The value to mask
            visible_chars: Number of characters to show at end

        Returns:
            Masked string like "****abcd"
        """
        if not value or len(value) <= visible_chars:
            return "****"
        return f"****{value[-visible_chars:]}"


# Global service instance (lazy initialization)
_encryption_service: EncryptionService | None = None


def get_encryption_service() -> EncryptionService:
    """
    Get the global encryption service instance.

    Lazily initializes from settings on first call.

    Returns:
        EncryptionService instance
    """
    global _encryption_service
    if _encryption_service is None:
        from kg_builder.config import settings

        _encryption_service = EncryptionService(
            master_key=settings.ENCRYPTION_MASTER_KEY,
            enabled=settings.ENCRYPTION_ENABLED,
        )
    return _encryption_service


def reset_encryption_service() -> None:
    """
    Reset the global encryption service.

    Useful for testing or after configuration changes.
    """
    global _encryption_service
    if _encryption_service:
        _encryption_service.clear_tenant_cache()
    _encryption_service = None
