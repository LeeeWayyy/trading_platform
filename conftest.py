"""
Root conftest for all tests.

This ensures the redis module is properly initialized before test collection
to prevent import conflicts with redis.asyncio aliasing.

IMPORTANT: This file must exist at the project root to be loaded first.
"""

import sys

# Import redis IMMEDIATELY at conftest load time, before any test files are collected.
# This ensures the real redis module is in sys.modules before test_main_full_cover.py
# (or any other file) can replace it with a stub.
import redis
import redis.asyncio
import redis.connection
import redis.exceptions

# Store strong references to prevent garbage collection and module replacement
_ORIGINAL_REDIS = redis
_ORIGINAL_REDIS_ASYNCIO = redis.asyncio
_ORIGINAL_REDIS_EXCEPTIONS = redis.exceptions
_ORIGINAL_REDIS_CONNECTION = redis.connection


def pytest_configure(config):
    """Hook that runs before test collection starts.

    Ensure the real redis modules are in sys.modules.
    """
    # Force the real modules back into sys.modules
    sys.modules["redis"] = _ORIGINAL_REDIS
    sys.modules["redis.asyncio"] = _ORIGINAL_REDIS_ASYNCIO
    sys.modules["redis.exceptions"] = _ORIGINAL_REDIS_EXCEPTIONS
    sys.modules["redis.connection"] = _ORIGINAL_REDIS_CONNECTION


def pytest_collection_modifyitems(session, config, items):
    """Hook that runs after all test items are collected.

    Restore the real redis modules after collection in case any test file
    replaced them with stubs during import.
    """
    sys.modules["redis"] = _ORIGINAL_REDIS
    sys.modules["redis.asyncio"] = _ORIGINAL_REDIS_ASYNCIO
    sys.modules["redis.exceptions"] = _ORIGINAL_REDIS_EXCEPTIONS
    sys.modules["redis.connection"] = _ORIGINAL_REDIS_CONNECTION
