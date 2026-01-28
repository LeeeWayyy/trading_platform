// Hierarchical orders grid helpers (tree data + expansion persistence)

(function() {
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

    function isTerminalStatus(status) {
        return TERMINAL_STATUSES.has(String(status || '').toLowerCase());
    }

    function collectExpandedIds(api) {
        const expanded = [];
        api.forEachNode(node => {
            if (!node || !node.expanded) return;
            if (!node.data || !node.data.is_parent) return;
            const id = node.data.client_order_id;
            if (id) expanded.push(String(id));
        });
        return expanded;
    }

    function restoreExpansion(api, expandedIds) {
        if (!api || !Array.isArray(expandedIds)) return;
        const idSet = new Set(expandedIds.map(id => String(id)));
        api.forEachNode(node => {
            if (!node || !node.data || !node.data.is_parent) return;
            const id = String(node.data.client_order_id || '');
            if (!id) return;
            node.setExpanded(idSet.has(id));
        });
    }

    function dispatchExpansion(gridId, expandedIds) {
        window.dispatchEvent(new CustomEvent('hierarchical_orders_expansion', {
            detail: {
                grid_id: gridId,
                expanded_ids: expandedIds
            }
        }));
    }

    function register(api, gridId) {
        if (!api) return;
        let timer = null;
        const emit = () => {
            const expandedIds = collectExpandedIds(api);
            dispatchExpansion(gridId, expandedIds);
        };
        const scheduleEmit = () => {
            if (timer) window.clearTimeout(timer);
            timer = window.setTimeout(emit, 150);
        };
        api.addEventListener('rowGroupOpened', scheduleEmit);
        api.addEventListener('rowDataUpdated', scheduleEmit);
    }

    window.HierarchicalOrdersGrid = {
        register,
        restoreExpansion
    };

    window.hierarchicalCancelRenderer = function(params) {
        if (!params.data) return document.createElement('span');

        const btn = document.createElement('button');
        const isParent = !!params.data.is_parent;
        const disabledBase = window._tradingState?.readOnly;

        if (isParent) {
            const children = (params.node?.childrenAfterFilter || [])
                .map(node => node?.data)
                .filter(child => child && !isTerminalStatus(child.status));
            const cancelable = children.filter(child => !!child.client_order_id);
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
                            parent_order_id: params.data?.client_order_id || '',
                            symbol: params.data?.symbol || '',
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

        const disabled = disabledBase || !params.data?.client_order_id || isTerminalStatus(params.data?.status);
        btn.className = disabled
            ? 'px-2 py-1 text-xs bg-gray-400 text-gray-200 rounded cursor-not-allowed'
            : 'px-2 py-1 text-xs bg-red-500 text-white rounded hover:bg-red-600';
        btn.textContent = 'Cancel';
        btn.disabled = disabled;
        if (!disabled) {
            btn.onclick = function() {
                window.dispatchEvent(new CustomEvent('cancel_order', {
                    detail: {
                        client_order_id: params.data?.client_order_id || '',
                        symbol: params.data?.symbol || '',
                        broker_order_id: params.data?._broker_order_id || ''
                    }
                }));
            };
        }
        return btn;
    };
})();
