// AG Grid custom renderers for NiceGUI trading console
// Loaded as external script for CSP compliance

window._tradingState = {
    killSwitchEngaged: false,
    circuitBreakerTripped: false
};

window.addEventListener('trading_state_change', function(event) {
    const detail = event.detail || {};
    if ('killSwitch' in detail) {
        window._tradingState.killSwitchEngaged = detail.killSwitch;
    }
    if ('circuitBreaker' in detail) {
        window._tradingState.circuitBreakerTripped = detail.circuitBreaker;
    }
    if (window._positionsGridApi) window._positionsGridApi.refreshCells();
    if (window._ordersGridApi) window._ordersGridApi.refreshCells();
});

function isClosePositionDisabled() {
    return window._tradingState.killSwitchEngaged;
}

function isCancelOrderDisabled() {
    // Cancels are always allowed (risk-reducing), even during kill switch/circuit breaker.
    return false;
}

function isNewEntryDisabled() {
    return window._tradingState.killSwitchEngaged ||
           window._tradingState.circuitBreakerTripped;
}

window.statusBadgeRenderer = function(params) {
    const colors = {
        'pending': 'bg-yellow-100 text-yellow-800',
        'new': 'bg-blue-100 text-blue-800',
        'partial': 'bg-orange-100 text-orange-800',
        'filled': 'bg-green-100 text-green-800',
        'cancelled': 'bg-gray-100 text-gray-800',
        'rejected': 'bg-red-100 text-red-800',
    };
    const colorClass = colors[params.value?.toLowerCase()] || 'bg-gray-100';
    const rawStatus = params.value || '';
    const escapedStatus = rawStatus
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
    const statusBadge = '<span class="px-2 py-0.5 rounded text-xs ' + colorClass + '">' +
           escapedStatus + '</span>';
    let warningBadge = '';
    if (params.data?._missing_all_ids) {
        warningBadge = ' <span class="text-red-600 font-bold" title="CRITICAL: Order has no valid ID - cancel disabled, contact support">X</span>';
    } else if (params.data?._missing_client_order_id) {
        warningBadge = ' <span class="text-yellow-600" title="Order using broker ID (missing client_order_id)">!</span>';
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
