// Cell flash manager for AG Grid price/P&L updates.
// Tracks previous values, applies flash classes, and respects GridThrottle degraded mode.
window.CellFlashManager = {
    // Previous values by grid_id -> row_id -> column_id -> value
    previousValues: {},

    // Flash duration in ms
    flashDuration: 500,

    // Columns that should flash on change
    flashColumns: ['unrealized_pl', 'unrealized_plpc', 'current_price'],
    flashColumnsByGrid: {},

    /**
     * Initialize for a grid.
     * @param {string} gridId - Grid identifier
     * @param {Array} columnsToFlash - Column IDs that should flash
     */
    init(gridId, columnsToFlash = null) {
        this.previousValues[gridId] = {};
        if (columnsToFlash) {
            this.flashColumnsByGrid[gridId] = columnsToFlash;
        }
    },

    /**
     * Check if flash should be suppressed (grid in degraded mode).
     * @param {string} gridId
     */
    shouldSuppressFlash(gridId) {
        if (window.GridThrottle && window.GridThrottle.getMetrics) {
            const metrics = window.GridThrottle.getMetrics(gridId);
            if (metrics && metrics.degradedMode) {
                return true;
            }
        }
        return false;
    },

    _normalizeValue(value) {
        if (value === null || value === undefined) {
            return null;
        }
        if (typeof value === 'string') {
            const trimmed = value.trim();
            if (trimmed === '') {
                return null;
            }
            const numeric = Number(trimmed);
            if (!Number.isNaN(numeric)) {
                return numeric;
            }
        }
        if (typeof value === 'number' && !Number.isNaN(value)) {
            return value;
        }
        return value;
    },

    /**
     * Process cell value change and apply flash if needed.
     *
     * @param {string} gridId - Grid identifier
     * @param {string} rowId - Row identifier
     * @param {string} colId - Column identifier
     * @param {number} newValue - New cell value
     * @param {HTMLElement} cellElement - The cell DOM element
     */
    processChange(gridId, rowId, colId, newValue, cellElement) {
        const columns = this.flashColumnsByGrid[gridId] || this.flashColumns;
        if (!columns.includes(colId)) {
            return;
        }

        if (this.shouldSuppressFlash(gridId)) {
            return;
        }

        if (!this.previousValues[gridId]) {
            this.previousValues[gridId] = {};
        }
        if (!this.previousValues[gridId][rowId]) {
            this.previousValues[gridId][rowId] = {};
        }

        const normalizedNew = this._normalizeValue(newValue);
        const prevValue = this.previousValues[gridId][rowId][colId];

        this.previousValues[gridId][rowId][colId] = normalizedNew;

        if (prevValue === undefined) {
            return;
        }

        if (prevValue === normalizedNew) {
            return;
        }

        if (typeof prevValue !== 'number' || typeof normalizedNew !== 'number') {
            return;
        }

        const flashClass = normalizedNew > prevValue ? 'cell-flash-up' : 'cell-flash-down';

        cellElement.classList.remove('cell-flash-up', 'cell-flash-down');
        void cellElement.offsetWidth;
        cellElement.classList.add(flashClass);

        setTimeout(() => {
            cellElement.classList.remove(flashClass);
        }, this.flashDuration);
    },

    /**
     * AG Grid cell renderer with flash support.
     */
    createFlashRenderer(gridId, colId) {
        return (params) => {
            const value = params.value;
            const rowId = params.data?.symbol || params.data?.client_order_id || params.node?.id;

            const span = document.createElement('span');
            span.textContent = this.formatValue(value, colId);
            span.dataset.gridId = gridId;
            span.dataset.rowId = rowId;
            span.dataset.colId = colId;
            const flashTarget = params.eGridCell || span;

            if (rowId && params.node) {
                setTimeout(() => {
                    this.processChange(gridId, rowId, colId, value, flashTarget);
                }, 0);
            }

            return span;
        };
    },

    /**
     * Format value for display (basic number formatting).
     */
    formatValue(value, colId) {
        if (value === null || value === undefined) {
            if (colId === 'unrealized_plpc') {
                return '--.--%';
            }
            if (colId === 'unrealized_pl' || colId === 'current_price') {
                return '$--.--';
            }
            return '-';
        }
        const normalized = this._normalizeValue(value);
        if (typeof normalized === 'number' && !Number.isNaN(normalized)) {
            if (colId === 'unrealized_pl' || colId === 'current_price') {
                return '$' + normalized.toLocaleString('en-US', {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                });
            }
            if (colId === 'unrealized_plpc') {
                return (normalized * 100).toFixed(2) + '%';
            }
            return normalized.toLocaleString();
        }
        return String(value);
    },

    /**
     * Clean up stored values for a grid (call on grid destroy).
     */
    cleanup(gridId) {
        delete this.previousValues[gridId];
        delete this.flashColumnsByGrid[gridId];
    }
};
