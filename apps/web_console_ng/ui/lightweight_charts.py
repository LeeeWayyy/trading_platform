"""Lightweight Charts Integration for NiceGUI.

Library: TradingView Lightweight Charts (Apache 2.0 License)
Version: 4.1.0 (pinned for stability)

Licensing Notes:
- Apache 2.0 License allows commercial use
- Attribution required (included in chart footer)
- Data source: Alpaca Market Data API

Security Notes:
- CDN assets loaded with SRI (Subresource Integrity) hash
- CSP allowlist entry required: script-src cdn.jsdelivr.net
- Alternative: Host locally in /static/vendor/ for airgapped deployments
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from nicegui import ui

logger = logging.getLogger(__name__)

# CDN with SRI hash for supply-chain security
# Hash generated via: curl -s "$CDN_URL" | openssl dgst -sha384 -binary | openssl base64 -A
LIGHTWEIGHT_CHARTS_CDN = (
    "https://cdn.jsdelivr.net/npm/lightweight-charts@4.1.0/dist/lightweight-charts.standalone.production.js"
)
LIGHTWEIGHT_CHARTS_SRI = "sha384-rcCMiCptH4kTlEbg0euOTUKWe72TESbrjElatnG+9BfbmUIV268UK/Pro5biJdGm"

# Local fallback path (for airgapped/high-security deployments)
# CRITICAL: Download and verify hash before deployment:
#   curl -o static/vendor/lightweight-charts.4.1.0.production.js "$LIGHTWEIGHT_CHARTS_CDN"
#   openssl dgst -sha384 static/vendor/lightweight-charts.4.1.0.production.js
LIGHTWEIGHT_CHARTS_LOCAL = "/static/vendor/lightweight-charts.4.1.0.production.js"

# Chart initialization JavaScript template
CHART_INIT_JS = """
(async function() {{
    const container = document.getElementById('{container_id}');
    if (!container) return;

    const loadScriptOnce = (id, src, integrity = null) => {{
        const existing = document.getElementById(id);
        if (existing) {{
            return new Promise((resolve, reject) => {{
                if (existing.dataset.loaded === 'true') {{
                    resolve();
                    return;
                }}
                existing.addEventListener('load', () => resolve(), {{ once: true }});
                existing.addEventListener('error', () => reject(new Error(`Failed to load ${{src}}`)), {{ once: true }});
            }});
        }}

        const script = document.createElement('script');
        script.id = id;
        script.src = src;
        if (integrity) {{
            script.integrity = integrity;
            script.crossOrigin = 'anonymous';
        }}
        return new Promise((resolve, reject) => {{
            script.onload = () => {{
                script.dataset.loaded = 'true';
                resolve();
            }};
            script.onerror = () => reject(new Error(`Failed to load ${{src}}`));
            document.head.appendChild(script);
        }});
    }};

    if (typeof window.LightweightCharts === 'undefined') {{
        window.__lwc_loading_promise = window.__lwc_loading_promise || (async () => {{
            try {{
                await loadScriptOnce('lwc-script-cdn-v410', '{cdn}', '{sri}');
            }} catch (e) {{
                try {{
                    await loadScriptOnce('lwc-script-local-v410', '{local}');
                }} catch (fallbackError) {{
                    console.warn('LightweightCharts load failed from CDN and local fallback', fallbackError);
                }}
            }}
        }})();

        try {{
            await window.__lwc_loading_promise;
        }} finally {{
            if (typeof window.LightweightCharts !== 'undefined') {{
                window.__lwc_ready = true;
            }}
        }}
    }}

    if (typeof window.LightweightCharts === 'undefined') {{
        console.warn('LightweightCharts unavailable; skipping chart init for {chart_id}');
        return;
    }}
    const lwc = window.LightweightCharts;

    // Create chart
    const chart = lwc.createChart(container, {{
        width: {width},
        height: {height},
        layout: {{
            background: {{ type: 'solid', color: '#0f172a' }},
            textColor: '#94a3b8',
        }},
        grid: {{
            vertLines: {{ color: '#1e293b' }},
            horzLines: {{ color: '#334155' }},
        }},
        crosshair: {{
            mode: lwc.CrosshairMode.Normal,
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
    _lock: asyncio.Lock | None = None

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        """Lazily create a shared async lock for loader coordination."""
        if cls._lock is None:
            cls._lock = asyncio.Lock()
        return cls._lock

    @classmethod
    async def ensure_loaded(
        cls,
        *,
        run_javascript: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        """Ensure the library is loaded exactly once with SRI verification."""
        if cls._ready:
            return

        run_js = run_javascript or ui.run_javascript

        async with cls._get_lock():
            if cls._ready:
                return

            cls._loaded = True

            try:
                # Load with SRI hash and crossorigin for supply-chain security
                # Falls back to local copy if CDN fails
                await run_js(
                    f"""
                    (async function() {{
                        if (typeof window.LightweightCharts !== 'undefined') {{
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
                """,
                    timeout=10.0,
                )

                # Wait for library to be ready
                ready_timed_out = True
                for _ in range(100):  # Max 5 seconds
                    try:
                        ready = await run_js("window.__lwc_ready === true")
                        ready_bool = ready is True or (
                            isinstance(ready, str) and ready.strip().lower() == "true"
                        )
                        if ready_bool:
                            ready_timed_out = False
                            cls._ready = True
                            return
                    except Exception:
                        pass  # JavaScript may not be ready yet
                    await asyncio.sleep(0.05)
            except Exception:
                cls._loaded = False
                cls._ready = False
                raise

            cls._loaded = False
            if ready_timed_out:
                raise RuntimeError("Timed out waiting for Lightweight Charts readiness flag")

    @classmethod
    def reset(cls) -> None:
        """Reset loader state (for testing)."""
        cls._loaded = False
        cls._ready = False
        cls._lock = None


__all__ = [
    "LightweightChartsLoader",
    "CHART_INIT_JS",
    "LIGHTWEIGHT_CHARTS_CDN",
    "LIGHTWEIGHT_CHARTS_SRI",
]
