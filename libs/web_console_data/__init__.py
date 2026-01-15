"""Web Console Data Models and Schemas.

Provides data access layer for the Web Console including:
- Strategy-scoped queries with encryption
- User authorization and data isolation
"""

from libs.web_console_data.strategy_scoped_queries import StrategyScopedDataAccess

__all__ = ["StrategyScopedDataAccess"]
