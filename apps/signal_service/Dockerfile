# Multi-stage Docker build for Signal Service
# Stage 1: Builder - install dependencies (including qlib from source for ARM64)
# Stage 2: Runtime - copy only needed artifacts for smaller image
#
# Build modes:
#   - Local (ARM64): BUILD_MODE=local (default) - uses service-specific requirements,
#                    builds qlib from source, copies artifacts
#   - CI (x86_64):   BUILD_MODE=ci - uses root requirements.txt with pyqlib from pip

# =============================================================================
# Stage 1: Builder
# =============================================================================
FROM python:3.11-slim as builder

# Build mode: "local" (ARM64, qlib from source) or "ci" (x86_64, pyqlib from pip)
ARG BUILD_MODE=local

# Install build dependencies (including those needed for qlib)
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    make \
    libpq-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /build

# Copy requirements - use service-specific for local, root for CI
# For local ARM64 builds: excludes pyqlib (built from source below)
# For CI x86_64 builds: includes pyqlib from pip
COPY apps/signal_service/requirements-docker.txt requirements-local.txt
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

# Copy qlib source and build from source for ARM64 compatibility (local mode only)
# In CI mode, pyqlib is already installed from pip via requirements-ci.txt
COPY qlib* /build/qlib_src/
RUN . /opt/venv/bin/activate && \
    if [ "$BUILD_MODE" != "ci" ] && [ -d "/build/qlib_src" ] && [ "$(ls -A /build/qlib_src 2>/dev/null)" ]; then \
        cd /build/qlib_src && \
        pip install --no-cache-dir cython numpy && \
        pip install --no-cache-dir .; \
    fi


# =============================================================================
# Stage 2: Runtime
# =============================================================================
FROM python:3.11-slim

# Build mode needs to be redeclared in each stage
ARG BUILD_MODE=local

# Install runtime dependencies only (no build tools)
RUN apt-get update && apt-get install -y \
    libpq5 \
    libgomp1 \
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

# Copy model artifacts (local mode only - artifacts may not exist in CI)
# Use wildcard to make COPY optional (won't fail if artifacts/ doesn't exist)
COPY --chown=trader:trader artifact[s]/ ./artifacts/

# Copy apps (changes frequently)
COPY --chown=trader:trader apps/__init__.py ./apps/
COPY --chown=trader:trader apps/signal_service/ ./apps/signal_service/

# Switch to non-root user
USER trader

# Expose port
EXPOSE 8001

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8001/health || exit 1

# Set Python path to include the app directory
ENV PYTHONPATH=/app:$PYTHONPATH

# Default command
CMD ["uvicorn", "apps.signal_service.main:app", "--host", "0.0.0.0", "--port", "8001"]
