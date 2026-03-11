"""Universe management backend (P6T15/T15.1).

Synchronous manager wrapping ``UniverseProvider`` (built-in universes)
and ``CRSPLocalProvider`` (enrichment) with file-based custom universe
persistence under ``data/universes/``.

All methods are **synchronous** (Polars I/O + CPU). The async service
layer (``UniverseService``) MUST wrap calls with ``asyncio.to_thread()``.

Architecture Notes (see ADR-0037 — file-based persistence):
    - **Storage:** JSON files + ``fcntl.flock`` advisory lock + atomic rename.
      Sufficient for current scale (<200 universes). POSIX-only.
    - **Identity:** Canonical universe ID is the filename stem (``foo.json``
      → ``foo``). Any divergent ``"id"`` inside the JSON is ignored with a
      warning log. This prevents lifecycle drift (cache/delete by wrong key).
    - **Caching:** In-memory ``(universe_id, as_of_date) -> DataFrame`` with
      30-minute TTL. Generation counter invalidates stale entries on
      delete/recreate to prevent in-flight compute from caching old data.
      Cross-process changes detected via file mtime comparison.
    - **Error normalisation:** Malformed JSON or invalid schema fields raise
      ``UniverseCorruptError`` (never raw Pydantic ``ValidationError``).
    - **Migration trigger:** When custom universe count exceeds the per-user
      limit (~20) across many users or multi-writer concurrency is needed,
      migrate to PostgreSQL with row-level ``FOR UPDATE`` locks.
"""

from __future__ import annotations

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]  # Windows — no advisory locking

import json
import logging
import os
import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
from pydantic import ValidationError

from libs.data.data_providers.universe import CRSPUnavailableError, UniverseProvider
from libs.data.schemas.universe import (
    UniverseFilterDTO,
    UniverseMetadata,
)

logger = logging.getLogger(__name__)


def _parse_utc_timestamp(raw: str | None) -> datetime | None:
    """Parse an ISO timestamp string and ensure UTC timezone.

    Naive timestamps are assumed UTC and have tzinfo set explicitly.
    """
    if raw is None:
        return None
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


_UNIVERSE_ID_PATTERN = re.compile(r"^[a-z0-9_]{1,64}$")
# Accepts alphanumeric + common IdP separators (@._ - |) so email-style
# subjects, OIDC sub claims, and Auth0 IDs (auth0|12345) pass validation
# (aligned with service layer).
_CREATED_BY_PATTERN = re.compile(r"^[a-zA-Z0-9_@.\-|]{1,128}$")
# Ticker format: 1-10 chars of A-Z, 0-9, dot, hyphen (e.g. AAPL, BRK.B, BF-B)
_TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,10}$")
_MAX_CUSTOM_PER_USER = 20
_MAX_MANUAL_SYMBOLS = 5000
_CACHE_TTL_NS = 1_800_000_000_000  # 30 minutes in nanoseconds
_MAX_CACHE_ENTRIES = 50

_ENRICHED_SCHEMA = {
    "permno": pl.Int64,
    "ticker": pl.Utf8,
    "market_cap": pl.Float64,
    "adv_20d": pl.Float64,
}

# Built-in universe definitions
_BUILT_IN_UNIVERSES: list[dict[str, str]] = [
    {"id": "SP500", "name": "S&P 500"},
    {"id": "R1000", "name": "Russell 1000"},
]
_BUILT_IN_IDS: set[str] = {bi["id"] for bi in _BUILT_IN_UNIVERSES}


class ConflictError(ValueError):
    """Raised when a universe ID already exists."""

    pass


class UniverseNotFoundError(ValueError):
    """Raised when a universe ID does not exist."""

    pass


class UniverseCorruptError(ValueError):
    """Raised when a universe file is corrupt or unreadable."""

    pass


class UniverseManager:
    """Synchronous universe management backend.

    Provides listing, constituent retrieval with CRSP enrichment,
    filter application, and file-based custom universe CRUD.

    Args:
        universes_dir: Directory for custom universe JSON files.
        universe_provider: Provider for built-in index constituents.
        crsp_provider: Provider for CRSP daily data (enrichment).
            Pass ``None`` if CRSP data is unavailable.
    """

    def __init__(
        self,
        universes_dir: Path,
        universe_provider: UniverseProvider | None = None,
        crsp_provider: Any | None = None,
    ) -> None:
        self._universes_dir = Path(universes_dir).resolve()
        self._universes_dir.mkdir(parents=True, exist_ok=True)
        self._lock_path = self._universes_dir / ".lock"
        self._universe_provider = universe_provider
        self._crsp_provider = crsp_provider

        # In-memory cache: (universe_id, as_of_date) ->
        #   (df, wall_clock_ns, source_mtime_ns)
        # wall_clock_ns is used for TTL expiry; source_mtime_ns is the
        # file's st_mtime_ns captured BEFORE compute, used for cross-process
        # invalidation (prevents missing updates during _compute_enriched).
        self._enriched_cache: dict[
            tuple[str, date], tuple[pl.DataFrame, int, int]
        ] = {}
        self._cache_lock = threading.Lock()
        # Separate lock for file write operations (non-POSIX fallback).
        # Using _cache_lock for both would risk deadlock if a file-op
        # caller later needs _cache_lock inside the same scope.
        self._file_op_lock = threading.Lock()
        # Per-key locks to prevent cache stampede (concurrent enrichment)
        self._compute_locks: dict[tuple[str, date], threading.Lock] = {}
        # Track unresolved tickers from manual list resolution.
        # Value: (unresolved_tickers, generation_at_write_time).
        self._last_unresolved: dict[tuple[str, date], tuple[list[str], int]] = {}
        # Generation counter per universe ID.  Incremented on delete/recreate.
        # In-flight enrichment captures the generation before compute; if it
        # doesn't match at cache-write time the result is discarded.
        self._universe_generation: dict[str, int] = {}

    @property
    def universe_provider(self) -> UniverseProvider | None:
        """The configured universe provider (may be None)."""
        return self._universe_provider

    @universe_provider.setter
    def universe_provider(self, provider: UniverseProvider | None) -> None:
        self._universe_provider = provider

    @property
    def crsp_provider(self) -> Any | None:
        """The configured CRSP provider (may be None)."""
        return self._crsp_provider

    @crsp_provider.setter
    def crsp_provider(self, provider: Any | None) -> None:
        self._crsp_provider = provider

    def _is_custom_modified_since(self, universe_id: str, cache_ts_ns: int) -> bool:
        """Check if a custom universe JSON was modified or deleted since ``cache_ts_ns``.

        Uses nanosecond precision (``st_mtime_ns``) to avoid missing
        rapid rewrites on coarse-timestamp filesystems.

        Returns ``False`` for built-in universes (no JSON file to check).
        Returns ``True`` (invalidate) if a custom universe file was
        deleted externally.
        """
        # Built-in universes have no JSON file — skip file check
        if universe_id in _BUILT_IN_IDS:
            return False
        path = self._universes_dir / f"{universe_id}.json"
        try:
            return path.stat().st_mtime_ns > cache_ts_ns
        except FileNotFoundError:
            # File deleted externally — invalidate to prevent serving stale data
            return True
        except OSError:
            return False

    def _get_source_mtime_ns(self, universe_id: str) -> int:
        """Snapshot the custom universe file's mtime (nanoseconds).

        Returns 0 for built-in universes or if the file doesn't exist.
        """
        if universe_id in _BUILT_IN_IDS:
            return 0
        path = self._universes_dir / f"{universe_id}.json"
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return 0

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def list_universes(self) -> list[UniverseMetadata]:
        """List all available universes (built-in + custom).

        Returns:
            List of universe metadata, built-in first then custom sorted by name.
        """
        result: list[UniverseMetadata] = []

        for bi in _BUILT_IN_UNIVERSES:
            result.append(
                UniverseMetadata(
                    id=bi["id"],
                    name=bi["name"],
                    universe_type="built_in",
                )
            )

        # Load custom universes from JSON files
        custom: list[UniverseMetadata] = []
        for path in self._universes_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                # Canonical ID is always the filename stem (not JSON "id")
                custom.append(
                    UniverseMetadata(
                        id=path.stem,
                        name=data.get("name", path.stem),
                        universe_type="custom",
                        base_universe_id=data.get("base_universe_id"),
                        filters=[
                            UniverseFilterDTO(**f) for f in data.get("filters", [])
                        ],
                        exclude_symbols=data.get("exclude_symbols", []),
                        manual_symbols=data.get("manual_symbols"),
                        created_by=data.get("created_by"),
                        created_at=_parse_utc_timestamp(data.get("created_at")),
                    )
                )
            except (
                json.JSONDecodeError,
                KeyError,
                TypeError,
                ValueError,
                ValidationError,
                OSError,
            ):
                logger.warning(
                    "universe_json_parse_failed",
                    extra={"path": str(path)},
                    exc_info=True,
                )

        # Sort custom universes by name for stable ordering
        custom.sort(key=lambda u: u.name.lower())
        result.extend(custom)
        return result

    def get_symbol_count(self, universe_id: str, as_of_date: date) -> int | None:
        """Get constituent count for a universe.

        Returns None if CRSP data is unavailable.
        """
        if self._universe_provider is None:
            return None
        try:
            df = self._universe_provider.get_constituents(universe_id, as_of_date)
            return df.height
        except CRSPUnavailableError:
            return None

    # ------------------------------------------------------------------
    # Constituent Retrieval
    # ------------------------------------------------------------------

    def get_constituents(
        self,
        universe_id: str,
        as_of_date: date,
    ) -> pl.DataFrame:
        """Get raw constituents (permno only) for a built-in universe.

        Returns:
            DataFrame with column ``permno`` (Int64).

        Raises:
            CRSPUnavailableError: If provider is unavailable.
            ValueError: If universe_id is not a built-in universe.
        """
        if self._universe_provider is None:
            raise CRSPUnavailableError("Universe provider not configured")

        return self._universe_provider.get_constituents(universe_id, as_of_date)

    def get_enriched_constituents(
        self,
        universe_id: str,
        as_of_date: date,
    ) -> pl.DataFrame:
        """Get constituents enriched with CRSP metrics.

        Returns DataFrame with columns:
            ``[permno, ticker, market_cap, adv_20d]``

        Uses lazy scan with predicate pushdown (30-day lookback covers
        20 trading days with buffer).

        Caches results per ``(universe_id, as_of_date)`` for 30 minutes.

        Raises:
            CRSPUnavailableError: If CRSP data is unavailable.
        """
        cache_key = (universe_id, as_of_date)
        with self._cache_lock:
            cached = self._enriched_cache.get(cache_key)
            if cached is not None:
                df, wall_ts, src_mtime = cached
                if (
                    time.time_ns() - wall_ts < _CACHE_TTL_NS
                    and not self._is_custom_modified_since(universe_id, src_mtime)
                ):
                    return df.clone()
            # Get or create per-key lock to prevent cache stampede
            if cache_key not in self._compute_locks:
                self._compute_locks[cache_key] = threading.Lock()
            key_lock = self._compute_locks[cache_key]

        # Per-key lock ensures only one thread computes enrichment.
        # Wrap in try/except so lock-map cleanup happens AFTER key_lock
        # is released (prevents race where another thread creates a new
        # lock for the same key while the old lock is still held).
        try:
            with key_lock:
                # Re-check cache (another thread may have filled it)
                with self._cache_lock:
                    cached = self._enriched_cache.get(cache_key)
                    if cached is not None:
                        df, wall_ts, src_mtime = cached
                        if (
                            time.time_ns() - wall_ts < _CACHE_TTL_NS
                            and not self._is_custom_modified_since(universe_id, src_mtime)
                        ):
                            return df.clone()
                    # Capture generation before compute; stale results are discarded
                    gen_before = self._universe_generation.get(universe_id, 0)

                # Bounded retry loop: recompute if generation changed during
                # compute (delete/recreate race).  Max 2 retries to prevent
                # infinite loop under continuous mutation.
                _MAX_GEN_RETRIES = 2
                gen_stable = False
                for _attempt in range(_MAX_GEN_RETRIES + 1):
                    # Snapshot file mtime BEFORE compute so cross-process
                    # updates during _compute_enriched() are detected.
                    pre_mtime = self._get_source_mtime_ns(universe_id)
                    result = self._compute_enriched(universe_id, as_of_date)

                    with self._cache_lock:
                        gen_after = self._universe_generation.get(universe_id, 0)
                        if gen_before == gen_after:
                            gen_stable = True
                            break  # Consistent — proceed to cache
                        # Generation changed — retry with updated baseline
                        logger.warning(
                            "enriched_generation_stale",
                            extra={
                                "universe_id": universe_id,
                                "gen_before": gen_before,
                                "gen_after": gen_after,
                                "attempt": _attempt + 1,
                            },
                        )
                        gen_before = gen_after

                with self._cache_lock:
                    if gen_stable:
                        self._enriched_cache[cache_key] = (
                            result, time.time_ns(), pre_mtime,
                        )
                    else:
                        # Retries exhausted — fail closed.  Do NOT return
                        # potentially stale data in a trading context.
                        logger.warning(
                            "enriched_generation_retries_exhausted",
                            extra={
                                "universe_id": universe_id,
                                "retries": _MAX_GEN_RETRIES,
                            },
                        )
                        raise ValueError(
                            f"Universe '{universe_id}' is being mutated concurrently; "
                            "enrichment could not converge — retry later"
                        )

                    # Evict oldest entries if caches exceed size limit
                    if len(self._enriched_cache) > _MAX_CACHE_ENTRIES:
                        oldest_key = min(
                            self._enriched_cache,
                            key=lambda k: self._enriched_cache[k][1],
                        )
                        del self._enriched_cache[oldest_key]
                        self._last_unresolved.pop(oldest_key, None)
                        # Only remove lock if not currently held
                        lock = self._compute_locks.get(oldest_key)
                        if lock is not None and not lock.locked():
                            del self._compute_locks[oldest_key]

                    # Prune stale entries from _last_unresolved
                    if len(self._last_unresolved) > _MAX_CACHE_ENTRIES:
                        stale = [
                            k for k in self._last_unresolved
                            if k not in self._enriched_cache
                        ]
                        for k in stale:
                            del self._last_unresolved[k]

                    # Prune stale compute locks (skip in-flight ones)
                    if len(self._compute_locks) > _MAX_CACHE_ENTRIES:
                        stale_locks = [
                            k for k, lock_obj in self._compute_locks.items()
                            if k not in self._enriched_cache and not lock_obj.locked()
                        ]
                        for k in stale_locks:
                            del self._compute_locks[k]

        except Exception:
            # key_lock is released by this point — safe to clean up
            # the lock-map entry without causing a replacement race.
            with self._cache_lock:
                if cache_key not in self._enriched_cache:
                    stale_lock = self._compute_locks.get(cache_key)
                    if stale_lock is not None and not stale_lock.locked():
                        del self._compute_locks[cache_key]
            raise

        return result.clone()

    def _compute_enriched(
        self,
        universe_id: str,
        as_of_date: date,
    ) -> pl.DataFrame:
        """Compute enriched constituents from CRSP data."""
        # Resolve universe to a list of permnos
        meta = self.get_universe_metadata(universe_id)

        if meta.manual_symbols:
            enriched = self._resolve_manual_list(meta, as_of_date)
        elif meta.universe_type == "custom" and meta.base_universe_id:
            base_permnos = self.get_constituents(meta.base_universe_id, as_of_date)
            if base_permnos.is_empty():
                return pl.DataFrame(schema=_ENRICHED_SCHEMA)
            enriched = self._enrich_permnos(
                base_permnos["permno"].to_list(), as_of_date
            )
        elif meta.universe_type == "built_in":
            base_permnos = self.get_constituents(universe_id, as_of_date)
            if base_permnos.is_empty():
                return pl.DataFrame(schema=_ENRICHED_SCHEMA)
            enriched = self._enrich_permnos(
                base_permnos["permno"].to_list(), as_of_date
            )
        else:
            raise ValueError(f"Cannot resolve universe '{universe_id}'")

        # Apply filters and exclusions for custom universes (including manual)
        if meta.universe_type == "custom":
            if meta.exclude_symbols:
                exclude_upper = [s.upper() for s in meta.exclude_symbols]
                enriched = enriched.filter(
                    pl.col("ticker").is_null()
                    | ~pl.col("ticker").is_in(exclude_upper)
                )
            if meta.filters:
                enriched = self.apply_filters(enriched, meta.filters)

        return enriched

    def _resolve_manual_list(
        self,
        meta: UniverseMetadata,
        as_of_date: date,
    ) -> pl.DataFrame:
        """Resolve manual symbol list to enriched constituents.

        Unresolved tickers are skipped (cached for later retrieval).
        Ambiguous tickers use the most recent active PERMNO.
        """
        from libs.data.data_providers.crsp_local_provider import (
            AmbiguousTickerError,
            ManifestVersionChangedError,
        )
        from libs.data.data_quality.exceptions import DataNotFoundError

        if not meta.manual_symbols:
            return pl.DataFrame(schema=_ENRICHED_SCHEMA)

        if self._crsp_provider is None:
            raise CRSPUnavailableError(
                "CRSP provider required for manual symbol resolution"
            )

        resolved_permnos: list[int] = []
        unresolved: list[str] = []
        for ticker in meta.manual_symbols:
            try:
                permno = self._crsp_provider.ticker_to_permno(
                    ticker.upper(), as_of_date
                )
                resolved_permnos.append(permno)
            except AmbiguousTickerError as e:
                # Use highest PERMNO (most recently issued) for determinism
                chosen = max(e.permnos)
                resolved_permnos.append(chosen)
                logger.info(
                    "universe_ambiguous_ticker",
                    extra={
                        "ticker": ticker,
                        "permnos": sorted(e.permnos),
                        "chosen": chosen,
                    },
                )
            except DataNotFoundError:
                unresolved.append(ticker)
            except ManifestVersionChangedError as exc:
                raise CRSPUnavailableError(
                    "CRSP manifest changed during resolution — retry later"
                ) from exc

        # Log unresolved tickers (aggregated to avoid log amplification)
        if unresolved:
            logger.info(
                "universe_unresolved_tickers",
                extra={
                    "universe_id": meta.id,
                    "count": len(unresolved),
                    "sample": unresolved[:10],
                    "as_of_date": str(as_of_date),
                },
            )

        # Cache unresolved tickers for get_unresolved_tickers() with generation
        with self._cache_lock:
            gen = self._universe_generation.get(meta.id, 0)
            self._last_unresolved[(meta.id, as_of_date)] = (unresolved, gen)

        if not resolved_permnos:
            return pl.DataFrame(schema=_ENRICHED_SCHEMA)

        enriched = self._enrich_permnos(resolved_permnos, as_of_date)

        # Log PERMNOs that resolved but have no price data (inactive/halted)
        returned_permnos = set(enriched["permno"].to_list()) if not enriched.is_empty() else set()
        missing_permnos = set(resolved_permnos) - returned_permnos
        if missing_permnos:
            logger.info(
                "universe_inactive_permnos",
                extra={
                    "universe_id": meta.id,
                    "inactive_count": len(missing_permnos),
                    "permnos": sorted(missing_permnos),
                },
            )

        return enriched

    def get_unresolved_tickers(
        self,
        universe_id: str,
        as_of_date: date,
    ) -> list[str]:
        """Get list of tickers from manual list that could not be resolved.

        Returns cached results from the most recent ``get_enriched_constituents``
        call, or re-resolves if not cached.
        """
        # Check if already resolved during enrichment (generation-aware)
        with self._cache_lock:
            cached_entry = self._last_unresolved.get(
                (universe_id, as_of_date)
            )
            if cached_entry is not None:
                cached_result, cached_gen = cached_entry
                current_gen = self._universe_generation.get(universe_id, 0)
                if cached_gen == current_gen:
                    return list(cached_result)

        from libs.data.data_providers.crsp_local_provider import (
            AmbiguousTickerError,
            ManifestVersionChangedError,
        )
        from libs.data.data_quality.exceptions import DataNotFoundError

        meta = self.get_universe_metadata(universe_id)
        if meta.manual_symbols is None or self._crsp_provider is None:
            return []

        unresolved: list[str] = []
        for ticker in meta.manual_symbols:
            try:
                self._crsp_provider.ticker_to_permno(ticker.upper(), as_of_date)
            except AmbiguousTickerError:
                pass  # Ambiguous is resolved, not unresolved
            except DataNotFoundError:
                unresolved.append(ticker)
            except ManifestVersionChangedError as exc:
                raise CRSPUnavailableError(
                    "CRSP manifest changed during resolution — retry later"
                ) from exc

        # Cache for subsequent calls (with generation)
        with self._cache_lock:
            gen = self._universe_generation.get(universe_id, 0)
            self._last_unresolved[(universe_id, as_of_date)] = (unresolved, gen)

        return list(unresolved)

    def _enrich_permnos(
        self,
        permnos: list[int],
        as_of_date: date,
    ) -> pl.DataFrame:
        """Enrich a list of PERMNOs with CRSP metrics.

        Computes:
            - ``market_cap = abs(prc) * shrout`` ($thousands)
            - ``adv_20d = mean(abs(prc) * vol, 20 days)`` ($ notional)
        """
        if self._crsp_provider is None:
            raise CRSPUnavailableError("CRSP provider not configured")

        # 30-day calendar lookback covers 20 trading days with buffer
        lookback_start = as_of_date - timedelta(days=30)

        try:
            daily_df = self._crsp_provider.get_daily_prices(
                start_date=lookback_start,
                end_date=as_of_date,
                permnos=permnos,
                columns=["date", "permno", "ticker", "prc", "vol", "shrout"],
                adjust_prices=False,
            )
        except Exception as e:
            logger.warning(
                "universe_crsp_load_failed",
                extra={"error": str(e), "permnos_count": len(permnos)},
                exc_info=True,
            )
            raise CRSPUnavailableError("CRSP data load failed") from e

        if daily_df.is_empty():
            return pl.DataFrame(schema=_ENRICHED_SCHEMA)

        # Compute abs(prc) for CRSP bid/ask encoding
        daily_df = daily_df.with_columns(pl.col("prc").abs().alias("abs_prc"))

        # Market cap from most recent date: abs(prc) * shrout ($thousands)
        latest = (
            daily_df.sort("date", descending=True)
            .group_by("permno")
            .first()
            .select([
                "permno",
                "ticker",
                (pl.col("abs_prc") * pl.col("shrout")).alias("market_cap"),
            ])
        )

        # ADV: 20-day mean of abs(prc) * vol
        adv = (
            daily_df.sort("date", descending=True)
            .group_by("permno")
            .head(20)
            .with_columns(
                (pl.col("abs_prc") * pl.col("vol")).alias("dollar_volume")
            )
            .group_by("permno")
            .agg(pl.col("dollar_volume").mean().alias("adv_20d"))
        )

        result: pl.DataFrame = latest.join(adv, on="permno", how="left")
        return result

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    @staticmethod
    def apply_filters(
        df: pl.DataFrame,
        filters: list[UniverseFilterDTO],
    ) -> pl.DataFrame:
        """Apply filter criteria to a constituents DataFrame.

        Supported fields: ``market_cap``, ``adv_20d``.
        Supported operators: ``gt``, ``lt``, ``gte``, ``lte``.
        """
        for f in filters:
            col = pl.col(f.field)
            if f.operator == "gt":
                df = df.filter(col > f.value)
            elif f.operator == "lt":
                df = df.filter(col < f.value)
            elif f.operator == "gte":
                df = df.filter(col >= f.value)
            elif f.operator == "lte":
                df = df.filter(col <= f.value)
            else:
                logger.warning(
                    "universe_unknown_filter_operator",
                    extra={"operator": f.operator, "field": f.field},
                )

        return df

    # ------------------------------------------------------------------
    # Custom Universe CRUD
    # ------------------------------------------------------------------

    def save_custom(
        self,
        definition: dict[str, Any],
        created_by: str,
    ) -> str:
        """Save a custom universe definition to JSON.

        Atomic write pattern with file-system advisory lock:
        1. Check duplicate ID (within lock scope)
        2. Write temp file
        3. Atomic rename

        Args:
            definition: Custom universe definition dict.
            created_by: Authenticated user ID.

        Returns:
            The universe ID.

        Raises:
            ConflictError: If universe ID already exists.
            ValueError: If validation fails.
        """
        name = definition.get("name", "")
        if not name:
            raise ValueError("Universe name is required")

        universe_id = self._slugify(name)
        self._validate_universe_id(universe_id)

        # Validate created_by
        if not created_by or not _CREATED_BY_PATTERN.match(created_by):
            raise ValueError(
                f"Invalid created_by '{created_by}': "
                "must be 1-128 alphanumeric, underscore, hyphen, dot, @, or | characters"
            )

        # Validate exactly one source is provided (XOR)
        manual_symbols = definition.get("manual_symbols")
        base_universe_id = definition.get("base_universe_id")
        has_manual = bool(manual_symbols)
        has_base = bool(base_universe_id)
        if not has_manual and not has_base:
            raise ValueError(
                "Either base_universe_id or manual_symbols must be provided"
            )
        if has_manual and has_base:
            raise ValueError(
                "Cannot specify both base_universe_id and manual_symbols"
            )

        # Validate base_universe_id against known universes
        if base_universe_id is not None:
            known_ids = {bi["id"] for bi in _BUILT_IN_UNIVERSES}
            if base_universe_id not in known_ids:
                raise ValueError(
                    f"Unknown base universe '{base_universe_id}'. "
                    f"Valid options: {', '.join(sorted(known_ids))}"
                )

        # Validate manual_symbols type, format, and limit
        if manual_symbols is not None:
            if not isinstance(manual_symbols, list):
                raise ValueError(
                    f"manual_symbols must be a list, got {type(manual_symbols).__name__}"
                )
            for s in manual_symbols:
                if not isinstance(s, str):
                    raise ValueError("All manual_symbols entries must be strings")
                upper = s.strip().upper()
                if upper and not _TICKER_RE.match(upper):
                    raise ValueError(f"Invalid ticker format: '{s}'")
            if len(manual_symbols) > _MAX_MANUAL_SYMBOLS:
                raise ValueError(
                    f"Manual symbol list exceeds maximum of {_MAX_MANUAL_SYMBOLS}"
                )

        # Validate exclude_symbols type and format
        exclude_symbols = definition.get("exclude_symbols", [])
        if not isinstance(exclude_symbols, list):
            raise ValueError(
                f"exclude_symbols must be a list, got {type(exclude_symbols).__name__}"
            )
        for s in exclude_symbols:
            if not isinstance(s, str):
                raise ValueError("All exclude_symbols entries must be strings")
            upper = s.strip().upper()
            if upper and not _TICKER_RE.match(upper):
                raise ValueError(f"Invalid ticker format: '{s}'")

        # Reject if trying to overwrite built-in
        if universe_id.upper() in {bi["id"] for bi in _BUILT_IN_UNIVERSES}:
            raise ConflictError(
                f"Cannot create custom universe with built-in ID '{universe_id}'"
            )

        with self._acquire_lock():
            # Check duplicate
            target = self._universes_dir / f"{universe_id}.json"
            if target.exists():
                raise ConflictError(f"Universe '{universe_id}' already exists")

            # Check per-user limit by scanning on-disk state (under file lock).
            # Always read from disk to guarantee correctness across multiple
            # workers/processes.  Early exit avoids reading all files.
            user_count = 0
            for p in self._universes_dir.glob("*.json"):
                if self._file_created_by(p) == created_by:
                    user_count += 1
                    if user_count >= _MAX_CUSTOM_PER_USER:
                        break
            if user_count >= _MAX_CUSTOM_PER_USER:
                raise ValueError(
                    f"Maximum of {_MAX_CUSTOM_PER_USER} custom universes per user"
                )

            # Validate filters before persistence to prevent self-corrupting files
            validated_filters: list[dict[str, Any]] = []
            for f in definition.get("filters", []):
                try:
                    dto = UniverseFilterDTO(
                        field=f["field"], operator=f["operator"], value=f["value"]
                    )
                except (ValidationError, KeyError, TypeError, ValueError) as exc:
                    raise ValueError(f"Invalid filter: {exc}") from exc
                validated_filters.append(dto.model_dump())

            # Build JSON payload
            # Canonicalize tickers: strip, uppercase, deduplicate (preserve order)
            def _normalize_tickers(raw: list[str]) -> list[str]:
                seen: set[str] = set()
                result: list[str] = []
                for s in raw:
                    upper = s.strip().upper()
                    if upper and upper not in seen:
                        seen.add(upper)
                        result.append(upper)
                return result

            # Normalize empty manual list to None to prevent contradictory
            # state where manual_symbols=[] triggers manual resolution mode
            # but produces zero constituents.
            normalized_manual = (
                _normalize_tickers(manual_symbols) if manual_symbols else None
            )

            # Guard: if all manual symbols were blank/whitespace, normalization
            # produces [] — reject early to prevent a self-corrupting file with
            # no constituent source.
            if manual_symbols and normalized_manual is not None and len(normalized_manual) == 0:
                raise ValueError(
                    "manual_symbols contains no valid tickers after normalization"
                )
            normalized_exclude = _normalize_tickers(exclude_symbols)

            payload = {
                "id": universe_id,
                "name": name,
                "created_by": created_by,
                "created_at": datetime.now(UTC).isoformat(),
                "base_universe_id": definition.get("base_universe_id"),
                "filters": validated_filters,
                "exclude_symbols": normalized_exclude,
                "manual_symbols": normalized_manual,
            }

            # Atomic write: temp file -> fsync -> rename -> fsync parent
            tmp_path = self._universes_dir / f"{universe_id}.json.tmp"
            try:
                fd = os.open(
                    str(tmp_path),
                    os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                    0o600,  # Owner-only read/write; explicit to avoid umask variance
                )
                try:
                    os.write(fd, json.dumps(payload, indent=2, allow_nan=False).encode())
                    os.fsync(fd)
                finally:
                    os.close(fd)
                os.rename(str(tmp_path), str(target))
                # fsync parent directory so the rename is durable
                dir_fd = os.open(str(self._universes_dir), os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise

        # Bump generation so in-flight enrichment for old definition is discarded
        with self._cache_lock:
            self._universe_generation[universe_id] = (
                self._universe_generation.get(universe_id, 0) + 1
            )

        logger.info(
            "universe_custom_created",
            extra={
                "universe_id": universe_id,
                "created_by": created_by,
                "base": definition.get("base_universe_id"),
            },
        )
        return universe_id

    def delete_custom(self, universe_id: str) -> None:
        """Delete a custom universe definition.

        Raises:
            ValueError: If universe_id is invalid or built-in.
            FileNotFoundError: If universe doesn't exist.
        """
        # Check built-in before ID validation (built-in IDs are uppercase)
        if universe_id in {bi["id"] for bi in _BUILT_IN_UNIVERSES}:
            raise ValueError(f"Cannot delete built-in universe '{universe_id}'")

        self._validate_universe_id(universe_id)

        with self._acquire_lock():
            target = self._universes_dir / f"{universe_id}.json"
            if not target.exists():
                raise FileNotFoundError(
                    f"Universe '{universe_id}' not found"
                )
            target.unlink()
            # fsync parent directory so the deletion is durable.
            # Best-effort: if fsync fails, file is already deleted so
            # cache invalidation must still proceed.
            try:
                dir_fd = os.open(str(self._universes_dir), os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                logger.warning(
                    "delete_fsync_failed",
                    extra={"universe_id": universe_id},
                    exc_info=True,
                )

        # Invalidate caches and bump generation to discard in-flight enrichment
        with self._cache_lock:
            self._universe_generation[universe_id] = (
                self._universe_generation.get(universe_id, 0) + 1
            )
            keys_to_remove = [
                k for k in self._enriched_cache if k[0] == universe_id
            ]
            for k in keys_to_remove:
                del self._enriched_cache[k]
            unresolved_keys = [
                k for k in self._last_unresolved if k[0] == universe_id
            ]
            for k in unresolved_keys:
                del self._last_unresolved[k]
            lock_keys = [
                k for k, lock in self._compute_locks.items()
                if k[0] == universe_id and not lock.locked()
            ]
            for k in lock_keys:
                del self._compute_locks[k]

        logger.info(
            "universe_custom_deleted",
            extra={"universe_id": universe_id},
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def get_universe_metadata(self, universe_id: str) -> UniverseMetadata:
        """Get metadata for any universe (built-in or custom)."""
        # Check built-in (IDs are uppercase, skip ID format validation)
        for bi in _BUILT_IN_UNIVERSES:
            if bi["id"] == universe_id:
                return UniverseMetadata(
                    id=bi["id"],
                    name=bi["name"],
                    universe_type="built_in",
                )

        # Validate custom ID format before filesystem access
        self._validate_universe_id(universe_id)

        # Atomic read — avoids TOCTOU race with concurrent delete
        path = self._universes_dir / f"{universe_id}.json"
        self._validate_path_safety(path)

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise UniverseNotFoundError(f"Universe '{universe_id}' not found") from None
        except (json.JSONDecodeError, OSError) as exc:
            raise UniverseCorruptError(
                f"Universe '{universe_id}' metadata is corrupt or unreadable"
            ) from exc

        # Canonical ID is the filename stem — ignore any divergent "id" inside
        # the JSON payload to prevent lifecycle drift (delete/cache by wrong key).
        stored_id = data.get("id")
        if stored_id is not None and stored_id != universe_id:
            logger.warning(
                "universe_id_filename_mismatch",
                extra={
                    "filename_id": universe_id,
                    "stored_id": stored_id,
                },
            )

        try:
            return UniverseMetadata(
                id=universe_id,
                name=data.get("name", universe_id),
                universe_type="custom",
                base_universe_id=data.get("base_universe_id"),
                filters=[UniverseFilterDTO(**f) for f in data.get("filters", [])],
                exclude_symbols=data.get("exclude_symbols", []),
                manual_symbols=data.get("manual_symbols"),
                created_by=data.get("created_by"),
                created_at=_parse_utc_timestamp(data.get("created_at")),
            )
        except (ValidationError, KeyError, TypeError, ValueError) as exc:
            raise UniverseCorruptError(
                f"Universe '{universe_id}' metadata is corrupt or unreadable"
            ) from exc

    def _validate_universe_id(self, universe_id: str) -> None:
        """Validate universe ID format."""
        if not _UNIVERSE_ID_PATTERN.match(universe_id):
            raise ValueError(
                f"Invalid universe ID '{universe_id}': "
                "must be 1-64 lowercase alphanumeric/underscore characters"
            )

    def _validate_path_safety(self, path: Path) -> None:
        """Validate path is within universes directory (no traversal)."""
        resolved = path.resolve()
        if not resolved.is_relative_to(self._universes_dir):
            raise ValueError(
                f"Path traversal detected: {path} is outside {self._universes_dir}"
            )

    @staticmethod
    def _slugify(name: str) -> str:
        """Convert name to URL-safe slug."""
        slug = name.strip().lower()
        slug = re.sub(r"[^a-z0-9]+", "_", slug)
        slug = slug.strip("_")
        return slug[:64]

    @staticmethod
    def _file_created_by(path: Path) -> str | None:
        """Read created_by from a universe JSON file."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            val: str | None = data.get("created_by")
            return val
        except (json.JSONDecodeError, OSError):
            return None

    @contextmanager
    def _acquire_lock(self) -> Iterator[None]:
        """Acquire exclusive file-system lock for write operations.

        Uses ``fcntl.flock`` advisory locking on POSIX systems. On
        platforms where ``fcntl`` is unavailable (Windows), falls back
        to a thread-level ``threading.Lock``. The thread lock protects
        against in-process races but not cross-process writes.
        """
        if fcntl is not None:
            with open(self._lock_path, "a+") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        else:
            # Non-POSIX fallback: thread-level lock only.
            # Uses dedicated _file_op_lock (not _cache_lock) to avoid
            # deadlock when callers later acquire _cache_lock.
            with self._file_op_lock:
                yield


__all__ = [
    "ConflictError",
    "UniverseManager",
]
