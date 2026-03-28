"""Tests for UniverseService (P6T15/T15.1)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import polars as pl
import pytest

from libs.data.data_providers.universe import CRSPUnavailableError
from libs.data.universe_manager import ConflictError, UniverseManager
from libs.web_console_services.schemas.universe import (
    CustomUniverseDefinitionDTO,
    UniverseFilterDTO,
)
from libs.web_console_services.universe_service import UniverseService


@pytest.fixture()
def tmp_universes_dir(tmp_path: Path) -> Path:
    d = tmp_path / "universes"
    d.mkdir()
    return d


@pytest.fixture()
def mock_manager(tmp_universes_dir: Path) -> UniverseManager:
    """Create UniverseManager with mocked providers."""
    provider = MagicMock()
    provider.get_constituents.return_value = pl.DataFrame(
        {"permno": [10001, 10002, 10003]}, schema={"permno": pl.Int64}
    )

    crsp = MagicMock()
    crsp.get_daily_prices.return_value = pl.DataFrame(
        {
            "date": [date(2026, 1, 10)] * 3,
            "permno": [10001, 10002, 10003],
            "ticker": ["AAPL", "MSFT", "GOOGL"],
            "prc": [200.0, 150.0, 180.0],
            "vol": [50_000_000.0, 30_000_000.0, 20_000_000.0],
            "shrout": [15_000.0, 7_500.0, 12_000.0],
        }
    )

    return UniverseManager(
        universes_dir=tmp_universes_dir,
        universe_provider=provider,
        crsp_provider=crsp,
    )


@pytest.fixture()
def service(mock_manager: UniverseManager) -> UniverseService:
    return UniverseService(mock_manager)


def _admin_user() -> dict[str, Any]:
    return {"role": "admin", "user_id": "admin-1", "strategies": []}


def _researcher_user() -> dict[str, Any]:
    return {"role": "researcher", "user_id": "researcher-1", "strategies": []}


def _operator_user() -> dict[str, Any]:
    return {"role": "operator", "user_id": "operator-1", "strategies": []}


def _viewer_user() -> dict[str, Any]:
    return {"role": "viewer", "user_id": "viewer-1", "strategies": []}


@pytest.mark.unit()
class TestGetUniverseList:
    """Tests for listing universes."""

    @pytest.mark.asyncio()
    async def test_admin_sees_all(self, service: UniverseService) -> None:
        result = await service.get_universe_list(
            _admin_user(), as_of_date=date(2026, 1, 10)
        )
        ids = [u.id for u in result]
        assert "SP500" in ids
        assert "R1000" in ids

    @pytest.mark.asyncio()
    async def test_researcher_can_view(self, service: UniverseService) -> None:
        result = await service.get_universe_list(
            _researcher_user(), as_of_date=date(2026, 1, 10)
        )
        assert len(result) >= 2

    @pytest.mark.asyncio()
    async def test_viewer_allowed_single_admin(self, service: UniverseService) -> None:
        """P6T19: Viewer can list universes — single-admin model."""
        result = await service.get_universe_list(_viewer_user())
        assert isinstance(result, list)

    @pytest.mark.asyncio()
    async def test_list_with_symbol_count(
        self, service: UniverseService
    ) -> None:
        result = await service.get_universe_list(
            _admin_user(), as_of_date=date(2026, 1, 10)
        )
        sp500 = next(u for u in result if u.id == "SP500")
        assert sp500.symbol_count == 3  # From mock provider
        assert sp500.count_is_approximate is False

    @pytest.mark.asyncio()
    async def test_manual_universe_count_is_approximate(
        self, service: UniverseService
    ) -> None:
        """Manual universes show pre-CRSP-resolution count as approximate."""
        await service.create_custom_universe(
            _operator_user(),
            CustomUniverseDefinitionDTO(
                name="Manual Approx",
                manual_symbols=["AAPL", "MSFT", "INVALID"],
            ),
        )
        result = await service.get_universe_list(
            _admin_user(), as_of_date=date(2026, 1, 10)
        )
        manual = next(u for u in result if u.id == "manual_approx")
        assert manual.symbol_count == 3
        assert manual.count_is_approximate is True


@pytest.mark.unit()
class TestGetUniverseDetail:
    """Tests for universe detail retrieval."""

    @pytest.mark.asyncio()
    async def test_detail_returns_constituents(
        self, service: UniverseService
    ) -> None:
        result = await service.get_universe_detail(
            _admin_user(), "SP500", date(2026, 1, 10)
        )
        assert result.symbol_count == 3
        assert len(result.constituents) == 3
        assert result.constituents[0].ticker is not None

    @pytest.mark.asyncio()
    async def test_detail_viewer_allowed_single_admin(
        self, service: UniverseService
    ) -> None:
        """P6T19: Viewer can get universe detail — single-admin model."""
        result = await service.get_universe_detail(
            _viewer_user(), "SP500", date(2026, 1, 10)
        )
        assert result is not None

    @pytest.mark.asyncio()
    async def test_detail_crsp_unavailable(
        self,
        service: UniverseService,
        mock_manager: UniverseManager,
    ) -> None:
        # Make provider raise
        mock_manager._universe_provider.get_constituents.side_effect = (
            CRSPUnavailableError("test")
        )
        result = await service.get_universe_detail(
            _admin_user(), "SP500", date(2026, 1, 10)
        )
        assert result.crsp_unavailable is True
        assert result.error_message == "CRSP data is currently unavailable"

    @pytest.mark.asyncio()
    async def test_detail_degrades_on_unresolved_crsp_failure(
        self,
        service: UniverseService,
        mock_manager: UniverseManager,
        tmp_universes_dir: Path,
    ) -> None:
        """If get_unresolved_tickers raises CRSPUnavailableError, detail degrades gracefully."""
        import json

        # Create manual list universe
        (tmp_universes_dir / "manual_test.json").write_text(
            json.dumps({
                "id": "manual_test",
                "name": "Manual Test",
                "manual_symbols": ["AAPL"],
                "filters": [],
                "exclude_symbols": [],
            })
        )
        # Make ticker resolution work but unresolved lookup fail
        mock_manager._crsp_provider.ticker_to_permno.return_value = 10001
        original_get_unresolved = mock_manager.get_unresolved_tickers
        mock_manager.get_unresolved_tickers = MagicMock(
            side_effect=CRSPUnavailableError("manifest changed")
        )
        try:
            result = await service.get_universe_detail(
                _admin_user(), "manual_test", date(2026, 1, 10)
            )
            # Should NOT raise — degrades gracefully
            assert result.unresolved_tickers == []
            assert result.error_message is None
        finally:
            mock_manager.get_unresolved_tickers = original_get_unresolved


@pytest.mark.unit()
class TestPreviewFilter:
    """Tests for filter preview."""

    @pytest.mark.asyncio()
    async def test_preview_returns_count(
        self, service: UniverseService
    ) -> None:
        count = await service.preview_filter(
            _admin_user(),
            "SP500",
            [],
            date(2026, 1, 10),
        )
        assert count == 3

    @pytest.mark.asyncio()
    async def test_preview_with_filters(
        self, service: UniverseService
    ) -> None:
        count = await service.preview_filter(
            _admin_user(),
            "SP500",
            [UniverseFilterDTO(field="market_cap", operator="gt", value=2_000_000)],
            date(2026, 1, 10),
        )
        assert count < 3

    @pytest.mark.asyncio()
    async def test_preview_viewer_allowed_single_admin(
        self, service: UniverseService
    ) -> None:
        """P6T19: Viewer can preview filters — single-admin model."""
        result = await service.preview_filter(
            _viewer_user(), "SP500", [], date(2026, 1, 10)
        )
        assert result is not None


@pytest.mark.unit()
class TestCreateCustom:
    """Tests for custom universe creation."""

    @pytest.mark.asyncio()
    async def test_create_succeeds(
        self, service: UniverseService
    ) -> None:
        uid = await service.create_custom_universe(
            _operator_user(),
            CustomUniverseDefinitionDTO(
                name="Test Custom",
                base_universe_id="SP500",
            ),
        )
        assert uid == "test_custom"

    @pytest.mark.asyncio()
    async def test_create_researcher_allowed_single_admin(
        self, service: UniverseService
    ) -> None:
        """P6T19: Researcher can create universes — single-admin model."""
        uid = await service.create_custom_universe(
            _researcher_user(),
            CustomUniverseDefinitionDTO(
                name="Should Succeed",
                base_universe_id="SP500",
            ),
        )
        assert uid == "should_succeed"

    @pytest.mark.asyncio()
    async def test_create_with_auth0_user_succeeds(
        self, service: UniverseService
    ) -> None:
        """Auth0 user IDs (auth0|12345) must work for universe creation."""
        auth0_user = {"role": "operator", "user_id": "auth0|12345", "strategies": []}
        uid = await service.create_custom_universe(
            auth0_user,
            CustomUniverseDefinitionDTO(
                name="Auth0 Universe",
                base_universe_id="SP500",
            ),
        )
        assert uid == "auth0_universe"

    @pytest.mark.asyncio()
    async def test_create_duplicate_raises_conflict(
        self, service: UniverseService
    ) -> None:
        await service.create_custom_universe(
            _operator_user(),
            CustomUniverseDefinitionDTO(
                name="Dup Test",
                base_universe_id="SP500",
            ),
        )
        with pytest.raises(ConflictError):
            await service.create_custom_universe(
                _operator_user(),
                CustomUniverseDefinitionDTO(
                    name="Dup Test",
                    base_universe_id="SP500",
                ),
            )


@pytest.mark.unit()
class TestDeleteCustom:
    """Tests for custom universe deletion."""

    @pytest.mark.asyncio()
    async def test_delete_succeeds(
        self, service: UniverseService
    ) -> None:
        await service.create_custom_universe(
            _operator_user(),
            CustomUniverseDefinitionDTO(
                name="Delete Me",
                base_universe_id="SP500",
            ),
        )
        await service.delete_custom_universe(_operator_user(), "delete_me")
        # Verify it's gone from listing
        result = await service.get_universe_list(_operator_user())
        ids = [u.id for u in result]
        assert "delete_me" not in ids

    @pytest.mark.asyncio()
    async def test_delete_researcher_allowed_single_admin(
        self, service: UniverseService
    ) -> None:
        """P6T19: Researcher can delete universes — single-admin model."""
        # First create with operator, then delete with researcher
        uid = await service.create_custom_universe(
            _operator_user(),
            CustomUniverseDefinitionDTO(
                name="Delete Me Research",
                base_universe_id="SP500",
            ),
        )
        # Single-admin: researcher can delete (has_permission always True)
        # delete_custom_universe returns None (no exception means success)
        await service.delete_custom_universe(
            _researcher_user(), uid
        )
        # Verify it's actually deleted
        result = await service.get_universe_list(_researcher_user())
        ids = [u.id for u in result]
        assert uid not in ids

    @pytest.mark.asyncio()
    async def test_delete_by_different_operator_succeeds(
        self, service: UniverseService
    ) -> None:
        """MANAGE_UNIVERSES is admin-level — any holder can delete any universe."""
        operator_a = {"role": "operator", "user_id": "op-a", "strategies": []}
        operator_b = {"role": "operator", "user_id": "op-b", "strategies": []}
        await service.create_custom_universe(
            operator_a,
            CustomUniverseDefinitionDTO(
                name="Cross Delete",
                base_universe_id="SP500",
            ),
        )
        # operator_b deletes operator_a's universe — by design
        await service.delete_custom_universe(operator_b, "cross_delete")
        result = await service.get_universe_list(operator_b)
        ids = [u.id for u in result]
        assert "cross_delete" not in ids


@pytest.mark.unit()
class TestTickerNormalization:
    """Tests for ticker format validation and normalization in DTOs."""

    def test_manual_symbols_normalized(self) -> None:
        dto = CustomUniverseDefinitionDTO(
            name="Test",
            manual_symbols=["aapl", " msft ", "GOOGL"],
        )
        assert dto.manual_symbols == ["AAPL", "MSFT", "GOOGL"]

    def test_exclude_symbols_normalized(self) -> None:
        dto = CustomUniverseDefinitionDTO(
            name="Test",
            base_universe_id="SP500",
            exclude_symbols=[" tsla ", "gme"],
        )
        assert dto.exclude_symbols == ["TSLA", "GME"]

    def test_invalid_ticker_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="Invalid ticker format"):
            CustomUniverseDefinitionDTO(
                name="Test",
                manual_symbols=["AAPL", "INVALID!!!TICKER"],
            )

    def test_empty_strings_stripped(self) -> None:
        dto = CustomUniverseDefinitionDTO(
            name="Test",
            manual_symbols=["AAPL", "", " ", "MSFT"],
        )
        assert dto.manual_symbols == ["AAPL", "MSFT"]

    def test_dot_hyphen_tickers_accepted(self) -> None:
        dto = CustomUniverseDefinitionDTO(
            name="Test",
            manual_symbols=["BRK.B", "BF-B"],
        )
        assert dto.manual_symbols == ["BRK.B", "BF-B"]

    def test_duplicate_manual_symbols_deduped(self) -> None:
        dto = CustomUniverseDefinitionDTO(
            name="Test",
            manual_symbols=["AAPL", "MSFT", "aapl", "MSFT"],
        )
        assert dto.manual_symbols == ["AAPL", "MSFT"]

    def test_duplicate_exclude_symbols_deduped(self) -> None:
        dto = CustomUniverseDefinitionDTO(
            name="Test",
            base_universe_id="SP500",
            exclude_symbols=["TSLA", "tsla", "GME"],
        )
        assert dto.exclude_symbols == ["TSLA", "GME"]

    def test_auth0_pipe_user_id_accepted(self) -> None:
        """Auth0 user IDs with pipe character (auth0|12345) must be accepted."""
        from libs.web_console_services.universe_service import _get_user_id

        user = {"user_id": "auth0|12345", "role": "operator"}
        assert _get_user_id(user) == "auth0|12345"

    def test_google_oauth_pipe_user_id_accepted(self) -> None:
        """Google OAuth IDs with pipe (google-oauth2|1234) must be accepted."""
        from libs.web_console_services.universe_service import _get_user_id

        user = {"user_id": "google-oauth2|987654321", "role": "operator"}
        assert _get_user_id(user) == "google-oauth2|987654321"

    def test_string_payload_rejected_for_manual(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="must be a list"):
            CustomUniverseDefinitionDTO(
                name="Test",
                manual_symbols="AAPL",  # type: ignore[arg-type]
            )

    def test_string_payload_rejected_for_exclude(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="must be a list"):
            CustomUniverseDefinitionDTO(
                name="Test",
                base_universe_id="SP500",
                exclude_symbols="TSLA",  # type: ignore[arg-type]
            )
