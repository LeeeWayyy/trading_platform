"""
Tests for SecretManager interface and exception hierarchy.

This module validates:
1. Exception hierarchy (SecretManagerError, SecretNotFoundError, SecretAccessError, SecretWriteError)
2. Exception context handling (secret name + backend)
3. SecretManager is abstract (cannot instantiate)
4. SecretManager requires subclass implementation of abstract methods
5. SecretManager context manager protocol (with statement support)
"""

import pytest

from libs.secrets.exceptions import (
    SecretAccessError,
    SecretManagerError,
    SecretNotFoundError,
    SecretWriteError,
)
from libs.secrets.manager import SecretManager


# ============================================================================
# EXCEPTION TESTS
# ============================================================================


class TestSecretManagerError:
    """Test base exception SecretManagerError."""

    def test_basic_initialization(self):
        """Test basic exception initialization with message only."""
        exc = SecretManagerError("Something went wrong")
        assert str(exc) == "Something went wrong"
        assert exc.message == "Something went wrong"
        assert exc.secret_name is None
        assert exc.backend is None

    def test_initialization_with_secret_name(self):
        """Test exception with secret name context."""
        exc = SecretManagerError(
            "Operation failed",
            secret_name="database/password",
        )
        assert exc.secret_name == "database/password"
        assert "secret: database/password" in str(exc)

    def test_initialization_with_backend(self):
        """Test exception with backend context."""
        exc = SecretManagerError(
            "Operation failed",
            backend="vault",
        )
        assert exc.backend == "vault"
        assert "backend: vault" in str(exc)

    def test_initialization_with_full_context(self):
        """Test exception with both secret name and backend."""
        exc = SecretManagerError(
            "Connection timeout",
            secret_name="alpaca/api_key_id",
            backend="aws",
        )
        assert "Connection timeout" in str(exc)
        assert "secret: alpaca/api_key_id" in str(exc)
        assert "backend: aws" in str(exc)

    def test_string_representation_format(self):
        """Test formatted string representation."""
        exc = SecretManagerError(
            "Test message",
            secret_name="test/secret",
            backend="test_backend",
        )
        expected = "Test message (secret: test/secret, backend: test_backend)"
        assert str(exc) == expected


class TestSecretNotFoundError:
    """Test SecretNotFoundError exception."""

    def test_basic_initialization(self):
        """Test basic initialization with secret name and backend."""
        exc = SecretNotFoundError(
            secret_name="database/password",
            backend="vault",
        )
        assert exc.secret_name == "database/password"
        assert exc.backend == "vault"
        assert "database/password" in str(exc)
        assert "VAULT" in str(exc)  # Backend uppercased in message

    def test_initialization_with_context(self):
        """Test initialization with additional context."""
        exc = SecretNotFoundError(
            secret_name="staging/alpaca/api_key_id",
            backend="aws",
            additional_context="Check namespace: staging",
        )
        assert "staging/alpaca/api_key_id" in str(exc)
        assert "Check namespace: staging" in str(exc)

    def test_inheritance(self):
        """Test SecretNotFoundError inherits from SecretManagerError."""
        exc = SecretNotFoundError(
            secret_name="test/secret",
            backend="env",
        )
        assert isinstance(exc, SecretManagerError)
        assert isinstance(exc, SecretNotFoundError)

    def test_validation_rejects_non_string_secret_name(self):
        """Test runtime validation rejects non-string secret_name."""
        with pytest.raises(TypeError) as exc_info:
            SecretNotFoundError(
                secret_name=None,  # type: ignore[arg-type]
                backend="vault",
            )
        assert "secret_name must be a non-empty string" in str(exc_info.value)

    def test_validation_rejects_empty_secret_name(self):
        """Test runtime validation rejects empty secret_name."""
        with pytest.raises(TypeError) as exc_info:
            SecretNotFoundError(
                secret_name="",
                backend="vault",
            )
        assert "secret_name must be a non-empty string" in str(exc_info.value)

    def test_validation_rejects_non_string_backend(self):
        """Test runtime validation rejects non-string backend."""
        with pytest.raises(TypeError) as exc_info:
            SecretNotFoundError(
                secret_name="database/password",
                backend=123,  # type: ignore[arg-type]
            )
        assert "backend must be a non-empty string" in str(exc_info.value)

    def test_validation_rejects_empty_backend(self):
        """Test runtime validation rejects empty backend."""
        with pytest.raises(TypeError) as exc_info:
            SecretNotFoundError(
                secret_name="database/password",
                backend="",
            )
        assert "backend must be a non-empty string" in str(exc_info.value)


class TestSecretAccessError:
    """Test SecretAccessError exception."""

    def test_basic_initialization(self):
        """Test basic initialization with reason."""
        exc = SecretAccessError(
            secret_name="database/password",
            backend="vault",
            reason="Invalid token",
        )
        assert exc.secret_name == "database/password"
        assert exc.backend == "vault"
        assert "Invalid token" in str(exc)
        assert "Access denied" in str(exc)

    def test_permission_denied_reason(self):
        """Test with permission denied reason."""
        exc = SecretAccessError(
            secret_name="prod/alpaca/api_key_id",
            backend="aws",
            reason="Permission denied (GetSecretValue)",
        )
        assert "Permission denied" in str(exc)

    def test_inheritance(self):
        """Test SecretAccessError inherits from SecretManagerError."""
        exc = SecretAccessError(
            secret_name="test/secret",
            backend="vault",
            reason="Test",
        )
        assert isinstance(exc, SecretManagerError)
        assert isinstance(exc, SecretAccessError)

    def test_validation_rejects_invalid_parameters(self):
        """Test runtime validation rejects invalid parameters."""
        # Test invalid secret_name
        with pytest.raises(TypeError) as exc_info:
            SecretAccessError(
                secret_name="",
                backend="vault",
                reason="Test",
            )
        assert "secret_name must be a non-empty string" in str(exc_info.value)

        # Test invalid backend
        with pytest.raises(TypeError) as exc_info:
            SecretAccessError(
                secret_name="test/secret",
                backend="",
                reason="Test",
            )
        assert "backend must be a non-empty string" in str(exc_info.value)

        # Test invalid reason
        with pytest.raises(TypeError) as exc_info:
            SecretAccessError(
                secret_name="test/secret",
                backend="vault",
                reason="",
            )
        assert "reason must be a non-empty string" in str(exc_info.value)


class TestSecretWriteError:
    """Test SecretWriteError exception."""

    def test_basic_initialization(self):
        """Test basic initialization with reason."""
        exc = SecretWriteError(
            secret_name="database/password",
            backend="vault",
            reason="Read-only mode",
        )
        assert exc.secret_name == "database/password"
        assert exc.backend == "vault"
        assert "Read-only mode" in str(exc)
        assert "Failed to write secret" in str(exc)

    def test_vault_standby_reason(self):
        """Test with Vault standby mode reason."""
        exc = SecretWriteError(
            secret_name="staging/alpaca/api_key_id",
            backend="vault",
            reason="Vault in standby mode",
        )
        assert "Vault in standby mode" in str(exc)

    def test_inheritance(self):
        """Test SecretWriteError inherits from SecretManagerError."""
        exc = SecretWriteError(
            secret_name="test/secret",
            backend="env",
            reason="Test",
        )
        assert isinstance(exc, SecretManagerError)
        assert isinstance(exc, SecretWriteError)

    def test_validation_rejects_invalid_parameters(self):
        """Test runtime validation rejects invalid parameters."""
        # Test invalid secret_name
        with pytest.raises(TypeError) as exc_info:
            SecretWriteError(
                secret_name="",
                backend="vault",
                reason="Test",
            )
        assert "secret_name must be a non-empty string" in str(exc_info.value)

        # Test invalid backend
        with pytest.raises(TypeError) as exc_info:
            SecretWriteError(
                secret_name="test/secret",
                backend="",
                reason="Test",
            )
        assert "backend must be a non-empty string" in str(exc_info.value)

        # Test invalid reason
        with pytest.raises(TypeError) as exc_info:
            SecretWriteError(
                secret_name="test/secret",
                backend="vault",
                reason="",
            )
        assert "reason must be a non-empty string" in str(exc_info.value)


# ============================================================================
# SECRET MANAGER INTERFACE TESTS
# ============================================================================


class TestSecretManagerInterface:
    """Test SecretManager abstract interface."""

    def test_cannot_instantiate_abstract_class(self):
        """Test SecretManager cannot be instantiated directly."""
        with pytest.raises(TypeError) as exc_info:
            SecretManager()

        # Python 3.11+ error message includes abstract method names
        assert "abstract" in str(exc_info.value).lower()

    def test_subclass_must_implement_get_secret(self):
        """Test subclass must implement get_secret()."""

        class IncompleteManager(SecretManager):
            def list_secrets(self, prefix=None):
                return []

            def set_secret(self, name, value):
                pass

        with pytest.raises(TypeError) as exc_info:
            IncompleteManager()

        assert "get_secret" in str(exc_info.value) or "abstract" in str(
            exc_info.value
        ).lower()

    def test_subclass_must_implement_list_secrets(self):
        """Test subclass must implement list_secrets()."""

        class IncompleteManager(SecretManager):
            def get_secret(self, name):
                return "value"

            def set_secret(self, name, value):
                pass

        with pytest.raises(TypeError) as exc_info:
            IncompleteManager()

        assert "list_secrets" in str(exc_info.value) or "abstract" in str(
            exc_info.value
        ).lower()

    def test_subclass_must_implement_set_secret(self):
        """Test subclass must implement set_secret()."""

        class IncompleteManager(SecretManager):
            def get_secret(self, name):
                return "value"

            def list_secrets(self, prefix=None):
                return []

        with pytest.raises(TypeError) as exc_info:
            IncompleteManager()

        assert "set_secret" in str(exc_info.value) or "abstract" in str(
            exc_info.value
        ).lower()

    def test_complete_implementation_works(self):
        """Test complete implementation can be instantiated."""

        class CompleteManager(SecretManager):
            def get_secret(self, name):
                return f"value_for_{name}"

            def list_secrets(self, prefix=None):
                return ["secret1", "secret2"]

            def set_secret(self, name, value):
                pass

        manager = CompleteManager()
        assert isinstance(manager, SecretManager)
        assert manager.get_secret("test") == "value_for_test"
        assert manager.list_secrets() == ["secret1", "secret2"]

    def test_close_method_default_implementation(self):
        """Test close() method has default no-op implementation."""

        class MinimalManager(SecretManager):
            def get_secret(self, name):
                return "value"

            def list_secrets(self, prefix=None):
                return []

            def set_secret(self, name, value):
                pass

        manager = MinimalManager()
        # Should not raise exception (default no-op)
        manager.close()

    def test_context_manager_protocol(self):
        """Test SecretManager supports with statement."""

        class TestManager(SecretManager):
            def __init__(self):
                self.closed = False

            def get_secret(self, name):
                return "value"

            def list_secrets(self, prefix=None):
                return []

            def set_secret(self, name, value):
                pass

            def close(self):
                self.closed = True

        with TestManager() as manager:
            assert isinstance(manager, SecretManager)
            assert not manager.closed

        # After exiting context, close() should be called
        assert manager.closed

    def test_context_manager_calls_close_on_exception(self):
        """Test context manager calls close() even if exception occurs."""

        class TestManager(SecretManager):
            def __init__(self):
                self.closed = False

            def get_secret(self, name):
                return "value"

            def list_secrets(self, prefix=None):
                return []

            def set_secret(self, name, value):
                pass

            def close(self):
                self.closed = True

        try:
            with TestManager() as manager:
                raise ValueError("Test exception")
        except ValueError:
            pass

        # close() should still be called
        assert manager.closed
