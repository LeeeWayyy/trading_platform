"""
Pytest fixtures for Market Data Service tests.

This module provides test configuration to handle environment variables
required by the Market Data Service settings.
"""

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _set_test_environment():
    """
    Set environment variables for tests.

    This fixture automatically sets dummy values for required environment
    variables before any tests run. The scope="session" ensures it runs
    once per test session, and autouse=True means it applies to all tests.

    Required for Market Data Service which needs Alpaca credentials in settings.
    """
    # Set dummy Alpaca credentials for testing
    os.environ.setdefault("ALPACA_API_KEY", "test_api_key_123")
    os.environ.setdefault("ALPACA_SECRET_KEY", "test_secret_key_456")

    # Set other required environment variables
    os.environ.setdefault("REDIS_HOST", "localhost")
    os.environ.setdefault("REDIS_PORT", "6379")
    os.environ.setdefault("EXECUTION_GATEWAY_URL", "http://localhost:8002")

    return

    # Cleanup handled automatically by pytest
