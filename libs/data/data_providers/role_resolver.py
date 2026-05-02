"""Role-based historical data source resolution.

The resolver maps strategy/backtest data roles to concrete providers. It is
deliberately separate from the legacy `provider` field so CRSP can re-enter via
configuration when it becomes available again.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from libs.data.data_providers.registry import ProviderType

UNIVERSE_SOURCES = frozenset({"auto", "crsp", "explicit_symbols", "alpaca_sip_active_assets"})
PRICE_SOURCES = frozenset({"auto", "alpaca_sip", "crsp", "yfinance"})
CORP_ACTIONS_SOURCES = frozenset({"auto", "alpaca_sip", "crsp", "none"})
ADJUSTMENT_MODES = frozenset({"raw", "split", "dividend", "all"})


@dataclass(frozen=True)
class DataRoleConfig:
    """Requested data roles from strategy/backtest config."""

    universe_source: str = "auto"
    price_source: str = "auto"
    corp_actions_source: str = "auto"
    adjustment_mode: str = "raw"
    requires_pit_universe: bool = True

    @classmethod
    def from_mapping(cls, raw: dict[str, Any] | None) -> DataRoleConfig:
        """Build and validate role config from a mapping."""
        data = raw or {}
        config = cls(
            universe_source=str(data.get("universe_source", "auto")).lower().strip(),
            price_source=str(data.get("price_source", "auto")).lower().strip(),
            corp_actions_source=str(data.get("corp_actions_source", "auto")).lower().strip(),
            adjustment_mode=str(data.get("adjustment_mode", "raw")).lower().strip(),
            requires_pit_universe=_parse_bool(
                data.get("requires_pit_universe", True),
                field_name="requires_pit_universe",
            ),
        )
        config.validate()
        return config

    def validate(self) -> None:
        """Validate role source names."""
        _validate_choice("universe_source", self.universe_source, UNIVERSE_SOURCES)
        _validate_choice("price_source", self.price_source, PRICE_SOURCES)
        _validate_choice("corp_actions_source", self.corp_actions_source, CORP_ACTIONS_SOURCES)
        _validate_choice("adjustment_mode", self.adjustment_mode, ADJUSTMENT_MODES)


@dataclass(frozen=True)
class ResolvedDataRoles:
    """Concrete data roles after applying environment defaults."""

    universe_source: str
    price_source: str
    corp_actions_source: str
    adjustment_mode: str
    requires_pit_universe: bool
    explicit_symbol_count: int
    crsp_available: bool

    def to_provider_type(self) -> ProviderType:
        """Bridge resolved roles to the existing legacy backtest provider paths."""
        if self.universe_source == "crsp" and self.price_source == "crsp":
            return ProviderType.CRSP
        if self.universe_source == "explicit_symbols" and self.price_source == "yfinance":
            return ProviderType.YFINANCE
        if self.universe_source == "explicit_symbols" and self.price_source == "alpaca_sip":
            return ProviderType.ALPACA_SIP
        if self.universe_source == "crsp" and self.price_source == "alpaca_sip":
            return ProviderType.HYBRID_CRSP_UNIVERSE_SIP_PRICES
        raise ValueError(
            "Resolved role combination is not supported by current backtest paths: "
            f"universe_source={self.universe_source}, price_source={self.price_source}"
        )

    def to_metadata(self) -> dict[str, str]:
        """Return stable metadata for logs/reports."""
        return {
            "universe_source": self.universe_source,
            "price_source": self.price_source,
            "corp_actions_source": self.corp_actions_source,
            "adjustment_mode": self.adjustment_mode,
            "requires_pit_universe": str(self.requires_pit_universe).lower(),
            "explicit_symbol_count": str(self.explicit_symbol_count),
            "crsp_available": str(self.crsp_available).lower(),
        }


def resolve_data_roles(
    config: DataRoleConfig,
    *,
    explicit_symbols: list[str] | tuple[str, ...] | None,
    environ: Mapping[str, str] | None = None,
) -> ResolvedDataRoles:
    """Resolve `auto` roles using environment defaults and safety gates."""
    env = environ if environ is not None else os.environ
    crsp_available = _env_bool(env.get("CRSP_AVAILABLE"), default=True)
    explicit_symbol_count = len(explicit_symbols or ())

    universe_source = _resolve_auto(
        config.universe_source,
        env_name="HISTORICAL_UNIVERSE_SOURCE_DEFAULT",
        env=env,
        default="crsp" if crsp_available else "explicit_symbols",
    )
    price_source = _resolve_auto(
        config.price_source,
        env_name="HISTORICAL_PRICE_SOURCE_DEFAULT",
        env=env,
        default="crsp" if crsp_available else "alpaca_sip",
    )
    corp_actions_source = _resolve_auto(
        config.corp_actions_source,
        env_name="HISTORICAL_CORP_ACTIONS_SOURCE_DEFAULT",
        env=env,
        default="crsp" if crsp_available else "alpaca_sip",
    )

    _validate_choice("universe_source", universe_source, UNIVERSE_SOURCES - {"auto"})
    _validate_choice("price_source", price_source, PRICE_SOURCES - {"auto"})
    _validate_choice("corp_actions_source", corp_actions_source, CORP_ACTIONS_SOURCES - {"auto"})

    if universe_source == "alpaca_sip_active_assets":
        raise ValueError(
            "alpaca_sip_active_assets is not enabled: local Alpaca assets snapshot support "
            "must exist before active-assets universes can be used."
        )

    if universe_source == "explicit_symbols" and explicit_symbol_count == 0:
        raise ValueError(
            "universe_source=explicit_symbols requires an explicit symbol list. "
            "AUTO must not invent a non-PIT universe."
        )

    if config.requires_pit_universe and universe_source != "crsp":
        raise ValueError(
            "requires_pit_universe=true requires universe_source=crsp. "
            f"Resolved universe_source={universe_source}."
        )

    crsp_roles = [
        role_name
        for role_name, source in (
            ("universe_source", universe_source),
            ("price_source", price_source),
            ("corp_actions_source", corp_actions_source),
        )
        if source == "crsp"
    ]
    if crsp_roles and not crsp_available:
        raise ValueError("CRSP role requested but CRSP_AVAILABLE=false: " + ", ".join(crsp_roles))

    return ResolvedDataRoles(
        universe_source=universe_source,
        price_source=price_source,
        corp_actions_source=corp_actions_source,
        adjustment_mode=config.adjustment_mode,
        requires_pit_universe=config.requires_pit_universe,
        explicit_symbol_count=explicit_symbol_count,
        crsp_available=crsp_available,
    )


def _resolve_auto(
    value: str,
    *,
    env_name: str,
    env: Mapping[str, str],
    default: str,
) -> str:
    if value != "auto":
        return value
    return env.get(env_name, default).lower().strip()


def _validate_choice(name: str, value: str, choices: frozenset[str]) -> None:
    if value not in choices:
        raise ValueError(f"Invalid {name}: '{value}'. Must be one of: {sorted(choices)}")


def _env_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.lower().strip() in {"1", "true", "yes", "y"}


def _parse_bool(value: object, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.lower().strip()
        if normalized in {"1", "true", "yes", "y"}:
            return True
        if normalized in {"0", "false", "no", "n"}:
            return False
    raise ValueError(f"Invalid {field_name}: expected boolean, got {value!r}")
