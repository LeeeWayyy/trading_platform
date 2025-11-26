# Nginx reverse proxy with mTLS and OAuth2 support
# Component 3 of P2T3 Phase 2 (mTLS) and Component 2 of P3T3 Phase 3 (OAuth2)
#
# Build: docker build -t trading-platform-nginx -f apps/web_console/nginx/Dockerfile .
# Run (mTLS): docker-compose --profile mtls up -d
# Run (OAuth2): docker-compose --profile oauth2 up -d
#
# IMPORTANT: Certificates and dhparam.pem MUST be generated on host BEFORE starting.
# See: docs/RUNBOOKS/web-console-mtls-setup.md (mTLS)
#      docs/RUNBOOKS/web-console-oauth2-setup.md (OAuth2)

FROM nginx:1.25-alpine

# Install OpenSSL for OCSP testing and certificate validation
# Install bash and gettext for entrypoint script and envsubst
RUN apk add --no-cache openssl bash gettext

# Copy nginx configurations
COPY apps/web_console/nginx/nginx.conf /etc/nginx/nginx-mtls.conf
COPY apps/web_console/nginx/nginx-oauth2.conf /etc/nginx/nginx-oauth2.conf

# Component 5: Copy OAuth2 template and entrypoint script
# Template will be processed at runtime by entrypoint.sh
COPY apps/web_console/nginx/nginx-oauth2.conf.template /etc/nginx/nginx-oauth2.conf.template
COPY apps/web_console/nginx/entrypoint.sh /app/nginx/entrypoint.sh

# Create certificate directory (will be mounted from host at runtime)
RUN mkdir -p /etc/nginx/certs

# Create log directory
RUN mkdir -p /var/log/nginx

# Create entrypoint script to select config based on AUTH_MODE
RUN echo '#!/bin/sh' > /docker-entrypoint.sh && \
    echo 'if [ "$AUTH_MODE" = "oauth2" ]; then' >> /docker-entrypoint.sh && \
    echo '  echo "Using OAuth2 configuration with template processing"' >> /docker-entrypoint.sh && \
    echo '  exec /app/nginx/entrypoint.sh' >> /docker-entrypoint.sh && \
    echo 'else' >> /docker-entrypoint.sh && \
    echo '  echo "Using mTLS configuration"' >> /docker-entrypoint.sh && \
    echo '  cp /etc/nginx/nginx-mtls.conf /etc/nginx/nginx.conf' >> /docker-entrypoint.sh && \
    echo '  exec nginx -g "daemon off;"' >> /docker-entrypoint.sh && \
    echo 'fi' >> /docker-entrypoint.sh && \
    chmod +x /docker-entrypoint.sh

# Make entrypoint script executable
RUN chmod +x /app/nginx/entrypoint.sh

# NOTE: nginx -t validation removed from build because certificates
# are mounted at runtime via docker-compose volume.
# Validation happens at startup via docker-compose healthcheck.

# Expose HTTPS port
EXPOSE 443

# Health check (validates config after certs mounted)
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD nginx -t && pgrep nginx || exit 1

# Use entrypoint script to select config
ENTRYPOINT ["/docker-entrypoint.sh"]
