# libs/platform

## Identity
- **Type:** Library Group (Platform Services)
- **Location:** `libs/platform/`

## Overview
Platform service libraries for administration, alerts, analytics, secrets management, tax lot tracking, and web console authentication:

- **admin/** - Admin utilities for API key management
- **alerts/** - Alert rules, delivery models, and notification workflows
- **analytics/** - Analytics tools for microstructure, event studies, and attribution
- **secrets/** - Secrets management with pluggable backends
- **tax/** - Tax lot tracking and wash sale detection
- **web_console_auth/** - JWT/mTLS auth library for web console

## Libraries

### libs/platform/admin
See [libs/admin.md](./admin.md) for detailed specification.

**Purpose:** Admin utilities for API key generation, hashing, validation, and revocation tracking.

**Key Features:**
- API key generation
- Key hashing and validation
- Revocation tracking

### libs/platform/alerts
See [libs/alerts.md](./alerts.md) for detailed specification.

**Purpose:** Alert rules, delivery models, and PII masking helpers for notification workflows.

**Key Features:**
- Alert manager
- Delivery service
- Multiple channels (email, Slack, SMS)
- PII masking
- Deduplication

### libs/platform/analytics
See [libs/analytics.md](./analytics.md) for detailed specification.

**Purpose:** Analytics tools for microstructure analysis, event studies, volatility modeling, and factor attribution.

**Key Features:**
- Execution quality analysis
- Microstructure modeling
- Event study framework
- Factor attribution
- Volatility modeling

### libs/platform/secrets
See [libs/secrets.md](./secrets.md) for detailed specification.

**Purpose:** Secrets management with pluggable backends for Vault, AWS Secrets Manager, and environment variables.

**Key Features:**
- Vault backend integration
- AWS Secrets Manager backend
- Environment variable backend (dev only)
- 90-day secret rotation
- Caching with TTL

### libs/platform/tax
See [libs/tax.md](./tax.md) for detailed specification.

**Purpose:** Tax lot tracking, wash sale detection, and Form 8949 export utilities.

**Key Features:**
- Tax lot tracking
- Wash sale detection
- Tax loss harvesting
- Form 8949 export

### libs/platform/web_console_auth
See [libs/web_console_auth.md](./web_console_auth.md) for detailed specification.

**Purpose:** JWT/mTLS auth library for web console sessions, roles, permissions, and rate limiting.

**Key Features:**
- JWT manager
- Session management
- Role-based permissions
- Rate limiter
- Audit logger
- JWKS validation

## Dependencies
- **Internal:** libs/core/common, libs/core/redis_client
- **External:** boto3 (AWS), hvac (Vault), cryptography

## Related Specs
- Individual library specs listed above
- [../services/auth_service.md](../services/auth_service.md) - Authentication service
- [../services/web_console.md](../services/web_console.md) - Web console
- [../services/web_console_ng.md](../services/web_console_ng.md) - Next-gen web console

## Metadata
- **Last Updated:** 2026-01-14
- **Source Files:** `libs/platform/` (group index)
- **ADRs:** N/A
