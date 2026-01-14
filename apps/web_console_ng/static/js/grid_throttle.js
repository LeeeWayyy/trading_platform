// Grid throttle - monitors update rates and toggles degradation mode PER GRID
// NOTE: Actual backpressure (queue + drop oldest) is handled Python-side in realtime.py
// This module only monitors browser-side update rates for degradation control
window.GridThrottle = {
    // Config loaded from server via data attribute on <body> element
    // Set by Python: <body data-grid-degrade-threshold="120" data-grid-debug="false">
    config: {
        degradeThreshold: 120,       // Default, overridden by data-grid-degrade-threshold
        hysteresisCount: 2,          // Consecutive windows before toggling (prevents flapping)
        debug: false,                // Gated logging, set via data-grid-debug="true"
    },

    // Per-grid state (not global - each grid has independent rate tracking)
    gridStates: {},

    // Track last row count per grid for fallback delta calculation
    lastCounts: {},

    // Track which grids use async transactions (to gate onRowDataUpdated)
    asyncGrids: new Set(),

    // Initialize config from server-provided data attributes (call once at page load)
    initConfig() {
        const body = document.body;
        const threshold = body.dataset.gridDegradeThreshold;
        const debug = body.dataset.gridDebug;
        if (threshold) this.config.degradeThreshold = parseInt(threshold, 10);
        if (debug === 'true') this.config.debug = true;
    },

    // Register a grid as using async transactions (call at grid init, before first update)
    // This prevents double-counting if onRowDataUpdated fires before first async flush
    registerAsyncGrid(gridId) {
        this.asyncGrids.add(gridId);
    },

    // Get or create state for a grid
    _getGridState(gridId) {
        if (!this.gridStates[gridId]) {
            this.gridStates[gridId] = {
                updateCount: 0,
                lastResetTime: Date.now(),
                degradedMode: false,
                consecutiveAbove: 0,   // Consecutive windows above threshold (hysteresis)
                consecutiveBelow: 0,   // Consecutive windows below threshold (hysteresis)
            };
        }
        return this.gridStates[gridId];
    },

    // PRIMARY: Record transaction result from onAsyncTransactionsFlushed
    // This accurately counts adds/updates/removes from transaction results
    recordTransactionResult(gridApi, gridId, results) {
        // Mark this grid as using async transactions (gates onRowDataUpdated)
        this.asyncGrids.add(gridId);

        let totalUpdates = 0;
        for (const result of results) {
            totalUpdates += (result.add?.length || 0) +
                           (result.update?.length || 0) +
                           (result.remove?.length || 0);
        }
        // Only count if there were actual operations (avoid overcounting empty flushes)
        if (totalUpdates > 0) {
            this._addToUpdateCount(gridApi, gridId, totalUpdates);
        }
    },

    // FALLBACK: Record update from onRowDataUpdated (for setRowData path ONLY)
    // Gated to avoid double-counting with async transactions
    recordUpdate(gridApi, gridId) {
        // GATE: Skip if this grid uses async transactions (avoid double-counting)
        if (this.asyncGrids.has(gridId)) {
            return;
        }

        // For setRowData, use currentCount as the update size
        // This handles full refresh scenarios where row count stays same but all data changed
        // (delta=0 would undercount in that case)
        const currentCount = gridApi.getDisplayedRowCount();
        const lastCount = this.lastCounts[gridId] || 0;
        const delta = Math.abs(currentCount - lastCount);
        this.lastCounts[gridId] = currentCount;

        // If delta is 0, assume full refresh (setRowData replaces all rows)
        // Use currentCount to capture the full refresh cost
        const updateSize = delta > 0 ? delta : currentCount || 1;
        this._addToUpdateCount(gridApi, gridId, updateSize);
    },

    // Internal: Add to update count and check degradation threshold (per-grid)
    // Uses hysteresis to prevent flapping: requires N consecutive windows above/below threshold
    _addToUpdateCount(gridApi, gridId, count) {
        const state = this._getGridState(gridId);
        state.updateCount += count;
        const elapsed = Date.now() - state.lastResetTime;

        if (elapsed >= 1000) {
            const rate = Math.round(state.updateCount * 1000 / elapsed);
            state.updateCount = 0;
            state.lastResetTime = Date.now();

            // Hysteresis: track consecutive windows above/below threshold
            const aboveThreshold = rate > this.config.degradeThreshold;
            if (aboveThreshold) {
                state.consecutiveAbove++;
                state.consecutiveBelow = 0;
            } else {
                state.consecutiveBelow++;
                state.consecutiveAbove = 0;
            }

            // Toggle degradation mode only after N consecutive windows (prevents flapping)
            const shouldDegrade = state.consecutiveAbove >= this.config.hysteresisCount;
            const shouldRecover = state.consecutiveBelow >= this.config.hysteresisCount;

            if (shouldDegrade && !state.degradedMode) {
                state.degradedMode = true;
                this.setDegradedMode(gridApi, gridId, true);
                if (this.config.debug) {
                    console.log(`Grid ${gridId} degradation mode: ON (rate: ${rate}/sec, threshold: ${this.config.degradeThreshold})`);
                }
            } else if (shouldRecover && state.degradedMode) {
                state.degradedMode = false;
                this.setDegradedMode(gridApi, gridId, false);
                if (this.config.debug) {
                    console.log(`Grid ${gridId} degradation mode: OFF (rate: ${rate}/sec, threshold: ${this.config.degradeThreshold})`);
                }
            }
        }
    },

    setDegradedMode(gridApi, gridId, degraded) {
        // Disable animations in degraded mode for THIS grid
        gridApi.setGridOption('animateRows', !degraded);
        // Emit event for metrics
        window.dispatchEvent(new CustomEvent('grid_degradation_change', {
            detail: { gridId, degraded, timestamp: Date.now() }
        }));
    },

    // Get current metrics for debugging/monitoring
    getMetrics(gridId) {
        const state = this._getGridState(gridId);
        return {
            gridId,
            updateCount: state.updateCount,
            degradedMode: state.degradedMode,
            consecutiveAbove: state.consecutiveAbove,
            consecutiveBelow: state.consecutiveBelow,
        };
    }
};

// Initialize config from server data attributes
// Handle both cases: script loaded before or after DOMContentLoaded
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => window.GridThrottle.initConfig());
} else {
    // DOM already loaded, init immediately
    window.GridThrottle.initConfig();
}
