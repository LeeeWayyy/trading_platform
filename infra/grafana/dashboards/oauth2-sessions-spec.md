# OAuth2 Sessions Grafana Dashboard Specification

**Dashboard ID:** `oauth2-sessions`

**Purpose:** Monitor OAuth2/OIDC authentication health, mTLS fallback, and session management

**Related:** P2T3 Phase 3 Component 1+6+7, ADR-015

---

## Dashboard Metadata

```json
{
  "title": "OAuth2 Sessions & Authentication",
  "tags": ["oauth2", "authentication", "mtls", "security"],
  "timezone": "browser",
  "refresh": "30s",
  "time": {
    "from": "now-6h",
    "to": "now"
  }
}
```

---

## Variables

### `auth0_domain`
- **Type:** Query
- **Data Source:** Prometheus
- **Query:** `label_values(oauth2_idp_health_consecutive_failures, auth0_domain)`
- **Refresh:** On Dashboard Load
- **Multi-Select:** false

---

## Panels

### Row 1: IdP Health & Fallback Status

#### Panel 1.1: IdP Health Status (Stat)
**Query:**
```promql
(oauth2_idp_health_consecutive_failures{auth0_domain="$auth0_domain"} == 0) * 1 +
(oauth2_idp_health_consecutive_failures{auth0_domain="$auth0_domain"} > 0) * 0
```

**Thresholds:**
- Green (1): Healthy
- Red (0): Unhealthy

**Value Mappings:**
- 1 → "✓ Healthy"
- 0 → "✗ Unhealthy"

**Unit:** None

---

#### Panel 1.2: Fallback Mode Active (Stat)
**Query:**
```promql
oauth2_idp_fallback_mode{auth0_domain="$auth0_domain"}
```

**Thresholds:**
- Green (0): Normal OAuth2
- Orange (1): Fallback Active

**Value Mappings:**
- 0 → "OAuth2 Active"
- 1 → "⚠ mTLS Fallback"

**Unit:** None

---

#### Panel 1.3: Consecutive Failures (Graph)
**Query:**
```promql
oauth2_idp_health_consecutive_failures{auth0_domain="$auth0_domain"}
```

**Thresholds:**
- 0-2: Green (normal)
- 3+: Red (fallback trigger)

**Y-Axis:** Count
**Legend:** "Consecutive Failures"

---

#### Panel 1.4: Consecutive Successes (Graph)
**Query:**
```promql
oauth2_idp_health_consecutive_successes{auth0_domain="$auth0_domain"}
```

**Thresholds:**
- 0-4: Yellow (recovery in progress)
- 5+: Green (stability threshold reached)

**Y-Axis:** Count
**Legend:** "Consecutive Successes"

---

### Row 2: mTLS Fallback Metrics

#### Panel 2.1: mTLS Authentication Rate (Graph)
**Queries:**
- **Success:** `rate(oauth2_mtls_auth_total{result="success"}[5m])`
- **Failure:** `rate(oauth2_mtls_auth_total{result="failure"}[5m])`

**Y-Axis:** Requests/sec
**Legend:**
- Success (green line)
- Failure (red line)

---

#### Panel 2.2: mTLS Failure Reasons (Pie Chart)
**Query:**
```promql
sum by (reason) (increase(oauth2_mtls_auth_failures_total[1h]))
```

**Legend:**
- `expired`: Certificate Expired
- `revoked`: Certificate Revoked
- `cn_not_allowed`: CN Not in Allowlist
- `crl_error`: CRL Check Failed

---

#### Panel 2.3: Admin Certificate Expiry (Table)
**Query:**
```promql
sort_desc(
  (oauth2_mtls_cert_not_after_timestamp - time()) / 86400
)
```

**Columns:**
- **CN:** `{cn}`
- **Days Until Expiry:** Value (sorted descending)
- **Expires At:** `oauth2_mtls_cert_not_after_timestamp` (formatted as timestamp)

**Thresholds:**
- Green: >7 days
- Yellow: 1-7 days
- Red: <1 day

---

#### Panel 2.4: CRL Fetch Status (Stat)
**Query:**
```promql
(time() - oauth2_mtls_crl_last_update_timestamp) / 3600
```

**Title:** "CRL Age (hours)"

**Thresholds:**
- Green: <1h (fresh)
- Yellow: 1-12h (aging)
- Red: >12h (stale)

**Unit:** hours

---

### Row 3: Session Management

#### Panel 3.1: Active Sessions (Graph)
**Query:**
```promql
oauth2_active_sessions_count
```

**Y-Axis:** Count
**Legend:** "Active Sessions"
**Threshold:** 1000 (warning level from alerts)

---

#### Panel 3.2: Session Secret Age (Stat)
**Query:**
```promql
(time() - oauth2_session_secret_last_rotation_timestamp) / 86400
```

**Title:** "Days Since Last Rotation"

**Thresholds:**
- Green: <85 days
- Yellow: 85-90 days
- Red: >90 days (overdue)

**Unit:** days

---

#### Panel 3.3: Session Creation Rate (Graph)
**Query:**
```promql
rate(oauth2_session_created_total[5m])
```

**Y-Axis:** Sessions/sec
**Legend:** "Creation Rate"

---

#### Panel 3.4: Session Signature Failures (Graph)
**Queries:**
- **Total:** `rate(oauth2_session_signature_failures_total[5m])`
- **By Reason:** `sum by (reason) (rate(oauth2_session_signature_failures_total[5m]))`

**Y-Axis:** Failures/sec
**Legend:**
- Invalid signature (red)
- Expired signature (orange)
- Missing signature (yellow)

---

### Row 4: OAuth2 Flow Metrics

#### Panel 4.1: Authorization Success Rate (Graph)
**Queries:**
- **Success:** `rate(oauth2_authorization_total{result="success"}[5m])`
- **Failure:** `rate(oauth2_authorization_total{result="failure"}[5m])`

**Y-Axis:** Requests/sec
**Legend:**
- Success (green line)
- Failure (red line)

---

#### Panel 4.2: Token Refresh Success Rate (Graph)
**Queries:**
- **Success:** `rate(oauth2_token_refresh_total{result="success"}[5m])`
- **Failure:** `rate(oauth2_token_refresh_total{result="failure"}[5m])`

**Y-Axis:** Requests/sec
**Legend:**
- Success (green line)
- Failure (red line)

---

#### Panel 4.3: Authorization Failure Reasons (Pie Chart)
**Query:**
```promql
sum by (reason) (increase(oauth2_authorization_failures_total[1h]))
```

**Legend:**
- `invalid_client`: Client ID/Secret Error
- `redirect_uri_mismatch`: Redirect URI Mismatch
- `access_denied`: User Denied Access
- `server_error`: Auth0 Server Error

---

#### Panel 4.4: IdP Response Time (Graph)
**Query:**
```promql
histogram_quantile(0.95, rate(oauth2_idp_health_check_duration_seconds_bucket[5m]))
histogram_quantile(0.50, rate(oauth2_idp_health_check_duration_seconds_bucket[5m]))
```

**Y-Axis:** seconds
**Legend:**
- P95 (red line)
- P50 (blue line)

---

### Row 5: Alerts Summary

#### Panel 5.1: Active Alerts (Stat)
**Query:**
```promql
count(ALERTS{alertname=~"IdP.*|Mtls.*|OAuth2.*|Session.*", alertstate="firing"})
```

**Thresholds:**
- Green: 0 (no alerts)
- Red: >0 (alerts firing)

**Value Mappings:**
- 0 → "No Active Alerts"
- >0 → "{{ value }} Active"

---

#### Panel 5.2: Alert Timeline (Alert List)
**Data Source:** Prometheus
**Show:** Current State + Alert History
**Filter:** `alertname=~"IdP.*|Mtls.*|OAuth2.*|Session.*"`
**Columns:**
- Time
- Alert Name
- Severity
- Summary

---

## Annotations

### Auth0 Incidents
**Query:**
```promql
ALERTS{alertname="IdPHealthCheckFailed", alertstate="firing"}
```

**Color:** Red
**Text:** "IdP Outage: {{ $labels.summary }}"

---

### Fallback Mode Changes
**Query:**
```promql
changes(oauth2_idp_fallback_mode[5m]) > 0
```

**Color:** Orange
**Text:** "Fallback Mode: {{ if eq $value 1 }}Activated{{ else }}Deactivated{{ end }}"

---

### Session Secret Rotations
**Query:**
```promql
changes(oauth2_session_secret_last_rotation_timestamp[1h]) > 0
```

**Color:** Blue
**Text:** "Session Secret Rotated"

---

## Links

### Runbooks
- Auth0 IdP Outage: `docs/RUNBOOKS/auth0-idp-outage.md`
- mTLS Certificate Management: `docs/RUNBOOKS/mtls-fallback-admin-certs.md`
- OAuth2 Session Cleanup: `docs/RUNBOOKS/oauth2-session-cleanup.md`
- Session Key Rotation: `docs/RUNBOOKS/session-key-rotation.md`

### External Links
- Auth0 Status Page: `https://status.auth0.com`
- Prometheus Alerts UI: `http://prometheus:9090/alerts`

---

## Export/Import

### Manual Creation
1. Navigate to Grafana → Dashboards → New Dashboard
2. Add panels using specifications above
3. Configure variables
4. Add annotations
5. Save with tags: `oauth2`, `authentication`, `mtls`, `security`

### Terraform/Automation
```hcl
resource "grafana_dashboard" "oauth2_sessions" {
  config_json = file("${path.module}/oauth2-sessions.json")
}
```

### JSON Export Template
```json
{
  "dashboard": {
    "title": "OAuth2 Sessions & Authentication",
    "panels": [
      // Use panel specifications above to generate full JSON
    ],
    "templating": {
      "list": [
        {
          "name": "auth0_domain",
          "type": "query",
          "datasource": "Prometheus",
          "query": "label_values(oauth2_idp_health_consecutive_failures, auth0_domain)"
        }
      ]
    }
  }
}
```

---

## Maintenance

### Metric Name Changes
If metric names change in application code, update:
1. Panel queries
2. Alert expressions
3. Annotation queries

### Adding New Panels
Follow naming convention:
- Row N: Category Name
- Panel N.M: Specific Metric

### Dashboard Versioning
- Tag releases: `v1.0`, `v1.1`, etc.
- Document changes in git commit messages
- Test on staging before production

---

## Related Documentation

- **Prometheus Alerts:** `infra/prometheus/alerts/oauth2.yml`
- **ADR-015:** OAuth2/OIDC Authentication Architecture
- **P2T3 Phase 3 Plan:** `docs/TASKS/P2T3-Phase3_Component6-7_Plan.md`

---

**Version:** 1.0

**Last Updated:** 2025-11-26

**Maintained By:** Platform Team
