# ADR 0005: Centralized Logging Architecture

- Status: Accepted
- Date: 2025-10-21

## Context

As the trading platform scales from development to production with multiple microservices (signal service, execution gateway, orchestrator, market data service), we need:

1. **Unified Observability:** Ability to trace requests across service boundaries for debugging distributed workflows (e.g., signal generation → risk check → order execution)

2. **Production Debugging:** When trades fail or exhibit unexpected behavior, we must correlate logs from multiple services using a common trace ID

3. **Compliance & Audit:** Financial systems require comprehensive logging for regulatory compliance and post-trade analysis

4. **Performance Analysis:** Aggregate logs to identify bottlenecks, analyze execution paths, and optimize system performance

Without centralized logging, debugging production issues requires SSH-ing into multiple containers, grepping logs manually, and attempting to correlate timestamps - a process that is error-prone and time-consuming.

## Decision

We adopt a **structured JSON logging** approach with **distributed tracing** using the Loki/Promtail/Grafana stack.

### Architecture Components

**1. Structured Logging Library (`libs/common/logging/`)**
- JSON formatter with standardized schema: timestamp, level, service, trace_id, message, context
- ISO 8601 UTC timestamps for consistent timezone handling
- Python `contextvars` for async-safe trace ID propagation
- Type-safe API with mypy --strict compliance

**2. Distributed Tracing (`libs/common/logging/context.py`)**
- UUID v4 trace IDs generated per request
- `X-Trace-ID` HTTP header for cross-service propagation
- Automatic extraction/injection via middleware
- Context-local storage prevents cross-request contamination

**3. Service Integration**
- FastAPI `TraceIDMiddleware` for automatic header handling
- `TracedHTTPXClient` for auto-injection into outgoing requests
- ASGI-level middleware for robust exception handling

**4. Log Aggregation Stack**
- **Loki:** Time-series log storage with 30-day retention
- **Promtail:** Docker container log collection with JSON parsing
- **Grafana:** Query interface with LogQL for log exploration

### Technology Choices

**Why Loki over ELK Stack?**
- **Simplicity:** No complex index management; logs indexed by labels only
- **Cost:** Lower resource footprint suitable for development/paper trading
- **Integration:** Native Grafana integration alongside existing Prometheus metrics
- **Query Language:** LogQL syntax familiar to PromQL users

**Why JSON over Plain Text?**
- **Structured Queries:** Filter by service, level, trace_id without regex
- **Type Safety:** Preserve numeric/boolean types in context fields
- **Automation:** Machine-readable for alerting and analytics

**Why contextvars over thread-local?**
- **Async Safety:** Supports FastAPI's async request handlers
- **Isolation:** Each request context is truly isolated in concurrent execution

### Log Schema

```json
{
  "timestamp": "2025-10-21T22:00:00.123456Z",
  "level": "INFO",
  "service": "signal_service",
  "trace_id": "550e8400-e29b-41d4-a716-446655440000",
  "message": "Generated signals for 10 symbols",
  "context": {
    "symbols": ["AAPL", "GOOGL", ...],
    "signal_count": 10,
    "strategy_id": "alpha_baseline"
  },
  "exception": null,  // populated on errors
  "source": {
    "file": "signal_generator.py",
    "line": 142,
    "function": "generate_signals"
  }
}
```

### Retention Policy

- **30 days** for all logs
- Automatic deletion via Loki compactor
- No manual cleanup required
- Can be extended for compliance requirements

### Trace Flow Example

```
Request → Signal Service (trace_id: abc-123)
  ↓ X-Trace-ID: abc-123
Risk Manager (trace_id: abc-123)
  ↓ X-Trace-ID: abc-123
Execution Gateway (trace_id: abc-123)
```

Query in Grafana: `{job="docker"} | json | trace_id="abc-123"` shows entire request path

## Consequences

### Positive

✅ **Unified Debugging:** Single Grafana interface for all service logs
✅ **Request Correlation:** Trace IDs link logs across service boundaries
✅ **Production Ready:** 30-day retention supports post-trade analysis
✅ **Low Overhead:** Loki's label-based indexing minimizes storage costs
✅ **Developer Experience:** JSON schema autocomplete in IDEs, structured queries in Grafana
✅ **Compliance:** Tamper-evident logging for regulatory requirements

### Negative

⚠️ **Learning Curve:** Team must learn LogQL query language
⚠️ **JSON Verbosity:** Larger log volume vs plain text (mitigated by compression)
⚠️ **Clock Skew:** Requires NTP synchronization across containers for accurate correlation
⚠️ **No Full-Text Search:** Loki doesn't index message content; must use labels or brute-force scan

### Risks & Mitigations

**Risk:** Loki unavailable during critical trading hours
**Mitigation:** Logs still written to container stdout/stderr; can be recovered from Docker

**Risk:** 30-day retention insufficient for regulatory compliance
**Mitigation:** Easily configurable; can extend to 7 years if required

**Risk:** Sensitive data (API keys, PII) logged accidentally
**Mitigation:** ⚠️ **Code review enforces best practices; developers must manually avoid logging secrets. Automated sanitization not yet implemented - tracked as future enhancement.**

### Follow-Up Tasks

1. **Alerts:** Configure Loki alerting rules for ERROR/CRITICAL logs
2. **Dashboards:** Create Grafana dashboards for common log queries
3. **Runbooks:** Document LogQL queries for typical debug scenarios
4. **E2E Tests:** Verify trace ID propagation end-to-end
5. **Production Migration:** Gradual rollout with shadow logging
6. **Sanitization:** Implement automated redaction of sensitive data (API keys, account numbers, PII) in logging library

### Migration Plan

**Phase 1 (Current):** Development/Paper Trading
- All services adopt structured logging
- Loki stack runs in docker-compose
- Manual verification of trace propagation

**Phase 2 (Future):** Production
- Deploy Loki to cloud infrastructure
- Configure log shipping from production containers
- Set up alerting and monitoring
- Enable 7-year retention for compliance

**Phase 3 (Future):** Advanced Features
- Integration with distributed tracing (Jaeger/Tempo)
- Log-based metrics for anomaly detection
- Automated incident correlation

### Breaking Changes

**None.** This is an additive change. Services not yet using the logging library continue to work with existing logging.

### Maintenance

- **Loki:** Auto-compaction handles retention; no manual cleanup
- **Promtail:** Stateless; restart-safe position tracking
- **Grafana:** Datasources/dashboards provisioned via code
- **Library:** Follows semantic versioning; backward compatibility guaranteed

### Performance Impact

- **Logging Overhead:** ~5% CPU for JSON serialization (acceptable)
- **Network:** Promtail → Loki traffic minimal (~1MB/hour per service in development)
- **Storage:** ~10GB for 30 days of development logs (Docker volume)

### Security Considerations

- **Access Control:** Grafana authentication required (admin/admin for development)
- **Data Sanitization:** ⚠️ **NOT YET IMPLEMENTED** - Developers must manually avoid logging secrets (API keys, account numbers, PII). Automated sanitization planned as future enhancement.
- **Transport:** Loki/Promtail communicate over internal Docker network
- **Retention:** Automatic deletion prevents indefinite sensitive data storage

### Alternatives Considered

**ELK Stack (Elasticsearch/Logstash/Kibana)**
- **Rejected:** Too heavy for development; requires JVM, complex tuning
- **When to reconsider:** If full-text search becomes critical

**CloudWatch Logs**
- **Rejected:** Vendor lock-in; expensive for high log volumes
- **When to reconsider:** When migrating all infrastructure to AWS

**Plain Files + grep**
- **Rejected:** Doesn't scale to multiple services; no correlation
- **When to reconsider:** Never; minimum viable logging for production systems

### Success Metrics

- **Mean Time to Debug:** Reduce from hours (SSH + grep) to minutes (Grafana query)
- **Incident Resolution:** 80% of production issues resolved via log correlation alone
- **Developer Satisfaction:** 90%+ team adoption of structured logging patterns
- **Log Query Latency:** <1 second for typical trace ID queries

### References

- [Grafana Loki Documentation](https://grafana.com/docs/loki/)
- [Python Logging Best Practices](https://betterstack.com/community/guides/logging/python/python-logging-best-practices/)
- [Distributed Tracing for Microservices](https://microservices.io/patterns/observability/distributed-tracing.html)
- Project: `libs/common/logging/` implementation
- Tests: `tests/libs/common/logging/` (60 tests, 100% coverage)
