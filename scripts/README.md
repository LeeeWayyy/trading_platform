# Scripts Directory

**Last Updated:** 2026-01-14
**Organization:** Reorganized from flat structure to categorized subdirectories

---

## Directory Structure

```
scripts/
├── README.md          # This file
├── dev/               # Development utilities
├── ops/               # Operations & monitoring
├── data/              # Data management
├── admin/             # Administrative tasks
├── testing/           # Test validation scripts
├── hooks/             # Git hooks
└── ai_workflow/       # AI workflow automation
```

---

## Development Utilities (dev/)

Scripts for code generation, testing, and documentation.

| Script | Purpose |
|--------|---------|
| `generate_architecture.py` | Generate architecture diagrams |
| `generate_certs.py` | Generate SSL certificates for local development |
| `generate_golden_results.py` | Generate golden test results |
| `generate_test_data.py` | Generate synthetic test data |
| `quick_train_test.py` | Fast model training for testing |
| `fix_links.py` | Fix broken documentation links |
| `check_links.py` | Check documentation links |
| `check-markdown-links.sh` | Shell script to check markdown links |
| `check_doc_freshness.py` | Check documentation freshness |
| `validate_doc_index.sh` | Validate documentation index |
| `migrate_implementation_guides.py` | Migrate implementation guides |
| `renumber_phase.py` | Renumber phase identifiers |

---

## Operations (ops/)

Scripts for system operations, paper trading, and monitoring.

| Script | Purpose |
|--------|---------|
| `paper_run.py` | Execute end-to-end paper trading for a date |
| `operational_status.sh` | Check system operational status |
| `fetch_data.py` | Fetch market data |
| `model_cli.py` | Model registry CLI |
| `register_model.sh` | Register model in registry |
| `ensure_web_console_jwt_keys.py` | Ensure JWT keys for web console |
| `disable_mtls_fallback.sh` | Disable mTLS fallback |
| `manage_roles.py` | Manage user roles |

**Common Usage:**
```bash
# Execute paper trading
python scripts/ops/paper_run.py --date 2026-01-14

# Check operational status
bash scripts/ops/operational_status.sh

# Register a model
bash scripts/ops/register_model.sh model_name model_version
```

---

## Data Management (data/)

Scripts for data synchronization and queries.

| Script | Purpose |
|--------|---------|
| `taq_query.py` | Query TAQ data |
| `taq_sync.py` | Synchronize TAQ data |
| `wrds_sync.py` | Synchronize WRDS data |

**Common Usage:**
```bash
# Sync TAQ data
python scripts/data/taq_sync.py --start-date 2026-01-01

# Query TAQ data
python scripts/data/taq_query.py --symbol AAPL
```

---

## Administrative (admin/)

Scripts for task management, workflow gates, and git utilities.

| Script | Purpose |
|--------|---------|
| `workflow_gate.py` | AI workflow gate enforcement |
| `tasks.py` | Task lifecycle management |
| `context_checkpoint.py` | Context management for AI sessions |
| `update_task_state.py` | Update task state |
| `git_utils.py` | Git utilities |
| `compute_review_hash.py` | Compute review hashes |

**Common Usage:**
```bash
# Start a task
./scripts/admin/workflow_gate.py start-task docs/TASKS/TASK.md feature/branch

# Advance workflow phase
./scripts/admin/workflow_gate.py advance implement

# Create context checkpoint
./scripts/admin/context_checkpoint.py create --type delegation
```

---

## Testing & Validation (testing/)

Scripts for validating system components (renamed from test_* to validate_*).

| Script | Purpose |
|--------|---------|
| `validate_alpaca_live.py` | Validate Alpaca live API connection |
| `validate_enhanced_pnl_integration.py` | Validate enhanced P&L integration |
| `validate_health_check.sh` | Validate system health |
| `validate_p1_p2_model_registry.py` | Validate Phase 1-2 model registry |
| `validate_p3_signal_generator.py` | Validate Phase 3 signal generator |
| `validate_p4_fastapi.py` | Validate Phase 4 FastAPI integration |
| `validate_p5_hot_reload.py` | Validate Phase 5 hot reload |
| `validate_paper_run.py` | Validate paper run workflow |
| `validate_t4_execution_gateway.py` | Validate execution gateway (Task 4) |
| `validate_t5_orchestrator.py` | Validate orchestrator (Task 5) |
| `validate_env.py` | Validate environment setup |
| `verify_branch_protection.py` | Verify git branch protection |
| `verify_gate_compliance.py` | Verify workflow gate compliance |
| `integration_test.py` | Integration tests |

**Common Usage:**
```bash
# Validate Alpaca connection
python scripts/testing/validate_alpaca_live.py

# Validate environment
python scripts/testing/validate_env.py

# Run integration tests
python scripts/testing/integration_test.py
```

---

## Git Hooks (hooks/)

Git hooks for pre-commit checks and validation.

| Script | Purpose |
|--------|---------|
| `pre-commit-hook.sh` | Pre-commit validation |
| `ci_with_timeout.sh` | CI execution with timeout |
| `setup_testing_env.sh` | Setup testing environment |

**Installation:**
```bash
# Link pre-commit hook
ln -s ../../scripts/hooks/pre-commit-hook.sh .git/hooks/pre-commit
```

---

## AI Workflow (ai_workflow/)

Internal workflow automation for AI-assisted development.

| Script | Purpose |
|--------|---------|
| `config.py` | Workflow configuration |
| `constants.py` | Workflow constants |
| `core.py` | Core workflow logic |
| `hash_utils.py` | Hash computation utilities |
| `reviewers.py` | Review orchestration |
| `subtasks.py` | Subtask management |
| `pr_workflow.py` | PR workflow automation |
| `delegation.py` | Agent delegation logic |
| `git_utils.py` | Git utilities for workflow |

---

## Adding New Scripts

When adding new scripts, follow these guidelines:

1. **Choose the right category:**
   - `dev/` - Code generation, testing utilities, documentation tools
   - `ops/` - Production operations, monitoring, deployment
   - `data/` - Data synchronization, ETL, queries
   - `admin/` - Task management, workflow automation
   - `testing/` - Validation scripts (use `validate_*` naming)
   - `hooks/` - Git hooks only
   - `ai_workflow/` - AI workflow automation (internal use)

2. **Naming conventions:**
   - Use snake_case for Python scripts: `my_script.py`
   - Use kebab-case for shell scripts: `my-script.sh`
   - Test/validation scripts: `validate_*.py` or `verify_*.py` (not `test_*`)

3. **Documentation requirements:**
   - Add docstring at top of file explaining purpose
   - Add `--help` flag for CLI scripts
   - Update this README with script entry

4. **Testing:**
   - Add tests in `tests/scripts/` matching structure
   - Test script should be `tests/scripts/test_<category>_<script>.py`

---

## Migration Notes

**2026-01-14:** Scripts reorganized from flat structure to categorized subdirectories:
- 12 scripts moved to `dev/`
- 8 scripts moved to `ops/`
- 3 scripts moved to `data/`
- 6 scripts moved to `admin/`
- 14 scripts moved to `testing/` (renamed from `test_*` to `validate_*`)
- 3 scripts moved to `hooks/`
- `ai_workflow/` already existed

**Breaking Changes:**
- Test scripts renamed: `test_*.py` → `validate_*.py`
- Paths changed: `scripts/paper_run.py` → `scripts/ops/paper_run.py`
- Update Makefile and CI references accordingly

---

## Quick Reference

**Most commonly used scripts:**

```bash
# Paper trading
python scripts/ops/paper_run.py

# Operational status
bash scripts/ops/operational_status.sh

# Workflow management
./scripts/admin/workflow_gate.py status

# Environment validation
python scripts/testing/validate_env.py

# Health check
bash scripts/testing/validate_health_check.sh
```

---

**See Also:**
- [Runbooks](../docs/RUNBOOKS/ops.md)
- [Operations Runbook](../docs/RUNBOOKS/ops.md)
- [AI Workflow Guide](../docs/AI/Workflows/README.md)
