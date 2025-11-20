# Web Console Dockerfile
# Build: docker build -t trading-platform-web-console -f apps/web_console/Dockerfile .
# Run: docker run -p 8501:8501 --env-file .env trading-platform-web-console

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for running the application
RUN groupadd -r appuser && useradd -r -g appuser -u 1000 appuser

# Copy requirements first for better caching
COPY apps/web_console/requirements.txt /app/apps/web_console/requirements.txt

# Install Python dependencies
RUN pip install --no-cache-dir -r apps/web_console/requirements.txt

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
