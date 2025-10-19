"""
Apps package - FastAPI microservices for the trading platform.

This package contains all microservice applications:
- execution_gateway: Order execution and Alpaca API integration
- signal_service: ML model serving and signal generation
- reconciler: State reconciliation between DB and broker
- risk_manager: Pre/post-trade risk checks and circuit breakers
- cli: Command-line operational tools
"""
