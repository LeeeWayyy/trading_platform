// DOM ladder rendering helpers

(function() {
    function formatPrice(value) {
        const num = Number(value);
        if (!Number.isFinite(num)) return '--';
        return num.toFixed(2);
    }

    function formatSize(value) {
        const num = Number(value);
        if (!Number.isFinite(num)) return '--';
        return num.toFixed(0);
    }

    function ensureContainer(containerId) {
        const el = document.getElementById(containerId);
        if (!el) return null;
        if (!el.dataset.domReady) {
            el.dataset.domReady = '1';
            el.classList.add('dom-ladder-root');
        }
        return el;
    }

    function dispatchPriceClick(symbol, side, price) {
        window.dispatchEvent(new CustomEvent('dom_price_click', {
            detail: {
                symbol: symbol,
                side: side,
                price: price
            }
        }));
    }

    function buildRow(level, side, symbol) {
        const row = document.createElement('div');
        row.className = `dom-ladder-row dom-ladder-${side}`;
        if (level.is_large) {
            row.classList.add('dom-ladder-large');
        }

        const sizeCell = document.createElement('div');
        sizeCell.className = 'dom-ladder-size';
        sizeCell.textContent = formatSize(level.size);

        const priceCell = document.createElement('button');
        priceCell.className = 'dom-ladder-price';
        priceCell.textContent = formatPrice(level.price);
        priceCell.onclick = function() {
            const actionSide = side === 'bid' ? 'sell' : 'buy';
            dispatchPriceClick(symbol, actionSide, level.price);
        };

        const bar = document.createElement('div');
        bar.className = 'dom-ladder-bar';
        const ratio = Math.max(0, Math.min(1, Number(level.ratio || 0)));
        bar.style.width = `${ratio * 100}%`;

        if (side === 'bid') {
            row.appendChild(sizeCell);
            row.appendChild(priceCell);
            row.appendChild(bar);
        } else {
            row.appendChild(bar);
            row.appendChild(priceCell);
            row.appendChild(sizeCell);
        }

        return row;
    }

    function update(containerId, payload) {
        const container = ensureContainer(containerId);
        if (!container) return;
        if (!payload) return;

        container.innerHTML = '';

        const symbol = payload.symbol || '--';
        const asks = Array.isArray(payload.asks) ? payload.asks.slice() : [];
        const bids = Array.isArray(payload.bids) ? payload.bids.slice() : [];

        asks.reverse().forEach(level => {
            container.appendChild(buildRow(level, 'ask', symbol));
        });

        const midRow = document.createElement('div');
        midRow.className = 'dom-ladder-mid';
        midRow.textContent = payload.mid ? `Mid ${formatPrice(payload.mid)}` : 'Mid --';
        container.appendChild(midRow);

        bids.forEach(level => {
            container.appendChild(buildRow(level, 'bid', symbol));
        });
    }

    function clear(containerId, message) {
        const container = ensureContainer(containerId);
        if (!container) return;
        container.innerHTML = '';
        const empty = document.createElement('div');
        empty.className = 'dom-ladder-empty';
        empty.textContent = message || 'No depth data';
        container.appendChild(empty);
    }

    window.DOMLadder = {
        update,
        clear
    };
})();
