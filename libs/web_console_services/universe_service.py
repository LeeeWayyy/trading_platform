"""Universe management service (P6T15/T15.1).

Async, permission-aware service layer wrapping the synchronous
``UniverseManager``. All blocking calls use ``asyncio.to_thread()``
to avoid blocking the NiceGUI event loop.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date
from typing import Any

import polars as pl

from libs.data.data_providers.universe import CRSPUnavailableError
from libs.data.universe_manager import (
    UniverseCorruptError,
    UniverseManager,
    UniverseNotFoundError,
)
from libs.platform.web_console_auth.permissions import (
    Permission,
    has_dataset_permission,
    has_permission,
)
from libs.web_console_services.schemas.universe import (
    CustomUniverseDefinitionDTO,
    UniverseAnalyticsDTO,
    UniverseComparisonDTO,
    UniverseConstituentDTO,
    UniverseDetailDTO,
    UniverseFilterDTO,
    UniverseListItemDTO,
)

logger = logging.getLogger(__name__)

# Accepts alphanumeric + common IdP separators (@._ - |) so email-style
# subjects (user@company.com), OIDC sub claims, and Auth0 IDs
# (auth0|12345) pass validation.
_USER_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_@.\-|]{1,128}$")
# Broader than manager's custom-ID pattern (lowercase underscore only) because
# this must also accept built-in IDs like "SP500" (uppercase).  Manager
# enforces stricter validation for custom-only IDs at persistence time.
_UNIVERSE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-.]{1,128}$")

# Mock sector weights (11 GICS sectors, sum ≈ 1.0) — P6T15/T15.2
_MOCK_SECTOR_WEIGHTS: dict[str, float] = {
    "Information Technology": 0.28,
    "Health Care": 0.13,
    "Financials": 0.13,
    "Consumer Discretionary": 0.10,
    "Communication Services": 0.09,
    "Industrials": 0.08,
    "Consumer Staples": 0.06,
    "Energy": 0.04,
    "Utilities": 0.03,
    "Real Estate": 0.03,
    "Materials": 0.03,
}

# Mock factor exposures — P6T15/T15.2
_MOCK_FACTOR_EXPOSURE: dict[str, float] = {
    "Market": 1.0,
    "Size": -0.3,
    "Value": 0.15,
    "Momentum": 0.2,
    "Volatility": -0.1,
}


def safe_user_id(user: Any) -> str:
    """Extract user ID for logging (never raises).

    Delegates to ``_get_user_id`` for validated extraction, falling back
    to a best-effort string extraction on any error.
    """
    try:
        return _get_user_id(user)
    except (ValueError, TypeError, AttributeError):
        if isinstance(user, dict):
            return str(user.get("user_id") or user.get("username") or "unknown")
        return str(getattr(user, "user_id", None) or getattr(user, "username", "unknown"))


def _get_user_id(user: Any) -> str:
    """Extract and validate user ID from user object.

    Raises:
        ValueError: If user ID is missing or invalid.
    """
    if isinstance(user, dict):
        val = user.get("user_id") or user.get("username")
    else:
        val = getattr(user, "user_id", None) or getattr(user, "username", None)
    if val is None or val == "":
        raise ValueError("User ID is required")
    raw = str(val).strip()
    if not raw:
        raise ValueError("User ID is required")
    if not _USER_ID_PATTERN.match(raw):
        raise ValueError(
            f"Invalid user ID '{raw}': must be 1-128 alphanumeric, underscore, hyphen, dot, @, or | characters"
        )
    return raw


def _validate_universe_id(universe_id: str) -> None:
    """Validate universe ID format (defense-in-depth).

    This intentionally accepts a broader pattern than the manager's
    custom-ID validator (``[a-z0-9_]{1,64}``) because read paths must
    also accept built-in IDs like ``"SP500"`` (uppercase) and potential
    future IDs with dots/hyphens.  The manager enforces the stricter
    pattern at custom-ID generation time.

    Raises:
        ValueError: If universe_id is empty, has invalid characters,
            or contains ``..`` (path traversal).
    """
    if not universe_id or not universe_id.strip():
        raise ValueError("Universe ID is required")
    if not _UNIVERSE_ID_PATTERN.match(universe_id):
        raise ValueError(
            f"Invalid universe ID '{universe_id}': must be 1-128 alphanumeric, "
            "underscore, hyphen, or dot characters"
        )
    if ".." in universe_id:
        raise ValueError(
            f"Invalid universe ID '{universe_id}': must not contain '..'"
        )


class UniverseService:
    """Async universe management service with permission enforcement.

    Args:
        manager: Synchronous universe manager instance.
    """

    def __init__(self, manager: UniverseManager) -> None:
        self._manager = manager

    @property
    def manager(self) -> UniverseManager:
        """The underlying synchronous universe manager."""
        return self._manager

    async def get_universe_list(
        self,
        user: Any,
        as_of_date: date | None = None,
    ) -> list[UniverseListItemDTO]:
        """List all universes with optional symbol counts.

        Requires ``VIEW_UNIVERSES`` permission.
        """
        if not has_permission(user, Permission.VIEW_UNIVERSES):
            raise PermissionError("Permission 'view_universes' required")

        universes = await asyncio.to_thread(self._manager.list_universes)

        can_query_crsp = as_of_date and has_dataset_permission(user, "crsp")

        # Collect unique base IDs that need symbol counts (deduplicated)
        ids_needing_count: set[str] = set()
        for u in universes:
            if u.universe_type == "built_in" and can_query_crsp:
                ids_needing_count.add(u.id)
            elif (
                u.universe_type == "custom"
                and u.manual_symbols is None
                and u.base_universe_id
                and not u.filters
                and not u.exclude_symbols
                and can_query_crsp
            ):
                ids_needing_count.add(u.base_universe_id)

        # Fetch all counts in parallel (degrade per-ID on error)
        count_map: dict[str, int | None] = {}
        if ids_needing_count and as_of_date:
            id_list = sorted(ids_needing_count)
            results = await asyncio.gather(
                *(
                    asyncio.to_thread(
                        self._manager.get_symbol_count, uid, as_of_date
                    )
                    for uid in id_list
                ),
                return_exceptions=True,
            )
            for uid, result in zip(id_list, results, strict=True):
                if isinstance(result, BaseException):
                    logger.warning(
                        "universe_symbol_count_failed",
                        extra={
                            "universe_id": uid,
                            "error": str(result),
                            "user_id": safe_user_id(user),
                        },
                    )
                    count_map[uid] = None
                else:
                    count_map[uid] = result

        items: list[UniverseListItemDTO] = []
        for u in universes:
            symbol_count: int | None = None
            base: str | None = None
            approx = False

            if u.universe_type == "built_in":
                base = "CRSP"
                symbol_count = count_map.get(u.id)
            else:
                base = u.base_universe_id or "Manual List"
                if u.manual_symbols is not None:
                    symbol_count = len(u.manual_symbols)
                    approx = True  # pre-CRSP resolution; some may be unresolved
                elif (
                    u.base_universe_id
                    and not u.filters
                    and not u.exclude_symbols
                ):
                    # Only use base count for unfiltered custom universes;
                    # filtered universes would show misleading base counts.
                    symbol_count = count_map.get(u.base_universe_id)

            items.append(
                UniverseListItemDTO(
                    id=u.id,
                    name=u.name,
                    universe_type=u.universe_type,
                    symbol_count=symbol_count,
                    count_is_approximate=approx,
                    last_updated=(
                        u.created_at.strftime("%Y-%m-%d") if u.created_at else None
                    ),
                    base=base,
                )
            )

        return items

    async def get_universe_detail(
        self,
        user: Any,
        universe_id: str,
        as_of_date: date,
    ) -> UniverseDetailDTO:
        """Get detailed universe view with enriched constituents.

        Requires ``VIEW_UNIVERSES`` and ``dataset:crsp`` permissions.
        """
        if not has_permission(user, Permission.VIEW_UNIVERSES):
            raise PermissionError("Permission 'view_universes' required")

        if not has_dataset_permission(user, "crsp"):
            return UniverseDetailDTO(
                id=universe_id,
                name=universe_id,
                universe_type="unknown",
                crsp_unavailable=True,
                error_message="CRSP data access denied",
            )

        try:
            _validate_universe_id(universe_id)
        except ValueError as e:
            logger.warning(
                "universe_detail_invalid_id",
                extra={
                    "universe_id": universe_id,
                    "error": str(e),
                    "user_id": safe_user_id(user),
                },
            )
            return UniverseDetailDTO(
                id=universe_id,
                name=universe_id,
                universe_type="unknown",
                error_message="Invalid universe ID",
            )

        try:
            enriched_df = await asyncio.to_thread(
                self._manager.get_enriched_constituents,
                universe_id,
                as_of_date,
            )
        except UniverseNotFoundError as e:
            logger.warning(
                "universe_detail_not_found",
                extra={
                    "universe_id": universe_id,
                    "error": str(e),
                    "user_id": safe_user_id(user),
                },
            )
            return UniverseDetailDTO(
                id=universe_id,
                name=universe_id,
                universe_type="unknown",
                error_message=f"Universe '{universe_id}' not found",
            )
        except UniverseCorruptError as e:
            logger.error(
                "universe_detail_corrupt",
                extra={
                    "universe_id": universe_id,
                    "error": str(e),
                    "user_id": safe_user_id(user),
                },
            )
            return UniverseDetailDTO(
                id=universe_id,
                name=universe_id,
                universe_type="unknown",
                error_message="Universe metadata is corrupt or unreadable",
            )
        except ValueError as e:
            # Distinguish retry-exhaustion (concurrent mutation) from generic
            # validation errors so the UI can prompt the user to retry.
            is_retry_exhaustion = "retry later" in str(e).lower()
            logger.warning(
                "universe_detail_error",
                extra={
                    "universe_id": universe_id,
                    "error": str(e),
                    "user_id": safe_user_id(user),
                    "is_retry_exhaustion": is_retry_exhaustion,
                },
            )
            return UniverseDetailDTO(
                id=universe_id,
                name=universe_id,
                universe_type="unknown",
                error_message=(
                    "System busy — please try again"
                    if is_retry_exhaustion
                    else "Universe detail unavailable"
                ),
            )
        except CRSPUnavailableError as e:
            logger.warning(
                "universe_detail_crsp_unavailable",
                extra={
                    "universe_id": universe_id,
                    "error": str(e),
                    "user_id": safe_user_id(user),
                },
            )
            return UniverseDetailDTO(
                id=universe_id,
                name=universe_id,
                universe_type="unknown",
                crsp_unavailable=True,
                error_message="CRSP data is currently unavailable",
            )

        constituents: list[UniverseConstituentDTO] = []
        for row in enriched_df.iter_rows(named=True):
            constituents.append(
                UniverseConstituentDTO(
                    permno=row["permno"],
                    ticker=row.get("ticker"),
                    market_cap=row.get("market_cap"),
                    adv_20d=row.get("adv_20d"),
                )
            )

        # Get universe metadata for detail fields
        try:
            meta = await asyncio.to_thread(
                self._manager.get_universe_metadata, universe_id
            )
        except (UniverseNotFoundError, UniverseCorruptError) as e:
            logger.warning(
                "universe_metadata_fetch_failed",
                extra={
                    "universe_id": universe_id,
                    "error": str(e),
                    "user_id": safe_user_id(user),
                },
            )
            meta = None

        # Get unresolved tickers for manual list universes
        unresolved: list[str] = []
        if meta and meta.manual_symbols is not None:
            try:
                unresolved = await asyncio.to_thread(
                    self._manager.get_unresolved_tickers,
                    universe_id,
                    as_of_date,
                )
            except CRSPUnavailableError:
                logger.warning(
                    "universe_unresolved_crsp_unavailable",
                    extra={
                        "universe_id": universe_id,
                        "user_id": safe_user_id(user),
                    },
                )

        return UniverseDetailDTO(
            id=universe_id,
            name=meta.name if meta else universe_id,
            universe_type=meta.universe_type if meta else "unknown",
            constituents=constituents,
            symbol_count=len(constituents),
            filters_applied=meta.filters if meta else [],
            unresolved_tickers=unresolved,
            as_of_date=as_of_date,
            base_universe_id=meta.base_universe_id if meta else None,
            exclude_symbols=meta.exclude_symbols if meta else [],
        )

    async def preview_filter(
        self,
        user: Any,
        base_universe_id: str,
        filters: list[UniverseFilterDTO],
        as_of_date: date,
        exclude_symbols: list[str] | None = None,
    ) -> int:
        """Preview filter result count without saving.

        Requires ``VIEW_UNIVERSES`` permission.

        Args:
            exclude_symbols: Optional list of tickers to exclude from count.

        Returns:
            Number of constituents matching filters after exclusions.
        """
        if not has_permission(user, Permission.VIEW_UNIVERSES):
            raise PermissionError("Permission 'view_universes' required")

        if not has_dataset_permission(user, "crsp"):
            raise PermissionError("CRSP data access denied")

        _validate_universe_id(base_universe_id)

        enriched = await asyncio.to_thread(
            self._manager.get_enriched_constituents,
            base_universe_id,
            as_of_date,
        )

        if filters:
            enriched = await asyncio.to_thread(
                UniverseManager.apply_filters, enriched, filters
            )

        if exclude_symbols:
            exclude_upper = [s.upper() for s in exclude_symbols]
            enriched = enriched.filter(
                pl.col("ticker").is_null()
                | ~pl.col("ticker").is_in(exclude_upper)
            )

        return enriched.height

    async def create_custom_universe(
        self,
        user: Any,
        definition: CustomUniverseDefinitionDTO,
    ) -> str:
        """Create a custom universe.

        Requires ``MANAGE_UNIVERSES`` permission.

        Returns:
            The created universe ID.

        Raises:
            PermissionError: If user lacks permission.
            ConflictError: If universe ID already exists.
            ValueError: If validation fails.
        """
        if not has_permission(user, Permission.MANAGE_UNIVERSES):
            raise PermissionError("Permission 'manage_universes' required")

        user_id = _get_user_id(user)

        definition_dict = {
            "name": definition.name,
            "base_universe_id": definition.base_universe_id,
            "filters": [
                {"field": f.field, "operator": f.operator, "value": f.value}
                for f in definition.filters
            ],
            "exclude_symbols": definition.exclude_symbols,
            "manual_symbols": definition.manual_symbols,
        }

        universe_id = await asyncio.to_thread(
            self._manager.save_custom,
            definition_dict,
            user_id,
        )

        logger.info(
            "universe_created",
            extra={
                "user_id": user_id,
                "universe_id": universe_id,
                "universe_name": definition.name,
                "base_universe_id": definition.base_universe_id,
                "filter_count": len(definition.filters),
                "manual_symbol_count": len(definition.manual_symbols)
                if definition.manual_symbols
                else 0,
            },
        )

        return universe_id

    async def delete_custom_universe(
        self,
        user: Any,
        universe_id: str,
    ) -> None:
        """Delete a custom universe.

        Requires ``MANAGE_UNIVERSES`` permission. This is an admin-level
        permission that intentionally allows managing any user's universes
        (no per-resource ownership check).
        """
        if not has_permission(user, Permission.MANAGE_UNIVERSES):
            raise PermissionError("Permission 'manage_universes' required")

        _validate_universe_id(universe_id)
        user_id = _get_user_id(user)

        await asyncio.to_thread(
            self._manager.delete_custom,
            universe_id,
        )

        logger.info(
            "universe_deleted",
            extra={
                "user_id": user_id,
                "universe_id": universe_id,
            },
        )

    async def get_universe_analytics(
        self,
        user: Any,
        universe_id: str,
        as_of_date: date,
        *,
        include_distributions: bool = True,
    ) -> UniverseAnalyticsDTO:
        """Compute analytics summary for a universe (P6T15/T15.2).

        Uses real enriched data for market cap and ADV distributions.
        Sector and factor data are mock (v1) with flags set to ``True``.

        Args:
            include_distributions: If False, omit distribution lists for
                lightweight comparison payloads.

        Requires ``VIEW_UNIVERSES`` and ``dataset:crsp`` permissions.
        """
        if not has_permission(user, Permission.VIEW_UNIVERSES):
            raise PermissionError("Permission 'view_universes' required")

        if not has_dataset_permission(user, "crsp"):
            raise PermissionError("CRSP data access denied")

        _log_ctx = {
            "universe_id": universe_id,
            "user_id": safe_user_id(user),
            "as_of": as_of_date.isoformat(),
        }

        try:
            _validate_universe_id(universe_id)
        except ValueError as e:
            logger.warning("universe_analytics_invalid_id", extra={**_log_ctx, "error": str(e)})
            return UniverseAnalyticsDTO(
                universe_id=universe_id,
                symbol_count=0,
                avg_market_cap=0.0,
                median_adv=0.0,
                total_market_cap=0.0,
                error_message="Invalid universe ID",
            )

        try:
            enriched_df = await asyncio.to_thread(
                self._manager.get_enriched_constituents,
                universe_id,
                as_of_date,
            )
        except UniverseNotFoundError as e:
            logger.warning("universe_analytics_not_found", extra={**_log_ctx, "error": str(e)})
            return UniverseAnalyticsDTO(
                universe_id=universe_id,
                symbol_count=0,
                avg_market_cap=0.0,
                median_adv=0.0,
                total_market_cap=0.0,
                error_message=f"Universe '{universe_id}' not found",
            )
        except UniverseCorruptError as e:
            logger.error("universe_analytics_corrupt", extra={**_log_ctx, "error": str(e)})
            return UniverseAnalyticsDTO(
                universe_id=universe_id,
                symbol_count=0,
                avg_market_cap=0.0,
                median_adv=0.0,
                total_market_cap=0.0,
                error_message="Universe metadata is corrupt or unreadable",
            )
        except CRSPUnavailableError as e:
            logger.warning("universe_analytics_crsp_unavailable", extra={**_log_ctx, "error": str(e)})
            return UniverseAnalyticsDTO(
                universe_id=universe_id,
                symbol_count=0,
                avg_market_cap=0.0,
                median_adv=0.0,
                total_market_cap=0.0,
                crsp_unavailable=True,
                error_message="CRSP data is currently unavailable",
            )
        except ValueError as e:
            is_retry_exhaustion = "retry later" in str(e).lower()
            logger.warning("universe_analytics_error", extra={
                **_log_ctx, "error": str(e), "is_retry_exhaustion": is_retry_exhaustion,
            })
            return UniverseAnalyticsDTO(
                universe_id=universe_id,
                symbol_count=0,
                avg_market_cap=0.0,
                median_adv=0.0,
                total_market_cap=0.0,
                error_message=(
                    "System busy — please try again"
                    if is_retry_exhaustion
                    else "Unable to compute analytics"
                ),
            )

        symbol_count = enriched_df.height

        if symbol_count == 0:
            return UniverseAnalyticsDTO(
                universe_id=universe_id,
                symbol_count=0,
                avg_market_cap=0.0,
                median_adv=0.0,
                total_market_cap=0.0,
            )

        mcap_series = enriched_df["market_cap"]
        adv_series = enriched_df["adv_20d"]

        # Pre-filter to finite positive values — used for both aggregates
        # and distributions to keep summary cards consistent with charts
        finite_mcap = mcap_series.filter(
            mcap_series.is_finite() & (mcap_series > 0)
        )
        finite_adv = adv_series.filter(
            adv_series.is_finite() & (adv_series > 0)
        )

        mcap_mean = finite_mcap.mean()
        adv_med = finite_adv.median()
        mcap_sum = finite_mcap.sum()
        avg_market_cap = float(mcap_mean) if mcap_mean is not None else 0.0  # type: ignore[arg-type]
        median_adv = float(adv_med) if adv_med is not None else 0.0  # type: ignore[arg-type]
        total_market_cap = float(mcap_sum) if mcap_sum is not None else 0.0

        valid_mcap: list[float] = []
        valid_adv: list[float] = []
        if include_distributions:
            valid_mcap = finite_mcap.to_list()
            valid_adv = finite_adv.to_list()

        dropped_mcap = symbol_count - len(finite_mcap)
        dropped_adv = symbol_count - len(finite_adv)
        if dropped_mcap > 0:
            logger.info(
                "universe_analytics_mcap_dropped",
                extra={
                    **_log_ctx,
                    "dropped_count": dropped_mcap,
                },
            )
        if dropped_adv > 0:
            logger.info(
                "universe_analytics_adv_dropped",
                extra={
                    **_log_ctx,
                    "dropped_count": dropped_adv,
                },
            )

        return UniverseAnalyticsDTO(
            universe_id=universe_id,
            symbol_count=symbol_count,
            avg_market_cap=avg_market_cap,
            median_adv=median_adv,
            total_market_cap=total_market_cap,
            market_cap_distribution=valid_mcap,
            adv_distribution=valid_adv,
            sector_distribution=dict(_MOCK_SECTOR_WEIGHTS),
            factor_exposure=dict(_MOCK_FACTOR_EXPOSURE),
        )

    async def compare_universes(
        self,
        user: Any,
        universe_a_id: str,
        universe_b_id: str,
        as_of_date: date,
    ) -> UniverseComparisonDTO:
        """Compare two universes side by side (P6T15/T15.2).

        Fetches lightweight analytics (no distributions) for both
        universes in parallel, then computes constituent overlap
        using permno set intersection.  ``overlap_pct`` is the
        percentage of the *smaller* universe that appears in both.

        Requires ``VIEW_UNIVERSES`` and ``dataset:crsp`` permissions.
        """
        if not has_permission(user, Permission.VIEW_UNIVERSES):
            raise PermissionError("Permission 'view_universes' required")

        if not has_dataset_permission(user, "crsp"):
            raise PermissionError("CRSP data access denied")

        _log_ctx = {
            "universe_a": universe_a_id,
            "universe_b": universe_b_id,
            "user_id": safe_user_id(user),
            "as_of": as_of_date.isoformat(),
        }

        try:
            _validate_universe_id(universe_a_id)
            _validate_universe_id(universe_b_id)
        except ValueError as e:
            logger.warning("universe_compare_invalid_id", extra={**_log_ctx, "error": str(e)})
            _err_msg = "Invalid universe ID"
            empty_a = UniverseAnalyticsDTO(
                universe_id=universe_a_id, symbol_count=0,
                avg_market_cap=0.0, median_adv=0.0, total_market_cap=0.0,
                error_message=_err_msg,
            )
            empty_b = UniverseAnalyticsDTO(
                universe_id=universe_b_id, symbol_count=0,
                avg_market_cap=0.0, median_adv=0.0, total_market_cap=0.0,
                error_message=_err_msg,
            )
            return UniverseComparisonDTO(
                universe_a_stats=empty_a, universe_b_stats=empty_b,
                overlap_count=0, overlap_pct=0.0,
                error_message=_err_msg,
            )

        # Fetch lightweight analytics for both (parallel, skip distributions).
        # Analytics and overlap both read via get_enriched_constituents which
        # is cached by (universe_id, as_of_date) in UniverseManager, so the
        # overlap phase reads the same snapshot — no cross-phase drift risk.
        stats_a, stats_b = await asyncio.gather(
            self.get_universe_analytics(
                user, universe_a_id, as_of_date, include_distributions=False,
            ),
            self.get_universe_analytics(
                user, universe_b_id, as_of_date, include_distributions=False,
            ),
        )

        # If either analytics has an error, return early with the error
        if stats_a.error_message or stats_b.error_message:
            err_msgs = [
                m
                for m in (stats_a.error_message, stats_b.error_message)
                if m
            ]
            return UniverseComparisonDTO(
                universe_a_stats=stats_a,
                universe_b_stats=stats_b,
                overlap_count=0,
                overlap_pct=0.0,
                error_message="; ".join(err_msgs),
            )

        # Compute overlap using permnos (cached enriched data — parallel fetch)
        try:
            df_a, df_b = await asyncio.gather(
                asyncio.to_thread(
                    self._manager.get_enriched_constituents,
                    universe_a_id,
                    as_of_date,
                ),
                asyncio.to_thread(
                    self._manager.get_enriched_constituents,
                    universe_b_id,
                    as_of_date,
                ),
            )
        except UniverseNotFoundError as e:
            logger.warning("universe_compare_overlap_failed", extra={**_log_ctx, "error": str(e)})
            return UniverseComparisonDTO(
                universe_a_stats=stats_a,
                universe_b_stats=stats_b,
                overlap_count=0,
                overlap_pct=0.0,
                error_message="One or both universes not found",
            )
        except (UniverseCorruptError, CRSPUnavailableError, ValueError) as e:
            logger.warning("universe_compare_overlap_failed", extra={**_log_ctx, "error": str(e)})
            return UniverseComparisonDTO(
                universe_a_stats=stats_a,
                universe_b_stats=stats_b,
                overlap_count=0,
                overlap_pct=0.0,
                error_message="Overlap computation unavailable",
            )

        permnos_a = set(df_a["permno"].to_list()) if not df_a.is_empty() else set()
        permnos_b = set(df_b["permno"].to_list()) if not df_b.is_empty() else set()

        overlap_count = len(permnos_a & permnos_b)
        smaller_size = min(len(permnos_a), len(permnos_b))
        overlap_pct = (
            (overlap_count / smaller_size * 100.0) if smaller_size > 0 else 0.0
        )

        return UniverseComparisonDTO(
            universe_a_stats=stats_a,
            universe_b_stats=stats_b,
            overlap_count=overlap_count,
            overlap_pct=round(overlap_pct, 1),
        )


__all__ = [
    "UniverseService",
    "safe_user_id",
]
