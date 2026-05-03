"""Tests for data provider registry provenance helpers."""

from __future__ import annotations

import pytest

from libs.data.data_providers.registry import (
    ProviderType,
    get_provider_spec,
    provider_ids_for_roles,
)


def test_alpaca_sip_registry_defaults_to_raw_adjustment() -> None:
    assert get_provider_spec(ProviderType.ALPACA_SIP).default_adjustment_mode == "raw"
    assert (
        get_provider_spec(ProviderType.HYBRID_CRSP_UNIVERSE_SIP_PRICES).default_adjustment_mode
        == "raw"
    )


def test_provider_ids_for_roles_non_strict_unknown_source_returns_unknown() -> None:
    provider_ids = provider_ids_for_roles(
        {
            "universe_source": "explicit_symbols",
            "price_source": "alpaca_sip_typo",
            "corp_actions_source": "alpaca_sip",
        }
    )

    assert provider_ids == {
        "universe_source": "explicit_symbols",
        "price_source": "unknown",
        "corp_actions_source": "alpaca_sip",
    }


def test_provider_ids_for_roles_strict_rejects_unknown_source() -> None:
    with pytest.raises(ValueError, match="Unknown data role source"):
        provider_ids_for_roles(
            {
                "universe_source": "explicit_symbols",
                "price_source": "alpaca_sip_typo",
                "corp_actions_source": "alpaca_sip",
            },
            strict=True,
        )


def test_provider_ids_for_roles_preserves_partial_public_contract() -> None:
    provider_ids = provider_ids_for_roles({"price_source": "alpaca_sip"})

    assert provider_ids == {
        "universe_source": "unknown",
        "price_source": "alpaca_sip",
        "corp_actions_source": "unknown",
    }


def test_provider_ids_for_roles_strict_rejects_missing_source() -> None:
    with pytest.raises(ValueError, match="Missing data role source"):
        provider_ids_for_roles({"price_source": "alpaca_sip"}, strict=True)
