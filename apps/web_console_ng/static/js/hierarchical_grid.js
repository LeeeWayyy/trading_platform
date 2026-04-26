// Hierarchical orders grid helpers (community-grid compatible expansion + persistence)

(function() {
    const DEFAULT_GRID_ID = 'hierarchical_orders_grid';
    const expansionByGrid = new Map();
    const gridStatsByGrid = new Map();
    const registeredApis = new WeakSet();
    const TERMINAL_STATUSES = new Set([
        'filled',
        'canceled',
        'cancelled',
        'expired',
        'failed',
        'rejected',
        'replaced',
        'done_for_day',
        'blocked_kill_switch',
        'blocked_circuit_breaker'
    ]);

    function normalizeId(value) {
        return String(value || '').trim();
    }

    function isTerminalStatus(status) {
        return TERMINAL_STATUSES.has(String(status || '').toLowerCase());
    }

    function isChildRow(row) {
        if (!row) return false;
        if (row.is_child === true) return true;
        const path = Array.isArray(row.hierarchy_path) ? row.hierarchy_path : [];
        return path.length > 1;
    }

    function isParentRow(row) {
        if (!row) return false;
        if (row.is_parent === true) return true;
        return !isChildRow(row) && Number(row.child_count || 0) > 0;
    }

    function resolveParentId(row) {
        if (!row) return '';
        const explicitParent = normalizeId(row.parent_client_order_id);
        if (explicitParent) return explicitParent;
        const path = Array.isArray(row.hierarchy_path) ? row.hierarchy_path : [];
        if (path.length > 1) return normalizeId(path[0]);
        const parentOrderId = normalizeId(row.parent_order_id);
        if (parentOrderId) return parentOrderId;
        return '';
    }

    function getExpandedSet(gridId) {
        const key = normalizeId(gridId) || DEFAULT_GRID_ID;
        if (!expansionByGrid.has(key)) {
            expansionByGrid.set(key, new Set());
        }
        return expansionByGrid.get(key);
    }

    function getGridStats(gridId) {
        const key = normalizeId(gridId) || DEFAULT_GRID_ID;
        if (!gridStatsByGrid.has(key)) {
            gridStatsByGrid.set(key, {
                parentIdsWithChildren: new Set(),
                childParentIds: new Set(),
                childrenByParent: new Map()
            });
        }
        return gridStatsByGrid.get(key);
    }

    function recomputeGridStats(api, gridId) {
        const stats = getGridStats(gridId);
        stats.parentIdsWithChildren.clear();
        stats.childParentIds.clear();
        stats.childrenByParent.clear();
        if (!api) return stats;
        api.forEachNode(node => {
            const row = node && node.data ? node.data : null;
            if (!row) return;
            if (isParentRow(row) && Number(row.child_count || 0) > 0) {
                const parentId = normalizeId(row.client_order_id);
                if (parentId) stats.parentIdsWithChildren.add(parentId);
                return;
            }
            if (!isChildRow(row) || row.is_orphan) return;
            const parentId = resolveParentId(row);
            if (!parentId) return;
            stats.childParentIds.add(parentId);
            if (!stats.childrenByParent.has(parentId)) {
                stats.childrenByParent.set(parentId, []);
            }
            stats.childrenByParent.get(parentId).push(row);
        });
        return stats;
    }

    function setExpandedIds(gridId, expandedIds) {
        const nextSet = new Set(
            Array.isArray(expandedIds)
                ? expandedIds.map(normalizeId).filter(Boolean)
                : []
        );
        const expandedSet = getExpandedSet(gridId);
        expandedSet.clear();
        nextSet.forEach(id => expandedSet.add(id));
        window._hierarchicalOrdersExpanded = Array.from(expandedSet);
        return expandedSet;
    }

    function collectExpandedIds(gridId) {
        return Array.from(getExpandedSet(gridId));
    }

    function dispatchExpansion(gridId, expandedIds) {
        window.dispatchEvent(new CustomEvent('hierarchical_orders_expansion', {
            detail: {
                grid_id: gridId,
                expanded_ids: expandedIds
            }
        }));
    }

    function isRowVisible(row, gridId) {
        if (!row || !isChildRow(row)) return true;
        if (row.is_orphan) return true;
        const parentId = resolveParentId(row);
        if (!parentId) return true;
        return getExpandedSet(gridId).has(parentId);
    }

    function hasCollapsedParents(gridId) {
        const stats = getGridStats(gridId);
        if (stats.childParentIds.size === 0) return false;
        const expandedSet = getExpandedSet(gridId);
        for (const parentId of stats.childParentIds) {
            if (!expandedSet.has(parentId)) return true;
        }
        return false;
    }

    function getChildrenForParent(gridId, parentId) {
        const stats = getGridStats(gridId);
        return stats.childrenByParent.get(parentId) || [];
    }

    function refreshVisibility(api, gridId = DEFAULT_GRID_ID) {
        if (!api) return;
        recomputeGridStats(api, gridId);
        api.onFilterChanged();
        api.refreshCells({ force: true, columns: ['symbol'] });
    }

    function restoreExpansion(api, expandedIds, gridId = DEFAULT_GRID_ID) {
        setExpandedIds(gridId, expandedIds);
        refreshVisibility(api, gridId);
    }

    function register(api, gridId = DEFAULT_GRID_ID) {
        const seedExpansion = Array.isArray(window._hierarchicalOrdersExpanded)
            ? window._hierarchicalOrdersExpanded
            : collectExpandedIds(gridId);
        setExpandedIds(gridId, seedExpansion);
        recomputeGridStats(api, gridId);
        if (!api || registeredApis.has(api)) return;
        api.addEventListener('rowDataUpdated', function() {
            recomputeGridStats(api, gridId);
        });
        registeredApis.add(api);
    }

    function toggleParent(api, parentId, gridId = DEFAULT_GRID_ID) {
        const id = normalizeId(parentId);
        if (!id) return;
        const expandedSet = getExpandedSet(gridId);
        if (expandedSet.has(id)) {
            expandedSet.delete(id);
        } else {
            expandedSet.add(id);
        }
        const expandedIds = collectExpandedIds(gridId);
        window._hierarchicalOrdersExpanded = expandedIds;
        dispatchExpansion(gridId, expandedIds);
        if (api) {
            refreshVisibility(api, gridId);
        }
    }

    function isExpanded(parentId, gridId = DEFAULT_GRID_ID) {
        const id = normalizeId(parentId);
        if (!id) return false;
        return getExpandedSet(gridId).has(id);
    }

    window.HierarchicalOrdersGrid = {
        collectExpandedIds,
        hasCollapsedParents,
        isExpanded,
        isRowVisible,
        refreshVisibility,
        register,
        restoreExpansion,
        toggleParent
    };

    window.hierarchicalSymbolRenderer = function(params) {
        if (!params || !params.data) return document.createElement('span');

        const row = params.data;
        const symbol = String(row.symbol || '');
        const wrapper = document.createElement('div');
        wrapper.className = 'flex items-center gap-1';

        if (isParentRow(row)) {
            const parentId = normalizeId(row.client_order_id);
            const hasChildren = Number(row.child_count || 0) > 0;
            if (hasChildren) {
                const toggle = document.createElement('button');
                toggle.type = 'button';
                toggle.className = 'h-4 w-4 text-xs text-gray-300 hover:text-white';
                toggle.textContent = isExpanded(parentId) ? '▾' : '▸';
                toggle.onclick = function(event) {
                    event.stopPropagation();
                    toggleParent(params.api, parentId);
                };
                wrapper.appendChild(toggle);
            } else {
                const spacer = document.createElement('span');
                spacer.className = 'inline-block w-4';
                spacer.textContent = '';
                wrapper.appendChild(spacer);
            }
            const label = document.createElement('span');
            label.className = 'font-semibold';
            label.textContent = symbol;
            wrapper.appendChild(label);
            return wrapper;
        }

        if (isChildRow(row)) {
            const spacer = document.createElement('span');
            spacer.className = 'inline-block w-4 text-gray-400';
            spacer.textContent = '↳';
            wrapper.appendChild(spacer);
            const label = document.createElement('span');
            label.className = 'text-xs';
            label.textContent = symbol;
            wrapper.appendChild(label);
            return wrapper;
        }

        const label = document.createElement('span');
        label.textContent = symbol;
        wrapper.appendChild(label);
        return wrapper;
    };

    window.hierarchicalCancelRenderer = function(params) {
        if (!params || !params.data) return document.createElement('span');

        const btn = document.createElement('button');
        const disabledBase = window._tradingState?.readOnly;
        const row = params.data;

        if (isParentRow(row)) {
            const parentId = normalizeId(row.client_order_id);
            // Intentionally include collapsed children so Cancel All applies
            // to the full parent order rather than only currently visible rows.
            const children = parentId
                ? getChildrenForParent(DEFAULT_GRID_ID, parentId).filter(
                    child => child && !isTerminalStatus(child.status)
                )
                : [];

            const cancelable = children.filter(child => !!normalizeId(child.client_order_id));
            const disabled = disabledBase || cancelable.length === 0;
            btn.className = disabled
                ? 'px-2 py-1 text-xs bg-gray-400 text-gray-200 rounded cursor-not-allowed'
                : 'px-2 py-1 text-xs bg-red-500 text-white rounded hover:bg-red-600';
            btn.textContent = 'Cancel All';
            btn.disabled = disabled;
            if (!disabled) {
                btn.onclick = function() {
                    window.dispatchEvent(new CustomEvent('cancel_parent_order', {
                        detail: {
                            parent_order_id: parentId,
                            symbol: row.symbol || '',
                            children: cancelable.map(child => ({
                                client_order_id: child.client_order_id || '',
                                status: child.status || '',
                                qty: child.qty || 0,
                                slice_num: child.slice_num
                            }))
                        }
                    }));
                };
            }
            return btn;
        }

        const disabled = disabledBase || !row.client_order_id || isTerminalStatus(row.status);
        btn.className = disabled
            ? 'px-2 py-1 text-xs bg-gray-400 text-gray-200 rounded cursor-not-allowed'
            : 'px-2 py-1 text-xs bg-red-500 text-white rounded hover:bg-red-600';
        btn.textContent = 'Cancel';
        btn.disabled = disabled;
        if (!disabled) {
            btn.onclick = function() {
                window.dispatchEvent(new CustomEvent('cancel_order', {
                    detail: {
                        client_order_id: row.client_order_id || '',
                        symbol: row.symbol || '',
                        broker_order_id: row._broker_order_id || ''
                    }
                }));
            };
        }
        return btn;
    };
})();
