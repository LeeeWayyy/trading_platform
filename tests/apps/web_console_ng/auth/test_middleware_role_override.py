"""Unit tests for AuthMiddleware (P6T19: role override removed).

P6T19: _override_role_from_db and _apply_role_override have been removed
from AuthMiddleware (single-admin model hardcodes role="admin" in dispatch).

These tests verify:
- The methods no longer exist on the middleware class
- The middleware can still be instantiated
"""

from __future__ import annotations

from unittest.mock import MagicMock

from apps.web_console_ng.auth.middleware import AuthMiddleware


def test_middleware_instantiation() -> None:
    """AuthMiddleware can still be instantiated after P6T19 changes."""
    middleware = AuthMiddleware(app=MagicMock())
    assert middleware is not None


def test_override_role_from_db_removed() -> None:
    """P6T19: _override_role_from_db method no longer exists."""
    middleware = AuthMiddleware(app=MagicMock())
    assert not hasattr(middleware, "_override_role_from_db")


def test_apply_role_override_removed() -> None:
    """P6T19: _apply_role_override static method no longer exists."""
    assert not hasattr(AuthMiddleware, "_apply_role_override")
