"""Tests for libs/platform/secrets/__init__.py lazy loading.

Tests cover the __getattr__ lazy loading mechanism for backend implementations.
"""

import pytest


class TestLazyLoadingBackends:
    """Test the __getattr__ lazy loading mechanism."""

    def test_lazy_load_aws_secrets_manager(self) -> None:
        """Test lazy loading of AWSSecretsManager."""
        from libs.platform import secrets

        # Access AWSSecretsManager via __getattr__
        aws_cls = secrets.AWSSecretsManager
        assert aws_cls is not None
        assert aws_cls.__name__ == "AWSSecretsManager"

    def test_lazy_load_vault_secret_manager(self) -> None:
        """Test lazy loading of VaultSecretManager."""
        from libs.platform import secrets

        # Access VaultSecretManager via __getattr__
        vault_cls = secrets.VaultSecretManager
        assert vault_cls is not None
        assert vault_cls.__name__ == "VaultSecretManager"

    def test_lazy_load_env_secret_manager(self) -> None:
        """Test lazy loading of EnvSecretManager."""
        from libs.platform import secrets

        # Access EnvSecretManager via __getattr__
        env_cls = secrets.EnvSecretManager
        assert env_cls is not None
        assert env_cls.__name__ == "EnvSecretManager"

    def test_lazy_load_unknown_attribute_raises_attribute_error(self) -> None:
        """Test that unknown attributes raise AttributeError."""
        from libs.platform import secrets

        with pytest.raises(
            AttributeError, match="module 'libs.platform.secrets' has no attribute 'NonExistent'"
        ):
            _ = secrets.NonExistent

    def test_lazy_load_returns_same_class_each_time(self) -> None:
        """Test that lazy loading returns the same class on repeated access."""
        from libs.platform import secrets

        aws_cls1 = secrets.AWSSecretsManager
        aws_cls2 = secrets.AWSSecretsManager
        assert aws_cls1 is aws_cls2


class TestModuleExports:
    """Test that all expected exports are available."""

    def test_all_exports_available(self) -> None:
        """Test that all __all__ exports are accessible."""
        from libs.platform import secrets

        expected_exports = [
            "SecretManager",
            "create_secret_manager",
            "EnvSecretManager",
            "VaultSecretManager",
            "AWSSecretsManager",
            "SecretCache",
            "SecretManagerError",
            "SecretNotFoundError",
            "SecretAccessError",
            "SecretWriteError",
        ]

        for export in expected_exports:
            assert hasattr(secrets, export), f"Missing export: {export}"

    def test_exceptions_are_importable(self) -> None:
        """Test that exception classes can be imported directly."""
        from libs.platform.secrets import (
            SecretAccessError,
            SecretManagerError,
            SecretNotFoundError,
            SecretWriteError,
        )

        assert SecretManagerError is not None
        assert SecretNotFoundError is not None
        assert SecretAccessError is not None
        assert SecretWriteError is not None

    def test_cache_is_importable(self) -> None:
        """Test that SecretCache can be imported directly."""
        from libs.platform.secrets import SecretCache

        assert SecretCache is not None

    def test_factory_is_importable(self) -> None:
        """Test that create_secret_manager can be imported directly."""
        from libs.platform.secrets import create_secret_manager

        assert create_secret_manager is not None
        assert callable(create_secret_manager)

    def test_manager_is_importable(self) -> None:
        """Test that SecretManager can be imported directly."""
        from libs.platform.secrets import SecretManager

        assert SecretManager is not None
