from __future__ import annotations

import pytest

from libs.data.data_providers.registry import ProviderType
from libs.data.data_providers.role_resolver import DataRoleConfig, resolve_data_roles


def test_auto_resolves_to_sip_with_explicit_symbols_when_crsp_unavailable() -> None:
    roles = resolve_data_roles(
        DataRoleConfig.from_mapping({"requires_pit_universe": False}),
        explicit_symbols=["AAPL", "MSFT"],
        environ={
            "CRSP_AVAILABLE": "false",
            "HISTORICAL_UNIVERSE_SOURCE_DEFAULT": "explicit_symbols",
            "HISTORICAL_PRICE_SOURCE_DEFAULT": "alpaca_sip",
            "HISTORICAL_CORP_ACTIONS_SOURCE_DEFAULT": "alpaca_sip",
        },
    )

    assert roles.universe_source == "explicit_symbols"
    assert roles.price_source == "alpaca_sip"
    assert roles.corp_actions_source == "alpaca_sip"
    assert roles.to_provider_type() == ProviderType.ALPACA_SIP


def test_auto_explicit_symbols_without_symbol_list_fails_loudly() -> None:
    with pytest.raises(ValueError, match="requires an explicit symbol list"):
        resolve_data_roles(
            DataRoleConfig.from_mapping({"requires_pit_universe": False}),
            explicit_symbols=None,
            environ={
                "CRSP_AVAILABLE": "false",
                "HISTORICAL_UNIVERSE_SOURCE_DEFAULT": "explicit_symbols",
                "HISTORICAL_PRICE_SOURCE_DEFAULT": "alpaca_sip",
            },
        )


def test_requires_pit_universe_blocks_non_crsp_universe() -> None:
    with pytest.raises(ValueError, match="requires_pit_universe=true"):
        resolve_data_roles(
            DataRoleConfig.from_mapping({"requires_pit_universe": True}),
            explicit_symbols=["AAPL"],
            environ={
                "CRSP_AVAILABLE": "false",
                "HISTORICAL_UNIVERSE_SOURCE_DEFAULT": "explicit_symbols",
            },
        )


def test_active_assets_universe_is_not_enabled() -> None:
    with pytest.raises(ValueError, match="local Alpaca assets snapshot"):
        resolve_data_roles(
            DataRoleConfig.from_mapping(
                {
                    "universe_source": "alpaca_sip_active_assets",
                    "price_source": "alpaca_sip",
                    "requires_pit_universe": False,
                }
            ),
            explicit_symbols=None,
            environ={"CRSP_AVAILABLE": "false"},
        )


def test_crsp_reentry_flips_universe_by_config() -> None:
    roles = resolve_data_roles(
        DataRoleConfig.from_mapping({}),
        explicit_symbols=None,
        environ={
            "CRSP_AVAILABLE": "true",
            "HISTORICAL_UNIVERSE_SOURCE_DEFAULT": "crsp",
            "HISTORICAL_PRICE_SOURCE_DEFAULT": "alpaca_sip",
            "HISTORICAL_CORP_ACTIONS_SOURCE_DEFAULT": "alpaca_sip",
        },
    )

    assert roles.universe_source == "crsp"
    assert roles.price_source == "alpaca_sip"
    assert roles.to_provider_type() == ProviderType.HYBRID_CRSP_UNIVERSE_SIP_PRICES
