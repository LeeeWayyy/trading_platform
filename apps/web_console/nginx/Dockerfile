# Nginx reverse proxy with mTLS support
# Component 3 of P2T3 Phase 2
#
# Build: docker build -t trading-platform-nginx -f apps/web_console/nginx/Dockerfile .
# Run: docker-compose --profile mtls up -d
#
# IMPORTANT: Certificates and dhparam.pem MUST be generated on host BEFORE starting.
# See: docs/RUNBOOKS/web-console-mtls-setup.md

FROM nginx:1.25-alpine

# Install OpenSSL for OCSP testing and certificate validation
RUN apk add --no-cache openssl

# Copy nginx configuration
COPY apps/web_console/nginx/nginx.conf /etc/nginx/nginx.conf

# Create certificate directory (will be mounted from host at runtime)
RUN mkdir -p /etc/nginx/certs

# Create log directory
RUN mkdir -p /var/log/nginx

# NOTE: nginx -t validation removed from build because certificates
# are mounted at runtime via docker-compose volume.
# Validation happens at startup via docker-compose healthcheck.

# Expose HTTPS port
EXPOSE 443

# Health check (validates config after certs mounted)
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD nginx -t && pgrep nginx || exit 1

# Run nginx in foreground
CMD ["nginx", "-g", "daemon off;"]
