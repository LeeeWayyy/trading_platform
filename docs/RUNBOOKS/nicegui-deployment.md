# NiceGUI Deployment Runbook

**Last Updated:** 2026-01-04
**Related:** [nicegui-architecture](../CONCEPTS/nicegui-architecture.md)

## Prerequisites

- Docker and docker-compose installed
- Redis running and accessible
- PostgreSQL running and accessible
- Environment variables configured

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `DATABASE_URL` | PostgreSQL connection string | Yes |
| `REDIS_URL` | Redis connection string | Yes |
| `SECRET_KEY` | Session encryption key | Yes |
| `AUTH_PROVIDER` | oauth2/mtls/basic/dev | Yes |
| `OAUTH2_CLIENT_ID` | OAuth2 client ID | If OAuth2 |
| `OAUTH2_CLIENT_SECRET` | OAuth2 client secret | If OAuth2 |

## Docker Build

```bash
# Build the NiceGUI image
docker build -t web-console-ng:latest -f apps/web_console_ng/Dockerfile .

# Verify build
docker images | grep web-console-ng
```

## Docker Compose Deployment

```yaml
# docker-compose.yml
services:
  web-console-ng:
    image: web-console-ng:latest
    ports:
      - "8080:8080"
    environment:
      - DATABASE_URL=postgresql://user:pass@db:5432/trading
      - REDIS_URL=redis://redis:6379
      - SECRET_KEY=${SECRET_KEY}
      - AUTH_PROVIDER=oauth2
    depends_on:
      - db
      - redis
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 30s
      timeout: 10s
      retries: 3
```

```bash
# Deploy
docker-compose up -d web-console-ng

# Check status
docker-compose ps
docker-compose logs -f web-console-ng
```

## Health Check Verification

```bash
# Check health endpoint
curl -s http://localhost:8080/health | jq .

# Expected response:
# {
#   "status": "healthy",
#   "database": "connected",
#   "redis": "connected",
#   "version": "1.0.0"
# }
```

## Nginx Configuration

```nginx
upstream nicegui {
    server web-console-ng:8080;
}

server {
    listen 443 ssl;
    server_name console.example.com;

    ssl_certificate /etc/nginx/ssl/cert.pem;
    ssl_certificate_key /etc/nginx/ssl/key.pem;

    location / {
        proxy_pass http://nicegui;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Scaling Procedures

### Horizontal Scaling

```bash
# Scale to 3 instances
docker-compose up -d --scale web-console-ng=3

# Verify instances
docker-compose ps web-console-ng
```

### Resource Limits

```yaml
services:
  web-console-ng:
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 2G
        reservations:
          cpus: '0.5'
          memory: 512M
```

## Rollback Procedure

```bash
# List previous images
docker images web-console-ng --format "{{.Tag}} {{.CreatedAt}}"

# Rollback to previous version
docker-compose stop web-console-ng
docker tag web-console-ng:latest web-console-ng:rollback
docker tag web-console-ng:v1.0.0 web-console-ng:latest
docker-compose up -d web-console-ng

# Verify
docker-compose logs -f web-console-ng
```

## Monitoring

- Prometheus metrics at `/metrics`
- Grafana dashboard for key metrics
- Alert on health check failures

## Troubleshooting Quick Reference

| Issue | Check | Resolution |
|-------|-------|------------|
| Container not starting | `docker logs` | Check environment vars |
| Health check failing | Redis/DB connectivity | Verify network |
| WebSocket issues | Nginx config | Check upgrade headers |
| High memory | Connection leaks | Restart container |
