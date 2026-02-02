"""Tests for AttributionService.

P6T10: Track 10 - Quantile & Attribution Analytics

Coverage targets:
- Date validation (start_date > end_date)
- Schema validation (required columns)
- Column renaming (daily_return -> return)
- Date dtype normalization
- Duplicate date handling (averaging)
- Factory pattern (_create_attribution)
- Permission checks via data_access
- Async/sync bridge (run_in_threadpool)
"""

from __future__ import annotations

import importlib.util
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Skip if optional heavy deps missing
_missing = [mod for mod in ("polars",) if importlib.util.find_spec(mod) is None]
if _missing:
    pytest.skip(
        f"Skipping attribution service tests because dependencies are missing: {', '.join(_missing)}",
        allow_module_level=True,
    )


from libs.web_console_services.attribution_service import AttributionService

# ------------------------------------------------------------------ Fixtures


@pytest.fixture()
def mock_data_access() -> MagicMock:
    """Mock StrategyScopedDataAccess."""
    mock = MagicMock()
    mock.get_portfolio_returns = AsyncMock()
    return mock


@pytest.fixture()
def mock_ff_provider() -> MagicMock:
    """Mock FamaFrenchLocalProvider."""
    return MagicMock()


@pytest.fixture()
def mock_crsp_provider() -> MagicMock:
    """Mock CRSPLocalProvider."""
    return MagicMock()


@pytest.fixture()
def service(
    mock_data_access: MagicMock,
    mock_ff_provider: MagicMock,
    mock_crsp_provider: MagicMock,
) -> AttributionService:
    """Create AttributionService with mocks."""
    return AttributionService(mock_data_access, mock_ff_provider, mock_crsp_provider)


@pytest.fixture()
def service_without_crsp(
    mock_data_access: MagicMock,
    mock_ff_provider: MagicMock,
) -> AttributionService:
    """Create AttributionService without CRSP provider."""
    return AttributionService(mock_data_access, mock_ff_provider, crsp_provider=None)


# ------------------------------------------------------------------ Date Validation Tests


@pytest.mark.unit()
@pytest.mark.asyncio()
class TestDateValidation:
    """Tests for date validation in run_attribution."""

    async def test_start_after_end_raises(
        self,
        service: AttributionService,
        mock_data_access: MagicMock,
    ):
        """start_date > end_date should raise ValueError."""
        with pytest.raises(ValueError, match="start_date.*must be <= end_date"):
            await service.run_attribution(
                strategy_id="strat-1",
                start_date=date(2024, 2, 1),  # After
                end_date=date(2024, 1, 1),  # Before
            )

        # Should not call data_access
        mock_data_access.get_portfolio_returns.assert_not_called()

    async def test_same_date_is_valid(
        self,
        service: AttributionService,
        mock_data_access: MagicMock,
        mock_ff_provider: MagicMock,
    ):
        """start_date == end_date should be valid."""
        mock_data_access.get_portfolio_returns.return_value = [
            {"date": date(2024, 1, 1), "daily_return": 0.01}
        ]

        # Mock FactorAttribution to avoid actual calculation
        with patch(
            "libs.web_console_services.attribution_service.FactorAttribution"
        ) as mock_fa_class:
            mock_fa = MagicMock()
            mock_result = MagicMock()
            mock_fa.fit.return_value = mock_result
            mock_fa_class.return_value = mock_fa

            _result = await service.run_attribution(
                strategy_id="strat-1",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 1),
            )

        mock_data_access.get_portfolio_returns.assert_called_once()


# ------------------------------------------------------------------ Schema Validation Tests


@pytest.mark.unit()
@pytest.mark.asyncio()
class TestSchemaValidation:
    """Tests for return data schema validation."""

    async def test_no_returns_raises(
        self,
        service: AttributionService,
        mock_data_access: MagicMock,
    ):
        """Empty returns should raise InsufficientObservationsError."""
        from libs.platform.analytics.attribution import InsufficientObservationsError

        mock_data_access.get_portfolio_returns.return_value = []

        with pytest.raises(InsufficientObservationsError, match="No return data"):
            await service.run_attribution(
                strategy_id="strat-1",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
            )

    async def test_missing_return_column_raises(
        self,
        service: AttributionService,
        mock_data_access: MagicMock,
    ):
        """Missing return column should raise InsufficientObservationsError."""
        from libs.platform.analytics.attribution import InsufficientObservationsError

        # Has date but no return column
        mock_data_access.get_portfolio_returns.return_value = [
            {"date": date(2024, 1, 1), "other_col": 0.01}
        ]

        with pytest.raises(InsufficientObservationsError, match="missing required return column"):
            await service.run_attribution(
                strategy_id="strat-1",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
            )

    async def test_missing_date_column_raises(
        self,
        service: AttributionService,
        mock_data_access: MagicMock,
    ):
        """Missing date column should raise InsufficientObservationsError."""
        from libs.platform.analytics.attribution import InsufficientObservationsError

        # Has return but no date column
        mock_data_access.get_portfolio_returns.return_value = [{"daily_return": 0.01}]

        with pytest.raises(InsufficientObservationsError, match="missing required columns"):
            await service.run_attribution(
                strategy_id="strat-1",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
            )

    async def test_daily_return_column_accepted(
        self,
        service: AttributionService,
        mock_data_access: MagicMock,
    ):
        """'daily_return' column should be accepted and renamed."""
        mock_data_access.get_portfolio_returns.return_value = [
            {"date": date(2024, 1, 1), "daily_return": 0.01}
        ]

        with patch(
            "libs.web_console_services.attribution_service.FactorAttribution"
        ) as mock_fa_class:
            mock_fa = MagicMock()
            mock_result = MagicMock()
            mock_fa.fit.return_value = mock_result
            mock_fa_class.return_value = mock_fa

            result = await service.run_attribution(
                strategy_id="strat-1",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
            )

        assert result == mock_result

    async def test_return_column_accepted(
        self,
        service: AttributionService,
        mock_data_access: MagicMock,
    ):
        """'return' column should be accepted directly."""
        mock_data_access.get_portfolio_returns.return_value = [
            {"date": date(2024, 1, 1), "return": 0.01}
        ]

        with patch(
            "libs.web_console_services.attribution_service.FactorAttribution"
        ) as mock_fa_class:
            mock_fa = MagicMock()
            mock_result = MagicMock()
            mock_fa.fit.return_value = mock_result
            mock_fa_class.return_value = mock_fa

            result = await service.run_attribution(
                strategy_id="strat-1",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
            )

        assert result == mock_result


# ------------------------------------------------------------------ Date Dtype Normalization Tests


@pytest.mark.unit()
@pytest.mark.asyncio()
class TestDateDtypeNormalization:
    """Tests for date dtype normalization."""

    async def test_string_date_raises(
        self,
        service: AttributionService,
        mock_data_access: MagicMock,
    ):
        """String date that can't be cast should raise."""
        from libs.platform.analytics.attribution import InsufficientObservationsError

        # String dates that look like dates but need casting
        mock_data_access.get_portfolio_returns.return_value = [
            {"date": "invalid-date", "daily_return": 0.01}
        ]

        with pytest.raises(InsufficientObservationsError, match="Failed to convert"):
            await service.run_attribution(
                strategy_id="strat-1",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
            )


# ------------------------------------------------------------------ Duplicate Handling Tests


@pytest.mark.unit()
@pytest.mark.asyncio()
class TestDuplicateHandling:
    """Tests for duplicate date handling."""

    async def test_duplicate_dates_averaged(
        self,
        service: AttributionService,
        mock_data_access: MagicMock,
    ):
        """Duplicate dates should be averaged."""
        # Two entries for same date with different returns
        mock_data_access.get_portfolio_returns.return_value = [
            {"date": date(2024, 1, 1), "daily_return": 0.01},
            {"date": date(2024, 1, 1), "daily_return": 0.03},  # Duplicate
        ]

        with patch(
            "libs.web_console_services.attribution_service.FactorAttribution"
        ) as mock_fa_class:
            mock_fa = MagicMock()
            mock_result = MagicMock()
            mock_fa.fit.return_value = mock_result
            mock_fa_class.return_value = mock_fa

            _result = await service.run_attribution(
                strategy_id="strat-1",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
            )

        # Verify fit was called
        mock_fa.fit.assert_called_once()
        fit_call = mock_fa.fit.call_args
        returns_df = fit_call[0][0]  # First positional arg

        # Should have 1 row (duplicates merged)
        assert returns_df.height == 1
        # Should be average: (0.01 + 0.03) / 2 = 0.02
        assert abs(returns_df["return"][0] - 0.02) < 1e-6


# ------------------------------------------------------------------ Factory Pattern Tests


@pytest.mark.unit()
class TestFactoryPattern:
    """Tests for _create_attribution factory method."""

    def test_creates_ff3_attribution(
        self,
        service: AttributionService,
        mock_ff_provider: MagicMock,
    ):
        """Should create FactorAttribution with ff3 config."""
        with patch("libs.web_console_services.attribution_service.FactorAttribution") as mock_class:
            service._create_attribution("ff3")

            mock_class.assert_called_once()
            call_kwargs = mock_class.call_args[1]
            assert call_kwargs["config"].model == "ff3"

    def test_creates_ff5_attribution(
        self,
        service: AttributionService,
        mock_ff_provider: MagicMock,
    ):
        """Should create FactorAttribution with ff5 config."""
        with patch("libs.web_console_services.attribution_service.FactorAttribution") as mock_class:
            service._create_attribution("ff5")

            mock_class.assert_called_once()
            call_kwargs = mock_class.call_args[1]
            assert call_kwargs["config"].model == "ff5"

    def test_creates_ff6_attribution(
        self,
        service: AttributionService,
        mock_ff_provider: MagicMock,
    ):
        """Should create FactorAttribution with ff6 config."""
        with patch("libs.web_console_services.attribution_service.FactorAttribution") as mock_class:
            service._create_attribution("ff6")

            mock_class.assert_called_once()
            call_kwargs = mock_class.call_args[1]
            assert call_kwargs["config"].model == "ff6"

    def test_passes_providers(
        self,
        service: AttributionService,
        mock_ff_provider: MagicMock,
        mock_crsp_provider: MagicMock,
    ):
        """Should pass providers to FactorAttribution."""
        with patch("libs.web_console_services.attribution_service.FactorAttribution") as mock_class:
            service._create_attribution("ff5")

            call_kwargs = mock_class.call_args[1]
            assert call_kwargs["ff_provider"] == mock_ff_provider
            assert call_kwargs["crsp_provider"] == mock_crsp_provider

    def test_none_crsp_provider_passed(
        self,
        service_without_crsp: AttributionService,
        mock_ff_provider: MagicMock,
    ):
        """Should pass None CRSP provider when not provided."""
        with patch("libs.web_console_services.attribution_service.FactorAttribution") as mock_class:
            service_without_crsp._create_attribution("ff5")

            call_kwargs = mock_class.call_args[1]
            assert call_kwargs["crsp_provider"] is None


# ------------------------------------------------------------------ Model Selection Tests


@pytest.mark.unit()
@pytest.mark.asyncio()
class TestModelSelection:
    """Tests for model selection in run_attribution."""

    async def test_default_model_is_ff5(
        self,
        service: AttributionService,
        mock_data_access: MagicMock,
    ):
        """Default model should be ff5."""
        mock_data_access.get_portfolio_returns.return_value = [
            {"date": date(2024, 1, 1), "daily_return": 0.01}
        ]

        with patch(
            "libs.web_console_services.attribution_service.FactorAttribution"
        ) as mock_fa_class:
            mock_fa = MagicMock()
            mock_result = MagicMock()
            mock_fa.fit.return_value = mock_result
            mock_fa_class.return_value = mock_fa

            with patch(
                "libs.web_console_services.attribution_service.FactorAttributionConfig"
            ) as mock_config_class:
                await service.run_attribution(
                    strategy_id="strat-1",
                    start_date=date(2024, 1, 1),
                    end_date=date(2024, 1, 31),
                    # model not specified, should default to ff5
                )

            # Config should be created with ff5 model
            mock_config_class.assert_called_with(model="ff5")

    async def test_custom_model_passed(
        self,
        service: AttributionService,
        mock_data_access: MagicMock,
    ):
        """Custom model should be passed to factory."""
        mock_data_access.get_portfolio_returns.return_value = [
            {"date": date(2024, 1, 1), "daily_return": 0.01}
        ]

        with patch(
            "libs.web_console_services.attribution_service.FactorAttribution"
        ) as mock_fa_class:
            mock_fa = MagicMock()
            mock_result = MagicMock()
            mock_fa.fit.return_value = mock_result
            mock_fa_class.return_value = mock_fa

            with patch(
                "libs.web_console_services.attribution_service.FactorAttributionConfig"
            ) as mock_config_class:
                await service.run_attribution(
                    strategy_id="strat-1",
                    start_date=date(2024, 1, 1),
                    end_date=date(2024, 1, 31),
                    model="ff3",
                )

            mock_config_class.assert_called_with(model="ff3")


# ------------------------------------------------------------------ Permission Tests


@pytest.mark.unit()
@pytest.mark.asyncio()
class TestPermissionChecks:
    """Tests for permission checks."""

    async def test_get_portfolio_returns_checked(
        self,
        service: AttributionService,
        mock_data_access: MagicMock,
    ):
        """Should call get_portfolio_returns which checks ownership."""
        mock_data_access.get_portfolio_returns.side_effect = PermissionError("Forbidden")

        with pytest.raises(PermissionError):
            await service.run_attribution(
                strategy_id="strat-1",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
            )

        mock_data_access.get_portfolio_returns.assert_called_once_with(
            "strat-1",
            date(2024, 1, 1),
            date(2024, 1, 31),
        )
