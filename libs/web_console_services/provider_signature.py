"""Sanitized provider provenance signatures for UI/API payloads."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from pydantic import AwareDatetime, BaseModel

_MAX_SIGNATURE_VALUE_LENGTH = 2048
_MAX_SIGNATURE_COLLECTION_ITEMS = 32
_SENSITIVE_VALUE_MARKERS = (
    "authorization=",
    "api_key=",
    "api_secret=",
    "apikey=",
    "apisecret=",
    "bearer ",
    "password=",
    "pwd=",
    "secret=",
    "signature=",
    "submit_token=",
    "token=",
    "x-amz-credential=",
    "x-amz-signature=",
)


class ProviderSignatureDTO(BaseModel):
    """Replay-safe provider signature fields exposed to UI consumers."""

    provider_id: str | None = None
    provider_version: str | None = None
    source_feed: str | None = None
    adjustment_mode: str | None = None
    canonical_storage_mode: str | None = None
    read_time_adjustment_mode: str | None = None
    symbol_set_hash: str | None = None
    query_params_hash: str | None = None
    manifest_id: str | None = None
    manifest_reference: str | None = None
    manifest_checksum: str | None = None
    manifest_version: str | None = None
    schema_version: str | None = None
    sync_started_at: AwareDatetime | None = None
    sync_finished_at: AwareDatetime | None = None
    data_roles: dict[str, str] | None = None
    dataset_keys: list[str] | None = None


_ALLOWED_SIGNATURE_KEYS = frozenset(ProviderSignatureDTO.model_fields)
_STRING_FIELDS = {
    "provider_id",
    "provider_version",
    "source_feed",
    "adjustment_mode",
    "canonical_storage_mode",
    "read_time_adjustment_mode",
    "symbol_set_hash",
    "query_params_hash",
    "manifest_id",
    "manifest_reference",
    "manifest_checksum",
    "manifest_version",
    "schema_version",
}


def sanitize_provider_signature(raw: Mapping[str, Any]) -> ProviderSignatureDTO:
    """Return an allowlisted provider signature DTO.

    Unknown keys are dropped, so credentials, auth headers, signed URLs, tokens,
    and raw request payloads cannot leak through this helper.
    """
    sanitized: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in _ALLOWED_SIGNATURE_KEYS or value is None:
            continue
        if key in _STRING_FIELDS:
            text = str(value)
            if _is_safe_string_value(text):
                sanitized[key] = text
            continue
        if key in {"sync_started_at", "sync_finished_at"}:
            if isinstance(value, datetime):
                sanitized[key] = value
            continue
        if key == "data_roles":
            roles = _sanitize_string_mapping(value)
            if roles:
                sanitized[key] = roles
            continue
        if key == "dataset_keys":
            dataset_keys = _sanitize_string_sequence(value)
            if dataset_keys:
                sanitized[key] = dataset_keys

    return ProviderSignatureDTO.model_validate(sanitized)


def _sanitize_string_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        if len(result) >= _MAX_SIGNATURE_COLLECTION_ITEMS:
            break
        key = str(raw_key)
        mapped = str(raw_value)
        if (
            key
            and mapped
            and _is_safe_string_value(key)
            and _is_safe_string_value(mapped)
        ):
            result[key] = mapped
    return result


def _sanitize_string_sequence(value: Any) -> list[str]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        return []
    result: list[str] = []
    for item in value:
        if len(result) >= _MAX_SIGNATURE_COLLECTION_ITEMS:
            break
        text = str(item)
        if text and _is_safe_string_value(text):
            result.append(text)
    return result


def _is_safe_string_value(value: str) -> bool:
    if len(value) > _MAX_SIGNATURE_VALUE_LENGTH:
        return False
    lowered = value.lower()
    return not (
        any(marker in lowered for marker in _SENSITIVE_VALUE_MARKERS)
        or _looks_like_jwt(value)
    )


def _looks_like_jwt(value: str) -> bool:
    parts = value.split(".")
    return len(parts) == 3 and parts[0].startswith("eyJ")


__all__ = ["ProviderSignatureDTO", "sanitize_provider_signature"]
