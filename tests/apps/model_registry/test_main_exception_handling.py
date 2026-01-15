"""Tests for model_registry exception handling.

Tests specific exception handling patterns in model_registry startup
and manifest integrity checks.
"""

import os
import pickle
from unittest.mock import Mock, patch

import pytest

# Set required environment variables before importing main
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:8501")

from apps.model_registry.main import lifespan
from libs.models.models import ManifestIntegrityError


@pytest.mark.asyncio()
async def test_lifespan_handles_manifest_file_not_found():
    """Test lifespan handles FileNotFoundError during manifest integrity check."""
    from fastapi import FastAPI

    app = FastAPI()

    with (
        patch("apps.model_registry.main.ModelRegistry") as mock_registry,
        patch("apps.model_registry.main.RegistryManifestManager") as mock_manager,
    ):
        # Setup mocks
        mock_registry_instance = Mock()
        mock_registry.return_value = mock_registry_instance

        mock_manager_instance = Mock()
        mock_manager_instance.exists.return_value = True
        mock_manager_instance.verify_integrity.side_effect = FileNotFoundError(
            "Manifest file not found"
        )
        mock_manager.return_value = mock_manager_instance

        # Expect ManifestIntegrityError to be raised
        with pytest.raises(ManifestIntegrityError) as exc_info:
            async with lifespan(app):
                pass

        assert "Integrity check failed" in str(exc_info.value)
        assert "Manifest file not found" in str(exc_info.value)


@pytest.mark.asyncio()
async def test_lifespan_handles_manifest_os_error():
    """Test lifespan handles OSError during manifest integrity check."""
    from fastapi import FastAPI

    app = FastAPI()

    with (
        patch("apps.model_registry.main.ModelRegistry") as mock_registry,
        patch("apps.model_registry.main.RegistryManifestManager") as mock_manager,
    ):
        # Setup mocks
        mock_registry_instance = Mock()
        mock_registry.return_value = mock_registry_instance

        mock_manager_instance = Mock()
        mock_manager_instance.exists.return_value = True
        mock_manager_instance.verify_integrity.side_effect = OSError("Permission denied")
        mock_manager.return_value = mock_manager_instance

        # Expect ManifestIntegrityError to be raised
        with pytest.raises(ManifestIntegrityError) as exc_info:
            async with lifespan(app):
                pass

        assert "Integrity check failed" in str(exc_info.value)
        assert "Permission denied" in str(exc_info.value)


@pytest.mark.asyncio()
async def test_lifespan_handles_manifest_value_error():
    """Test lifespan handles ValueError during manifest integrity check."""
    from fastapi import FastAPI

    app = FastAPI()

    with (
        patch("apps.model_registry.main.ModelRegistry") as mock_registry,
        patch("apps.model_registry.main.RegistryManifestManager") as mock_manager,
    ):
        # Setup mocks
        mock_registry_instance = Mock()
        mock_registry.return_value = mock_registry_instance

        mock_manager_instance = Mock()
        mock_manager_instance.exists.return_value = True
        mock_manager_instance.verify_integrity.side_effect = ValueError("Invalid checksum format")
        mock_manager.return_value = mock_manager_instance

        # Expect ManifestIntegrityError to be raised
        with pytest.raises(ManifestIntegrityError) as exc_info:
            async with lifespan(app):
                pass

        assert "Integrity check failed" in str(exc_info.value)
        assert "Invalid checksum format" in str(exc_info.value)


@pytest.mark.asyncio()
async def test_lifespan_handles_manifest_pickle_error():
    """Test lifespan handles pickle.PickleError during manifest integrity check."""
    from fastapi import FastAPI

    app = FastAPI()

    with (
        patch("apps.model_registry.main.ModelRegistry") as mock_registry,
        patch("apps.model_registry.main.RegistryManifestManager") as mock_manager,
    ):
        # Setup mocks
        mock_registry_instance = Mock()
        mock_registry.return_value = mock_registry_instance

        mock_manager_instance = Mock()
        mock_manager_instance.exists.return_value = True
        mock_manager_instance.verify_integrity.side_effect = pickle.PickleError("Corrupted pickle data")
        mock_manager.return_value = mock_manager_instance

        # Expect ManifestIntegrityError to be raised
        with pytest.raises(ManifestIntegrityError) as exc_info:
            async with lifespan(app):
                pass

        assert "Integrity check failed" in str(exc_info.value)
        assert "Corrupted pickle data" in str(exc_info.value)


@pytest.mark.asyncio()
async def test_lifespan_handles_runtime_error():
    """Test lifespan handles RuntimeError (e.g., auth config errors)."""
    from fastapi import FastAPI

    app = FastAPI()

    with patch.dict("os.environ", {"MODEL_REGISTRY_AUTH_DISABLED": "true"}):
        # Expect RuntimeError to be raised for auth bypass attempt
        with pytest.raises(RuntimeError) as exc_info:
            async with lifespan(app):
                pass

        assert "MODEL_REGISTRY_AUTH_DISABLED is unsupported" in str(exc_info.value)


@pytest.mark.asyncio()
async def test_lifespan_handles_file_not_found_error_on_startup():
    """Test lifespan handles FileNotFoundError during registry initialization."""
    from fastapi import FastAPI

    app = FastAPI()

    with patch("apps.model_registry.main.ModelRegistry") as mock_registry:
        mock_registry.side_effect = FileNotFoundError("Registry directory not found")

        # Expect FileNotFoundError to be raised
        with pytest.raises(FileNotFoundError) as exc_info:
            async with lifespan(app):
                pass

        assert "Registry directory not found" in str(exc_info.value)


@pytest.mark.asyncio()
async def test_lifespan_handles_os_error_on_startup():
    """Test lifespan handles OSError during registry initialization."""
    from fastapi import FastAPI

    app = FastAPI()

    with patch("apps.model_registry.main.ModelRegistry") as mock_registry:
        mock_registry.side_effect = OSError("Disk full")

        # Expect OSError to be raised
        with pytest.raises(OSError, match="Disk full"):
            async with lifespan(app):
                pass


@pytest.mark.asyncio()
async def test_lifespan_handles_value_error_on_get_manifest():
    """Test lifespan handles ValueError during get_manifest call."""
    from fastapi import FastAPI

    app = FastAPI()

    with (
        patch("apps.model_registry.main.ModelRegistry") as mock_registry,
        patch("apps.model_registry.main.RegistryManifestManager") as mock_manager,
    ):
        # Setup mocks
        mock_registry_instance = Mock()
        mock_registry_instance.get_manifest.side_effect = ValueError("Invalid manifest data")
        mock_registry.return_value = mock_registry_instance

        mock_manager_instance = Mock()
        mock_manager_instance.exists.return_value = True
        mock_manager_instance.verify_integrity.return_value = True
        mock_manager.return_value = mock_manager_instance

        # Expect ValueError to be raised
        with pytest.raises(ValueError, match="Invalid manifest"):
            async with lifespan(app):
                pass


@pytest.mark.asyncio()
async def test_lifespan_handles_pickle_error_on_get_manifest():
    """Test lifespan handles pickle.PickleError during get_manifest call."""
    from fastapi import FastAPI

    app = FastAPI()

    with (
        patch("apps.model_registry.main.ModelRegistry") as mock_registry,
        patch("apps.model_registry.main.RegistryManifestManager") as mock_manager,
    ):
        # Setup mocks
        mock_registry_instance = Mock()
        mock_registry_instance.get_manifest.side_effect = pickle.PickleError("Cannot unpickle model")
        mock_registry.return_value = mock_registry_instance

        mock_manager_instance = Mock()
        mock_manager_instance.exists.return_value = True
        mock_manager_instance.verify_integrity.return_value = True
        mock_manager.return_value = mock_manager_instance

        # Expect pickle.PickleError to be raised
        with pytest.raises(pickle.PickleError) as exc_info:
            async with lifespan(app):
                pass

        assert "Cannot unpickle model" in str(exc_info.value)
