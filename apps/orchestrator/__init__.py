"""
Orchestrator Service - T5 Implementation.

Coordinates the complete trading flow:
1. Fetch signals from Signal Service (T3)
2. Map signals to executable orders (position sizing)
3. Submit orders to Execution Gateway (T4)
4. Track order execution and fills
5. Report results and handle errors

Architecture:
    Orchestrator → Signal Service (HTTP) → ML Model
                 ↓
                 Execution Gateway (HTTP) → Alpaca API
"""

__version__ = "0.1.0"
