# Incident Reports

This directory contains post-incident reports for production issues, rollbacks, and operational events.

## Template

Create new incident reports using the format: `YYYY-MM-DD-brief-description.md`

### Incident Report Template

```markdown
# Incident: [Brief Description]

**Date:** YYYY-MM-DD
**Severity:** Critical | High | Medium | Low
**Duration:** HH:MM (detection to resolution)
**Impact:** [User-facing impact description]

## Timeline

- **HH:MM** - Issue detected (describe trigger)
- **HH:MM** - Investigation began
- **HH:MM** - Root cause identified
- **HH:MM** - Fix deployed / rollback initiated
- **HH:MM** - Issue resolved, monitoring

## Root Cause

[Technical explanation of what went wrong]

## Resolution

[What was done to fix it]

## Action Items

- [ ] Task 1 (Owner, Due Date)
- [ ] Task 2 (Owner, Due Date)

## Lessons Learned

**What went well:**
- ...

**What could be improved:**
- ...

## Related

- PR: #123
- Rollback commit: abc123
- Follow-up task: P1T15
```

## Index

<!-- Add incident reports here as they are created -->

*No incidents recorded yet.*
