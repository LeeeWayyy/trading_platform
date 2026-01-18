"""Unit tests for libs.web_console_services.tax_lot_service."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from libs.platform.web_console_auth.permissions import Permission
from libs.web_console_services.tax_lot_service import TaxLotService, _to_decimal


@pytest.fixture()
def mock_db_pool() -> Mock:
    return Mock()


@pytest.fixture()
def viewer_user() -> dict[str, Any]:
    return {"user_id": "viewer-1", "role": "viewer"}


def _mock_acquire_connection(mock_conn: AsyncMock) -> AsyncMock:
    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_conn
    mock_cm.__aexit__.return_value = None
    return mock_cm


def _mock_cursor_conn(mock_cursor: AsyncMock) -> AsyncMock:
    mock_conn = AsyncMock()
    mock_conn.cursor.return_value.__aenter__.return_value = mock_cursor
    mock_conn.cursor.return_value.__aexit__.return_value = None
    return mock_conn


class TestToDecimal:
    def test_to_decimal_invalid(self) -> None:
        with pytest.raises(ValueError, match="Invalid decimal value"):
            _to_decimal("not-a-number")


class TestListLots:
    @pytest.mark.asyncio()
    async def test_list_lots_invalid_limit(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any]
    ) -> None:
        service = TaxLotService(db_pool=mock_db_pool, user=viewer_user)

        with patch(
            "libs.web_console_services.tax_lot_service.has_permission",
            return_value=True,
        ):
            with pytest.raises(ValueError, match="limit must be between 1 and 500"):
                await service.list_lots(limit=1000)

    @pytest.mark.asyncio()
    async def test_list_lots_requires_user_context(self, mock_db_pool: Mock) -> None:
        service = TaxLotService(db_pool=mock_db_pool, user={"role": "viewer"})

        with patch(
            "libs.web_console_services.tax_lot_service.has_permission",
            return_value=True,
        ):
            with pytest.raises(PermissionError, match="User context required"):
                await service.list_lots()

    @pytest.mark.asyncio()
    async def test_list_lots_requires_manage_for_all_users(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any]
    ) -> None:
        service = TaxLotService(db_pool=mock_db_pool, user=viewer_user)

        def _perm(user: dict[str, Any], permission: Permission) -> bool:
            return permission == Permission.VIEW_TAX_LOTS

        with patch(
            "libs.web_console_services.tax_lot_service.has_permission",
            side_effect=_perm,
        ):
            with pytest.raises(PermissionError, match="manage_tax_lots"):
                await service.list_lots(all_users=True)

    @pytest.mark.asyncio()
    async def test_list_lots_returns_rows(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any]
    ) -> None:
        service = TaxLotService(db_pool=mock_db_pool, user=viewer_user)

        row = {
            "id": "lot-1",
            "symbol": "AAPL",
            "quantity": Decimal("10"),
            "total_cost": Decimal("100"),
            "acquired_at": datetime(2024, 1, 1, tzinfo=UTC),
            "remaining_quantity": Decimal("10"),
            "closed_at": None,
        }
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=[row])
        mock_conn = _mock_cursor_conn(mock_cursor)

        with patch(
            "libs.web_console_services.tax_lot_service.has_permission",
            return_value=True,
        ), patch(
            "libs.web_console_services.tax_lot_service.acquire_connection",
            return_value=_mock_acquire_connection(mock_conn),
        ):
            lots = await service.list_lots()

        assert len(lots) == 1
        assert lots[0].lot_id == "lot-1"
        assert lots[0].status == "open"


class TestCreateLot:
    @pytest.mark.asyncio()
    async def test_create_lot_requires_user_context(self, mock_db_pool: Mock) -> None:
        service = TaxLotService(db_pool=mock_db_pool, user={"role": "admin"})

        with patch(
            "libs.web_console_services.tax_lot_service.has_permission",
            return_value=True,
        ):
            with pytest.raises(PermissionError, match="User context required"):
                await service.create_lot(
                    symbol="AAPL",
                    quantity=1,
                    cost_basis=10,
                    acquisition_date=datetime.now(UTC),
                    strategy_id=None,
                    status="open",
                )

    @pytest.mark.asyncio()
    async def test_create_lot_invalid_acquisition_date(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any]
    ) -> None:
        service = TaxLotService(db_pool=mock_db_pool, user=viewer_user)

        with patch(
            "libs.web_console_services.tax_lot_service.has_permission",
            return_value=True,
        ):
            with pytest.raises(ValueError, match="acquisition_date must be datetime"):
                await service.create_lot(
                    symbol="AAPL",
                    quantity=1,
                    cost_basis=10,
                    acquisition_date="2024-01-01",  # type: ignore[arg-type]
                    strategy_id=None,
                    status="open",
                )

    @pytest.mark.asyncio()
    async def test_create_lot_closed_sets_status(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any]
    ) -> None:
        service = TaxLotService(db_pool=mock_db_pool, user=viewer_user)

        now = datetime(2024, 1, 1, tzinfo=UTC)
        row = {
            "id": "lot-1",
            "symbol": "AAPL",
            "quantity": Decimal("10"),
            "total_cost": Decimal("100"),
            "acquired_at": now,
            "remaining_quantity": Decimal("0"),
            "closed_at": datetime(2024, 1, 2, tzinfo=UTC),
        }
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=row)
        mock_conn = _mock_cursor_conn(mock_cursor)

        with patch(
            "libs.web_console_services.tax_lot_service.has_permission",
            return_value=True,
        ), patch(
            "libs.web_console_services.tax_lot_service.acquire_connection",
            return_value=_mock_acquire_connection(mock_conn),
        ):
            lot = await service.create_lot(
                symbol="AAPL",
                quantity=10,
                cost_basis=100,
                acquisition_date=now,
                strategy_id=None,
                status="closed",
            )

        assert lot.status == "closed"
        assert lot.quantity == Decimal("10")


class TestUpdateLot:
    @pytest.mark.asyncio()
    async def test_update_lot_no_valid_fields(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any]
    ) -> None:
        service = TaxLotService(db_pool=mock_db_pool, user=viewer_user)

        with patch(
            "libs.web_console_services.tax_lot_service.has_permission",
            return_value=True,
        ):
            with pytest.raises(ValueError, match="No valid fields provided"):
                await service.update_lot("lot-1", updates={"invalid": "x"})

    @pytest.mark.asyncio()
    async def test_update_lot_not_found(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any]
    ) -> None:
        service = TaxLotService(db_pool=mock_db_pool, user=viewer_user)

        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=None)
        mock_conn = _mock_cursor_conn(mock_cursor)

        with patch(
            "libs.web_console_services.tax_lot_service.has_permission",
            return_value=True,
        ), patch(
            "libs.web_console_services.tax_lot_service.acquire_connection",
            return_value=_mock_acquire_connection(mock_conn),
        ):
            with pytest.raises(ValueError, match="not found"):
                await service.update_lot("lot-1", updates={"symbol": "MSFT"})

    @pytest.mark.asyncio()
    async def test_update_lot_invalid_status(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any]
    ) -> None:
        service = TaxLotService(db_pool=mock_db_pool, user=viewer_user)

        row = {
            "id": "lot-1",
            "symbol": "AAPL",
            "quantity": Decimal("10"),
            "total_cost": Decimal("100"),
            "acquired_at": datetime(2024, 1, 1, tzinfo=UTC),
            "remaining_quantity": Decimal("10"),
            "closed_at": None,
        }
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=row)
        mock_conn = _mock_cursor_conn(mock_cursor)

        with patch(
            "libs.web_console_services.tax_lot_service.has_permission",
            return_value=True,
        ), patch(
            "libs.web_console_services.tax_lot_service.acquire_connection",
            return_value=_mock_acquire_connection(mock_conn),
        ):
            with pytest.raises(ValueError, match="status must be 'open' or 'closed'"):
                await service.update_lot("lot-1", updates={"status": "bad"})


class TestCloseLot:
    @pytest.mark.asyncio()
    async def test_close_lot_returns_none_when_missing(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any]
    ) -> None:
        service = TaxLotService(db_pool=mock_db_pool, user=viewer_user)

        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=None)
        mock_conn = _mock_cursor_conn(mock_cursor)

        with patch(
            "libs.web_console_services.tax_lot_service.has_permission",
            return_value=True,
        ), patch(
            "libs.web_console_services.tax_lot_service.acquire_connection",
            return_value=_mock_acquire_connection(mock_conn),
        ):
            result = await service.close_lot("lot-1")

        assert result is None


class TestRowToLot:
    def test_row_to_lot_derives_status_closed(self, mock_db_pool: Mock) -> None:
        service = TaxLotService(db_pool=mock_db_pool, user={"role": "viewer"})
        row = {
            "id": "lot-1",
            "symbol": "AAPL",
            "quantity": Decimal("10"),
            "total_cost": Decimal("100"),
            "acquired_at": datetime(2024, 1, 1, tzinfo=UTC),
            "remaining_quantity": Decimal("0"),
            "closed_at": None,
        }
        lot = service._row_to_lot(row)
        assert lot.status == "closed"

    def test_row_to_lot_strategy_override(self, mock_db_pool: Mock) -> None:
        service = TaxLotService(db_pool=mock_db_pool, user={"role": "viewer"})
        row = {
            "id": "lot-1",
            "symbol": "AAPL",
            "quantity": Decimal("10"),
            "total_cost": Decimal("100"),
            "acquired_at": datetime(2024, 1, 1, tzinfo=UTC),
            "remaining_quantity": Decimal("10"),
            "closed_at": None,
        }
        lot = service._row_to_lot(row, strategy_override="strat-1")
        assert lot.strategy_id == "strat-1"
