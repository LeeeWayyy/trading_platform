# NiceGUI Web Console (C0 minimal)
# Local build:
#   docker build -t trading-platform-web-console-ng -f apps/web_console_ng/Dockerfile .

FROM python:3.11-slim

WORKDIR /app

ARG APP_UID=1000
ARG APP_GID=1000

# Use web console specific requirements (excludes ML dependencies)
COPY apps/web_console_ng/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Create a non-root user and group for security. Allow non-unique IDs so
# host-aligned APP_UID/APP_GID values can reuse IDs already present in Debian.
RUN groupadd --gid "${APP_GID}" --non-unique appuser \
    && useradd --uid "${APP_UID}" --gid appuser --no-create-home --non-unique appuser

# Create .nicegui directory for NiceGUI persistence (user storage, etc.)
RUN mkdir -p /app/.nicegui && chown appuser:appuser /app/.nicegui
RUN mkdir -p /app/data/universes && chown -R appuser:appuser /app/data

COPY --chown=appuser:appuser apps/web_console_ng /app/apps/web_console_ng
COPY --chown=appuser:appuser libs /app/libs
COPY --chown=appuser:appuser scripts/data/__init__.py scripts/data/alpaca_sip_sync.py scripts/data/alpaca_corp_actions_sync.py /app/scripts/data/
COPY --chown=appuser:appuser config /app/config
COPY --chown=appuser:appuser strategies /app/strategies

ENV PYTHONPATH=/app

EXPOSE 8080

USER appuser

CMD ["python", "-m", "apps.web_console_ng.main"]
