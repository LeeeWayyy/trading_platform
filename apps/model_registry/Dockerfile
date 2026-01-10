# Multi-stage Docker build for Model Registry Service
# Stage 1: Builder - install dependencies
# Stage 2: Runtime - copy only needed artifacts for smaller image

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
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /build

# Copy requirements - use service-specific for local, root for CI
# For local ARM64 builds: excludes pyqlib (not needed for this service)
# For CI x86_64 builds: uses full requirements.txt
COPY apps/model_registry/requirements-docker.txt requirements-local.txt
COPY requirements.txt requirements-ci.txt

# Install Python dependencies into a virtual environment
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

# Copy apps (changes frequently)
COPY --chown=trader:trader apps/__init__.py ./apps/
COPY --chown=trader:trader apps/model_registry/ ./apps/model_registry/

# Switch to non-root user
USER trader

# Expose port
EXPOSE 8005

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8005/health || exit 1

# Set Python path to include the app directory
ENV PYTHONPATH=/app:$PYTHONPATH

# Default command
CMD ["uvicorn", "apps.model_registry.main:app", "--host", "0.0.0.0", "--port", "8005"]
