---
name: operational-guardrails
description: Operational guardrails — runbook detail, recovery procedures, monitoring, environment modes. Trigger on operational, recovery, circuit breaker recovery, monitoring, drawdown, environment modes, DRY_RUN, kill switch, alerts.
---

# Operational Guardrails

Detailed operational procedures for the trading platform. Safety-critical summaries are in the main AI guide; this skill provides runbook-level detail.

## Environment Modes

- **DRY_RUN=true** (default): Logs orders, doesn't submit to broker
- **Paper Trading:** `DRY_RUN=false` + Alpaca paper API credentials (real API, simulated money)
- **Live Trading:** Live API credentials (graduated rollout required)

## Pre-Trade Checks

Every order must pass ALL checks before submission:
1. **Circuit breaker state** — `redis.get("cb:state") != b"TRIPPED"` (MANDATORY)
2. **Per-symbol position limits** — `abs(current_pos + order.qty) <= limits.max_pos_per_symbol`
3. **Total notional limits** — aggregate portfolio exposure within bounds
4. **Blacklist enforcement** — blocked symbols never traded
5. **Daily loss limits** — stop trading when daily loss threshold breached
6. **Valid order state transitions** — no cancelling filled orders, no filling cancelled orders

## Post-Trade Monitoring

Continuous monitoring by the post-trade monitor service:
- **Drawdown calculation** — peak-to-trough decline tracked in real-time
- **Realized volatility tracking** — rolling window volatility measurement
- **Exposure monitoring** — sector, single-name, and gross/net exposure
- **Metrics to Prometheus** — 33 custom metrics across 7 services

## Circuit Breaker Recovery

**Trip triggers:** drawdown breach, broker API errors, data staleness (>30 minutes)

**When TRIPPED:**
- Block all new entry orders
- Allow risk-reducing exits only
- Recovery requires ALL of:
  1. Conditions that caused the trip have normalized
  2. Manual approval from authorized operator
  3. Quiet period observed (no further triggers)

**Recovery procedure:**
1. Identify trip cause in Grafana alerts dashboard
2. Verify conditions normalized (check relevant metrics)
3. Verify state consistency: `make status` (shows positions, orders, P&L)
4. Reset breaker: requires manual approval via web console or API
5. Monitor closely for 15 minutes post-recovery

## Kill Switch

Emergency stop — cancels all orders, flattens all positions, blocks new signals.

> **Status:** `make kill-switch` is a P1 placeholder (not yet implemented). Until implemented, use the broker's dashboard to cancel orders and flatten positions manually.

**Trigger (when implemented):** `make kill-switch` or `POST /api/v1/kill-switch/engage`

**Recovery from kill switch:**
1. Investigate root cause
2. Verify all positions flattened: `make status`
3. Clear kill switch state via web console
4. Resume with DRY_RUN=true first to verify signal generation
5. Gradually re-enable live trading

## Alert Operations

- Alert rules configured via Web Console
- Channels: Email (SMTP/SendGrid), Slack (webhook), SMS (Twilio)
- Rate limits enforced per channel
- Poison queue for failed deliveries (check `alert_deliveries` table)

## Common Operations

```bash
make status       # Check positions, open orders, P&L
make circuit-trip # Manually trip circuit breaker
make kill-switch  # Cancel all orders, flatten positions, block new signals (P1 placeholder)
make up           # Start infrastructure (Postgres, Redis, Grafana, Prometheus)
make down         # Stop infrastructure
```

## Runbook Reference

See [ops.md](../../../RUNBOOKS/ops.md) for the full operational runbook including:
- Daily checklist
- Incident response procedures
- System health monitoring
- Alert troubleshooting
