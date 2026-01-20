# Low Coverage Modules - Prioritized Test Plan

**Generated:** 2026-01-19
**Last Updated:** 2026-01-19 (Coverage improvements verified)
**Coverage Threshold:** <70% considered "low coverage"
**Total Modules Analyzed:** 42,937 statements, 81% overall coverage

---

## Executive Summary

This document identifies modules with coverage below 70%, categorized by criticality level.

**🎉 CRITICAL Priority Status: ALL MODULES NOW AT 88%+ COVERAGE**

As of 2026-01-19, all CRITICAL priority modules have been improved to excellent coverage levels:
- `api/dependencies.py`: **94%** (was 43%)
- `routes/orders.py`: **91%** (was 59%)
- `routes/slicing.py`: **91%** (was 67%)
- `model_registry.py`: **90%** (was 64%)
- `secrets/__init__.py`: **100%** (was 30%)
- `secrets/env_backend.py`: **100%** (was 82%)

**🎉 HIGH Priority Status: ALL 15 MODULES NOW AT 91%+ COVERAGE**

As of 2026-01-19, all HIGH priority modules have been improved:
- `rate_limiter.py`: **100%** (was 62%)
- `api_auth_dependency.py`: **97%** (was 56%)
- `market_hours.py`: **97%** (was 63%)
- `hash_utils.py`: **100%** (was 68%)
- `locking.py`: **99%** (was 64%)
- `prometheus_client.py`: **100%** (was 68%)
- `wrds_client.py`: **99%** (was 58%)
- `fama_french_local_provider.py`: **96%** (was 67%)
- `redis_ha.py`: **100%** (was 57%)
- `auth/middleware.py`: **98%** (was 63%)
- `auth/audit.py`: **98%** (was 59%)
- `core/client.py`: **100%** (was 63%)
- `connection_monitor.py`: **100%** (was 67%)
- `models/registry.py`: **91%** (was 68%)
- `config.py`: **94%** (was 68%)

**🎉 MEDIUM Priority Status: ALL 22 MODULES NOW AT 77%+ COVERAGE**

As of 2026-01-19, all MEDIUM priority modules have been improved:
- `delivery_service.py`: **96%** (was 56%)
- `ui/layout.py`: **85%** (was 57%)
- `pages/circuit_breaker.py`: **90%** (was 57%)
- `pages/alerts.py`: **100%** (was 57%)
- `header_metrics.py`: **97%** (was 54%)
- `market_clock.py`: **99%** (was 65%)
- `manual_order.py`: **99%** (was 65%)
- `factors/cache.py`: **99%** (was 67%)
- `taq_query_provider.py`: **99%** (was 70%)
- `sync_manager.py`: **93%** (was 73%)
- `manifest.py`: **96%** (was 73%)
- `session_store.py`: **97%** (was 72%)
- `positions_grid.py`: **99%** (was 71%)
- `realtime.py`: **97%** (was 71%)
- `serialization.py`: **100%** (was 70%)
- `taq_storage.py`: **99%** (was 70%)
- `orchestrator/main.py`: **94%** (was 70%)
- `backtest.py`: **85%+** (was 72%)
- `alpha/metrics.py`: **85%** (was 72%)
- `tax_lot_service.py`: **99%** (was 75%)
- `execution_quality.py`: **95%** (was 79%)
- `microstructure.py`: **77%** (was 75%, limited by Numba JIT code)

**Next Focus Areas: LOW Priority modules (UI pages)**

---

## ✅ CRITICAL Priority (Trading & Risk Management) - COMPLETED

**All CRITICAL modules now have 88%+ coverage.**

### 2. apps/execution_gateway/api/dependencies.py ✅
- **Coverage:** ~~43%~~ → **94%** ✅
- **Statements:** 243
- **Status:** IMPROVED - 6 failing tests fixed, coverage increased by 51%

### 3. apps/execution_gateway/routes/orders.py ✅
- **Coverage:** ~~59%~~ → **91%** ✅
- **Statements:** 267
- **Status:** IMPROVED - comprehensive test coverage achieved

### 4. libs/platform/secrets/__init__.py ✅
- **Coverage:** ~~30%~~ → **100%** ✅
- **Statements:** 17
- **Status:** COMPLETE - full coverage achieved

### 5. apps/execution_gateway/routes/slicing.py ✅
- **Coverage:** ~~67%~~ → **91%** ✅
- **Statements:** 236
- **Status:** IMPROVED - comprehensive test coverage achieved

### 6. apps/signal_service/model_registry.py ✅
- **Coverage:** ~~64%~~ → **90%** ✅
- **Statements:** 220
- **Status:** IMPROVED - comprehensive test coverage achieved

### 7. libs/platform/secrets/env_backend.py ✅
- **Coverage:** ~~82%~~ → **100%** ✅
- **Statements:** 73
- **Status:** COMPLETE - full coverage achieved

---

## ✅ HIGH Priority (Data Integrity & Core Services) - COMPLETED

**Impact:** Data corruption, auth failures, monitoring gaps, system instability

**🎉 All 15 HIGH Priority modules improved to 91%+ coverage (2026-01-19)**

### 8. libs/core/common/api_auth_dependency.py ✅
- **Coverage:** ~~56%~~ → **97%** ✅
- **Statements:** 260
- **Status:** IMPROVED - 20+ tests added covering auth validation and permission checks

### 9. apps/web_console_ng/core/redis_ha.py ✅
- **Coverage:** ~~57%~~ → **100%** ✅
- **Statements:** 159
- **Status:** COMPLETE - 39 tests added covering HA initialization, failover logic, SSL configuration

### 10. libs/data/data_providers/wrds_client.py ✅
- **Coverage:** ~~58%~~ → **99%** ✅
- **Statements:** 135
- **Status:** IMPROVED - 17 tests added covering data retrieval and error handling

### 11. apps/web_console_ng/auth/middleware.py ✅
- **Coverage:** ~~63%~~ → **98%** ✅
- **Statements:** 238
- **Status:** IMPROVED - 41 tests added covering auth checks, session validation, auth enforcement

### 12. apps/web_console_ng/auth/audit.py ✅
- **Coverage:** ~~59%~~ → **98%** ✅
- **Statements:** 124
- **Status:** IMPROVED - 18 tests added covering audit logging, singleton, flush loop

### 13. libs/common/market_hours.py ✅
- **Coverage:** ~~63%~~ → **97%** ✅
- **Statements:** 126
- **Status:** IMPROVED - 22 tests added covering market hours logic and edge cases

### 14. apps/web_console_ng/core/client.py ✅
- **Coverage:** ~~63%~~ → **100%** ✅
- **Statements:** 189
- **Status:** COMPLETE - 30 tests added covering client lifecycle, API endpoints, kill switch

### 15. libs/data/data_providers/locking.py ✅
- **Coverage:** ~~64%~~ → **99%** ✅
- **Statements:** 227
- **Status:** IMPROVED - 31 tests added covering locking logic and race conditions

### 16. apps/web_console_ng/core/connection_monitor.py ✅
- **Coverage:** ~~67%~~ → **100%** ✅
- **Statements:** 111
- **Status:** COMPLETE - 14 tests added covering monitoring logic, state transitions, badge rendering

### 17. libs/data/data_providers/fama_french_local_provider.py ✅
- **Coverage:** ~~67%~~ → **96%** ✅
- **Statements:** 345
- **Status:** IMPROVED - 39 tests added covering data loading and validation

### 18. libs/core/common/hash_utils.py ✅
- **Coverage:** ~~68%~~ → **100%** ✅
- **Statements:** 33
- **Status:** COMPLETE - 3 tests added for full coverage

### 19. libs/core/health/prometheus_client.py ✅
- **Coverage:** ~~68%~~ → **100%** ✅
- **Statements:** 120
- **Status:** COMPLETE - 22 tests added covering metrics reporting

### 20. libs/models/models/registry.py ✅
- **Coverage:** ~~68%~~ → **91%** ✅
- **Statements:** 345
- **Status:** IMPROVED - 53 tests added covering model lookup, registration, validation, manifest updates

### 21. apps/web_console_ng/config.py ✅
- **Coverage:** ~~68%~~ → **94%** ✅
- **Statements:** 197
- **Status:** IMPROVED - 32 tests added covering config loading, validation, encryption keys

### 22. libs/platform/web_console_auth/rate_limiter.py ✅
- **Coverage:** ~~62%~~ → **100%** ✅
- **Statements:** 142
- **Status:** COMPLETE - 33 tests added covering rate limiting logic

---

## ✅ MEDIUM Priority (Web Console & Monitoring) - COMPLETED

**Impact:** User experience degradation, reduced observability, feature unavailability

**🎉 All 22 MEDIUM Priority modules improved to 77%+ coverage (2026-01-19)**

### 23. libs/platform/alerts/delivery_service.py ✅
- **Coverage:** ~~56%~~ → **96%** ✅
- **Statements:** 290
- **Status:** IMPROVED - 68 tests covering delivery logic and alert routing

### 24. apps/web_console_ng/ui/layout.py ✅
- **Coverage:** ~~57%~~ → **85%** ✅
- **Statements:** 280
- **Status:** IMPROVED - 36 tests covering layout rendering and status polling

### 25. apps/web_console_ng/pages/circuit_breaker.py ✅
- **Coverage:** ~~57%~~ → **90%** ✅
- **Statements:** 196
- **Status:** IMPROVED - 29 tests covering circuit breaker UI

### 26. apps/web_console_ng/pages/alerts.py ✅
- **Coverage:** ~~57%~~ → **100%** ✅
- **Statements:** 237
- **Status:** COMPLETE - full coverage achieved

### 27. libs/data/data_providers/taq_query_provider.py ✅
- **Coverage:** ~~70%~~ → **99%** ✅
- **Statements:** 234
- **Status:** IMPROVED - 41 tests covering TAQ data queries

### 28. libs/data/data_providers/taq_storage.py ✅
- **Coverage:** ~~70%~~ → **99%** ✅
- **Statements:** 344
- **Status:** IMPROVED - comprehensive tests covering storage logic

### 29. apps/orchestrator/main.py ✅
- **Coverage:** ~~70%~~ → **94%** ✅
- **Statements:** 233
- **Status:** IMPROVED - 28 tests covering orchestration logic and endpoints

### 30. libs/data/data_quality/manifest.py ✅
- **Coverage:** ~~73%~~ → **96%** ✅
- **Statements:** 437
- **Status:** IMPROVED - 44 tests covering manifest management

### 31. libs/data/data_providers/sync_manager.py ✅
- **Coverage:** ~~73%~~ → **93%** ✅
- **Statements:** 410
- **Status:** IMPROVED - 61 tests covering sync logic

### 32. apps/web_console_ng/pages/backtest.py ✅
- **Coverage:** ~~72%~~ → **85%+** ✅
- **Statements:** 671
- **Status:** IMPROVED - 77 tests covering backtest configuration and execution

### 33. apps/web_console_ng/auth/session_store.py ✅
- **Coverage:** ~~72%~~ → **97%** ✅
- **Statements:** 269
- **Status:** IMPROVED - 58 tests covering session storage and management

### 34. apps/web_console_ng/components/positions_grid.py ✅
- **Coverage:** ~~71%~~ → **99%** ✅
- **Statements:** 175
- **Status:** IMPROVED - 35 tests covering positions display

### 35. apps/web_console_ng/core/realtime.py ✅
- **Coverage:** ~~71%~~ → **97%** ✅
- **Statements:** 158
- **Status:** IMPROVED - 25 tests covering realtime updates

### 36. libs/models/factors/cache.py ✅
- **Coverage:** ~~67%~~ → **99%** ✅
- **Statements:** 295
- **Status:** IMPROVED - 46 tests covering factor caching

### 37. libs/models/models/serialization.py ✅
- **Coverage:** ~~70%~~ → **100%** ✅
- **Statements:** 149
- **Status:** COMPLETE - 32 tests for full coverage

### 38. libs/trading/alpha/metrics.py ✅
- **Coverage:** ~~72%~~ → **85%** ✅
- **Statements:** 290
- **Status:** IMPROVED - 70 tests covering alpha metrics

### 39. libs/web_console_services/tax_lot_service.py ✅
- **Coverage:** ~~75%~~ → **99%** ✅
- **Statements:** 227
- **Status:** IMPROVED - 55 tests covering tax lot management

### 40. apps/web_console_ng/components/header_metrics.py ✅
- **Coverage:** ~~54%~~ → **97%** ✅
- **Statements:** 177
- **Status:** IMPROVED - 60 tests covering metrics display

### 41. apps/web_console_ng/components/market_clock.py ✅
- **Coverage:** ~~65%~~ → **99%** ✅
- **Statements:** 84
- **Status:** IMPROVED - 18 tests covering market clock logic

### 42. apps/web_console_ng/pages/manual_order.py ✅
- **Coverage:** ~~65%~~ → **99%** ✅
- **Statements:** 209
- **Status:** IMPROVED - 30 tests covering manual order UI

### 43. libs/platform/analytics/execution_quality.py ✅
- **Coverage:** ~~79%~~ → **95%** ✅
- **Statements:** 422
- **Status:** IMPROVED - 91 tests covering execution analytics

### 44. libs/platform/analytics/microstructure.py ✅
- **Coverage:** ~~75%~~ → **77%** ✅
- **Statements:** 472
- **Status:** IMPROVED - 90 tests (limited by Numba JIT compiled code)

---

## LOW Priority (UI Pages & Visualization)

**Impact:** Limited to UI/UX, no trading or data integrity impact

### 45. apps/web_console_ng/pages/dashboard.py
- **Coverage:** 27% (VERY LOW)
- **Statements:** 340
- **Missing:** 242 statements, 3 branches
- **Critical Paths:** Lines 195-618 (424 lines of dashboard logic)

### 46. apps/web_console_ng/pages/risk.py
- **Coverage:** 14% (VERY LOW)
- **Statements:** 169
- **Missing:** 142 statements
- **Critical Paths:** Lines 57-343 (287 lines of risk page)

### 47. apps/web_console_ng/pages/mfa_verify.py
- **Coverage:** 19% (VERY LOW)
- **Statements:** 65
- **Missing:** 49 statements
- **Critical Paths:** Lines 20-118 (MFA UI)

### 48. apps/web_console_ng/pages/compare.py
- **Coverage:** 36%
- **Statements:** 236
- **Missing:** 148 statements, 4 branches
- **Critical Paths:** Lines 60-247, 280-416, 475-608 (comparison UI)

### 49. apps/web_console_ng/pages/alpha_explorer.py
- **Coverage:** 41%
- **Statements:** 249
- **Missing:** 143 statements, 1 branch
- **Critical Paths:** Lines 64-300, 404-597 (alpha exploration UI)

### 50. apps/web_console_ng/pages/data_management.py
- **Coverage:** 42%
- **Statements:** 202
- **Missing:** 114 statements, 6 branches
- **Critical Paths:** Lines 56-116, 194-526 (data management UI)

### 51. apps/web_console_ng/pages/position_management.py
- **Coverage:** 45%
- **Statements:** 349
- **Missing:** 181 statements, 17 branches
- **Critical Paths:** Lines 210-424, 439-763 (position management UI)

### 52. apps/web_console_ng/pages/performance.py
- **Coverage:** 46%
- **Statements:** 168
- **Missing:** 86 statements, 2 branches
- **Critical Paths:** Lines 45-337, 353-436 (performance UI)

### 53. apps/web_console_ng/pages/journal.py
- **Coverage:** 46%
- **Statements:** 285
- **Missing:** 155 statements, 5 branches
- **Critical Paths:** Lines 53-395, 531-592 (journal UI)

### 54. libs/trading/backtest/__init__.py
- **Coverage:** 46%
- **Statements:** 26
- **Missing:** 14 statements
- **Critical Paths:** Lines 24-46 (backtest initialization)

### 55. apps/web_console_ng/pages/login.py
- **Coverage:** 47%
- **Statements:** 194
- **Missing:** 95 statements, 14 branches
- **Critical Paths:** Lines 87-163, 203-369 (login UI)

### 56. apps/web_console_ng/pages/notebook_launcher.py
- **Coverage:** 53%
- **Statements:** 233
- **Missing:** 111 statements, 6 branches
- **Critical Paths:** Lines 64-164, 236-493 (notebook launcher UI)

### 57. libs/models/models/compatibility.py
- **Coverage:** 72% (just above threshold)
- **Statements:** 69
- **Missing:** 17 statements
- **Critical Paths:** Lines 44-46, 212-231 (model compatibility checks)

### 58. apps/auth_service/routes/example_page.py
- **Coverage:** 62%
- **Statements:** 11
- **Missing:** 3 statements
- **Note:** Example/demo code

### 59. libs/platform/alerts/dedup.py
- **Coverage:** 65%
- **Statements:** 18
- **Missing:** 5 statements
- **Critical Paths:** Lines 27-31 (alert deduplication)

---

## IGNORE (Experimental/Spike Code)

### apps/web_console_ng/spike_c01.py
- **Coverage:** 0%
- **Note:** Experimental spike code - not production
- **Action:** No testing needed unless promoted to production

---

## Recommended Testing Order

### Phase 1: CRITICAL (Weeks 1-2)
1. **apps/execution_gateway/reconciliation.py** - DELETE (legacy, replaced)
2. **libs/platform/secrets/__init__.py** - 10 statements, HIGH impact
3. **apps/execution_gateway/api/dependencies.py** - 126 statements
4. **apps/execution_gateway/routes/orders.py** - 109 statements
5. **apps/execution_gateway/routes/slicing.py** - 77 statements
6. **apps/signal_service/model_registry.py** - 73 statements
7. **libs/platform/secrets/env_backend.py** - 15 statements

**Estimated Effort:** 40-60 hours
**Impact:** Eliminates critical trading/execution risks

### Phase 2: HIGH (Weeks 3-4)
8. **libs/core/common/api_auth_dependency.py** - 100 statements (auth critical)
9. **apps/web_console_ng/core/redis_ha.py** - 64 statements (HA critical)
10. **apps/web_console_ng/auth/middleware.py** - 79 statements
11. **libs/common/market_hours.py** - 39 statements (trading hours)
12. **libs/data/data_providers/locking.py** - 74 statements
13. **libs/data/data_providers/wrds_client.py** - 48 statements
14. **apps/web_console_ng/auth/audit.py** - 41 statements

**Estimated Effort:** 50-70 hours
**Impact:** Eliminates auth/security and data integrity risks

### Phase 3: MEDIUM (Weeks 5-6)
15-30. Web console core, monitoring, orchestration modules

**Estimated Effort:** 80-120 hours
**Impact:** Improves stability and observability

### Phase 4: LOW (Weeks 7-8)
31-59. UI pages and visualization components

**Estimated Effort:** 100-150 hours
**Impact:** Improves user experience and UI reliability

---

## Success Metrics

- **Coverage target:** 85% overall (from current 81%)
- **CRITICAL modules:** 95%+ coverage
- **HIGH modules:** 90%+ coverage
- **MEDIUM modules:** 85%+ coverage
- **LOW modules:** 75%+ coverage

---

## Notes

1. **Legacy reconciliation.py (0% coverage):**
   - Already replaced by modular `apps/execution_gateway/reconciliation/` (97% coverage)
   - **ACTION REQUIRED:** Delete legacy file to clean up codebase

2. **Large gap in web console pages:**
   - Many pages have 27-46% coverage
   - Consider UI testing framework (Playwright/Selenium) for E2E coverage
   - Focus on critical paths (order submission, position management) first

3. **Secrets management needs attention:**
   - `libs/platform/secrets/__init__.py` at 30% is unacceptable for security-critical code
   - Should be Phase 1 priority

4. **Order execution path gaps:**
   - `routes/orders.py` has 153 lines (818-971) of bulk operations untested
   - High risk for production trading

5. **Auth/security modules need improvement:**
   - Multiple auth-related modules in 56-63% range
   - Security vulnerabilities possible

---

## Next Steps

1. **Immediate:** Delete legacy `apps/execution_gateway/reconciliation.py`
2. **Week 1:** Start Phase 1 testing (CRITICAL modules)
3. **Track progress:** Update this document weekly with completion status
4. **CI enforcement:** Consider adding coverage gates for new code (85% minimum)

---

**Document Owner:** Development Team
**Last Updated:** 2026-01-19
**Review Frequency:** Weekly during active testing, monthly after completion
