# NiceGUI Web Console (C0 minimal)
# Local build:
#   docker build -t trading-platform-web-console-ng -f apps/web_console_ng/Dockerfile .

FROM python:3.11-slim

WORKDIR /app

# Use web console specific requirements (excludes ML dependencies)
COPY apps/web_console_ng/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Create a non-root user and group for security
RUN groupadd -r appuser && useradd --no-create-home -r -g appuser appuser

# Create .nicegui directory for NiceGUI persistence (user storage, etc.)
RUN mkdir -p /app/.nicegui && chown appuser:appuser /app/.nicegui

COPY --chown=appuser:appuser apps/web_console_ng /app/apps/web_console_ng
COPY --chown=appuser:appuser apps/web_console /app/apps/web_console
COPY --chown=appuser:appuser libs /app/libs

ENV PYTHONPATH=/app

EXPOSE 8080

USER appuser

CMD ["python", "-m", "apps.web_console_ng.main"]
