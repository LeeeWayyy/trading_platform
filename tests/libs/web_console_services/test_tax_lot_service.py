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


class AsyncContextManager:
    """Helper to create proper async context managers for mocking."""

    def __init__(self, return_value: Any) -> None:
        self._return_value = return_value

    async def __aenter__(self) -> Any:
        return self._return_value

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


def _mock_acquire_connection(mock_conn: AsyncMock) -> AsyncContextManager:
    """Create an async context manager that returns mock_conn."""
    return AsyncContextManager(mock_conn)


def _mock_cursor_conn(mock_cursor: AsyncMock) -> Mock:
    """Create a mock connection with proper cursor async context manager."""
    mock_conn = Mock()
    mock_conn.cursor.return_value = AsyncContextManager(mock_cursor)
    mock_conn.commit = AsyncMock()  # conn.commit() is awaited
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

        with (
            patch(
                "libs.web_console_services.tax_lot_service.has_permission",
                return_value=True,
            ),
            patch(
                "libs.web_console_services.tax_lot_service.acquire_connection",
                return_value=_mock_acquire_connection(mock_conn),
            ),
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

        with (
            patch(
                "libs.web_console_services.tax_lot_service.has_permission",
                return_value=True,
            ),
            patch(
                "libs.web_console_services.tax_lot_service.acquire_connection",
                return_value=_mock_acquire_connection(mock_conn),
            ),
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

        with (
            patch(
                "libs.web_console_services.tax_lot_service.has_permission",
                return_value=True,
            ),
            patch(
                "libs.web_console_services.tax_lot_service.acquire_connection",
                return_value=_mock_acquire_connection(mock_conn),
            ),
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

        with (
            patch(
                "libs.web_console_services.tax_lot_service.has_permission",
                return_value=True,
            ),
            patch(
                "libs.web_console_services.tax_lot_service.acquire_connection",
                return_value=_mock_acquire_connection(mock_conn),
            ),
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

        with (
            patch(
                "libs.web_console_services.tax_lot_service.has_permission",
                return_value=True,
            ),
            patch(
                "libs.web_console_services.tax_lot_service.acquire_connection",
                return_value=_mock_acquire_connection(mock_conn),
            ),
        ):
            result = await service.close_lot("lot-1")

        assert result is None


class TestGetLot:
    @pytest.mark.asyncio()
    async def test_get_lot_returns_lot(
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

        with (
            patch(
                "libs.web_console_services.tax_lot_service.has_permission",
                return_value=True,
            ),
            patch(
                "libs.web_console_services.tax_lot_service.acquire_connection",
                return_value=_mock_acquire_connection(mock_conn),
            ),
        ):
            lot = await service.get_lot("lot-1")

        assert lot is not None
        assert lot.lot_id == "lot-1"
        assert lot.symbol == "AAPL"
        assert lot.status == "open"

    @pytest.mark.asyncio()
    async def test_get_lot_returns_none_when_not_found(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any]
    ) -> None:
        service = TaxLotService(db_pool=mock_db_pool, user=viewer_user)

        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=None)
        mock_conn = _mock_cursor_conn(mock_cursor)

        with (
            patch(
                "libs.web_console_services.tax_lot_service.has_permission",
                return_value=True,
            ),
            patch(
                "libs.web_console_services.tax_lot_service.acquire_connection",
                return_value=_mock_acquire_connection(mock_conn),
            ),
        ):
            lot = await service.get_lot("nonexistent")

        assert lot is None

    @pytest.mark.asyncio()
    async def test_get_lot_requires_user_context(self, mock_db_pool: Mock) -> None:
        service = TaxLotService(db_pool=mock_db_pool, user={"role": "viewer"})

        with patch(
            "libs.web_console_services.tax_lot_service.has_permission",
            return_value=True,
        ):
            with pytest.raises(PermissionError, match="User context required"):
                await service.get_lot("lot-1")

    @pytest.mark.asyncio()
    async def test_get_lot_requires_manage_for_different_user(
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
                await service.get_lot("lot-1", user_id="other-user")

    @pytest.mark.asyncio()
    async def test_get_lot_all_users_requires_manage(
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

        with (
            patch(
                "libs.web_console_services.tax_lot_service.has_permission",
                return_value=True,
            ),
            patch(
                "libs.web_console_services.tax_lot_service.acquire_connection",
                return_value=_mock_acquire_connection(mock_conn),
            ),
        ):
            lot = await service.get_lot("lot-1", all_users=True)

        assert lot is not None
        assert lot.lot_id == "lot-1"


class TestListLotsAdditional:
    @pytest.mark.asyncio()
    async def test_list_lots_all_users_path(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any]
    ) -> None:
        """Test list_lots with all_users=True (covers line 90)."""
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

        with (
            patch(
                "libs.web_console_services.tax_lot_service.has_permission",
                return_value=True,
            ),
            patch(
                "libs.web_console_services.tax_lot_service.acquire_connection",
                return_value=_mock_acquire_connection(mock_conn),
            ),
        ):
            lots = await service.list_lots(all_users=True)

        assert len(lots) == 1
        assert lots[0].lot_id == "lot-1"


class TestCreateLotAdditional:
    @pytest.mark.asyncio()
    async def test_create_lot_invalid_status(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any]
    ) -> None:
        """Test create_lot with invalid status (covers line 178)."""
        service = TaxLotService(db_pool=mock_db_pool, user=viewer_user)

        with patch(
            "libs.web_console_services.tax_lot_service.has_permission",
            return_value=True,
        ):
            with pytest.raises(ValueError, match="status must be 'open' or 'closed'"):
                await service.create_lot(
                    symbol="AAPL",
                    quantity=10,
                    cost_basis=100,
                    acquisition_date=datetime.now(UTC),
                    strategy_id=None,
                    status="invalid",
                )

    @pytest.mark.asyncio()
    async def test_create_lot_runtime_error_on_no_return(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any]
    ) -> None:
        """Test create_lot RuntimeError when row is None (covers line 216)."""
        service = TaxLotService(db_pool=mock_db_pool, user=viewer_user)

        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=None)
        mock_conn = _mock_cursor_conn(mock_cursor)

        with (
            patch(
                "libs.web_console_services.tax_lot_service.has_permission",
                return_value=True,
            ),
            patch(
                "libs.web_console_services.tax_lot_service.acquire_connection",
                return_value=_mock_acquire_connection(mock_conn),
            ),
        ):
            with pytest.raises(RuntimeError, match="Tax lot creation failed"):
                await service.create_lot(
                    symbol="AAPL",
                    quantity=10,
                    cost_basis=100,
                    acquisition_date=datetime.now(UTC),
                    strategy_id=None,
                    status="open",
                )


class TestUpdateLotAdditional:
    @pytest.mark.asyncio()
    async def test_update_lot_requires_user_context(self, mock_db_pool: Mock) -> None:
        """Test update_lot requires user context (covers line 253)."""
        service = TaxLotService(db_pool=mock_db_pool, user={"role": "admin"})

        with patch(
            "libs.web_console_services.tax_lot_service.has_permission",
            return_value=True,
        ):
            with pytest.raises(PermissionError, match="User context required"):
                await service.update_lot("lot-1", updates={"symbol": "MSFT"})

    @pytest.mark.asyncio()
    async def test_update_lot_cross_user_access(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any]
    ) -> None:
        """Test update_lot with cross-user access (covers line 247)."""
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
        updated_row = {
            "id": "lot-1",
            "symbol": "MSFT",
            "quantity": Decimal("10"),
            "total_cost": Decimal("100"),
            "acquired_at": datetime(2024, 1, 1, tzinfo=UTC),
            "remaining_quantity": Decimal("10"),
            "closed_at": None,
        }
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(side_effect=[row, updated_row])
        mock_conn = _mock_cursor_conn(mock_cursor)

        with (
            patch(
                "libs.web_console_services.tax_lot_service.has_permission",
                return_value=True,
            ),
            patch(
                "libs.web_console_services.tax_lot_service.acquire_connection",
                return_value=_mock_acquire_connection(mock_conn),
            ),
        ):
            lot = await service.update_lot(
                "lot-1", updates={"symbol": "MSFT"}, user_id="other-user"
            )

        assert lot.symbol == "MSFT"

    @pytest.mark.asyncio()
    async def test_update_lot_all_users_path(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any]
    ) -> None:
        """Test update_lot with all_users=True (covers lines 272, 374-380)."""
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
        updated_row = {
            "id": "lot-1",
            "symbol": "MSFT",
            "quantity": Decimal("10"),
            "total_cost": Decimal("100"),
            "acquired_at": datetime(2024, 1, 1, tzinfo=UTC),
            "remaining_quantity": Decimal("10"),
            "closed_at": None,
        }
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(side_effect=[row, updated_row])
        mock_conn = _mock_cursor_conn(mock_cursor)

        with (
            patch(
                "libs.web_console_services.tax_lot_service.has_permission",
                return_value=True,
            ),
            patch(
                "libs.web_console_services.tax_lot_service.acquire_connection",
                return_value=_mock_acquire_connection(mock_conn),
            ),
        ):
            lot = await service.update_lot("lot-1", updates={"symbol": "MSFT"}, all_users=True)

        assert lot.symbol == "MSFT"

    @pytest.mark.asyncio()
    async def test_update_lot_status_to_closed(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any]
    ) -> None:
        """Test update_lot changing status to closed (covers lines 304-310, 357-361)."""
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
        updated_row = {
            "id": "lot-1",
            "symbol": "AAPL",
            "quantity": Decimal("10"),
            "total_cost": Decimal("100"),
            "acquired_at": datetime(2024, 1, 1, tzinfo=UTC),
            "remaining_quantity": Decimal("0"),
            "closed_at": datetime.now(UTC),
        }
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(side_effect=[row, updated_row])
        mock_conn = _mock_cursor_conn(mock_cursor)

        with (
            patch(
                "libs.web_console_services.tax_lot_service.has_permission",
                return_value=True,
            ),
            patch(
                "libs.web_console_services.tax_lot_service.acquire_connection",
                return_value=_mock_acquire_connection(mock_conn),
            ),
        ):
            lot = await service.update_lot("lot-1", updates={"status": "closed"})

        assert lot.status == "closed"

    @pytest.mark.asyncio()
    async def test_update_lot_status_to_open(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any]
    ) -> None:
        """Test update_lot changing status to open (covers lines 307-309)."""
        service = TaxLotService(db_pool=mock_db_pool, user=viewer_user)

        row = {
            "id": "lot-1",
            "symbol": "AAPL",
            "quantity": Decimal("10"),
            "total_cost": Decimal("100"),
            "acquired_at": datetime(2024, 1, 1, tzinfo=UTC),
            "remaining_quantity": Decimal("0"),
            "closed_at": datetime(2024, 2, 1, tzinfo=UTC),
        }
        updated_row = {
            "id": "lot-1",
            "symbol": "AAPL",
            "quantity": Decimal("10"),
            "total_cost": Decimal("100"),
            "acquired_at": datetime(2024, 1, 1, tzinfo=UTC),
            "remaining_quantity": Decimal("10"),
            "closed_at": None,
        }
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(side_effect=[row, updated_row])
        mock_conn = _mock_cursor_conn(mock_cursor)

        with (
            patch(
                "libs.web_console_services.tax_lot_service.has_permission",
                return_value=True,
            ),
            patch(
                "libs.web_console_services.tax_lot_service.acquire_connection",
                return_value=_mock_acquire_connection(mock_conn),
            ),
        ):
            lot = await service.update_lot("lot-1", updates={"status": "open"})

        assert lot.status == "open"

    @pytest.mark.asyncio()
    async def test_update_lot_quantity_without_cost_basis(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any]
    ) -> None:
        """Test update_lot updating quantity without cost_basis (covers lines 327-337, 350-355)."""
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
        updated_row = {
            "id": "lot-1",
            "symbol": "AAPL",
            "quantity": Decimal("20"),
            "total_cost": Decimal("100"),
            "acquired_at": datetime(2024, 1, 1, tzinfo=UTC),
            "remaining_quantity": Decimal("10"),
            "closed_at": None,
        }
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(side_effect=[row, updated_row])
        mock_conn = _mock_cursor_conn(mock_cursor)

        with (
            patch(
                "libs.web_console_services.tax_lot_service.has_permission",
                return_value=True,
            ),
            patch(
                "libs.web_console_services.tax_lot_service.acquire_connection",
                return_value=_mock_acquire_connection(mock_conn),
            ),
        ):
            lot = await service.update_lot("lot-1", updates={"quantity": Decimal("20")})

        assert lot.quantity == Decimal("20")

    @pytest.mark.asyncio()
    async def test_update_lot_cost_basis(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any]
    ) -> None:
        """Test update_lot updating cost_basis (covers lines 321-322, 338-340)."""
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
        updated_row = {
            "id": "lot-1",
            "symbol": "AAPL",
            "quantity": Decimal("10"),
            "total_cost": Decimal("200"),
            "acquired_at": datetime(2024, 1, 1, tzinfo=UTC),
            "remaining_quantity": Decimal("10"),
            "closed_at": None,
        }
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(side_effect=[row, updated_row])
        mock_conn = _mock_cursor_conn(mock_cursor)

        with (
            patch(
                "libs.web_console_services.tax_lot_service.has_permission",
                return_value=True,
            ),
            patch(
                "libs.web_console_services.tax_lot_service.acquire_connection",
                return_value=_mock_acquire_connection(mock_conn),
            ),
        ):
            lot = await service.update_lot("lot-1", updates={"cost_basis": Decimal("200")})

        assert lot.cost_basis == Decimal("200")

    @pytest.mark.asyncio()
    async def test_update_lot_acquisition_date(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any]
    ) -> None:
        """Test update_lot updating acquisition_date (covers lines 341-347)."""
        service = TaxLotService(db_pool=mock_db_pool, user=viewer_user)

        new_date = datetime(2024, 6, 1, tzinfo=UTC)
        row = {
            "id": "lot-1",
            "symbol": "AAPL",
            "quantity": Decimal("10"),
            "total_cost": Decimal("100"),
            "acquired_at": datetime(2024, 1, 1, tzinfo=UTC),
            "remaining_quantity": Decimal("10"),
            "closed_at": None,
        }
        updated_row = {
            "id": "lot-1",
            "symbol": "AAPL",
            "quantity": Decimal("10"),
            "total_cost": Decimal("100"),
            "acquired_at": new_date,
            "remaining_quantity": Decimal("10"),
            "closed_at": None,
        }
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(side_effect=[row, updated_row])
        mock_conn = _mock_cursor_conn(mock_cursor)

        with (
            patch(
                "libs.web_console_services.tax_lot_service.has_permission",
                return_value=True,
            ),
            patch(
                "libs.web_console_services.tax_lot_service.acquire_connection",
                return_value=_mock_acquire_connection(mock_conn),
            ),
        ):
            lot = await service.update_lot("lot-1", updates={"acquisition_date": new_date})

        assert lot.acquisition_date == new_date

    @pytest.mark.asyncio()
    async def test_update_lot_invalid_acquisition_date_type(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any]
    ) -> None:
        """Test update_lot with invalid acquisition_date type (covers lines 342-345)."""
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

        with (
            patch(
                "libs.web_console_services.tax_lot_service.has_permission",
                return_value=True,
            ),
            patch(
                "libs.web_console_services.tax_lot_service.acquire_connection",
                return_value=_mock_acquire_connection(mock_conn),
            ),
        ):
            with pytest.raises(ValueError, match="acquisition_date must be datetime"):
                await service.update_lot("lot-1", updates={"acquisition_date": "2024-01-01"})

    @pytest.mark.asyncio()
    async def test_update_lot_runtime_error_on_no_return(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any]
    ) -> None:
        """Test update_lot RuntimeError when updated row is None (covers lines 385-386)."""
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
        # First fetchone returns the row, second returns None (update failed)
        mock_cursor.fetchone = AsyncMock(side_effect=[row, None])
        mock_conn = _mock_cursor_conn(mock_cursor)

        with (
            patch(
                "libs.web_console_services.tax_lot_service.has_permission",
                return_value=True,
            ),
            patch(
                "libs.web_console_services.tax_lot_service.acquire_connection",
                return_value=_mock_acquire_connection(mock_conn),
            ),
        ):
            with pytest.raises(RuntimeError, match="update failed"):
                await service.update_lot("lot-1", updates={"symbol": "MSFT"})

    @pytest.mark.asyncio()
    async def test_update_lot_with_null_status_override(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any]
    ) -> None:
        """Test update_lot with null status (covers line 300)."""
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
        updated_row = {
            "id": "lot-1",
            "symbol": "MSFT",
            "quantity": Decimal("10"),
            "total_cost": Decimal("100"),
            "acquired_at": datetime(2024, 1, 1, tzinfo=UTC),
            "remaining_quantity": Decimal("10"),
            "closed_at": None,
        }
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(side_effect=[row, updated_row])
        mock_conn = _mock_cursor_conn(mock_cursor)

        with (
            patch(
                "libs.web_console_services.tax_lot_service.has_permission",
                return_value=True,
            ),
            patch(
                "libs.web_console_services.tax_lot_service.acquire_connection",
                return_value=_mock_acquire_connection(mock_conn),
            ),
        ):
            # Pass status as None explicitly - should not trigger status update
            lot = await service.update_lot("lot-1", updates={"symbol": "MSFT", "status": None})

        assert lot.symbol == "MSFT"
        assert lot.status == "open"


class TestCloseLotAdditional:
    @pytest.mark.asyncio()
    async def test_close_lot_requires_user_context(self, mock_db_pool: Mock) -> None:
        """Test close_lot requires user context (covers line 412)."""
        service = TaxLotService(db_pool=mock_db_pool, user={"role": "admin"})

        with patch(
            "libs.web_console_services.tax_lot_service.has_permission",
            return_value=True,
        ):
            with pytest.raises(PermissionError, match="User context required"):
                await service.close_lot("lot-1")

    @pytest.mark.asyncio()
    async def test_close_lot_all_users_path(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any]
    ) -> None:
        """Test close_lot with all_users=True (covers lines 431, 447)."""
        service = TaxLotService(db_pool=mock_db_pool, user=viewer_user)

        row = {
            "id": "lot-1",
            "symbol": "AAPL",
            "quantity": Decimal("10"),
            "total_cost": Decimal("100"),
            "acquired_at": datetime(2024, 1, 1, tzinfo=UTC),
            "remaining_quantity": Decimal("0"),
            "closed_at": datetime.now(UTC),
        }
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=row)
        mock_conn = _mock_cursor_conn(mock_cursor)

        with (
            patch(
                "libs.web_console_services.tax_lot_service.has_permission",
                return_value=True,
            ),
            patch(
                "libs.web_console_services.tax_lot_service.acquire_connection",
                return_value=_mock_acquire_connection(mock_conn),
            ),
        ):
            lot = await service.close_lot("lot-1", all_users=True)

        assert lot is not None
        assert lot.status == "closed"

    @pytest.mark.asyncio()
    async def test_close_lot_success_with_user_id(
        self, mock_db_pool: Mock, viewer_user: dict[str, Any]
    ) -> None:
        """Test close_lot with specific user_id (covers line 419-429)."""
        service = TaxLotService(db_pool=mock_db_pool, user=viewer_user)

        row = {
            "id": "lot-1",
            "symbol": "AAPL",
            "quantity": Decimal("10"),
            "total_cost": Decimal("100"),
            "acquired_at": datetime(2024, 1, 1, tzinfo=UTC),
            "remaining_quantity": Decimal("0"),
            "closed_at": datetime.now(UTC),
        }
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=row)
        mock_conn = _mock_cursor_conn(mock_cursor)

        with (
            patch(
                "libs.web_console_services.tax_lot_service.has_permission",
                return_value=True,
            ),
            patch(
                "libs.web_console_services.tax_lot_service.acquire_connection",
                return_value=_mock_acquire_connection(mock_conn),
            ),
        ):
            lot = await service.close_lot("lot-1")

        assert lot is not None
        assert lot.status == "closed"


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

    def test_row_to_lot_invalid_acquisition_date(self, mock_db_pool: Mock) -> None:
        """Test _row_to_lot with invalid acquisition_date (covers line 470)."""
        service = TaxLotService(db_pool=mock_db_pool, user={"role": "viewer"})
        row = {
            "id": "lot-1",
            "symbol": "AAPL",
            "quantity": Decimal("10"),
            "total_cost": Decimal("100"),
            "acquired_at": "not-a-datetime",
            "remaining_quantity": Decimal("10"),
            "closed_at": None,
        }
        with pytest.raises(ValueError, match="acquisition_date must be datetime"):
            service._row_to_lot(row)

    def test_row_to_lot_uses_cost_basis_fallback(self, mock_db_pool: Mock) -> None:
        """Test _row_to_lot uses cost_basis when total_cost is None (covers lines 474-475)."""
        service = TaxLotService(db_pool=mock_db_pool, user={"role": "viewer"})
        row = {
            "id": "lot-1",
            "symbol": "AAPL",
            "quantity": Decimal("10"),
            "total_cost": None,
            "cost_basis": Decimal("150"),
            "acquired_at": datetime(2024, 1, 1, tzinfo=UTC),
            "remaining_quantity": Decimal("10"),
            "closed_at": None,
        }
        lot = service._row_to_lot(row)
        assert lot.cost_basis == Decimal("150")

    def test_row_to_lot_strategy_id_none_string(self, mock_db_pool: Mock) -> None:
        """Test _row_to_lot converts 'None' string to None (covers line 483)."""
        service = TaxLotService(db_pool=mock_db_pool, user={"role": "viewer"})
        row = {
            "id": "lot-1",
            "symbol": "AAPL",
            "quantity": Decimal("10"),
            "total_cost": Decimal("100"),
            "acquired_at": datetime(2024, 1, 1, tzinfo=UTC),
            "remaining_quantity": Decimal("10"),
            "closed_at": None,
            "strategy_id": "None",
        }
        lot = service._row_to_lot(row)
        assert lot.strategy_id is None

    def test_row_to_lot_strategy_id_empty_string(self, mock_db_pool: Mock) -> None:
        """Test _row_to_lot converts empty string to None."""
        service = TaxLotService(db_pool=mock_db_pool, user={"role": "viewer"})
        row = {
            "id": "lot-1",
            "symbol": "AAPL",
            "quantity": Decimal("10"),
            "total_cost": Decimal("100"),
            "acquired_at": datetime(2024, 1, 1, tzinfo=UTC),
            "remaining_quantity": Decimal("10"),
            "closed_at": None,
            "strategy_id": "",
        }
        lot = service._row_to_lot(row)
        assert lot.strategy_id is None

    def test_row_to_lot_uses_lot_id_key(self, mock_db_pool: Mock) -> None:
        """Test _row_to_lot uses lot_id key when id is missing (covers line 467)."""
        service = TaxLotService(db_pool=mock_db_pool, user={"role": "viewer"})
        row = {
            "lot_id": "lot-1",
            "symbol": "AAPL",
            "quantity": Decimal("10"),
            "total_cost": Decimal("100"),
            "acquired_at": datetime(2024, 1, 1, tzinfo=UTC),
            "remaining_quantity": Decimal("10"),
            "closed_at": None,
        }
        lot = service._row_to_lot(row)
        assert lot.lot_id == "lot-1"

    def test_row_to_lot_uses_acquisition_date_key(self, mock_db_pool: Mock) -> None:
        """Test _row_to_lot uses acquisition_date key when acquired_at is missing (covers line 468)."""
        service = TaxLotService(db_pool=mock_db_pool, user={"role": "viewer"})
        acq_date = datetime(2024, 1, 1, tzinfo=UTC)
        row = {
            "id": "lot-1",
            "symbol": "AAPL",
            "quantity": Decimal("10"),
            "total_cost": Decimal("100"),
            "acquisition_date": acq_date,
            "remaining_quantity": Decimal("10"),
            "closed_at": None,
        }
        lot = service._row_to_lot(row)
        assert lot.acquisition_date == acq_date

    def test_row_to_lot_status_override_stripped(self, mock_db_pool: Mock) -> None:
        """Test _row_to_lot strips status_override (covers lines 485-486)."""
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
        lot = service._row_to_lot(row, status_override="  OPEN  ")
        assert lot.status == "open"

    def test_row_to_lot_uses_row_status(self, mock_db_pool: Mock) -> None:
        """Test _row_to_lot uses row status when no override (covers line 487)."""
        service = TaxLotService(db_pool=mock_db_pool, user={"role": "viewer"})
        row = {
            "id": "lot-1",
            "symbol": "AAPL",
            "quantity": Decimal("10"),
            "total_cost": Decimal("100"),
            "acquired_at": datetime(2024, 1, 1, tzinfo=UTC),
            "remaining_quantity": Decimal("10"),
            "closed_at": None,
            "status": "open",
        }
        lot = service._row_to_lot(row)
        assert lot.status == "open"


class TestDeriveStatus:
    def test_derive_status_closed_by_closed_at(self, mock_db_pool: Mock) -> None:
        """Test _derive_status returns closed when closed_at is set (covers line 505)."""
        row = {
            "closed_at": datetime(2024, 1, 1, tzinfo=UTC),
            "remaining_quantity": Decimal("10"),
        }
        status = TaxLotService._derive_status(row)
        assert status == "closed"

    def test_derive_status_closed_by_zero_remaining(self, mock_db_pool: Mock) -> None:
        """Test _derive_status returns closed when remaining_quantity is zero."""
        row = {
            "closed_at": None,
            "remaining_quantity": Decimal("0"),
        }
        status = TaxLotService._derive_status(row)
        assert status == "closed"

    def test_derive_status_closed_by_negative_remaining(self, mock_db_pool: Mock) -> None:
        """Test _derive_status returns closed when remaining_quantity is negative."""
        row = {
            "closed_at": None,
            "remaining_quantity": Decimal("-5"),
        }
        status = TaxLotService._derive_status(row)
        assert status == "closed"

    def test_derive_status_open(self, mock_db_pool: Mock) -> None:
        """Test _derive_status returns open when remaining_quantity is positive."""
        row = {
            "closed_at": None,
            "remaining_quantity": Decimal("10"),
        }
        status = TaxLotService._derive_status(row)
        assert status == "open"

    def test_derive_status_open_no_remaining(self, mock_db_pool: Mock) -> None:
        """Test _derive_status returns open when remaining_quantity is None."""
        row = {
            "closed_at": None,
            "remaining_quantity": None,
        }
        status = TaxLotService._derive_status(row)
        assert status == "open"

    def test_derive_status_invalid_remaining_quantity(self, mock_db_pool: Mock) -> None:
        """Test _derive_status handles invalid remaining_quantity (covers lines 509-510)."""
        row = {
            "closed_at": None,
            "remaining_quantity": "not-a-number",
        }
        status = TaxLotService._derive_status(row)
        # Invalid decimal defaults to 0, which is <= 0, so closed
        assert status == "closed"


class TestToDecimalAdditional:
    def test_to_decimal_none(self) -> None:
        """Test _to_decimal with None returns zero (covers line 520)."""
        result = _to_decimal(None)
        assert result == Decimal("0")

    def test_to_decimal_decimal(self) -> None:
        """Test _to_decimal with Decimal returns as-is (covers line 518)."""
        result = _to_decimal(Decimal("123.45"))
        assert result == Decimal("123.45")

    def test_to_decimal_int(self) -> None:
        """Test _to_decimal with int."""
        result = _to_decimal(100)
        assert result == Decimal("100")

    def test_to_decimal_float(self) -> None:
        """Test _to_decimal with float."""
        result = _to_decimal(100.5)
        assert result == Decimal("100.5")

    def test_to_decimal_string(self) -> None:
        """Test _to_decimal with string."""
        result = _to_decimal("100.25")
        assert result == Decimal("100.25")
