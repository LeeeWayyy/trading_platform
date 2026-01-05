# ADR-0031: NiceGUI Migration from Streamlit

**Status:** Accepted
**Date:** 2026-01-04
**Decision Makers:** Development Team
**Related ADRs:** None
**Related Tasks:** P5T1-P5T9

## Context

The trading platform's web console was originally built with Streamlit, a Python framework for building data applications. While Streamlit provided rapid development capabilities, several fundamental limitations became apparent as the application grew:

### Execution Model Limitations

1. **Script-Rerun Model**: Streamlit re-executes the entire script on every user interaction, causing:
   - UI flicker during updates
   - Loss of local state between reruns
   - Performance degradation with complex UIs
   - Difficulty implementing stateful components

2. **Synchronous Request Blocking**: All operations block the main thread:
   - Database queries freeze the UI
   - API calls cause visible delays
   - No concurrent operation support
   - Poor user experience for slow operations

3. **Session State Coupling**: `st.session_state` issues:
   - Global state pollution
   - Difficult to test
   - Race conditions between widgets
   - No isolation between components

4. **Flow Control Problems**: `st.stop()` non-standard behavior:
   - Exceptions for flow control
   - Difficult to reason about execution paths
   - Complicates error handling
   - Testing challenges

### UI/UX Limitations

5. **Static Data Tables**: Limited interactivity:
   - No inline editing
   - No advanced sorting/filtering
   - No column resizing
   - Poor performance with large datasets

6. **Polling Inefficiency**: `streamlit_autorefresh` required for updates:
   - Unnecessary network traffic
   - Battery drain on mobile devices
   - Delayed updates (polling interval)
   - No server-push capability

7. **Limited Layout Control**: Restricted UI customization:
   - Column-based layout only
   - Limited responsive design
   - Minimal CSS customization
   - No complex component composition

## Decision

Migrate the web console from Streamlit to NiceGUI framework.

### Key Changes

1. **Event-Driven AsyncIO Architecture**
   - Component-based UI updates (no full reruns)
   - Async/await for non-blocking operations
   - Proper state management per component

2. **FastAPI Middleware Integration**
   - `@requires_auth` decorator for authentication
   - Session-based auth with Redis backend
   - CSRF protection via double-submit cookie
   - Rate limiting at middleware level

3. **AG Grid for Interactive Tables**
   - Inline editing support
   - Advanced filtering and sorting
   - Column resizing and reordering
   - Virtual scrolling for large datasets

4. **WebSocket Push for Real-Time Updates**
   - Server-initiated updates
   - No polling required
   - Immediate data refresh
   - Reduced network traffic

## Alternatives Considered

### React/Next.js
- **Rejected**: Would require separate frontend repository, different skill set
- **Trade-off**: More ecosystem support but higher maintenance burden

### Vue.js
- **Rejected**: Same concerns as React regarding repository split
- **Trade-off**: Simpler than React but still requires frontend expertise

### Dash/Plotly
- **Rejected**: Callback complexity, limited async support
- **Trade-off**: Good for dashboards but poor for interactive applications

### Panel/Holoviz
- **Rejected**: Less mature, smaller community
- **Trade-off**: Good Python integration but limited enterprise support

### Streamlit Improvements
- **Rejected**: Fundamental model limitations cannot be addressed
- **Trade-off**: No migration cost but blocked on framework limitations

## Consequences

### Positive

1. **Real-Time Updates**: WebSocket push eliminates polling overhead
2. **Async Operations**: Non-blocking database and API calls
3. **Responsive UI**: Component updates without full page reruns
4. **FastAPI Integration**: Consistent patterns with existing backend services
5. **Better Testing**: Components can be unit tested in isolation
6. **Interactive Tables**: AG Grid provides professional data grid experience

### Negative

1. **Learning Curve**: Team needs to learn NiceGUI patterns
2. **Migration Effort**: ~70-96 days actual implementation time
3. **Smaller Community**: NiceGUI less popular than React ecosystem
4. **Documentation**: Custom patterns need internal documentation

### Trade-offs

1. **Python-Only**: Limits frontend developer hiring pool
2. **Framework Lock-in**: Migrating away would require another rewrite
3. **Performance Ceiling**: WebSocket still slower than native apps

## Security Considerations

### Session Architecture

- Sessions stored in Redis with configurable TTL
- Session IDs are cryptographically random UUIDs
- Session data encrypted at rest (optional)

### Authentication Flow

1. User submits credentials via login form
2. Backend validates against auth provider (OAuth2/MTLS/Basic)
3. Session created in Redis with user context
4. Session cookie set with secure flags:
   - `HttpOnly`: Prevents XSS access
   - `Secure`: HTTPS only
   - `SameSite=Strict`: CSRF protection
5. Subsequent requests validate session

### CSRF Protection

- Double-submit cookie pattern
- Custom header validation for state-changing operations
- Session-bound CSRF tokens

## Performance Requirements

| Metric | Streamlit Baseline | NiceGUI Target | Validation |
|--------|-------------------|----------------|------------|
| Page Load | 2-3s | <500ms | Lighthouse |
| Interaction Response | 500-2000ms | <100ms | User testing |
| Data Grid Render | 1-5s (1000 rows) | <200ms | Benchmark |
| Real-time Update | 5-30s (polling) | <100ms (push) | Network analysis |

## Rollback Plan

**Note:** Rollback is no longer available after Streamlit removal in P5T9.

The Streamlit codebase has been archived to:
- `apps/web_console/` - Removed (pages, components)
- `apps/web_console/README.md` - Documents remaining shared modules

If critical issues arise, the only path forward is fixing NiceGUI implementation.

## Implementation Notes

### Migration Path (Phases)

| Phase | Tasks | Description |
|-------|-------|-------------|
| P5T1 | Infrastructure | NiceGUI skeleton, auth, layout |
| P5T2 | Core Components | Positions grid, orders table, AG Grid |
| P5T3 | Dashboard | Real-time dashboard with WebSocket |
| P5T4-T5 | Trading Controls | Kill switch, manual orders |
| P5T6 | Charts | Performance charts, risk analytics |
| P5T7 | Admin Pages | Circuit breaker, health, alerts |
| P5T8 | Remaining Pages | Compare, journal, notebooks, etc. |
| P5T9 | Deprecation | Streamlit removal, documentation |

### Testing Approach

1. **Unit Tests**: Component logic tested in isolation
2. **Integration Tests**: Page rendering with mocked services
3. **E2E Tests**: Playwright-based browser automation
4. **Performance Tests**: Benchmark critical paths

### Timeline

- **Planning**: 2025-12
- **Implementation**: 2026-01-01 to 2026-01-04
- **Documentation**: 2026-01-04 (P5T9)

### Lessons Learned

1. **Demo Mode Pattern**: Pages gracefully degrade when services unavailable
2. **Async Patterns**: Always use `run.io_bound()` for sync service calls
3. **Component Isolation**: Keep components small and focused
4. **State Management**: Use `@ui.refreshable` for reactive updates
5. **Error Boundaries**: Handle exceptions at page level

---
**Last Updated:** 2026-01-04
**Author:** Development Team
