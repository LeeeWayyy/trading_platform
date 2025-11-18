# Web Console User Guide

**Version:** 0.1.0
**Last Updated:** 2024-11-17
**Status:** MVP (Development Mode)

---

## Table of Contents

1. [Overview](#overview)
2. [Getting Started](#getting-started)
3. [Authentication](#authentication)
4. [Dashboard](#dashboard)
5. [Manual Order Entry](#manual-order-entry)
6. [Kill Switch](#kill-switch)
7. [Audit Log](#audit-log)
8. [Troubleshooting](#troubleshooting)

---

## Overview

The Trading Platform Web Console is a Streamlit-based UI for operational oversight and manual intervention. It provides:

- **Real-time Dashboard**: Monitor positions, P&L, and system status
- **Manual Order Entry**: Submit orders with two-step confirmation
- **Emergency Kill Switch**: Halt all trading with audit trail
- **Audit Log Viewer**: Track all manual actions (placeholder)
- **Authentication**: Session management with timeout enforcement

**Target Users:** Operations team, traders, risk managers (non-technical operators)

**Access URL:** http://localhost:8501 (local) or https://console.trading-platform.example.com (production)

---

## Getting Started

### Prerequisites

1. **Execution Gateway** running on http://localhost:8002 (or configured URL)
2. **PostgreSQL** database for audit logging
3. **Python 3.11+** with Streamlit installed

### Installation

**Option 1: Docker (Recommended)**

```bash
# Start web console with infrastructure
docker-compose up web_console

# Access console at http://localhost:8501
```

**Option 2: Local Development**

```bash
# Install dependencies
pip install -r apps/web_console/requirements.txt

# Set environment variables
export EXECUTION_GATEWAY_URL=http://localhost:8002
export WEB_CONSOLE_AUTH_TYPE=dev
export WEB_CONSOLE_USER=admin
export WEB_CONSOLE_PASSWORD=admin

# Run Streamlit app
streamlit run apps/web_console/app.py --server.port 8501
```

### First Login

1. Navigate to http://localhost:8501
2. Enter username: `admin` (default)
3. Enter password: `admin` (default)
4. Click **Login**

**⚠️ WARNING:** Change default credentials in production!

---

## Authentication

### Session Management

- **Idle Timeout:** 15 minutes of inactivity
- **Absolute Timeout:** 4 hours since login
- **Session ID:** Unique identifier for audit trail

### Authentication Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| `dev` | Basic username/password | Local development only |
| `basic` | HTTP Basic Auth | Testing only (requires HTTPS) |
| `oauth2` | OAuth2/OIDC (planned) | Production (not yet implemented) |

**Current MVP:** Uses `dev` mode with credentials from `config.py`

### Security Features

✅ Session timeout enforcement
✅ Audit logging for all auth attempts
⚠️ OAuth2/OIDC integration pending
⚠️ MFA not yet supported

---

## Dashboard

The dashboard displays real-time trading system status.

### System Status Banner

- **🔴 KILL SWITCH ENGAGED:** All trading halted
- **⚠️ DRY RUN MODE:** Orders logged but not submitted to broker
- **✅ LIVE TRADING MODE:** Orders submitted to broker

### P&L Summary (4 Metrics)

| Metric | Description |
|--------|-------------|
| Total Positions | Number of open positions |
| Unrealized P&L | Profit/loss on open positions (with % change) |
| Real-time Prices | Count of positions with live market data |
| Last Update | Timestamp of last data refresh |

### Positions Table

Displays all open positions with columns:

- Symbol
- Quantity
- Entry Price
- Current Price
- Unrealized P&L (dollars)
- P&L % (percentage)
- Price Source (real-time, database, or fallback)

**Auto-refresh:** Every 10 seconds (configurable in `config.py`)

### Strategy Status (Placeholder)

⚠️ **Backend API Pending**

This section will display:
- List of all configured strategies
- Active/inactive status with toggle controls
- Last signal generation time
- Performance metrics per strategy

---

## Manual Order Entry

Submit manual orders with two-step confirmation and audit trail.

### Step 1: Order Entry Form

1. Navigate to **Manual Order Entry** page
2. Fill in order details:
   - **Symbol:** Stock ticker (e.g., AAPL, MSFT)
   - **Side:** buy or sell
   - **Quantity:** Number of shares (must be positive)
   - **Order Type:** market or limit
   - **Limit Price:** (required if order type = limit)
   - **Reason:** Justification for manual order (required, min 10 characters)
3. Click **Preview Order**

### Step 2: Confirmation

1. Review order summary carefully
2. Click **✅ Confirm & Submit** to execute
3. OR click **❌ Cancel** to abort

### Order Submission

- **Success:** Displays client_order_id and status
- **Failure:** Displays error message (API error, validation error, etc.)
- **Audit:** All submissions logged to audit trail

### Example Use Cases

**Scenario 1: Close position due to news event**
- Symbol: AAPL
- Side: sell
- Qty: 100
- Type: market
- Reason: "Closing position due to negative earnings report"

**Scenario 2: Enter limit order at specific price**
- Symbol: MSFT
- Side: buy
- Qty: 50
- Type: limit
- Limit Price: $350.00
- Reason: "Scaling into position at support level"

---

## Kill Switch

Emergency trading halt with operator-controlled engagement/disengagement.

### When to Use Kill Switch

✅ **Engage kill switch when:**
- Market anomaly detected (flash crash, data errors)
- System malfunction suspected
- Regulatory or compliance issue
- Need to pause trading for investigation

❌ **Do NOT use kill switch for:**
- Normal market volatility
- Minor P&L fluctuations
- Routine system maintenance (coordinate with dev team)

### Engage Kill Switch

1. Navigate to **Kill Switch** page
2. Fill in **Reason** (required, min 10 characters)
   - Example: "Market anomaly detected, halting for investigation"
3. Click **🔴 ENGAGE KILL SWITCH**
4. Confirm in dialog

**Effect:**
- All trading immediately halted
- New orders blocked
- Open orders remain active (cancel manually if needed)
- System displays "KILL SWITCH ENGAGED" banner

### Disengage Kill Switch

1. Navigate to **Kill Switch** page
2. Fill in **Notes** (required, min 10 characters)
   - Example: "Issue resolved, resuming trading"
3. Click **🟢 Disengage Kill Switch**
4. Confirm in dialog

**Effect:**
- Trading resumes
- System banner clears

### Audit Trail

All kill switch actions are logged with:
- Timestamp
- Operator (username)
- Action (engage/disengage)
- Reason/Notes
- Session ID

---

## Audit Log

View and filter all manual actions performed via web console.

### Current Status

⚠️ **Database Integration Pending**

Audit events are currently logged to server console logs. Full database integration with web UI is planned for next iteration.

### Planned Features

- Filter by date range, action type, user
- Search by keywords
- Export to CSV
- Pagination for large datasets

### Audit Event Types

| Action | Description |
|--------|-------------|
| `manual_order` | Manual order submitted via UI |
| `manual_order_failed` | Manual order submission failed |
| `kill_switch_engage` | Kill switch activated |
| `kill_switch_disengage` | Kill switch deactivated |
| `strategy_toggle` | Strategy enabled/disabled (planned) |

### Viewing Current Audit Trail

**Temporary workaround:** Check server logs

```bash
# View audit logs (if using Docker)
docker logs trading_platform_web_console | grep AUDIT

# View audit logs (if running locally)
# Check console output where Streamlit is running
```

---

## Troubleshooting

### Issue: Cannot Log In

**Symptoms:** "Invalid username or password" error

**Solutions:**
1. Verify credentials match `WEB_CONSOLE_USER` and `WEB_CONSOLE_PASSWORD` env vars
2. Check `config.py` for default credentials (dev mode)
3. Ensure `WEB_CONSOLE_AUTH_TYPE=dev` for local development

### Issue: "API Error" on Dashboard

**Symptoms:** Dashboard shows "API Error: positions - ..."

**Solutions:**
1. Verify Execution Gateway is running:
   ```bash
   curl http://localhost:8002/health
   ```
2. Check `EXECUTION_GATEWAY_URL` environment variable
3. Verify network connectivity between console and gateway
4. Check execution gateway logs for errors

### Issue: Session Timeout

**Symptoms:** Redirected to login after inactivity

**Solutions:**
- Expected behavior (15 min idle timeout, 4 hour absolute timeout)
- Log in again
- To adjust timeout: Set `SESSION_TIMEOUT_MINUTES` env var

### Issue: Order Submission Fails

**Symptoms:** Error message after clicking "Confirm & Submit"

**Common Causes:**
1. **Kill switch engaged:** Disengage kill switch first
2. **Gateway unreachable:** Check execution gateway health
3. **Invalid symbol:** Verify ticker exists
4. **Negative quantity:** Must be positive integer
5. **Limit price missing:** Required for limit orders

**Debugging:**
1. Check error message details
2. Review execution gateway logs:
   ```bash
   docker logs trading_platform_execution_gateway
   ```
3. Verify order parameters match API schema

### Issue: Real-time Prices Not Updating

**Symptoms:** Price Source shows "database" or "fallback"

**Solutions:**
1. Verify Market Data Service is running and populating Redis
2. Check Redis connectivity from execution gateway
3. Verify symbols are in configured universe
4. Wait for next market data tick (if markets closed)

### Issue: Docker Container Won't Start

**Symptoms:** `docker-compose up web_console` fails

**Solutions:**
1. Check Docker logs:
   ```bash
   docker logs trading_platform_web_console
   ```
2. Verify PostgreSQL is healthy:
   ```bash
   docker ps | grep postgres
   ```
3. Check port 8501 is not already in use:
   ```bash
   lsof -i :8501
   ```
4. Rebuild container:
   ```bash
   docker-compose build web_console
   docker-compose up web_console
   ```

---

## Getting Help

**Documentation:**
- Task document: `docs/TASKS/P2T3_TASK.md`
- API documentation: `docs/API/execution_gateway.openapi.yaml`
- Development standards: `docs/STANDARDS/`

**Logs:**
```bash
# Web console logs
docker logs -f trading_platform_web_console

# Execution gateway logs
docker logs -f trading_platform_execution_gateway

# PostgreSQL logs
docker logs -f trading_platform_postgres
```

**Support:**
- File issues in GitHub repository
- Contact dev team for urgent issues
- Check `docs/RUNBOOKS/ops.md` for operational procedures

---

## Appendix: Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `EXECUTION_GATEWAY_URL` | http://localhost:8002 | Execution gateway base URL |
| `WEB_CONSOLE_AUTH_TYPE` | dev | Authentication mode (dev, basic, oauth2) |
| `WEB_CONSOLE_USER` | admin | Username (dev mode only) |
| `WEB_CONSOLE_PASSWORD` | admin | Password (dev mode only) |
| `DATABASE_URL` | postgresql://postgres:postgres@localhost:5432/trading_platform | PostgreSQL connection string |
| `SESSION_TIMEOUT_MINUTES` | 15 | Idle session timeout |
| `SESSION_ABSOLUTE_TIMEOUT_HOURS` | 4 | Absolute session timeout |

---

**End of User Guide**
