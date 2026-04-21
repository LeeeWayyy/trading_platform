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
            if (existing.dataset.failed === 'true') {{
                existing.remove();
            }} else if (existing.dataset.loaded === 'true') {{
                return Promise.resolve();
            }} else {{
                return new Promise((resolve, reject) => {{
                    existing.addEventListener('load', () => resolve(), {{ once: true }});
                    existing.addEventListener('error', () => reject(new Error(`Failed to load ${{src}}`)), {{ once: true }});
                }});
            }}
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
                script.dataset.failed = 'false';
                resolve();
            }};
            script.onerror = () => {{
                script.dataset.failed = 'true';
                reject(new Error(`Failed to load ${{src}}`));
            }};
            document.head.appendChild(script);
        }});
    }};

    if (typeof window.LightweightCharts === 'undefined') {{
        window.__lwc_loading_promise = window.__lwc_loading_promise || (async () => {{
            try {{
                await loadScriptOnce('lwc-script-cdn-v410', '{cdn}', '{sri}');
            }} catch (cdnError) {{
                try {{
                    await loadScriptOnce('lwc-script-local-v410', '{local}');
                }} catch (fallbackError) {{
                    console.warn('LightweightCharts load failed from CDN and local fallback', fallbackError);
                    throw fallbackError;
                }}
            }}
        }})();

        try {{
            await window.__lwc_loading_promise;
        }} catch (loadError) {{
            window.__lwc_loading_promise = null;
            console.warn('LightweightCharts load promise failed; will retry on next init', loadError);
            throw loadError;
        }} finally {{
            window.__lwc_ready = typeof window.LightweightCharts !== 'undefined';
        }}
    }}

    if (typeof window.LightweightCharts === 'undefined') {{
        console.warn('LightweightCharts unavailable; skipping chart init for {chart_id}');
        return;
    }}
    const lwc = window.LightweightCharts;
    const MIN_CHART_WIDTH = 320;
    const MIN_CHART_HEIGHT = 180;

    // Create chart
    const initialWidth = Math.max(container.clientWidth || 0, {width}, MIN_CHART_WIDTH);
    const initialHeight = Math.max(container.clientHeight || 0, {height}, MIN_CHART_HEIGHT);
    const chart = lwc.createChart(container, {{
        width: initialWidth,
        height: initialHeight,
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
    const resizeObserver = new ResizeObserver(() => {{
        chart.applyOptions({{
            width: Math.max(container.clientWidth || 0, {width}, MIN_CHART_WIDTH),
            height: Math.max(container.clientHeight || 0, {height}, MIN_CHART_HEIGHT),
        }});
    }});
    resizeObserver.observe(container);
}})();
"""


__all__ = [
    "CHART_INIT_JS",
    "LIGHTWEIGHT_CHARTS_CDN",
    "LIGHTWEIGHT_CHARTS_SRI",
    "LIGHTWEIGHT_CHARTS_LOCAL",
]
