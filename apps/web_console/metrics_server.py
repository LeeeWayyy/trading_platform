"""
FastAPI metrics sidecar for Prometheus metrics collection.

This service runs alongside the Streamlit web_console app to expose
Prometheus metrics at /metrics endpoint with multiprocess support.

Component: P2T3-Phase3-Component6+7
"""

import os
from typing import Dict

from fastapi import FastAPI, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    generate_latest,
    multiprocess,
)

app = FastAPI(
    title="Web Console Metrics",
    description="Prometheus metrics endpoint for OAuth2/mTLS authentication system",
    version="1.0.0",
)


@app.get("/metrics")
def metrics() -> Response:
    """
    Prometheus metrics endpoint with multiprocess support.

    Collects metrics from all Streamlit worker processes via shared
    prometheus_multiproc_data volume (production), or from default
    REGISTRY in single-process/dev mode.

    Returns:
        Response with Prometheus text format metrics
    """
    multiproc_dir = os.getenv("PROMETHEUS_MULTIPROC_DIR")

    if multiproc_dir:
        # Production: Collect metrics from all worker processes
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
    else:
        # Dev/single-process: Use default registry
        from prometheus_client import REGISTRY as registry

    return Response(
        content=generate_latest(registry),
        media_type=CONTENT_TYPE_LATEST,
    )


@app.get("/health")
def health() -> Dict[str, str]:
    """
    Health check endpoint for Docker/k8s readiness/liveness probes.

    Returns:
        Dict with status
    """
    return {"status": "healthy"}
