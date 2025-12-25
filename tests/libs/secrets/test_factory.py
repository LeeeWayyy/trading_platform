"""
Tests for libs/secrets/factory.py - Secret Manager Factory Pattern.

Test Coverage:
    - Backend selection via SECRET_BACKEND environment variable
    - Production guardrail (EnvSecretManager only allowed in local)
    - Invalid backend name error handling
    - Default backend selection
    - Override parameters (backend, deployment_env)

Test Organization:
    - TestCreateSecretManagerBackendSelection: Backend selection logic
    - TestCreateSecretManagerProductionGuardrail: Security guardrails
    - TestCreateSecretManagerErrorHandling: Invalid configurations
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from libs.secrets import create_secret_manager
from libs.secrets.aws_backend import AWSSecretsManager
from libs.secrets.env_backend import EnvSecretManager
from libs.secrets.exceptions import SecretManagerError
from libs.secrets.vault_backend import VaultSecretManager


class TestCreateSecretManagerBackendSelection:
    """Test backend selection via SECRET_BACKEND environment variable."""

    @pytest.mark.unit()
    @patch("libs.secrets.env_backend.EnvSecretManager")
    def test_default_backend_env(self, mock_env_backend: object) -> None:
        """
        Default backend is EnvSecretManager when SECRET_BACKEND not set.

        Verifies that create_secret_manager() defaults to "env" backend for
        local development when SECRET_BACKEND environment variable is not set.
        """
        with patch.dict(os.environ, {}, clear=True):
            create_secret_manager()
            # Verify EnvSecretManager() was called
            assert mock_env_backend.called

    @pytest.mark.unit()
    @patch("libs.secrets.env_backend.EnvSecretManager")
    def test_env_backend_explicit(self, mock_env_backend: object) -> None:
        """
        Select EnvSecretManager when SECRET_BACKEND='env'.

        Verifies that create_secret_manager() returns EnvSecretManager instance
        when SECRET_BACKEND is explicitly set to "env".
        """
        with patch.dict(os.environ, {"SECRET_BACKEND": "env", "DEPLOYMENT_ENV": "local"}):
            create_secret_manager()
            assert mock_env_backend.called

    @pytest.mark.unit()
    @patch("libs.secrets.vault_backend.VaultSecretManager")
    def test_vault_backend_selection(self, mock_vault_backend: object) -> None:
        """
        Select VaultSecretManager when SECRET_BACKEND='vault'.

        Verifies that create_secret_manager() returns VaultSecretManager instance
        when SECRET_BACKEND is set to "vault".
        """
        with patch.dict(
            os.environ, {"SECRET_BACKEND": "vault", "VAULT_ADDR": "http://localhost:8200"}
        ):
            create_secret_manager()
            assert mock_vault_backend.called

    @pytest.mark.unit()
    @patch("libs.secrets.aws_backend.AWSSecretsManager")
    def test_aws_backend_selection(self, mock_aws_backend: object) -> None:
        """
        Select AWSSecretsManager when SECRET_BACKEND='aws'.

        Verifies that create_secret_manager() returns AWSSecretsManager instance
        when SECRET_BACKEND is set to "aws" and honors AWS_REGION environment variable.
        """
        # Test with AWS_REGION set
        with patch.dict(os.environ, {"SECRET_BACKEND": "aws", "AWS_REGION": "us-west-2"}):
            create_secret_manager()
            assert mock_aws_backend.called
            # Verify AWS_REGION was passed to constructor
            mock_aws_backend.assert_called_with(region_name="us-west-2")

        # Test with AWS_DEFAULT_REGION as fallback
        mock_aws_backend.reset_mock()
        with patch.dict(
            os.environ, {"SECRET_BACKEND": "aws", "AWS_DEFAULT_REGION": "eu-west-1"}, clear=True
        ):
            create_secret_manager()
            mock_aws_backend.assert_called_with(region_name="eu-west-1")

        # Test default region when neither is set
        mock_aws_backend.reset_mock()
        with patch.dict(os.environ, {"SECRET_BACKEND": "aws"}, clear=True):
            create_secret_manager()
            mock_aws_backend.assert_called_with(region_name="us-east-1")

    @pytest.mark.unit()
    @patch("libs.secrets.vault_backend.VaultSecretManager")
    def test_backend_override_parameter(self, mock_vault_backend: object) -> None:
        """
        Override backend selection via function parameter.

        Verifies that the backend parameter overrides SECRET_BACKEND environment
        variable (useful for testing and explicit backend selection).
        """
        with patch.dict(
            os.environ, {"SECRET_BACKEND": "env", "VAULT_ADDR": "http://localhost:8200"}
        ):
            # Override env var with backend parameter
            create_secret_manager(backend="vault")
            assert mock_vault_backend.called

    @pytest.mark.unit()
    @patch("libs.secrets.env_backend.EnvSecretManager")
    def test_case_insensitive_backend_name(self, mock_env_backend: object) -> None:
        """
        Backend names are case-insensitive.

        Verifies that SECRET_BACKEND values like "ENV", "Env", "env" all select
        the same backend (case-insensitive comparison).
        """
        test_cases = ["ENV", "Env", "env", " env ", "  ENV  "]

        for backend_name in test_cases:
            with patch.dict(os.environ, {"SECRET_BACKEND": backend_name}):
                create_secret_manager()
                assert mock_env_backend.called, f"Failed for backend: {backend_name}"

    @pytest.mark.unit()
    @patch("libs.secrets.env_backend.EnvSecretManager")
    def test_env_backend_loads_default_dotenv_when_present(
        self, mock_env_backend: object, tmp_path: Path, monkeypatch
    ) -> None:
        """
        EnvSecretManager should automatically load .env from the working directory when present.
        """
        monkeypatch.chdir(tmp_path)
        dotenv_file = tmp_path / ".env"
        dotenv_file.write_text("TEST_SECRET=1\n", encoding="utf-8")

        with patch.dict(os.environ, {"SECRET_BACKEND": "env", "DEPLOYMENT_ENV": "local"}):
            create_secret_manager()

        assert mock_env_backend.called
        kwargs = mock_env_backend.call_args.kwargs
        assert kwargs.get("dotenv_path") == dotenv_file

    @pytest.mark.unit()
    @patch("libs.secrets.env_backend.EnvSecretManager")
    def test_env_backend_uses_secret_dotenv_path_override(
        self, mock_env_backend: object, tmp_path: Path
    ) -> None:
        """
        SECRET_DOTENV_PATH override should be respected when the file exists.
        """
        dotenv_file = tmp_path / "custom.env"
        dotenv_file.write_text("TEST_SECRET=1\n", encoding="utf-8")

        with patch.dict(
            os.environ,
            {
                "SECRET_BACKEND": "env",
                "DEPLOYMENT_ENV": "local",
                "SECRET_DOTENV_PATH": str(dotenv_file),
            },
        ):
            create_secret_manager()

        assert mock_env_backend.called
        kwargs = mock_env_backend.call_args.kwargs
        assert kwargs.get("dotenv_path") == dotenv_file.resolve()


class TestCreateSecretManagerProductionGuardrail:
    """Test production guardrail preventing EnvSecretManager in staging/production."""

    @pytest.mark.unit()
    @patch("libs.secrets.env_backend.EnvSecretManager")
    def test_env_backend_allowed_in_local(self, mock_env_backend: object) -> None:
        """
        EnvSecretManager allowed when DEPLOYMENT_ENV='local'.

        Verifies that EnvSecretManager is allowed in local development environment
        (production guardrail only applies to staging/production).
        """
        with patch.dict(os.environ, {"SECRET_BACKEND": "env", "DEPLOYMENT_ENV": "local"}):
            create_secret_manager()
            assert mock_env_backend.called

    @pytest.mark.unit()
    @patch("libs.secrets.env_backend.EnvSecretManager")
    def test_env_backend_allowed_in_test(self, mock_env_backend: object) -> None:
        """
        EnvSecretManager allowed when DEPLOYMENT_ENV='test'.

        CI/E2E environments use DEPLOYMENT_ENV='test' and should permit the env backend.
        """
        with patch.dict(os.environ, {"SECRET_BACKEND": "env", "DEPLOYMENT_ENV": "test"}):
            create_secret_manager()
            assert mock_env_backend.called

    @pytest.mark.unit()
    def test_env_backend_blocked_in_staging(self) -> None:
        """
        EnvSecretManager blocked when DEPLOYMENT_ENV='staging'.

        Verifies that production guardrail raises SecretManagerError when attempting
        to use EnvSecretManager in staging environment (security violation).
        """
        with patch.dict(os.environ, {"SECRET_BACKEND": "env", "DEPLOYMENT_ENV": "staging"}):
            with pytest.raises(SecretManagerError) as exc_info:
                create_secret_manager()

            error_msg = str(exc_info.value)
            assert "EnvSecretManager not allowed in staging environment" in error_msg
            assert "SECURITY VIOLATION" in error_msg
            assert "LOCAL DEVELOPMENT ONLY" in error_msg

    @pytest.mark.unit()
    def test_env_backend_blocked_in_production(self) -> None:
        """
        EnvSecretManager blocked when DEPLOYMENT_ENV='production'.

        Verifies that production guardrail raises SecretManagerError when attempting
        to use EnvSecretManager in production environment (security violation).
        """
        with patch.dict(os.environ, {"SECRET_BACKEND": "env", "DEPLOYMENT_ENV": "production"}):
            with pytest.raises(SecretManagerError) as exc_info:
                create_secret_manager()

            error_msg = str(exc_info.value)
            assert "EnvSecretManager not allowed in production environment" in error_msg
            assert "SECURITY VIOLATION" in error_msg

    @pytest.mark.unit()
    @patch("libs.secrets.env_backend.EnvSecretManager")
    def test_env_backend_allowed_with_override_flag(self, mock_env_backend: object) -> None:
        """
        SECRET_ALLOW_ENV_IN_NON_LOCAL overrides the guardrail (emergency-only).
        """
        with patch.dict(
            os.environ,
            {
                "SECRET_BACKEND": "env",
                "DEPLOYMENT_ENV": "staging",
                "SECRET_ALLOW_ENV_IN_NON_LOCAL": "1",
            },
        ):
            create_secret_manager()

        assert mock_env_backend.called

    @pytest.mark.unit()
    def test_deployment_env_override_parameter(self) -> None:
        """
        Override deployment_env via function parameter.

        Verifies that deployment_env parameter overrides DEPLOYMENT_ENV environment
        variable (useful for testing guardrail behavior).
        """
        with patch.dict(os.environ, {"SECRET_BACKEND": "env", "DEPLOYMENT_ENV": "local"}):
            # Override env var with deployment_env parameter (should block)
            with pytest.raises(SecretManagerError) as exc_info:
                create_secret_manager(deployment_env="production")

            assert "EnvSecretManager not allowed in production" in str(exc_info.value)

    @pytest.mark.unit()
    @patch("libs.secrets.vault_backend.VaultSecretManager")
    def test_vault_allowed_in_production(self, mock_vault_backend: object) -> None:
        """
        VaultSecretManager allowed in production.

        Verifies that VaultSecretManager can be used in production environment
        (only EnvSecretManager is restricted).
        """
        with patch.dict(
            os.environ,
            {
                "SECRET_BACKEND": "vault",
                "DEPLOYMENT_ENV": "production",
                "VAULT_ADDR": "http://localhost:8200",
            },
        ):
            create_secret_manager()
            assert mock_vault_backend.called

    @pytest.mark.unit()
    @patch("libs.secrets.aws_backend.AWSSecretsManager")
    def test_aws_allowed_in_production(self, mock_aws_backend: object) -> None:
        """
        AWSSecretsManager allowed in production.

        Verifies that AWSSecretsManager can be used in production environment
        (only EnvSecretManager is restricted).
        """
        with patch.dict(os.environ, {"SECRET_BACKEND": "aws", "DEPLOYMENT_ENV": "production"}):
            create_secret_manager()
            assert mock_aws_backend.called


class TestCreateSecretManagerErrorHandling:
    """Test error handling for invalid backend names and configurations."""

    @pytest.mark.unit()
    def test_invalid_backend_name_raises_error(self) -> None:
        """
        Invalid backend name raises SecretManagerError.

        Verifies that factory raises helpful error message when SECRET_BACKEND
        contains an invalid value (not "vault", "aws", or "env").
        """
        with patch.dict(os.environ, {"SECRET_BACKEND": "invalid_backend"}):
            with pytest.raises(SecretManagerError) as exc_info:
                create_secret_manager()

            error_msg = str(exc_info.value)
            assert "Invalid SECRET_BACKEND: 'invalid_backend'" in error_msg
            assert "Valid options: 'vault', 'aws', 'env'" in error_msg

    @pytest.mark.unit()
    def test_secret_dotenv_path_missing_raises_error(self) -> None:
        """
        SECRET_DOTENV_PATH must point to an existing file.
        """
        with patch.dict(
            os.environ,
            {
                "SECRET_BACKEND": "env",
                "DEPLOYMENT_ENV": "local",
                "SECRET_DOTENV_PATH": "/tmp/does-not-exist.env",
            },
        ):
            with pytest.raises(SecretManagerError) as exc_info:
                create_secret_manager()

        assert "SECRET_DOTENV_PATH" in str(exc_info.value)

    @pytest.mark.unit()
    def test_empty_backend_name_defaults_to_env(self) -> None:
        """
        Empty backend name defaults to EnvSecretManager.

        Verifies that empty string or whitespace-only SECRET_BACKEND values
        default to "env" backend (local development).
        """
        with patch.dict(os.environ, {"SECRET_BACKEND": "", "DEPLOYMENT_ENV": "local"}):
            secret_mgr = create_secret_manager()
            assert isinstance(secret_mgr, EnvSecretManager)

    @pytest.mark.unit()
    def test_error_message_includes_adr_reference(self) -> None:
        """
        Error messages reference ADR-0017 for guidance.

        Verifies that error messages include reference to ADR-0017 documentation
        for migration guidance and troubleshooting.
        """
        with patch.dict(os.environ, {"SECRET_BACKEND": "invalid"}):
            with pytest.raises(SecretManagerError) as exc_info:
                create_secret_manager()

            assert "docs/ADRs/0017-secrets-management.md" in str(exc_info.value)


class TestCreateSecretManagerIntegration:
    """Integration tests with real backend instances (no mocking)."""

    @pytest.mark.unit()
    def test_returns_env_backend_instance(self) -> None:
        """
        Factory returns actual EnvSecretManager instance.

        Integration test verifying factory returns real EnvSecretManager instance
        (not mocked) with correct type.
        """
        with patch.dict(os.environ, {"SECRET_BACKEND": "env", "DEPLOYMENT_ENV": "local"}):
            secret_mgr = create_secret_manager()
            assert isinstance(secret_mgr, EnvSecretManager)

    @pytest.mark.unit()
    @patch("libs.secrets.vault_backend.hvac.Client")
    def test_returns_vault_backend_instance(self, mock_hvac_client: object) -> None:
        """
        Factory returns actual VaultSecretManager instance.

        Integration test verifying factory returns real VaultSecretManager instance
        (mocking only hvac.Client to avoid external Vault dependency).
        """
        # Configure mock to simulate authenticated, unsealed Vault
        mock_hvac_client.return_value.is_authenticated.return_value = True
        mock_hvac_client.return_value.sys.is_sealed.return_value = False  # Use proper hvac v2 API

        # Mock hvac.Client to avoid actual Vault connection
        with patch.dict(
            os.environ,
            {
                "SECRET_BACKEND": "vault",
                "VAULT_ADDR": "http://localhost:8200",
                "VAULT_TOKEN": "test-token",
            },
        ):
            secret_mgr = create_secret_manager()
            assert isinstance(secret_mgr, VaultSecretManager)

    @pytest.mark.unit()
    @patch("libs.secrets.aws_backend.boto3.client")
    def test_returns_aws_backend_instance(self, mock_boto_client: object) -> None:
        """
        Factory returns actual AWSSecretsManager instance.

        Integration test verifying factory returns real AWSSecretsManager instance
        (mocking only boto3.client to avoid AWS API calls).
        """
        # Mock boto3.client to avoid actual AWS API calls
        with patch.dict(os.environ, {"SECRET_BACKEND": "aws"}):
            secret_mgr = create_secret_manager()
            assert isinstance(secret_mgr, AWSSecretsManager)
