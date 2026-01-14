// Grid state manager for workspace persistence
// SECURITY: User identity derived from session cookie server-side, not client header

window.GridStateManager = {
    // Debounce state saves (500ms)
    saveTimeouts: {},

    // Track grids currently restoring state (to suppress save loops)
    // When restoring, column/sort/filter changes trigger events - we must ignore them
    _restoring: new Set(),

    // Get CSRF token from cookie (for POST/DELETE requests)
    _getCsrfToken() {
        const match = document.cookie
            .split('; ')
            .find(row => row.startsWith('ng_csrf='));
        return match ? match.split('=')[1] : '';
    },

    // Save grid state to backend (user derived from session server-side)
    async saveState(gridApi, gridId) {
        // GUARD: Skip save if currently restoring (prevents save loops)
        if (this._restoring.has(gridId)) {
            return;
        }

        // Clear pending save
        if (this.saveTimeouts[gridId]) {
            clearTimeout(this.saveTimeouts[gridId]);
        }

        // Debounce to avoid excessive saves
        this.saveTimeouts[gridId] = setTimeout(async () => {
            // Double-check we're not restoring (could have started during debounce)
            if (this._restoring.has(gridId)) {
                return;
            }

            const columnState = gridApi.getColumnState();
            const filterModel = gridApi.getFilterModel();
            const sortModel = gridApi.getSortModel();

            const state = {
                columns: columnState,
                filters: filterModel,
                sort: sortModel,
            };

            try {
                const response = await fetch('/api/workspace/grid/' + gridId, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRF-Token': this._getCsrfToken(),
                    },
                    credentials: 'same-origin',
                    body: JSON.stringify(state),
                });
                if (!response.ok) {
                    console.warn('Failed to save grid state:', response.status);
                }
            } catch (e) {
                console.warn('Failed to save grid state:', e);
            }
        }, 500);
    },

    // Restore grid state from backend (user derived from session server-side)
    // Sets _restoring flag to suppress auto-save during restoration
    async restoreState(gridApi, gridId) {
        // Mark as restoring to suppress saves from change events
        this._restoring.add(gridId);
        let restoreApplied = false;

        const clearRestoring = () => {
            if (!this._restoring.has(gridId)) {
                return;
            }
            this._restoring.delete(gridId);
            gridApi.removeEventListener('firstDataRendered', clearRestoring);
            gridApi.removeEventListener('columnEverythingChanged', clearRestoring);
            gridApi.removeEventListener('modelUpdated', clearRestoring);
        };

        try {
            const response = await fetch('/api/workspace/grid/' + gridId, {
                credentials: 'same-origin',
            });

            if (!response.ok) return false;

            const state = await response.json();
            if (!state) return false;

            // Register listeners before applying state to avoid missing synchronous events
            gridApi.addEventListener('firstDataRendered', clearRestoring);
            gridApi.addEventListener('columnEverythingChanged', clearRestoring);
            gridApi.addEventListener('modelUpdated', clearRestoring);

            if (state.columns) gridApi.applyColumnState({ state: state.columns, applyOrder: true });
            if (state.filters) {
                gridApi.setFilterModel(state.filters);
                // SAFETY: Emit event when filters are restored on critical grids
                // UI should show "Filters Active" warning to prevent hidden risk
                if (Object.keys(state.filters).length > 0) {
                    window.dispatchEvent(new CustomEvent('grid_filters_restored', {
                        detail: { gridId, filterCount: Object.keys(state.filters).length }
                    }));
                }
            }
            if (state.sort) gridApi.setSortModel(state.sort);

            // Fallback: if no grid events fire, clear restoring in next microtask.
            queueMicrotask(() => {
                if (this._restoring.has(gridId)) {
                    clearRestoring();
                }
            });

            restoreApplied = true;
            return true;
        } catch (e) {
            console.warn('Failed to restore grid state:', e);
            return false;
        } finally {
            if (!restoreApplied) {
                clearRestoring();
            }
        }
    },

    // Register event listeners for auto-save (no user_id needed - from session)
    registerAutoSave(gridApi, gridId) {
        const saveHandler = () => this.saveState(gridApi, gridId);

        gridApi.addEventListener('columnMoved', saveHandler);
        gridApi.addEventListener('columnResized', saveHandler);
        gridApi.addEventListener('sortChanged', saveHandler);
        gridApi.addEventListener('filterChanged', saveHandler);
    },

    // Reset grid state to defaults
    async resetState(gridApi, gridId) {
        try {
            const response = await fetch('/api/workspace/grid/' + gridId, {
                method: 'DELETE',
                headers: {
                    'X-CSRF-Token': this._getCsrfToken(),
                },
                credentials: 'same-origin',
            });
            if (response.ok) {
                gridApi.resetColumnState();
                gridApi.setFilterModel(null);
                gridApi.setSortModel([]);
            }
        } catch (e) {
            console.warn('Failed to reset grid state:', e);
        }
    }
};
