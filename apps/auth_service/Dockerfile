# FastAPI auth service Dockerfile
# Runs on port 8001, proxied by nginx for /auth/* routes

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY apps/auth_service/requirements.txt /app/apps/auth_service/requirements.txt
RUN pip install --no-cache-dir -r /app/apps/auth_service/requirements.txt

# Copy auth service code
COPY apps/auth_service /app/apps/auth_service

# Copy shared libs (network_utils, auth modules, etc.)
COPY libs /app/libs

# Create __init__.py files for Python package structure
RUN touch /app/apps/__init__.py && \
    touch /app/apps/auth_service/__init__.py && \
    touch /app/apps/auth_service/routes/__init__.py && \
    touch /app/libs/__init__.py && \
    touch /app/libs/common/__init__.py

# Set PYTHONPATH to include /app for imports
ENV PYTHONPATH=/app

# Expose port 8001
EXPOSE 8001

# Run uvicorn server
CMD ["uvicorn", "apps.auth_service.main:app", "--host", "0.0.0.0", "--port", "8001"]
