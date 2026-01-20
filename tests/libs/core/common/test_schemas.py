from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from pydantic import BaseModel

from libs.core.common.schemas import TimestampSerializerMixin


class ExampleResponse(TimestampSerializerMixin, BaseModel):
    timestamp: datetime


def test_timestamp_offer_z_suffix_for_utc() -> None:
    model = ExampleResponse(timestamp=datetime(2024, 1, 1, 0, 0, tzinfo=UTC))

    assert model.model_dump(mode="json")["timestamp"] == "2024-01-01T00:00:00Z"


def test_timestamp_serializer_preserves_non_utc_offset() -> None:
    tzinfo = timezone(timedelta(hours=5))
    model = ExampleResponse(timestamp=datetime(2024, 1, 1, 12, 0, tzinfo=tzinfo))

    assert model.model_dump(mode="json")["timestamp"] == "2024-01-01T12:00:00+05:00"


def test_timestamp_serializer_preserves_naive_datetime() -> None:
    model = ExampleResponse(timestamp=datetime(2024, 1, 1, 12, 0))

    assert model.model_dump(mode="json")["timestamp"] == "2024-01-01T12:00:00"
