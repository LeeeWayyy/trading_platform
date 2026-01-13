---
id: P6T16
title: "Professional Trading Terminal - Admin Pages"
phase: P6
task: T16
priority: P2
owner: "@development-team"
state: TASK
created: 2026-01-13
dependencies: [P5]
related_adrs: [ADR-0031-nicegui-migration]
related_docs: [P6_PLANNING.md]
features: [T16.1-T16.3]
---

# P6T16: Admin Pages

**Phase:** P6 (Professional Trading Terminal Upgrades)
**Status:** TASK (Not Started)
**Priority:** P2 (Backend Integration)
**Owner:** @development-team
**Created:** 2026-01-13
**Track:** Track 16 of 18
**Dependency:** P5 complete

---

## Objective

Complete missing admin pages: tax lot management, user management with RBAC, and API key management.

**Success looks like:**
- Tax lots page with cost basis and wash sale detection
- User management with RBAC
- API key revoke/rotate functionality
- All admin pages properly role-gated with audit logging

---

## Security Requirements (All T16 Tasks)

**RBAC Model:**
- VIEWER: Read-only access to non-admin pages
- TRADER: VIEWER + can place orders
- QUANT: TRADER + can run backtests, view models
- ADMIN: Full access including user management

**Audit Requirements:**
- All create/update/delete actions logged with user, timestamp, action, before/after state
- Audit logs exportable for compliance
- Sensitive data redacted in logs (passwords, API keys show as `***`)

---

## Tasks (3 total)

### T16.1: Tax Lot Management - MEDIUM PRIORITY

**Goal:** Complete tax lot UI missing from NiceGUI.

**Current State:**
- Backend: `apps/web_console/services/tax_lot_service.py`, `libs/tax/*`
- Migration: `db/migrations/0019_create_tax_lots.sql`
- Frontend: No NiceGUI page

**Features:**
- Display open tax lots with cost basis
- Show unrealized gains/losses
- Wash sale detection and warnings
- Tax-loss harvesting suggestions
- Lot selection for sales (FIFO, LIFO, specific)

**Acceptance Criteria:**
- [ ] Tax lots page accessible at `/tax-lots`
- [ ] Open lots displayed with cost basis
- [ ] Wash sale warnings functional
- [ ] Lot selection for sales working
- [ ] **RBAC:** Require TRADER role or higher to view
- [ ] **RBAC:** Require ADMIN role to change lot selection method
- [ ] **Audit:** Log all lot selection changes

**Files:**
- Create: `apps/web_console_ng/pages/tax_lots.py`, `apps/web_console_ng/components/tax_lot_table.py`, `apps/web_console_ng/components/wash_sale_warning.py`

---

### T16.2: User Management / RBAC Admin - MEDIUM PRIORITY

**Goal:** Admin page for user and role management.

**Current State:**
- Backend: `apps/web_console/services/user_management.py`
- Frontend: No NiceGUI page

**Features:**
- List all users with roles
- Create/edit user dialog
- Role assignment (Viewer, Trader, Quant, Admin)
- Permission management
- Activity log per user

**Acceptance Criteria:**
- [ ] Users page at `/admin/users`
- [ ] User list with roles displayed
- [ ] Create/edit user functional
- [ ] Role assignment working
- [ ] Activity log accessible
- [ ] **RBAC:** Require ADMIN role to access
- [ ] **RBAC:** Cannot edit own role (prevent self-elevation)
- [ ] **RBAC:** Cannot delete last ADMIN user
- [ ] **Security:** Password hashed before storage
- [ ] **Security:** Passwords never returned in API responses
- [ ] **Audit:** Log all user creates, edits, role changes, deletions

**Files:**
- Create: `apps/web_console_ng/pages/admin_users.py`, `apps/web_console_ng/components/user_form.py`, `apps/web_console_ng/components/role_selector.py`

---

### T16.3: API Key Revoke/Rotate - LOW PRIORITY

**Goal:** Complete API key management.

**Current State:**
- `apps/web_console_ng/pages/admin.py` has TODO for revoke/rotate

**Features:**
- Revoke button on API keys table
- Rotate action (revoke + create new)
- Confirmation dialogs for both actions
- Show last used timestamp

**Acceptance Criteria:**
- [ ] Revoke button functional
- [ ] Rotate action works (revoke + create)
- [ ] Confirmation dialogs present (type "REVOKE" to confirm)
- [ ] Last used timestamp displayed
- [ ] **RBAC:** Require ADMIN role to revoke/rotate keys
- [ ] **RBAC:** Users can only revoke their own keys (unless ADMIN)
- [ ] **Security:** Full key only shown once at creation (never again)
- [ ] **Security:** Display only last 4 characters of key in table
- [ ] **Audit:** Log all revoke and rotate actions with key ID (not full key)

**Files:**
- Modify: `apps/web_console_ng/pages/admin.py`

---

## Cross-Cutting RBAC Implementation

**Page-Level Authorization:**
```python
# Example pattern for all T16 pages
from apps.web_console_ng.auth.middleware import requires_role

@ui.page("/admin/users")
@requires_auth
@requires_role("ADMIN")  # New decorator
@main_layout
async def admin_users_page() -> None:
    ...
```

**Action-Level Authorization:**
```python
# Example pattern for destructive actions
async def delete_user(user_id: str) -> None:
    current_user = get_current_user()
    if current_user["role"] != "ADMIN":
        ui.notify("Permission denied", type="negative")
        return

    # Audit log
    audit_service.log_action(
        user_id=current_user["user_id"],
        action="DELETE_USER",
        target_id=user_id,
        details={"before": user_data}
    )

    await user_service.delete(user_id)
```

---

## Backend Integration Points

| Feature | Backend Location | Frontend Action |
|---------|-----------------|-----------------|
| Tax Lots | `tax_lot_service.py`, `libs/tax/*` | Create page + components |
| Users | `user_management.py` | Create admin page |
| API Keys | Existing admin endpoints | Complete revoke/rotate |

---

## Testing Strategy

### Unit Tests
- Tax lot calculations
- Wash sale detection
- User role validation
- RBAC decorator behavior

### Integration Tests
- Tax lot service integration
- User management CRUD
- Audit log creation

### E2E Tests
- Tax lots page workflow
- User creation/editing
- Role-based access denied scenarios

---

## Definition of Done

- [ ] All 3 tasks implemented
- [ ] Tax lots page functional
- [ ] User management working
- [ ] API key revoke/rotate complete
- [ ] **All pages properly role-gated**
- [ ] **All destructive actions have audit logs**
- [ ] Unit tests > 85% coverage
- [ ] E2E tests pass
- [ ] Code reviewed and approved

---

**Last Updated:** 2026-01-13
**Status:** TASK
