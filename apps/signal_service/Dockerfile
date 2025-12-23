# Multi-stage Docker build for Signal Service
# Stage 1: Builder - install dependencies (including qlib from source for ARM64)
# Stage 2: Runtime - copy only needed artifacts for smaller image

# =============================================================================
# Stage 1: Builder
# =============================================================================
FROM python:3.11-slim as builder

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

# Copy requirements first for better layer caching
# Use service-specific requirements (excludes pyqlib - we build from source)
COPY apps/signal_service/requirements-docker.txt requirements.txt

# Install Python dependencies into a virtual environment
# This allows us to copy only the venv to the runtime stage
RUN python -m venv /opt/venv && \
    . /opt/venv/bin/activate && \
    pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy qlib source and build from source for ARM64 compatibility
COPY qlib /build/qlib
RUN . /opt/venv/bin/activate && \
    cd /build/qlib && \
    pip install --no-cache-dir cython numpy && \
    pip install --no-cache-dir .

# =============================================================================
# Stage 2: Runtime
# =============================================================================
FROM python:3.11-slim

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

# Copy model artifacts
COPY --chown=trader:trader artifacts/ ./artifacts/

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
