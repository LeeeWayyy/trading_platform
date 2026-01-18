"""Tests for libs/platform/tax/protocols.py."""

import pytest

from libs.platform.tax import protocols


class _PoolWithConnection:
    def connection(self):  # pragma: no cover - structural check only
        return None


class _PoolWithoutConnection:
    pass


class TestAsyncConnectionPoolProtocol:
    """Tests for AsyncConnectionPool runtime protocol behavior."""

    @pytest.mark.unit()
    def test_runtime_checkable_allows_structural_isinstance(self) -> None:
        assert isinstance(_PoolWithConnection(), protocols.AsyncConnectionPool)

    @pytest.mark.unit()
    def test_runtime_checkable_rejects_missing_method(self) -> None:
        assert not isinstance(_PoolWithoutConnection(), protocols.AsyncConnectionPool)


class TestProtocolsExports:
    """Tests for __all__ exports."""

    @pytest.mark.unit()
    def test_exports_include_expected_names(self) -> None:
        assert set(protocols.__all__) == {
            "AsyncConnection",
            "AsyncConnectionPool",
            "AsyncCursor",
        }
