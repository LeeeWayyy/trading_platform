"""Unit tests for libs.platform.web_console_auth.helpers."""

from __future__ import annotations

from dataclasses import dataclass

from libs.platform.web_console_auth.helpers import get_user_id


@dataclass
class _User:
    user_id: str


def test_get_user_id_from_object_attribute() -> None:
    user = _User(user_id="abc123")
    assert get_user_id(user) == "abc123"


def test_get_user_id_from_dict() -> None:
    assert get_user_id({"user_id": 42}) == "42"


def test_get_user_id_unknown() -> None:
    assert get_user_id({"missing": "field"}) == "unknown"
    assert get_user_id(object()) == "unknown"
