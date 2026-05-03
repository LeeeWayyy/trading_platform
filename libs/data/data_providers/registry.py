"""Provider identity, capability, and reproducibility registry.

This module is the single source of truth for historical data-provider IDs used
by fetchers, backtest jobs, and UI display code. It intentionally stores
capabilities separately from adapter implementation so callers can make routing
decisions without duplicating provider strings.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import date
from enum import Enum
from typing import Any

from pydantic import BaseModel


class ProviderType(str, Enum):
    """Historical data provider identifiers."""

    YFINANCE = "yfinance"
    CRSP = "crsp"
    ALPACA_SIP = "alpaca_sip"
    HYBRID_CRSP_UNIVERSE_SIP_PRICES = "hybrid_crsp_universe_sip_prices"
    AUTO = "auto"

    @classmethod
    def from_string(cls, value: str, *, allow_auto: bool = True) -> ProviderType:
        """Parse a provider ID with consistent validation."""
        normalized = value.lower().strip()
        try:
            provider = cls(normalized)
        except ValueError as exc:
            valid = [p.value for p in cls if allow_auto or p is not cls.AUTO]
            raise ValueError(f"Invalid data provider: '{value}'. Must be one of: {valid}") from exc

        if provider is cls.AUTO and not allow_auto:
            valid = [p.value for p in cls if p is not cls.AUTO]
            raise ValueError(f"Invalid data provider: '{value}'. Must be one of: {valid}")

        return provider


class ProviderCapabilities(BaseModel, frozen=True):
    """Granular provider capabilities used by routing and reporting."""

    supports_pit_universe: bool
    supports_active_universe: bool
    supports_corp_actions: bool
    supports_intraday_bars: bool
    production_feed_parity: bool
    survivorship_safe: bool
    history_start: date | None


class ProviderSpec(BaseModel, frozen=True):
    """Registry metadata for one provider."""

    provider_id: ProviderType
    display_name: str
    description: str
    provider_version: str
    capabilities: ProviderCapabilities
    production_allowed: bool
    simple_backtest: bool
    requires_explicit_universe: bool
    source_feed: str | None = None
    default_adjustment_mode: str | None = None


_PROVIDER_SPECS: dict[ProviderType, ProviderSpec] = {
    ProviderType.CRSP: ProviderSpec(
        provider_id=ProviderType.CRSP,
        display_name="CRSP (production)",
        description="Point-in-time CRSP data for survivorship-aware research.",
        provider_version="1.0",
        capabilities=ProviderCapabilities(
            supports_pit_universe=True,
            supports_active_universe=False,
            supports_corp_actions=True,
            supports_intraday_bars=False,
            production_feed_parity=False,
            survivorship_safe=True,
            history_start=date(1925, 1, 1),
        ),
        production_allowed=True,
        simple_backtest=False,
        requires_explicit_universe=False,
        source_feed="crsp",
        default_adjustment_mode="crsp_ret",
    ),
    ProviderType.YFINANCE: ProviderSpec(
        provider_id=ProviderType.YFINANCE,
        display_name="Yahoo Finance (dev only)",
        description="Development-only cached yfinance data. Not PIT safe.",
        provider_version="1.0",
        capabilities=ProviderCapabilities(
            supports_pit_universe=False,
            supports_active_universe=False,
            supports_corp_actions=False,
            supports_intraday_bars=False,
            production_feed_parity=False,
            survivorship_safe=False,
            history_start=None,
        ),
        production_allowed=False,
        simple_backtest=True,
        requires_explicit_universe=True,
        source_feed="yfinance",
        default_adjustment_mode="provider_adjusted",
    ),
    ProviderType.ALPACA_SIP: ProviderSpec(
        provider_id=ProviderType.ALPACA_SIP,
        display_name="Alpaca SIP (local, non-PIT)",
        description="Local Alpaca SIP bars for execution-feed-parity research.",
        provider_version="1.0",
        capabilities=ProviderCapabilities(
            supports_pit_universe=False,
            supports_active_universe=False,
            supports_corp_actions=True,
            supports_intraday_bars=True,
            production_feed_parity=True,
            survivorship_safe=False,
            history_start=date(2016, 1, 1),
        ),
        production_allowed=False,
        simple_backtest=True,
        requires_explicit_universe=True,
        source_feed="sip",
        default_adjustment_mode="raw",
    ),
    ProviderType.HYBRID_CRSP_UNIVERSE_SIP_PRICES: ProviderSpec(
        provider_id=ProviderType.HYBRID_CRSP_UNIVERSE_SIP_PRICES,
        display_name="Hybrid CRSP Universe + Alpaca SIP Prices (research)",
        description="CRSP universe with local Alpaca SIP prices; research only.",
        provider_version="1.0",
        capabilities=ProviderCapabilities(
            supports_pit_universe=True,
            supports_active_universe=False,
            supports_corp_actions=True,
            supports_intraday_bars=True,
            production_feed_parity=True,
            survivorship_safe=False,
            history_start=date(2016, 1, 1),
        ),
        production_allowed=False,
        simple_backtest=True,
        requires_explicit_universe=False,
        source_feed="sip",
        default_adjustment_mode="raw",
    ),
}


def get_provider_spec(provider: ProviderType | str) -> ProviderSpec:
    """Return the registered spec for a provider."""
    provider_type = (
        provider if isinstance(provider, ProviderType) else ProviderType.from_string(provider)
    )
    if provider_type is ProviderType.AUTO:
        raise ValueError("AUTO has no concrete provider spec")
    return _PROVIDER_SPECS[provider_type]


def all_provider_specs() -> tuple[ProviderSpec, ...]:
    """Return concrete provider specs in stable UI order."""
    return tuple(_PROVIDER_SPECS[p] for p in _PROVIDER_SPECS)


def backtest_provider_specs() -> tuple[ProviderSpec, ...]:
    """Return providers selectable by legacy backtest-provider field."""
    return all_provider_specs()


def provider_display_map() -> dict[str, str]:
    """Map provider IDs to display labels for UI/config editors."""
    return provider_display_map_with_options()


def provider_display_inverse_map() -> dict[str, str]:
    """Map display labels back to provider IDs."""
    return {v: k for k, v in provider_display_map().items()}


def provider_display_map_with_options(*, include_auto: bool = False) -> dict[str, str]:
    """Map provider IDs to display labels, optionally including role-resolved AUTO."""
    providers = {spec.provider_id.value: spec.display_name for spec in backtest_provider_specs()}
    if include_auto:
        providers[ProviderType.AUTO.value] = "Auto (role-resolved)"
    return providers


def provider_display_inverse_map_with_options(*, include_auto: bool = False) -> dict[str, str]:
    """Map display labels back to provider IDs with optional AUTO support."""
    return {v: k for k, v in provider_display_map_with_options(include_auto=include_auto).items()}


def provider_ids_for_roles(roles: Mapping[str, str], *, strict: bool = False) -> dict[str, str]:
    """Convert resolved data roles to provider/source IDs for signatures."""
    role_to_id = {
        "universe_source": _role_source_to_provider_id(
            roles.get("universe_source", ""), strict=strict
        ),
        "price_source": _role_source_to_provider_id(roles.get("price_source", ""), strict=strict),
        "corp_actions_source": _role_source_to_provider_id(
            roles.get("corp_actions_source", ""), strict=strict
        ),
    }
    return role_to_id


def provider_versions_for_ids(provider_ids: Mapping[str, str]) -> dict[str, str]:
    """Return semantic provider versions for concrete provider IDs."""
    versions: dict[str, str] = {}
    for provider_id in sorted(set(provider_ids.values())):
        if provider_id in {"explicit_symbols", "none", "unknown"}:
            versions[provider_id] = "N/A"
            continue
        provider = ProviderType.from_string(provider_id, allow_auto=False)
        versions[provider_id] = get_provider_spec(provider).provider_version
    return versions


def source_feeds_for_provider_ids(provider_ids: Mapping[str, str]) -> dict[str, str]:
    """Return source-feed metadata for role provider IDs."""
    feeds: dict[str, str] = {}
    for role, provider_id in provider_ids.items():
        if provider_id in {"explicit_symbols", "none", "unknown"}:
            feeds[role] = provider_id
            continue
        provider = ProviderType.from_string(provider_id, allow_auto=False)
        feeds[role] = get_provider_spec(provider).source_feed or provider_id
    return feeds


def _role_source_to_provider_id(source: str, *, strict: bool) -> str:
    normalized = source.strip()
    if not normalized:
        if not strict:
            return "unknown"
        raise ValueError("Missing data role source in provider provenance")
    if normalized in {"crsp", "alpaca_sip", "yfinance"}:
        return normalized
    if normalized == "alpaca_sip_active_assets":
        return ProviderType.ALPACA_SIP.value
    if normalized in {"explicit_symbols", "none"}:
        return normalized
    if not strict:
        return "unknown"
    raise ValueError(f"Unknown data role source in provider provenance: {normalized}")


def build_manifest_id(dataset: str, manifest_version: int | str, checksum: str) -> str:
    """Build the stable manifest ID used in signatures and reports."""
    return f"{dataset}@v{manifest_version}:{checksum}"


def compute_symbol_set_hash(symbols: list[str] | tuple[str, ...]) -> str:
    """Hash a normalized symbol set deterministically."""
    normalized = sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def compute_data_signature(payload: dict[str, Any]) -> str:
    """Hash canonical JSON for reproducible backtest data provenance."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
