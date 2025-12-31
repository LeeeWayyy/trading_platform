# NiceGUI Web Console (C0 minimal)
# Local build:
#   docker build -t trading-platform-web-console-ng -f apps/web_console_ng/Dockerfile .

FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY apps/web_console_ng /app/apps/web_console_ng

ENV PYTHONPATH=/app

EXPOSE 8080

CMD ["python", "-m", "apps.web_console_ng.main"]
