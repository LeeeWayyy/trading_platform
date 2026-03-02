"""Web Console Data Models and Schemas.

Provides data access layer for the Web Console including:
- Exposure queries (fail-closed strategy-scoped positions)
- Strategy-scoped queries with encryption
- User authorization and data isolation

Imports are lazy to avoid pulling in heavy dependencies (e.g. cryptography)
when only lightweight modules like ``exposure_queries`` are needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from libs.web_console_data.exposure_queries import (
        ExposureQueryResult as ExposureQueryResult,
    )
    from libs.web_console_data.exposure_queries import (
        get_strategy_positions as get_strategy_positions,
    )
    from libs.web_console_data.strategy_scoped_queries import (
        StrategyScopedDataAccess as StrategyScopedDataAccess,
    )

__all__ = [
    "ExposureQueryResult",
    "StrategyScopedDataAccess",
    "get_strategy_positions",
]


def __getattr__(name: str) -> object:
    if name in ("ExposureQueryResult", "get_strategy_positions"):
        from libs.web_console_data.exposure_queries import (
            ExposureQueryResult,
            get_strategy_positions,
        )
        return ExposureQueryResult if name == "ExposureQueryResult" else get_strategy_positions
    if name == "StrategyScopedDataAccess":
        from libs.web_console_data.strategy_scoped_queries import (
            StrategyScopedDataAccess,
        )
        return StrategyScopedDataAccess
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
