// AG Grid custom renderers for NiceGUI trading console
// Loaded as external script for CSP compliance

window._tradingState = {
    killSwitchEngaged: false,
    circuitBreakerTripped: false,
    readOnly: false,
    connectionState: 'connected'
};

window.addEventListener('trading_state_change', function(event) {
    const detail = event.detail || {};
    if ('killSwitch' in detail) {
        window._tradingState.killSwitchEngaged = detail.killSwitch;
    }
    if ('killSwitchState' in detail) {
        window._tradingState.killSwitchEngaged = detail.killSwitchState === 'ENGAGED';
    }
    if ('circuitBreaker' in detail) {
        window._tradingState.circuitBreakerTripped = detail.circuitBreaker;
    }
    if ('circuitBreakerState' in detail) {
        window._tradingState.circuitBreakerTripped = detail.circuitBreakerState === 'TRIPPED';
    }
    if ('readOnly' in detail) {
        window._tradingState.readOnly = !!detail.readOnly;
    }
    if ('connectionState' in detail) {
        window._tradingState.connectionState = detail.connectionState;
    }
    if (window._positionsGridApi) window._positionsGridApi.refreshCells();
    if (window._ordersGridApi) window._ordersGridApi.refreshCells();
    if (window._hierarchicalOrdersGridApi) window._hierarchicalOrdersGridApi.refreshCells();
});

function isClosePositionDisabled() {
    return window._tradingState.killSwitchEngaged;
}

function isCancelOrderDisabled() {
    return window._tradingState.readOnly;
}

function isNewEntryDisabled() {
    if (window._tradingState.readOnly) return true;
    return window._tradingState.killSwitchEngaged ||
           window._tradingState.circuitBreakerTripped;
}

window.statusBadgeRenderer = function(params) {
    const colors = {
        'pending': 'background-color: var(--warning); color: var(--surface-0);',
        'new': 'background-color: var(--info); color: var(--surface-0);',
        'partial': 'background-color: var(--warning); color: var(--surface-0);',
        'filled': 'background-color: var(--profit); color: var(--surface-0);',
        'cancelled': 'background-color: var(--surface-2); color: var(--text-secondary);',
        'rejected': 'background-color: var(--loss); color: var(--surface-0);',
    };
    const style = colors[params.value?.toLowerCase()] || 'background-color: var(--surface-2); color: var(--text-secondary);';
    const rawStatus = params.value || '';
    const escapedStatus = rawStatus
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
    const statusBadge = '<span class="px-2 py-0.5 rounded text-xs" style="' + style + '">' +
           escapedStatus + '</span>';
    let warningBadge = '';
    if (params.data?._missing_all_ids) {
        warningBadge = ' <span style="color: var(--loss); font-weight: 700;" title="CRITICAL: Order has no valid ID - cancel disabled, contact support">X</span>';
    } else if (params.data?._missing_client_order_id) {
        warningBadge = ' <span style="color: var(--warning);" title="Order using broker ID (missing client_order_id)">!</span>';
    }
    return statusBadge + warningBadge;
};

window.cancelButtonRenderer = function(params) {
    if (!params.data) return document.createElement('span');

    const btn = document.createElement('button');
    const disabled = isCancelOrderDisabled() || params.data?._missing_all_ids;
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

window.closePositionRenderer = function(params) {
    if (!params.data) return document.createElement('span');

    const btn = document.createElement('button');
    const disabled = isClosePositionDisabled();
    btn.className = disabled
        ? 'px-2 py-1 text-xs bg-gray-400 text-gray-200 rounded cursor-not-allowed'
        : 'px-2 py-1 text-xs bg-red-500 text-white rounded hover:bg-red-600';
    btn.textContent = 'Close';
    btn.disabled = disabled;
    if (!disabled) {
        btn.onclick = function() {
            window.dispatchEvent(new CustomEvent('close_position', {
                detail: {
                    symbol: params.data?.symbol || '',
                    qty: params.data?.qty || 0
                }
            }));
        };
    }
    return btn;
};
