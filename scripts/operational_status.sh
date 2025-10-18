#!/usr/bin/env bash

################################################################################
# Operational Status Script
#
# Displays comprehensive operational overview of the trading platform by
# querying health endpoints and aggregating position/run data from all services.
#
# Usage:
#   ./scripts/operational_status.sh
#   make status
#
# Requirements:
#   - jq (brew install jq)
#   - curl
#   - Services running on default ports:
#     - Signal Service: 8001
#     - Execution Gateway: 8002
#     - Orchestrator: 8003
#
# Exit codes:
#   0 - All services healthy
#   1 - One or more services unhealthy
#   2 - Missing dependencies (jq, curl)
################################################################################

set -euo pipefail

# Service endpoints
SIGNAL_SERVICE_URL="${SIGNAL_SERVICE_URL:-http://localhost:8001}"
EXECUTION_GATEWAY_URL="${EXECUTION_GATEWAY_URL:-http://localhost:8002}"
ORCHESTRATOR_URL="${ORCHESTRATOR_URL:-http://localhost:8003}"

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# Emojis
CHECKMARK="✓"
CROSS="✗"
CHART="📊"
LIST="📋"
MONEY="💰"
GEAR="🔧"
WARN="⚠️"

# Check dependencies
check_dependencies() {
    local missing=()

    if ! command -v jq &> /dev/null; then
        missing+=("jq")
    fi

    if ! command -v curl &> /dev/null; then
        missing+=("curl")
    fi

    if [ ${#missing[@]} -gt 0 ]; then
        echo -e "${RED}${CROSS} Missing required dependencies: ${missing[*]}${NC}"
        echo "Install with: brew install ${missing[*]}"
        exit 2
    fi
}

# Query service health endpoint
check_service_health() {
    local service_name=$1
    local url=$2
    local timeout=${3:-2}

    local response
    local http_code

    # Try to query health endpoint
    response=$(curl -s -w "\n%{http_code}" --max-time "$timeout" "$url/health" 2>/dev/null || echo "000")
    http_code=$(echo "$response" | tail -n1)

    if [ "$http_code" = "200" ]; then
        echo -e "${GREEN}${CHECKMARK} ${service_name}${NC}: healthy"
        return 0
    elif [ "$http_code" = "000" ]; then
        echo -e "${RED}${CROSS} ${service_name}${NC}: ${RED}unreachable${NC} (is service running?)"
        return 1
    else
        echo -e "${YELLOW}${WARN} ${service_name}${NC}: ${YELLOW}degraded${NC} (HTTP $http_code)"
        return 1
    fi
}

# Get positions from Execution Gateway
get_positions() {
    local response
    local http_code

    response=$(curl -s -w "\n%{http_code}" --max-time 3 "$EXECUTION_GATEWAY_URL/api/v1/positions" 2>/dev/null || echo "{}\n000")
    http_code=$(echo "$response" | tail -n1)

    if [ "$http_code" != "200" ]; then
        echo -e "${YELLOW}${WARN} Unable to fetch positions (HTTP $http_code)${NC}"
        return 1
    fi

    local body
    body=$(echo "$response" | sed '$d')

    # Check if there are any positions
    local position_count
    position_count=$(echo "$body" | jq -r '.positions | length' 2>/dev/null || echo "0")

    if [ "$position_count" = "0" ]; then
        echo "  No open positions"
        return 0
    fi

    # Display each position
    echo "$body" | jq -r '.positions[] | "  \(.symbol): \(.qty) shares @ $\(.avg_entry_price) avg"' 2>/dev/null || \
        echo "  ${YELLOW}${WARN} Error parsing positions${NC}"
}

# Get recent runs from Orchestrator
get_recent_runs() {
    local response
    local http_code

    response=$(curl -s -w "\n%{http_code}" --max-time 3 "$ORCHESTRATOR_URL/api/v1/orchestration/runs?limit=5" 2>/dev/null || echo "{}\n000")
    http_code=$(echo "$response" | tail -n1)

    if [ "$http_code" != "200" ]; then
        echo -e "${YELLOW}${WARN} Unable to fetch recent runs (HTTP $http_code)${NC}"
        return 1
    fi

    local body
    body=$(echo "$response" | sed '$d')

    # Check if there are any runs
    local run_count
    run_count=$(echo "$body" | jq -r '.runs | length' 2>/dev/null || echo "0")

    if [ "$run_count" = "0" ]; then
        echo "  No runs recorded"
        return 0
    fi

    # Display each run
    echo "$body" | jq -r '.runs[] | "  \(.created_at | split("T")[0]) \(.created_at | split("T")[1] | split(".")[0]): \(.status // "UNKNOWN")"' 2>/dev/null | head -5 || \
        echo "  ${YELLOW}${WARN} Error parsing runs${NC}"
}

# Get P&L summary from Execution Gateway
get_pnl_summary() {
    local response
    local http_code

    response=$(curl -s -w "\n%{http_code}" --max-time 3 "$EXECUTION_GATEWAY_URL/api/v1/positions/pnl" 2>/dev/null || echo "{}\n000")
    http_code=$(echo "$response" | tail -n1)

    if [ "$http_code" != "200" ]; then
        echo -e "${YELLOW}${WARN} Unable to fetch P&L (HTTP $http_code)${NC}"
        return 1
    fi

    local body
    body=$(echo "$response" | sed '$d')

    # Extract P&L values
    local realized
    local unrealized
    local total

    realized=$(echo "$body" | jq -r '.realized_pnl // "0.00"' 2>/dev/null || echo "0.00")
    unrealized=$(echo "$body" | jq -r '.unrealized_pnl // "0.00"' 2>/dev/null || echo "0.00")
    total=$(echo "$body" | jq -r '.total_pnl // "0.00"' 2>/dev/null || echo "0.00")

    # Format with colors based on positive/negative
    local realized_color=$GREEN
    local unrealized_color=$GREEN
    local total_color=$GREEN

    if [[ "$realized" == -* ]]; then realized_color=$RED; fi
    if [[ "$unrealized" == -* ]]; then unrealized_color=$RED; fi
    if [[ "$total" == -* ]]; then total_color=$RED; fi

    echo -e "  Realized:    ${realized_color}\$${realized}${NC}"
    echo -e "  Unrealized:  ${unrealized_color}\$${unrealized}${NC}"
    echo -e "  ${BOLD}Total:       ${total_color}\$${total}${NC}"
}

# Main function
main() {
    check_dependencies

    echo ""
    echo -e "${BOLD}========================================================================${NC}"
    echo -e "${BOLD}  TRADING PLATFORM STATUS${NC}"
    echo -e "${BOLD}========================================================================${NC}"
    echo ""

    # Service Health Checks
    echo -e "${GEAR} ${BOLD}Services:${NC}"
    local all_healthy=0

    check_service_health "Signal Service (T3)" "$SIGNAL_SERVICE_URL" || all_healthy=1
    check_service_health "Execution Gateway (T4)" "$EXECUTION_GATEWAY_URL" || all_healthy=1
    check_service_health "Orchestrator (T5)" "$ORCHESTRATOR_URL" || all_healthy=1
    echo ""

    # Positions
    echo -e "${CHART} ${BOLD}Positions (T4):${NC}"
    get_positions
    echo ""

    # Recent Runs
    echo -e "${LIST} ${BOLD}Recent Runs (T5):${NC}"
    get_recent_runs
    echo ""

    # P&L Summary
    echo -e "${MONEY} ${BOLD}Latest P&L:${NC}"
    get_pnl_summary
    echo ""

    echo -e "${BOLD}========================================================================${NC}"
    echo ""

    exit $all_healthy
}

# Run main function
main

