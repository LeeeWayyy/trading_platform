"""
Secrets Management Exception Hierarchy.

This module defines all exceptions raised by the secrets management system,
providing clear error semantics for secret retrieval, access, and write failures.

Exception hierarchy:
    SecretManagerError (base)
    ├── SecretNotFoundError - Secret doesn't exist in backend
    ├── SecretAccessError - Permission/authentication failure
    └── SecretWriteError - Failed to write/update secret

All exceptions include structured context (secret name, backend type) without
exposing secret values (security requirement AC12).
"""


class SecretManagerError(Exception):
    """
    Base exception for all secrets management errors.

    This base class provides common context handling for all secret-related
    exceptions. Subclasses MUST NOT include secret values in error messages
    (security requirement AC12: secret redaction).

    Attributes:
        secret_name: Name/path of the secret (e.g., "database/password")
        backend: Backend type ("vault", "aws", "env")
        message: Human-readable error message (MUST NOT include secret value)

    Example:
        >>> try:
        ...     secret = secret_mgr.get_secret("database/password")
        ... except SecretManagerError as e:
        ...     logger.error(f"Secret error: {e.secret_name} ({e.backend})")
        ...     # CORRECT: Logs secret name only, NOT value
    """

    def __init__(
        self,
        message: str,
        secret_name: str | None = None,
        backend: str | None = None,
    ) -> None:
        """
        Initialize SecretManagerError with context.

        Args:
            message: Human-readable error message (MUST NOT include secret value)
            secret_name: Name/path of the secret (e.g., "staging/alpaca/api_key_id")
            backend: Backend type ("vault", "aws", "env")

        Security:
            - NEVER include secret values in message or attributes
            - Only log secret names/paths (safe for audit logs)
        """
        super().__init__(message)
        self.secret_name = secret_name
        self.backend = backend
        self.message = message

    def __str__(self) -> str:
        """
        Format error message with context (secret name + backend).

        Returns:
            Formatted error message including secret name and backend type
            (NEVER includes secret value - security requirement AC12)

        Example:
            >>> str(SecretManagerError("Timeout", "db/password", "vault"))
            "Timeout (secret: db/password, backend: vault)"
        """
        context_parts = []
        if self.secret_name:
            context_parts.append(f"secret: {self.secret_name}")
        if self.backend:
            context_parts.append(f"backend: {self.backend}")

        if context_parts:
            context = ", ".join(context_parts)
            return f"{self.message} ({context})"
        return self.message


class SecretNotFoundError(SecretManagerError):
    """
    Raised when a requested secret doesn't exist in the backend.

    This exception is raised when:
    - Secret path is valid but secret doesn't exist
    - Secret was deleted but code still expects it
    - Secret name is misspelled in configuration

    Common causes:
    - Migration incomplete (secret not yet populated in backend)
    - Wrong namespace (e.g., "staging" vs "prod")
    - Typo in secret name (e.g., "api_key_id" vs "api_key")

    Resolution:
    - Verify secret exists: `vault kv get secret/<namespace>/<name>`
    - Check namespace matches environment (staging/prod)
    - Review secret naming conventions in ADR-0017

    Example:
        >>> secret_mgr.get_secret("database/password")
        SecretNotFoundError: Secret 'database/password' not found in Vault
                            (secret: database/password, backend: vault)
    """

    def __init__(
        self,
        secret_name: str,
        backend: str,
        additional_context: str | None = None,
    ) -> None:
        """
        Initialize SecretNotFoundError with secret name and backend.

        Args:
            secret_name: Name/path of the missing secret
            backend: Backend type where secret was not found ("vault", "aws", "env")
            additional_context: Optional extra context (e.g., "Check namespace: staging")

        Raises:
            TypeError: If secret_name or backend is not a non-empty string

        Example:
            >>> raise SecretNotFoundError(
            ...     "staging/alpaca/api_key_id",
            ...     "vault",
            ...     "Verify secret exists in 'staging' namespace"
            ... )
        """
        # Runtime validation (defense against incorrect backend implementations)
        if not isinstance(secret_name, str) or not secret_name:
            raise TypeError("secret_name must be a non-empty string")
        if not isinstance(backend, str) or not backend:
            raise TypeError("backend must be a non-empty string")

        base_message = f"Secret '{secret_name}' not found in {backend.upper()}"
        if additional_context:
            base_message += f". {additional_context}"

        super().__init__(
            message=base_message,
            secret_name=secret_name,
            backend=backend,
        )


class SecretAccessError(SecretManagerError):
    """
    Raised when authentication/authorization fails accessing secrets backend.

    This exception is raised when:
    - Invalid Vault token or AWS IAM credentials
    - Insufficient permissions (read-only when write needed)
    - Backend unreachable (network timeout, connection refused)
    - Backend sealed/unavailable (Vault sealed mode)

    Common causes:
    - Expired credentials (Vault token TTL exceeded)
    - Wrong IAM role/policy (AWS permission denied)
    - Network issues (firewall, DNS, SSL/TLS)
    - Backend misconfiguration (wrong URL, port)

    Resolution:
    - Verify credentials: `vault login` or `aws sts get-caller-identity`
    - Check network: `curl $VAULT_ADDR/v1/sys/health`
    - Review permissions: Vault policies or AWS IAM policies

    Example:
        >>> secret_mgr.get_secret("database/password")
        SecretAccessError: Permission denied (GetSecretValue on database/password)
                          (secret: database/password, backend: aws)
    """

    def __init__(
        self,
        secret_name: str,
        backend: str,
        reason: str,
    ) -> None:
        """
        Initialize SecretAccessError with secret name, backend, and failure reason.

        Args:
            secret_name: Name/path of the secret (for audit logging)
            backend: Backend type ("vault", "aws", "env")
            reason: Specific failure reason (e.g., "Invalid token", "Permission denied")

        Raises:
            TypeError: If secret_name, backend, or reason is not a non-empty string

        Example:
            >>> raise SecretAccessError(
            ...     "prod/database/password",
            ...     "vault",
            ...     "Token expired (TTL: 1h)"
            ... )
        """
        # Runtime validation (defense against incorrect backend implementations)
        if not isinstance(secret_name, str) or not secret_name:
            raise TypeError("secret_name must be a non-empty string")
        if not isinstance(backend, str) or not backend:
            raise TypeError("backend must be a non-empty string")
        if not isinstance(reason, str) or not reason:
            raise TypeError("reason must be a non-empty string")

        message = f"Access denied: {reason}"
        super().__init__(
            message=message,
            secret_name=secret_name,
            backend=backend,
        )


class SecretWriteError(SecretManagerError):
    """
    Raised when writing/updating a secret fails.

    This exception is raised when:
    - Write operation fails due to backend error
    - Insufficient permissions (read-only access)
    - Backend in read-only mode (Vault standby node)
    - Network failure during write

    Common causes:
    - Wrong Vault node (standby vs active leader)
    - Read-only IAM policy (AWS)
    - Backend storage full/corrupted
    - Network timeout during write

    Resolution:
    - Verify write permissions: Vault policy or AWS IAM role
    - Check backend health: `vault status` (ensure active, unsealed)
    - Review backend logs for storage errors

    Example:
        >>> secret_mgr.set_secret("database/password", "new_password")
        SecretWriteError: Failed to write secret (Permission denied)
                         (secret: database/password, backend: vault)
    """

    def __init__(
        self,
        secret_name: str,
        backend: str,
        reason: str,
    ) -> None:
        """
        Initialize SecretWriteError with secret name, backend, and failure reason.

        Args:
            secret_name: Name/path of the secret (for audit logging)
            backend: Backend type ("vault", "aws", "env")
            reason: Specific failure reason (e.g., "Read-only mode", "Storage full")

        Raises:
            TypeError: If secret_name, backend, or reason is not a non-empty string

        Example:
            >>> raise SecretWriteError(
            ...     "staging/alpaca/api_key_id",
            ...     "vault",
            ...     "Vault in standby mode (write to active node)"
            ... )
        """
        # Runtime validation (defense against incorrect backend implementations)
        if not isinstance(secret_name, str) or not secret_name:
            raise TypeError("secret_name must be a non-empty string")
        if not isinstance(backend, str) or not backend:
            raise TypeError("backend must be a non-empty string")
        if not isinstance(reason, str) or not reason:
            raise TypeError("reason must be a non-empty string")

        message = f"Failed to write secret: {reason}"
        super().__init__(
            message=message,
            secret_name=secret_name,
            backend=backend,
        )
