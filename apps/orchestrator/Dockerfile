# Multi-stage Docker build for Orchestrator
# Stage 1: Builder - install dependencies
# Stage 2: Runtime - copy only needed artifacts for smaller image
#
# Build modes:
#   - Local (ARM64): BUILD_MODE=local (default) - uses service-specific requirements
#   - CI (x86_64):   BUILD_MODE=ci - uses root requirements.txt

# =============================================================================
# Stage 1: Builder
# =============================================================================
FROM python:3.11-slim as builder

# Build mode: "local" (ARM64) or "ci" (x86_64)
ARG BUILD_MODE=local

# Install build dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    make \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /build

# Copy requirements - use service-specific for local, root for CI
# For local ARM64 builds: excludes pyqlib (not needed for this service)
# For CI x86_64 builds: uses full requirements.txt
COPY apps/orchestrator/requirements-docker.txt requirements-local.txt
COPY requirements.txt requirements-ci.txt

# Install Python dependencies into a virtual environment
# This allows us to copy only the venv to the runtime stage
RUN python -m venv /opt/venv && \
    . /opt/venv/bin/activate && \
    pip install --no-cache-dir --upgrade pip && \
    if [ "$BUILD_MODE" = "ci" ]; then \
        pip install --no-cache-dir -r requirements-ci.txt; \
    else \
        pip install --no-cache-dir -r requirements-local.txt; \
    fi

# =============================================================================
# Stage 2: Runtime
# =============================================================================
FROM python:3.11-slim

# Install runtime dependencies only (no build tools)
RUN apt-get update && apt-get install -y \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN useradd -m -u 1000 trader && \
    mkdir -p /app && \
    chown -R trader:trader /app

# Set working directory
WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Set environment to use the venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Copy application code
# Copy libs first (most stable, best caching)
COPY --chown=trader:trader libs/ ./libs/

# Copy strategies (changes occasionally)
COPY --chown=trader:trader strategies/ ./strategies/

# Copy apps (changes frequently)
COPY --chown=trader:trader apps/__init__.py ./apps/
COPY --chown=trader:trader apps/orchestrator/ ./apps/orchestrator/

# Switch to non-root user
USER trader

# Expose port
EXPOSE 8003

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8003/health || exit 1

# Set Python path to include the app directory
ENV PYTHONPATH=/app:$PYTHONPATH

# Default command
CMD ["uvicorn", "apps.orchestrator.main:app", "--host", "0.0.0.0", "--port", "8003"]
