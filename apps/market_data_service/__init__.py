"""
Market Data Service

FastAPI service for real-time market data streaming from Alpaca.

Provides:
- WebSocket connection management
- Symbol subscription endpoints
- Real-time price distribution via Redis pub/sub
- Health monitoring

Usage:
    uvicorn apps.market_data_service.main:app --port 8004
"""
