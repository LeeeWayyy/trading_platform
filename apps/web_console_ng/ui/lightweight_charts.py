"""Lightweight Charts Integration for NiceGUI.

Library: TradingView Lightweight Charts (Apache 2.0 License)
Version: 4.1.0 (pinned for stability)

Licensing Notes:
- Apache 2.0 License allows commercial use
- Attribution required (included in chart footer)
- Data source: Alpaca Market Data API

Security Notes:
- CDN assets loaded with SRI (Subresource Integrity) hash
- CSP allowlist entry required: script-src unpkg.com
- Alternative: Host locally in /static/vendor/ for airgapped deployments
"""

from __future__ import annotations

import asyncio
import logging

from nicegui import ui

logger = logging.getLogger(__name__)

# CDN with SRI hash for supply-chain security
# Hash generated via: curl -s "$CDN_URL" | openssl dgst -sha384 -binary | openssl base64 -A
LIGHTWEIGHT_CHARTS_CDN = (
    "https://unpkg.com/lightweight-charts@4.1.0/dist/lightweight-charts.standalone.production.js"
)
LIGHTWEIGHT_CHARTS_SRI = "sha384-2PoRwGg4nLjjsqHMzWaFrNj9FH5kGXsMTxDQXnRrKvJpEfBKqGqSqGfxQR8hG2tM"

# Local fallback path (for airgapped/high-security deployments)
# CRITICAL: Download and verify hash before deployment:
#   curl -o static/vendor/lightweight-charts.4.1.0.production.js "$LIGHTWEIGHT_CHARTS_CDN"
#   openssl dgst -sha384 static/vendor/lightweight-charts.4.1.0.production.js
LIGHTWEIGHT_CHARTS_LOCAL = "/static/vendor/lightweight-charts.4.1.0.production.js"

# Chart initialization JavaScript template
CHART_INIT_JS = """
(function() {{
    const container = document.getElementById('{container_id}');
    if (!container) return;

    // Create chart
    const chart = LightweightCharts.createChart(container, {{
        width: {width},
        height: {height},
        layout: {{
            background: {{ type: 'solid', color: '#1e1e1e' }},
            textColor: '#d1d4dc',
        }},
        grid: {{
            vertLines: {{ color: '#2B2B43' }},
            horzLines: {{ color: '#363C4E' }},
        }},
        crosshair: {{
            mode: LightweightCharts.CrosshairMode.Normal,
        }},
        timeScale: {{
            timeVisible: true,
            secondsVisible: false,
        }},
    }});

    // Create candlestick series
    const candlestickSeries = chart.addCandlestickSeries({{
        upColor: '#26a69a',
        downColor: '#ef5350',
        borderVisible: false,
        wickUpColor: '#26a69a',
        wickDownColor: '#ef5350',
    }});

    // Store references
    window.__charts = window.__charts || {{}};
    window.__charts['{chart_id}'] = {{
        chart: chart,
        candlestickSeries: candlestickSeries,
        markers: [],
        vwapSeries: null,
        twapSeries: null,
    }};

    // Add attribution footer (required by Apache 2.0 license)
    const attribution = document.createElement('div');
    attribution.style.cssText = 'position:absolute;bottom:2px;right:4px;font-size:9px;color:#666;';
    attribution.innerHTML = 'Chart: <a href="https://tradingview.github.io/lightweight-charts/" target="_blank" rel="noopener noreferrer" style="color:#888;">Lightweight Charts</a> | Data: Alpaca';
    container.style.position = 'relative';
    container.appendChild(attribution);

    // Resize handler
    const resizeObserver = new ResizeObserver(entries => {{
        chart.applyOptions({{ width: container.clientWidth }});
    }});
    resizeObserver.observe(container);
}})();
"""


class LightweightChartsLoader:
    """Load Lightweight Charts library via CDN with SRI and fallback."""

    _loaded: bool = False
    _ready: bool = False  # Track if chart API is ready

    @classmethod
    async def ensure_loaded(cls) -> None:
        """Ensure the library is loaded exactly once with SRI verification."""
        if cls._loaded:
            # Wait for ready state if already loading
            for _ in range(100):  # Max 5 seconds
                if cls._ready:
                    return
                await asyncio.sleep(0.05)
            # Still not ready after waiting - raise error (FAIL-CLOSED)
            raise RuntimeError("Lightweight Charts library failed to load")  # noqa: TRY003

        cls._loaded = True

        # Load with SRI hash and crossorigin for supply-chain security
        # Falls back to local copy if CDN fails
        await ui.run_javascript(f"""
            (async function() {{
                if (typeof LightweightCharts !== 'undefined') {{
                    window.__lwc_ready = true;
                    return;
                }}

                try {{
                    const script = document.createElement('script');
                    script.src = '{LIGHTWEIGHT_CHARTS_CDN}';
                    script.integrity = '{LIGHTWEIGHT_CHARTS_SRI}';
                    script.crossOrigin = 'anonymous';

                    await new Promise((resolve, reject) => {{
                        script.onload = resolve;
                        script.onerror = reject;
                        document.head.appendChild(script);
                    }});
                    console.log('Lightweight Charts loaded from CDN');
                }} catch (e) {{
                    console.warn('CDN load failed, using local fallback:', e);
                    const fallback = document.createElement('script');
                    fallback.src = '{LIGHTWEIGHT_CHARTS_LOCAL}';
                    await new Promise((resolve, reject) => {{
                        fallback.onload = resolve;
                        fallback.onerror = reject;
                        document.head.appendChild(fallback);
                    }});
                    console.log('Lightweight Charts loaded from local fallback');
                }}
                window.__lwc_ready = true;
            }})();
        """)

        # Wait for library to be ready
        for _ in range(100):  # Max 5 seconds
            try:
                ready = await ui.run_javascript("window.__lwc_ready === true")
                if ready:
                    cls._ready = True
                    return
            except Exception:
                pass  # JavaScript may not be ready yet
            await asyncio.sleep(0.05)

        raise RuntimeError("Failed to load Lightweight Charts library")

    @classmethod
    def reset(cls) -> None:
        """Reset loader state (for testing)."""
        cls._loaded = False
        cls._ready = False


__all__ = [
    "LightweightChartsLoader",
    "CHART_INIT_JS",
    "LIGHTWEIGHT_CHARTS_CDN",
    "LIGHTWEIGHT_CHARTS_SRI",
]
