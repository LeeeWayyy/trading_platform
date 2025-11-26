#!/bin/bash
set -e

# Entrypoint script for Nginx OAuth2 configuration with CSP template processing
# Component 5 of P3T3 Phase 3: CSP Hardening
#
# Purpose:
# - Generate nginx.conf from template using envsubst
# - Replace ${CSP_REPORT_ONLY} placeholder to toggle CSP enforcement mode
# - Validate generated config before starting Nginx
#
# Environment Variables:
# - CSP_REPORT_ONLY: "true" for report-only mode, "false" for enforcement (default: false)
#
# Usage:
# - docker run -e CSP_REPORT_ONLY=true ...
# - docker-compose.yml: environment: CSP_REPORT_ONLY=${CSP_REPORT_ONLY:-false}

# Default CSP_REPORT_ONLY to "false" (enforcement mode) if not set
export CSP_REPORT_ONLY=${CSP_REPORT_ONLY:-false}

echo "========================================="
echo "Nginx OAuth2 Entrypoint"
echo "========================================="
echo "Generating nginx.conf from template..."
echo "CSP_REPORT_ONLY=${CSP_REPORT_ONLY}"
echo ""

# Generate nginx.conf from template
# envsubst replaces ${CSP_REPORT_ONLY} with environment variable value
# If CSP_REPORT_ONLY=true:  add_header Content-Security-Policy-Report-Only ...
# If CSP_REPORT_ONLY=false: add_header Content-Security-Policy ...
#
# NOTE: We use a special syntax in the template:
# add_header Content-Security-Policy${CSP_REPORT_ONLY:+-Report-Only} ...
#
# This evaluates to:
# - "Content-Security-Policy" if CSP_REPORT_ONLY is empty or "false"
# - "Content-Security-Policy-Report-Only" if CSP_REPORT_ONLY is "true"
#
# However, bash parameter expansion doesn't work exactly as expected in envsubst.
# We need to transform the boolean to the correct suffix.

# Transform CSP_REPORT_ONLY boolean to header suffix
# Codex Commit Review: MEDIUM - Normalize case to accept TRUE/True/1/yes/on
CSP_REPORT_ONLY_NORMALIZED=$(echo "$CSP_REPORT_ONLY" | tr '[:upper:]' '[:lower:]')
case "$CSP_REPORT_ONLY_NORMALIZED" in
    true|1|yes|on)
        export CSP_REPORT_ONLY="-Report-Only"
        echo "CSP Mode: Report-Only (violations logged, not blocked)"
        ;;
    *)
        export CSP_REPORT_ONLY=""
        echo "CSP Mode: Enforcement (violations blocked)"
        ;;
esac

# Run envsubst to replace ${CSP_REPORT_ONLY} in template
envsubst '${CSP_REPORT_ONLY}' < /etc/nginx/nginx-oauth2.conf.template > /etc/nginx/nginx.conf

echo ""
echo "Generated nginx.conf:"
echo "========================================="
# Show CSP header lines for verification
grep -n "Content-Security-Policy" /etc/nginx/nginx.conf || echo "WARNING: No CSP headers found!"
echo "========================================="
echo ""

# Validate generated config
echo "Validating nginx configuration..."
nginx -t

if [ $? -eq 0 ]; then
    echo "Configuration validation successful!"
else
    echo "ERROR: Configuration validation failed!"
    exit 1
fi

echo ""
echo "Starting Nginx..."
echo "========================================="

# Start Nginx (foreground mode)
exec nginx -g 'daemon off;'
