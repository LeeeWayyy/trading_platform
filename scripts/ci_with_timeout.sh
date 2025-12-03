#!/bin/bash
# CI wrapper with hang detection
# Monitors command output and fails if no progress for specified timeout

set -e

# Configuration
HANG_TIMEOUT=${HANG_TIMEOUT:-300}  # 5 minutes default
CHECK_INTERVAL=${CHECK_INTERVAL:-10}  # Check every 10 seconds
OUTPUT_FILE=$(mktemp)
LAST_SIZE=0
STALL_COUNT=0
MAX_STALLS=$((HANG_TIMEOUT / CHECK_INTERVAL))

# Cleanup on exit
cleanup() {
    if [ -n "$CMD_PID" ] && kill -0 "$CMD_PID" 2>/dev/null; then
        echo ""
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "⚠️  Terminating CI process (PID: $CMD_PID)"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        kill -TERM "$CMD_PID" 2>/dev/null || true
        sleep 2
        kill -9 "$CMD_PID" 2>/dev/null || true
    fi
    rm -f "$OUTPUT_FILE"
}
trap cleanup EXIT INT TERM

# Run command in background, tee output to file and stdout
"$@" 2>&1 | tee "$OUTPUT_FILE" &
CMD_PID=$!

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🔍 Hang detection enabled (timeout: ${HANG_TIMEOUT}s)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Monitor for hangs
while kill -0 "$CMD_PID" 2>/dev/null; do
    sleep "$CHECK_INTERVAL"

    # Check if process is still running
    if ! kill -0 "$CMD_PID" 2>/dev/null; then
        break
    fi

    # Check output file size for progress
    CURRENT_SIZE=$(stat -f%z "$OUTPUT_FILE" 2>/dev/null || stat -c%s "$OUTPUT_FILE" 2>/dev/null || echo "0")

    if [ "$CURRENT_SIZE" -eq "$LAST_SIZE" ]; then
        STALL_COUNT=$((STALL_COUNT + 1))
        STALL_TIME=$((STALL_COUNT * CHECK_INTERVAL))

        if [ "$STALL_COUNT" -ge 3 ]; then
            echo ""
            echo "⚠️  WARNING: No output progress for ${STALL_TIME}s (timeout at ${HANG_TIMEOUT}s)"
        fi

        if [ "$STALL_COUNT" -ge "$MAX_STALLS" ]; then
            echo ""
            echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            echo "❌ HANG DETECTED: No progress for ${HANG_TIMEOUT}s"
            echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            echo ""
            echo "Last 20 lines of output:"
            echo "---"
            tail -20 "$OUTPUT_FILE"
            echo "---"
            echo ""
            echo "Possible causes:"
            echo "  • Test waiting for user input"
            echo "  • Network timeout (Redis, database connection)"
            echo "  • Deadlock in async code"
            echo "  • Infinite loop in test"
            echo ""
            echo "To debug:"
            echo "  1. Run the specific test file directly"
            echo "  2. Check for missing mocks or fixtures"
            echo "  3. Look for tests that require infrastructure"
            echo ""

            # Kill the process
            kill -TERM "$CMD_PID" 2>/dev/null || true
            sleep 2
            kill -9 "$CMD_PID" 2>/dev/null || true

            exit 124  # Standard timeout exit code
        fi
    else
        # Progress detected, reset stall counter
        STALL_COUNT=0
        LAST_SIZE=$CURRENT_SIZE
    fi
done

# Wait for command to finish and get exit code
wait "$CMD_PID"
EXIT_CODE=$?

exit $EXIT_CODE
