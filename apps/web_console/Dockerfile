# Web Console Dockerfile - Multi-stage Build
# Build: docker build -t trading-platform-web-console -f apps/web_console/Dockerfile .
# Run: docker run -p 8501:8501 --env-file .env trading-platform-web-console
#
# Multi-stage build:
# - Stage 1 (builder): Install build dependencies and Python packages
# - Stage 2 (final): Copy installed packages, runtime dependencies only, non-root user

# ============================================================================
# Stage 1: Builder - Install dependencies with build tools
# ============================================================================
FROM python:3.11-slim as builder

WORKDIR /build

# Install build dependencies (gcc needed for some Python packages)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install to a target directory
COPY apps/web_console/requirements.txt .
RUN pip install --no-cache-dir --target=/build/packages -r requirements.txt

# ============================================================================
# Stage 2: Final - Minimal runtime image
# ============================================================================
FROM python:3.11-slim

WORKDIR /app

# Install only runtime dependencies (no gcc, only postgresql-client and curl)
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for running the application
RUN groupadd -r appuser && useradd -r -g appuser -u 1000 appuser

# Copy installed packages from builder stage
COPY --from=builder /build/packages /usr/local/lib/python3.11/site-packages

# Copy application code
COPY apps/web_console /app/apps/web_console

# Change ownership to non-root user
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose Streamlit port
EXPOSE 8501

# Health check (runs as appuser)
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

# Run Streamlit app as non-root user
CMD ["streamlit", "run", "apps/web_console/app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
