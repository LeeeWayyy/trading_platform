"""Tests for libs/platform/secrets/exceptions.py.

Covers:
- Base SecretManagerError formatting
- Validation logic for subclasses
- Message construction for specific error types
"""

import pytest

from libs.platform.secrets.exceptions import (
    SecretAccessError,
    SecretManagerError,
    SecretNotFoundError,
    SecretWriteError,
)


class TestSecretManagerError:
    """Tests for SecretManagerError base behavior."""

    @pytest.mark.unit()
    def test_str_without_context_returns_message(self) -> None:
        err = SecretManagerError("timeout")
        assert str(err) == "timeout"
        assert err.message == "timeout"
        assert err.secret_name is None
        assert err.backend is None

    @pytest.mark.unit()
    def test_str_with_secret_and_backend_adds_context(self) -> None:
        err = SecretManagerError(
            message="failed",
            secret_name="db/password",
            backend="vault",
        )
        assert str(err) == "failed (secret: db/password, backend: vault)"

    @pytest.mark.unit()
    def test_str_with_only_secret(self) -> None:
        err = SecretManagerError("failed", secret_name="db/password")
        assert str(err) == "failed (secret: db/password)"

    @pytest.mark.unit()
    def test_str_with_only_backend(self) -> None:
        err = SecretManagerError("failed", backend="aws")
        assert str(err) == "failed (backend: aws)"


class TestSecretNotFoundError:
    """Tests for SecretNotFoundError message and validation."""

    @pytest.mark.unit()
    def test_message_includes_uppercase_backend(self) -> None:
        err = SecretNotFoundError("alpha/key", "vault")
        assert err.message == "Secret 'alpha/key' not found in VAULT"
        assert err.secret_name == "alpha/key"
        assert err.backend == "vault"

    @pytest.mark.unit()
    def test_message_includes_additional_context(self) -> None:
        err = SecretNotFoundError("alpha/key", "aws", "Check namespace")
        assert err.message == "Secret 'alpha/key' not found in AWS. Check namespace"

    @pytest.mark.unit()
    @pytest.mark.parametrize(
        ("secret_name", "backend"),
        [
            ("", "vault"),
            (None, "vault"),
            ("alpha/key", ""),
            ("alpha/key", None),
        ],
    )
    def test_validation_rejects_missing_inputs(self, secret_name, backend) -> None:
        with pytest.raises(TypeError):
            SecretNotFoundError(secret_name, backend)  # type: ignore[arg-type]


class TestSecretAccessError:
    """Tests for SecretAccessError message and validation."""

    @pytest.mark.unit()
    def test_message_includes_reason(self) -> None:
        err = SecretAccessError("alpha/key", "vault", "Token expired")
        assert err.message == "Access denied: Token expired"
        assert str(err) == "Access denied: Token expired (secret: alpha/key, backend: vault)"

    @pytest.mark.unit()
    @pytest.mark.parametrize(
        ("secret_name", "backend", "reason"),
        [
            ("", "vault", "reason"),
            (None, "vault", "reason"),
            ("alpha/key", "", "reason"),
            ("alpha/key", None, "reason"),
            ("alpha/key", "vault", ""),
            ("alpha/key", "vault", None),
        ],
    )
    def test_validation_rejects_missing_inputs(
        self,
        secret_name,
        backend,
        reason,
    ) -> None:
        with pytest.raises(TypeError):
            SecretAccessError(secret_name, backend, reason)  # type: ignore[arg-type]


class TestSecretWriteError:
    """Tests for SecretWriteError message and validation."""

    @pytest.mark.unit()
    def test_message_includes_reason(self) -> None:
        err = SecretWriteError("alpha/key", "aws", "Read-only")
        assert err.message == "Failed to write secret: Read-only"
        assert str(err) == "Failed to write secret: Read-only (secret: alpha/key, backend: aws)"

    @pytest.mark.unit()
    @pytest.mark.parametrize(
        ("secret_name", "backend", "reason"),
        [
            ("", "aws", "reason"),
            (None, "aws", "reason"),
            ("alpha/key", "", "reason"),
            ("alpha/key", None, "reason"),
            ("alpha/key", "aws", ""),
            ("alpha/key", "aws", None),
        ],
    )
    def test_validation_rejects_missing_inputs(
        self,
        secret_name,
        backend,
        reason,
    ) -> None:
        with pytest.raises(TypeError):
            SecretWriteError(secret_name, backend, reason)  # type: ignore[arg-type]
