"""Bulk sync manager for Alpaca corporate-action announcements.

The installed ``alpaca-py==0.15.0`` package does not expose the corporate
actions client available in newer SDKs, so this module uses Alpaca's market
data REST endpoint directly while preserving the local parquet + manifest
pattern used by ``AlpacaSIPSyncManager``.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

import httpx
import polars as pl

from libs.data.data_providers.registry import (
    ProviderType,
    compute_symbol_set_hash,
    get_provider_spec,
)
from libs.data.data_quality.exceptions import DiskSpaceError
from libs.data.data_quality.manifest import ManifestManager, SyncManifest

logger = logging.getLogger(__name__)

ALPACA_CORP_ACTIONS_COLUMNS: tuple[str, ...] = (
    "id",
    "symbol",
    "cusip",
    "ca_type",
    "process_date",
    "ex_date",
    "record_date",
    "payable_date",
    "cash",
    "old_rate",
    "new_rate",
    "old_symbol",
    "new_symbol",
    "raw",
)
ALPACA_CORP_ACTIONS_SCHEMA: dict[str, type[pl.DataType]] = {
    "id": pl.Utf8,
    "symbol": pl.Utf8,
    "cusip": pl.Utf8,
    "ca_type": pl.Utf8,
    "process_date": pl.Date,
    "ex_date": pl.Date,
    "record_date": pl.Date,
    "payable_date": pl.Date,
    "cash": pl.Float64,
    "old_rate": pl.Float64,
    "new_rate": pl.Float64,
    "old_symbol": pl.Utf8,
    "new_symbol": pl.Utf8,
    "raw": pl.Utf8,
}
ACTION_CONTAINER_KEYS = (
    "items",
    "data",
    "corporate_actions",
    "announcements",
    "results",
)
ACTION_TYPE_KEYS = frozenset(
    {
        "cash_dividend",
        "cash_dividends",
        "stock_dividend",
        "stock_dividends",
        "stock_split",
        "stock_splits",
        "reverse_split",
        "reverse_splits",
        "merger",
        "mergers",
        "spinoff",
        "spinoffs",
        "name_change",
        "name_changes",
        "rights_distribution",
        "rights_distributions",
        "redemption",
        "redemptions",
    }
)


@dataclass(frozen=True)
class CorporateActionRoundTripCheck:
    """Known corporate-action event used for live round-trip validation."""

    label: str
    symbol: str
    start_date: datetime.date
    end_date: datetime.date
    expected_type_tokens: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Serialize check configuration to stable JSON-compatible values."""
        return {
            "label": self.label,
            "symbol": self.symbol,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "expected_type_tokens": list(self.expected_type_tokens),
        }


@dataclass(frozen=True)
class CorporateActionRoundTripResult:
    """Result for one corporate-action round-trip check."""

    check: CorporateActionRoundTripCheck
    raw_action_count: int
    matched_action_count: int
    matched_ids: tuple[str, ...]
    matched_types: tuple[str, ...]
    status: str

    def to_dict(self) -> dict[str, object]:
        """Serialize result to stable JSON-compatible values."""
        return {
            "check": self.check.to_dict(),
            "raw_action_count": self.raw_action_count,
            "matched_action_count": self.matched_action_count,
            "matched_ids": list(self.matched_ids),
            "matched_types": list(self.matched_types),
            "status": self.status,
        }


@dataclass(frozen=True)
class CorporateActionRoundTripReport:
    """Report for known corporate-action API round-trip validation."""

    status: str
    results: tuple[CorporateActionRoundTripResult, ...]

    @property
    def content_hash(self) -> str:
        """Return a deterministic SHA-256 hash of the report payload."""
        payload = self.to_dict(include_hash=False)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def to_dict(self, *, include_hash: bool = True) -> dict[str, object]:
        """Serialize report to stable JSON-compatible values."""
        payload: dict[str, object] = {
            "report_type": "alpaca_corporate_actions_round_trip",
            "status": self.status,
            "results": [result.to_dict() for result in self.results],
        }
        if include_hash:
            payload["content_hash"] = self.content_hash
        return payload


DEFAULT_ROUND_TRIP_CHECKS: tuple[CorporateActionRoundTripCheck, ...] = (
    CorporateActionRoundTripCheck(
        label="AAPL 4-for-1 split",
        symbol="AAPL",
        start_date=datetime.date(2020, 8, 1),
        end_date=datetime.date(2020, 9, 15),
        expected_type_tokens=("split",),
    ),
    CorporateActionRoundTripCheck(
        label="NVDA 10-for-1 split",
        symbol="NVDA",
        start_date=datetime.date(2024, 6, 1),
        end_date=datetime.date(2024, 6, 20),
        expected_type_tokens=("split",),
    ),
    CorporateActionRoundTripCheck(
        label="AAPL cash dividend",
        symbol="AAPL",
        start_date=datetime.date(2024, 2, 1),
        end_date=datetime.date(2024, 2, 20),
        expected_type_tokens=("dividend", "cash"),
    ),
)


class AlpacaCorporateActionsClient(Protocol):
    """Minimal client interface needed by ``AlpacaCorporateActionsSyncManager``."""

    def get_corporate_actions(self, params: Mapping[str, str | int]) -> Mapping[str, Any]:
        """Return one page of corporate-action announcements."""


class AlpacaCorporateActionsRestClient:
    """Direct REST client for Alpaca corporate-action announcements."""

    DEFAULT_BASE_URL = "https://data.alpaca.markets"
    ENDPOINT_PATH = "/v1/corporate-actions"

    def __init__(
        self,
        *,
        api_key: str,
        secret_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": secret_key,
        }
        self._client = httpx.Client(
            base_url=self.base_url,
            headers=self.headers,
            timeout=self.timeout_seconds,
        )

    def get_corporate_actions(self, params: Mapping[str, str | int]) -> Mapping[str, Any]:
        """Fetch one REST page and return decoded JSON."""
        response = self._client.get(self.ENDPOINT_PATH, params=dict(params))
        response.raise_for_status()
        payload = response.json()

        if not isinstance(payload, dict):
            raise ValueError(
                f"Unexpected Alpaca corporate actions payload: {type(payload).__name__}"
            )
        return cast(Mapping[str, Any], payload)

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    def __enter__(self) -> AlpacaCorporateActionsRestClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        self.close()


class AlpacaCorporateActionsSyncManager:
    """Sync Alpaca corporate-action announcements to local parquet."""

    DATASET_NAME = "alpaca_sip_corp_actions"
    DEFAULT_DATA_ROOT = Path("data")
    DEFAULT_STORAGE_PATH = Path("data/alpaca/sip/corp_actions")
    DEFAULT_LIMIT = 1000
    BYTES_PER_ROW_ESTIMATE = 1024
    UNFILTERED_ROWS_PER_YEAR_ESTIMATE = 50_000
    FILTERED_ROWS_PER_SYMBOL_YEAR_ESTIMATE = 25
    SYMBOL_REQUEST_CHUNK_SIZE = 200
    MAX_PAGES_PER_REQUEST = 1000
    MAX_ACTION_NESTING_DEPTH = 64

    def __init__(
        self,
        client: AlpacaCorporateActionsClient,
        storage_path: Path,
        manifest_manager: ManifestManager,
        *,
        data_root: Path | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> None:
        if limit < 1:
            raise ValueError("limit must be >= 1")

        self.client = client
        self.storage_path = Path(storage_path).resolve()
        self.manifest_manager = manifest_manager
        self.data_root = (data_root or self.DEFAULT_DATA_ROOT).resolve()
        self.limit = limit

        if not self.storage_path.is_relative_to(self.data_root):
            raise ValueError(
                f"storage_path '{storage_path}' must be within data_root '{self.data_root}'"
            )

        self.storage_path.mkdir(parents=True, exist_ok=True)

    def close(self) -> None:
        """Close any resources held by the configured client."""
        close = getattr(self.client, "close", None)
        if callable(close):
            close()

    def __enter__(self) -> AlpacaCorporateActionsSyncManager:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        self.close()

    @classmethod
    def from_env(
        cls,
        *,
        storage_path: Path | None = None,
        manifest_manager: ManifestManager | None = None,
        data_root: Path | None = None,
        limit: int = DEFAULT_LIMIT,
        base_url: str | None = None,
    ) -> AlpacaCorporateActionsSyncManager:
        """Build a manager from standard Alpaca environment variables."""
        api_key = os.getenv("ALPACA_API_KEY_ID") or os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_API_SECRET_KEY") or os.getenv("ALPACA_SECRET_KEY")
        if not api_key or not secret_key:
            raise ValueError(
                "Alpaca credentials required: set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY"
            )

        resolved_data_root = Path(data_root or cls.DEFAULT_DATA_ROOT)
        resolved_storage = Path(
            storage_path
            or os.getenv("ALPACA_CORP_ACTIONS_STORAGE_PATH")
            or cls.DEFAULT_STORAGE_PATH
        )
        resolved_manifest_manager = manifest_manager or ManifestManager(
            data_root=resolved_data_root
        )
        client = AlpacaCorporateActionsRestClient(
            api_key=api_key,
            secret_key=secret_key,
            base_url=base_url or os.getenv("ALPACA_DATA_BASE_URL") or "https://data.alpaca.markets",
        )
        return cls(
            client=client,
            storage_path=resolved_storage,
            manifest_manager=resolved_manifest_manager,
            data_root=resolved_data_root,
            limit=limit,
        )

    def full_sync(
        self,
        *,
        start_date: datetime.date,
        end_date: datetime.date,
        symbols: Sequence[str] | None = None,
        ca_types: Sequence[str] | None = None,
        ids: Sequence[str] | None = None,
    ) -> SyncManifest:
        """Sync a bounded corporate-action announcement range."""
        sync_started_at = datetime.datetime.now(datetime.UTC)
        self._validate_date_range(start_date, end_date)
        normalized_symbols = self._normalize_symbols(symbols or ())
        normalized_types = self._normalize_values(ca_types or (), upper=False)
        normalized_ids = self._normalize_values(ids or (), upper=False)
        if normalized_ids and (normalized_symbols or normalized_types):
            raise ValueError("ids cannot be combined with symbols or ca_types")

        base_params = self._build_base_params(
            start_date=start_date,
            end_date=end_date,
            symbols=normalized_symbols,
            ca_types=normalized_types,
            ids=normalized_ids,
        )
        sync_id = self._build_sync_id(base_params)
        output_path = self.storage_path / "snapshots" / sync_id / "corporate_actions.parquet"

        self._check_disk_space(
            estimated_rows=self._estimate_pre_fetch_rows(
                start_date=start_date,
                end_date=end_date,
                symbols=normalized_symbols,
                ca_types=normalized_types,
                ids=normalized_ids,
            )
        )
        actions = self._fetch_actions(
            start_date=start_date,
            end_date=end_date,
            symbols=normalized_symbols,
            ca_types=normalized_types,
            ids=normalized_ids,
            base_params=base_params,
        )
        df = self._rows_to_frame([self._row_from_action(action) for action in actions])

        with self.manifest_manager.acquire_lock(
            self.DATASET_NAME,
            writer_id=f"alpaca-corp-actions-sync:{os.getpid()}",
            timeout_seconds=60.0,
        ) as lock_token:
            self._check_disk_space(estimated_rows=max(1, len(actions)))
            checksum = self._atomic_write_parquet(df, output_path)
            manifest = self._create_manifest(
                file_paths=[str(output_path.relative_to(self.data_root))],
                row_count=df.height,
                start_date=start_date,
                end_date=end_date,
                checksum=checksum,
                params=base_params,
                symbols=normalized_symbols,
                sync_started_at=sync_started_at,
                sync_finished_at=datetime.datetime.now(datetime.UTC),
            )
            self.manifest_manager.save_manifest(manifest, lock_token)

        logger.info(
            "Alpaca corporate actions sync completed",
            extra={
                "event": "alpaca_corp_actions.sync.complete",
                "dataset": self.DATASET_NAME,
                "row_count": df.height,
                "file": str(output_path),
            },
        )
        return manifest

    def verify_integrity(self) -> list[str]:
        """Verify files, row count, and checksum against the current manifest."""
        errors: list[str] = []
        manifest = self.manifest_manager.load_manifest(self.DATASET_NAME)
        if manifest is None:
            return [f"No manifest found for {self.DATASET_NAME}"]

        partition_paths, path_errors = self._manifest_paths_for_verify(manifest)
        if path_errors:
            return path_errors

        total_rows = 0
        for path in partition_paths:
            if not path.exists():
                errors.append(f"Missing file: {path}")
                continue
            try:
                total_rows += int(pl.scan_parquet(path).select(pl.len()).collect().item())
            except (OSError, ValueError) as exc:
                errors.append(f"Cannot read {path}: {exc}")

        if errors:
            return errors

        computed_checksum = self._compute_combined_checksum_for_paths(partition_paths)
        if computed_checksum != manifest.checksum:
            errors.append(
                "Checksum mismatch: "
                f"manifest={manifest.checksum[:16]}..., "
                f"computed={computed_checksum[:16]}..."
            )

        if total_rows != manifest.row_count:
            errors.append(
                f"Row count mismatch: manifest={manifest.row_count}, computed={total_rows}"
            )

        return errors

    def run_round_trip_checks(
        self,
        checks: Sequence[CorporateActionRoundTripCheck] = DEFAULT_ROUND_TRIP_CHECKS,
    ) -> CorporateActionRoundTripReport:
        """Verify known split/dividend events are visible through the API."""
        results: list[CorporateActionRoundTripResult] = []
        for check in checks:
            self._validate_date_range(check.start_date, check.end_date)
            params = self._build_base_params(
                start_date=check.start_date,
                end_date=check.end_date,
                symbols=[check.symbol.upper().strip()],
                ca_types=[],
                ids=[],
            )
            actions = self._fetch_all_pages(params)
            rows = [self._row_from_action(action) for action in actions]
            matches = [row for row in rows if self._row_matches_round_trip_check(row, check)]
            results.append(
                CorporateActionRoundTripResult(
                    check=check,
                    raw_action_count=len(actions),
                    matched_action_count=len(matches),
                    matched_ids=tuple(sorted(str(row["id"]) for row in matches if row.get("id"))),
                    matched_types=tuple(
                        sorted({str(row["ca_type"]) for row in matches if row.get("ca_type")})
                    ),
                    status="passed" if matches else "failed",
                )
            )

        report_status = (
            "passed" if all(result.status == "passed" for result in results) else "failed"
        )
        return CorporateActionRoundTripReport(status=report_status, results=tuple(results))

    def _fetch_all_pages(self, base_params: Mapping[str, str | int]) -> list[Mapping[str, Any]]:
        params: dict[str, str | int] = dict(base_params)
        actions: list[Mapping[str, Any]] = []
        page_count = 0
        while True:
            page_count += 1
            if page_count > self.MAX_PAGES_PER_REQUEST:
                raise RuntimeError(
                    "Alpaca corporate actions pagination exceeded "
                    f"{self.MAX_PAGES_PER_REQUEST} pages"
                )
            payload = self.client.get_corporate_actions(params)
            actions.extend(self._actions_from_payload(payload))
            next_page_token = self._next_page_token(payload)
            if not next_page_token:
                break
            params["page_token"] = next_page_token
        return actions

    def _fetch_actions(
        self,
        *,
        start_date: datetime.date,
        end_date: datetime.date,
        symbols: Sequence[str],
        ca_types: Sequence[str],
        ids: Sequence[str],
        base_params: Mapping[str, str | int],
    ) -> list[Mapping[str, Any]]:
        if symbols and not ids:
            actions: list[Mapping[str, Any]] = []
            for symbol_chunk in self._chunks(symbols, self.SYMBOL_REQUEST_CHUNK_SIZE):
                chunk_params = self._build_base_params(
                    start_date=start_date,
                    end_date=end_date,
                    symbols=symbol_chunk,
                    ca_types=ca_types,
                    ids=[],
                )
                actions.extend(self._fetch_all_pages(chunk_params))
            return actions
        return self._fetch_all_pages(base_params)

    @staticmethod
    def _actions_from_payload(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        raw_actions: Any = []
        for key in ("corporate_actions", "announcements", "data", "items", "results"):
            value = payload.get(key)
            if value is not None:
                raw_actions = value
                break
        if isinstance(raw_actions, dict):
            candidates: list[Any] = []
            for raw_action_type, value in raw_actions.items():
                AlpacaCorporateActionsSyncManager._append_action_candidate(
                    candidates,
                    action_type=AlpacaCorporateActionsSyncManager._action_type_from_payload_key(
                        raw_action_type
                    ),
                    value=value,
                )
            raw_actions = candidates
        if not isinstance(raw_actions, list):
            raise ValueError("Unexpected corporate actions response shape")

        actions: list[Mapping[str, Any]] = []
        for action in raw_actions:
            if not isinstance(action, Mapping):
                raise ValueError(f"Unexpected corporate action item: {type(action).__name__}")
            actions.append(action)
        return actions

    @staticmethod
    def _append_action_candidate(
        candidates: list[Any],
        *,
        action_type: str | None,
        value: Any,
    ) -> None:
        stack: list[tuple[Any, int]] = [(value, 0)]
        while stack:
            current, depth = stack.pop()
            if depth > AlpacaCorporateActionsSyncManager.MAX_ACTION_NESTING_DEPTH:
                raise ValueError("Corporate actions payload is too deeply nested")

            if isinstance(current, list | tuple):
                stack.extend((item, depth + 1) for item in reversed(current))
                continue

            if isinstance(current, Mapping):
                if AlpacaCorporateActionsSyncManager._looks_like_action_mapping(current):
                    enriched = dict(current)
                    if action_type:
                        enriched.setdefault("ca_type", action_type)
                    candidates.append(enriched)
                    continue
                nested_containers = [
                    current[key]
                    for key in ACTION_CONTAINER_KEYS
                    if isinstance(current.get(key), Mapping | list | tuple)
                ]
                if nested_containers:
                    stack.extend((item, depth + 1) for item in reversed(nested_containers))
                    continue
                stack.extend((item, depth + 1) for item in reversed(tuple(current.values())))

    @staticmethod
    def _action_type_from_payload_key(key: object) -> str | None:
        key_text = str(key).strip()
        normalized = key_text.lower().replace("-", "_").replace(" ", "_")
        if normalized in ACTION_TYPE_KEYS:
            return normalized
        if AlpacaCorporateActionsSyncManager._looks_like_symbol_key(key_text):
            return None
        return key_text

    @staticmethod
    def _looks_like_symbol_key(key: str) -> bool:
        symbol = key.strip()
        if not 1 <= len(symbol) <= 12:
            return False
        if symbol.upper() != symbol:
            return False
        return any(char.isalpha() for char in symbol) and all(
            char.isalnum() or char in {".", "-"} for char in symbol
        )

    @staticmethod
    def _next_page_token(payload: Mapping[str, Any]) -> str | None:
        token = payload.get("next_page_token") or payload.get("next_token")
        if token is None:
            return None
        token_text = str(token).strip()
        return token_text or None

    def _row_from_action(self, action: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": self._optional_text(action, "id", "ca_id"),
            "symbol": self._optional_text(action, "symbol", "ticker"),
            "cusip": self._optional_text(action, "cusip"),
            "ca_type": self._optional_text(action, "ca_type", "type", "corporate_action_type"),
            "process_date": self._optional_date(action, "process_date", "declaration_date"),
            "ex_date": self._optional_date(action, "ex_date", "ex_dividend_date"),
            "record_date": self._optional_date(action, "record_date"),
            "payable_date": self._optional_date(action, "payable_date", "payment_date"),
            "cash": self._cash_amount(action),
            "old_rate": self._optional_float(action, "old_rate", "old_ratio"),
            "new_rate": self._optional_float(action, "new_rate", "new_ratio"),
            "old_symbol": self._optional_text(action, "old_symbol", "old_ticker"),
            "new_symbol": self._optional_text(action, "new_symbol", "new_ticker"),
            "raw": json.dumps(dict(action), sort_keys=True, default=str),
        }

    @staticmethod
    def _row_matches_round_trip_check(
        row: Mapping[str, Any],
        check: CorporateActionRoundTripCheck,
    ) -> bool:
        symbol = str(row.get("symbol") or "").upper().strip()
        if symbol != check.symbol.upper().strip():
            return False

        date_values = [
            value
            for value in (
                row.get("process_date"),
                row.get("ex_date"),
                row.get("record_date"),
                row.get("payable_date"),
            )
            if isinstance(value, datetime.date)
        ]
        if date_values and not any(
            check.start_date <= value <= check.end_date for value in date_values
        ):
            return False

        type_text = str(row.get("ca_type") or "").lower()
        normalized_type = type_text.replace("-", "_").replace(" ", "_")
        # Match only the structured type field so raw payload labels cannot
        # create false positives for known-event checks.
        return any(token.lower() in normalized_type for token in check.expected_type_tokens)

    def _rows_to_frame(self, rows: list[dict[str, Any]]) -> pl.DataFrame:
        if not rows:
            return pl.DataFrame(schema=ALPACA_CORP_ACTIONS_SCHEMA)

        df = pl.DataFrame(rows, schema=ALPACA_CORP_ACTIONS_SCHEMA).with_columns(
            pl.col("symbol").str.to_uppercase()
        )
        df_with_id = df.filter(pl.col("id").is_not_null())
        df_without_id = df.filter(pl.col("id").is_null())
        if not df_with_id.is_empty():
            df_with_id = df_with_id.unique(subset=["id"], keep="last", maintain_order=True)
        df = pl.concat([df_with_id, df_without_id], how="vertical")
        return df.sort(["process_date", "symbol", "id"], nulls_last=True).select(
            list(ALPACA_CORP_ACTIONS_COLUMNS)
        )

    @staticmethod
    def _optional_text(action: Mapping[str, Any], *names: str) -> str | None:
        for name in names:
            value = action.get(name)
            if value is not None and str(value).strip():
                return str(value).strip()
        return None

    @staticmethod
    def _optional_float(action: Mapping[str, Any], *names: str) -> float | None:
        for name in names:
            value = action.get(name)
            if value in (None, ""):
                continue
            return float(cast(Any, value))
        return None

    @staticmethod
    def _cash_amount(action: Mapping[str, Any]) -> float | None:
        cash = AlpacaCorporateActionsSyncManager._optional_float(action, "cash", "cash_amount")
        if cash is not None:
            return cash

        ca_type = str(action.get("ca_type") or action.get("type") or "").strip().lower()
        if ca_type in {"cash_dividend", "cash_dividends", "dividend", "dividends"}:
            return AlpacaCorporateActionsSyncManager._optional_float(action, "rate")
        return None

    @staticmethod
    def _optional_date(action: Mapping[str, Any], *names: str) -> datetime.date | None:
        for name in names:
            value = action.get(name)
            if value in (None, ""):
                continue
            if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
                return value
            if isinstance(value, datetime.datetime):
                if value.tzinfo is None:
                    return value.date()
                return value.astimezone(datetime.UTC).date()
            if isinstance(value, str):
                return datetime.date.fromisoformat(value[:10])
            raise ValueError(f"Unsupported date value for {name}: {value!r}")
        return None

    @staticmethod
    def _normalize_symbols(symbols: Sequence[str]) -> list[str]:
        return AlpacaCorporateActionsSyncManager._normalize_values(symbols, upper=True)

    @staticmethod
    def _chunks(values: Sequence[str], chunk_size: int) -> list[list[str]]:
        return [list(values[i : i + chunk_size]) for i in range(0, len(values), chunk_size)]

    @staticmethod
    def _normalize_values(values: Sequence[str], *, upper: bool) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = value.strip()
            if not item:
                continue
            if upper:
                item = item.upper()
            if item not in seen:
                normalized.append(item)
                seen.add(item)
        return sorted(normalized)

    def _build_base_params(
        self,
        *,
        start_date: datetime.date,
        end_date: datetime.date,
        symbols: Sequence[str],
        ca_types: Sequence[str],
        ids: Sequence[str],
    ) -> dict[str, str | int]:
        params: dict[str, str | int] = {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
            "limit": self.limit,
            "sort": "asc",
        }
        if symbols:
            params["symbols"] = ",".join(symbols)
        if ca_types:
            params["types"] = ",".join(ca_types)
        if ids:
            params["ids"] = ",".join(ids)
        return params

    @staticmethod
    def _validate_date_range(start_date: datetime.date, end_date: datetime.date) -> None:
        spec = get_provider_spec(ProviderType.ALPACA_SIP)
        history_start = spec.capabilities.history_start or datetime.date(2016, 1, 1)
        if start_date < history_start:
            raise ValueError(
                "Alpaca SIP corporate-action sync starts at " f"{history_start.isoformat()}"
            )
        if end_date < start_date:
            raise ValueError("end_date must be >= start_date")
        today = datetime.datetime.now(datetime.UTC).date()
        if end_date > today:
            raise ValueError("end_date cannot be in the future")

    def _build_sync_id(self, params: Mapping[str, str | int]) -> str:
        params_hash = hashlib.sha256(
            json.dumps(dict(params), sort_keys=True, default=str).encode()
        ).hexdigest()[:12]
        return params_hash

    def _atomic_write_parquet(self, df: pl.DataFrame, target_path: Path) -> str:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = target_path.with_suffix(".parquet.tmp")
        try:
            df.write_parquet(temp_path)
            temp_checksum = self._compute_checksum_and_fsync(temp_path)
            temp_path.replace(target_path)
            self._fsync_directory(target_path.parent)
            return temp_checksum
        except OSError as exc:
            if exc.errno == 28:
                raise DiskSpaceError(f"Disk full writing {target_path}") from exc
            raise
        finally:
            temp_path.unlink(missing_ok=True)

    @staticmethod
    def _compute_checksum(path: Path) -> str:
        hasher = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    @staticmethod
    def _compute_checksum_and_fsync(path: Path) -> str:
        hasher = hashlib.sha256()
        with open(path, "r+b") as handle:
            for chunk in iter(lambda: handle.read(8192), b""):
                hasher.update(chunk)
            os.fsync(handle.fileno())
        return hasher.hexdigest()

    def _compute_combined_checksum_for_paths(self, paths: Sequence[Path]) -> str:
        hasher = hashlib.sha256()
        for path in sorted(paths, key=str):
            if path.exists():
                hasher.update(self._compute_checksum(path).encode())
        return hasher.hexdigest()

    def _manifest_paths_for_verify(self, manifest: SyncManifest) -> tuple[list[Path], list[str]]:
        paths: list[Path] = []
        errors: list[str] = []
        for path_str in manifest.file_paths:
            resolved = self._resolve_manifest_path(Path(path_str))
            if not resolved.is_relative_to(self.storage_path):
                errors.append(
                    f"Manifest path outside storage_path: {path_str} "
                    f"(resolved: {resolved}, storage_path: {self.storage_path})"
                )
                continue
            paths.append(resolved)
        return paths, errors

    def _resolve_manifest_path(self, path: Path) -> Path:
        if path.is_absolute():
            return path.resolve()
        if len(path.parts) == 1:
            return (self.storage_path / path).resolve()
        if path.parts[0] == self.data_root.name:
            return (self.data_root.parent / path).resolve()
        if path.parts[0] == "alpaca":
            return (self.data_root / path).resolve()
        return (self.storage_path / path).resolve()

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        fd: int | None = None
        try:
            fd = os.open(path, os.O_RDONLY)
            os.fsync(fd)
        except OSError as exc:
            logger.debug(
                "Alpaca corporate actions directory fsync skipped",
                extra={"path": str(path), "error": str(exc)},
            )
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError as exc:
                    logger.debug(
                        "Alpaca corporate actions directory fsync close skipped",
                        extra={"path": str(path), "error": str(exc)},
                    )

    def _create_manifest(
        self,
        *,
        file_paths: list[str],
        row_count: int,
        start_date: datetime.date,
        end_date: datetime.date,
        checksum: str,
        params: Mapping[str, str | int],
        symbols: Sequence[str],
        sync_started_at: datetime.datetime,
        sync_finished_at: datetime.datetime,
    ) -> SyncManifest:
        query_hash = hashlib.sha256(
            json.dumps(dict(params), sort_keys=True, default=str).encode()
        ).hexdigest()
        spec = get_provider_spec(ProviderType.ALPACA_SIP)

        return SyncManifest(
            dataset=self.DATASET_NAME,
            sync_timestamp=datetime.datetime.now(datetime.UTC),
            start_date=start_date,
            end_date=end_date,
            row_count=row_count,
            checksum=checksum,
            schema_version="v1.0.0",
            wrds_query_hash=query_hash,
            file_paths=file_paths,
            validation_status="passed",
            provider_id=spec.provider_id.value,
            provider_version=spec.provider_version,
            source_feed=spec.source_feed,
            adjustment_mode=spec.default_adjustment_mode,
            symbol_set_hash=compute_symbol_set_hash(list(symbols)),
            sync_started_at=sync_started_at,
            sync_finished_at=sync_finished_at,
        )

    def _check_disk_space(self, estimated_rows: int) -> None:
        required_bytes = max(1, estimated_rows) * self.BYTES_PER_ROW_ESTIMATE * 2
        status = self.manifest_manager.check_disk_space(required_bytes)
        if status.level == "warning":
            logger.warning("Alpaca corporate-actions sync disk warning: %s", status.message)
        elif status.level == "critical":
            logger.critical("Alpaca corporate-actions sync disk critical: %s", status.message)
            raise DiskSpaceError(status.message)

    @staticmethod
    def _looks_like_action_mapping(value: Mapping[str, Any]) -> bool:
        if any(isinstance(value.get(key), Mapping | list | tuple) for key in ACTION_CONTAINER_KEYS):
            return False

        def has_scalar(*fields: str) -> bool:
            return any(
                field in value and not isinstance(value[field], Mapping | list | tuple)
                for field in fields
            )

        has_identifier = has_scalar("id", "ca_id")
        has_symbol = has_scalar("symbol", "ticker", "cusip")
        has_type = has_scalar("ca_type", "type", "corporate_action_type")
        has_date = has_scalar(
            "process_date",
            "ex_date",
            "record_date",
            "payable_date",
            "declaration_date",
            "ex_dividend_date",
            "payment_date",
        )
        return (
            has_identifier or (has_type and (has_symbol or has_date)) or (has_symbol and has_date)
        )

    @staticmethod
    def _estimate_pre_fetch_rows(
        *,
        start_date: datetime.date,
        end_date: datetime.date,
        symbols: Sequence[str],
        ca_types: Sequence[str],
        ids: Sequence[str],
    ) -> int:
        date_span_days = max(1, (end_date - start_date).days + 1)
        type_factor = max(1, len(ca_types))
        year_factor = max(1, (date_span_days + 365) // 366)
        if ids:
            return max(len(ids), len(ids) * year_factor)
        if not symbols:
            return (
                AlpacaCorporateActionsSyncManager.UNFILTERED_ROWS_PER_YEAR_ESTIMATE
                * type_factor
                * year_factor
            )
        symbol_factor = len(symbols)
        # Corporate actions are sparse, but the pre-fetch gate must still scale
        # with the request bounds so obviously unsafe syncs fail before API use.
        month_factor = max(1, (date_span_days + 30) // 31)
        monthly_floor = symbol_factor * type_factor * month_factor
        conservative_symbol_estimate = (
            symbol_factor
            * type_factor
            * year_factor
            * AlpacaCorporateActionsSyncManager.FILTERED_ROWS_PER_SYMBOL_YEAR_ESTIMATE
        )
        return max(monthly_floor, conservative_symbol_estimate)
