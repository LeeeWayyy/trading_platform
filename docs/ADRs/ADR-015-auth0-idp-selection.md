# ADR-015: Auth0 for Production OAuth2/OIDC Identity Provider

**Status:** Proposed
**Date:** 2025-11-23
**Deciders:** Platform Team
**Reviewers:** [To be assigned]
**Approver:** [To be assigned]
**Related:** P2T3 Phase 3 (OAuth2/OIDC Authentication)

## Context

Web console requires production-grade OAuth2/OIDC authentication with:
- SSO capabilities for future multi-service support
- MFA enforcement
- Centralized user management
- High availability (99.99%+ SLA)
- Compliance (SOC2, ISO27001)
- US-based data residency

Internal trading platform (not third-party SaaS), budget-conscious.

## Decision

Use **Auth0** as the production IdP for OAuth2/OIDC authentication.

## Rationale

### Auth0 Advantages:
1. **Fastest MVP setup**: 30-min registration vs. 2-3 days Keycloak setup
2. **Managed service**: No infrastructure maintenance, automatic updates
3. **99.99% SLA**: Enterprise-grade reliability with US-based hosting
4. **SOC2/ISO27001**: Built-in compliance certification
5. **Built-in MFA**: TOTP, SMS, email OTP support
6. **Cost-effective for internal use**: $240/year (1000 MAU free tier sufficient)
7. **Developer experience**: Excellent docs, SDKs, debugging tools

### Trade-offs Accepted:
1. **Vendor lock-in risk**: Mitigated by adapter pattern (future Keycloak migration possible)
2. **Ongoing cost**: $240/year acceptable for internal platform (<10 users)
3. **External dependency**: Mitigated by mTLS fallback for IdP outages

### Alternatives Considered:

| Option | Pros | Cons | Decision |
|--------|------|------|----------|
| **Keycloak (self-hosted)** | Open-source, no vendor lock-in, full control | 2-3 days setup, infrastructure maintenance, no built-in SLA | Rejected (time cost) |
| **Okta** | Enterprise-grade, similar features | $1500+/year, overkill for internal use | Rejected (cost) |
| **AWS Cognito** | AWS-native, cheap ($0-50/month) | Complex UX, limited MFA, harder SSO | Rejected (UX concerns) |
| **Roll-our-own JWT** | Full control, no external dependency | Security risk, no MFA, manual user mgmt, audit burden | Rejected (security) |

## Consequences

### Positive:
- Rapid deployment: Production-ready in 1.5 days vs. 1-2 weeks for self-hosted
- Zero infrastructure overhead: No servers to patch, monitor, or backup
- Enterprise security out-of-box: MFA, JWKS rotation, breach detection
- Future SSO support: Can add SAML/social logins without code changes

### Negative:
- External dependency: Auth0 outage blocks logins (mitigated by mTLS fallback)
- Recurring cost: $240/year (acceptable for internal platform)
- Migration effort if switching: Adapter pattern reduces but doesn't eliminate

### Mitigation:
- **mTLS fallback**: Emergency authentication mode for Auth0 outages
- **Adapter pattern**: Abstract IdP interface to ease future migration
- **AWS Secrets Manager**: Credentials portable to any IdP
- **Annual review**: Re-evaluate if user count grows or cost increases

## Implementation

- Phase 3 Component 1: Auth0 registration and configuration
- Phase 3 Component 2: OAuth2 flow with Auth0 endpoints
- Phase 3 Component 4: mTLS fallback for IdP outages
- Phase 4 (future): Keycloak adapter if migration needed

## References

- Auth0 Pricing: https://auth0.com/pricing
- Auth0 Documentation: https://auth0.com/docs
- P2T3 Phase 3 Final Plan: `docs/TASKS/P2T3_Phase3_FINAL_PLAN.md`
- mTLS fallback runbook: (to be created in Component 5)
