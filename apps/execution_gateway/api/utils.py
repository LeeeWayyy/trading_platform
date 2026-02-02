"""Shared utility functions for execution gateway API routes.

This module contains helper functions that are used across multiple
route handlers to avoid code duplication.
"""

from __future__ import annotations

from fastapi import Request


def get_client_ip(request: Request) -> str | None:
    """Extract client IP from request, respecting proxy headers.

    Checks X-Forwarded-For first (set by reverse proxy), then
    falls back to direct client connection.

    Args:
        request: FastAPI Request object

    Returns:
        Client IP address or None if unavailable
    """
    # Check X-Forwarded-For first (set by reverse proxy)
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # Take the first IP in the chain (original client)
        return forwarded_for.split(",")[0].strip()
    # Fall back to direct client
    if request.client:
        return request.client.host
    return None


def get_user_agent(request: Request) -> str | None:
    """Extract User-Agent from request headers.

    Args:
        request: FastAPI Request object

    Returns:
        User-Agent string or None if not present
    """
    return request.headers.get("User-Agent")


__all__ = ["get_client_ip", "get_user_agent"]
