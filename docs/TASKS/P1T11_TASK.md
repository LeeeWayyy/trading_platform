# P1T11 - Implement Clink-Based Zen-MCP Documentation

**Phase:** P1 (Production Hardening)
**Status:** 📋 Planning
**Priority:** HIGH
**Estimated Time:** 6-8 hours
**Owner:** Development Team
**Created:** 2025-10-21

---

## Objective

Implement comprehensive documentation for clink-based zen-mcp workflows, replacing direct zen tool usage with clink + codex/gemini-cli integration. Enable consistent, subscription-based code review workflows with built-in workflow reminders.

**Success Criteria:**
- [ ] All documentation uses clink exclusively (no direct zen tools)
- [ ] Review prompts include workflow reminders to keep Claude on track
- [ ] Model selection strategy clearly documented (codex=gpt-5-codex, gemini=2.5)
- [ ] Tested with sample commit and task document
- [ ] Team can follow workflows without confusion

---

## Requirements

### Functional Requirements

**FR1: Update CLAUDE.md**
- Remove all references to direct zen tools (chat, codereview, planner, etc.)
- Add clink workflow examples for:
  - Quick safety review (pre-commit)
  - Deep architecture review (pre-PR)
  - Task creation review (before starting work)
- Document model selection strategy
- Include cost model (subscription-based, $320-350/month)

**FR2: Create Review Prompts**
- Create `.claude/prompts/clink-reviews/` directory
- Implement 4 standardized prompts:
  1. `quick-safety-review.md` - Codex codereviewer for pre-commit
  2. `deep-architecture-review.md` - Gemini codereviewer for pre-PR
  3. `security-audit.md` - Codex codereviewer for security
  4. `task-creation-review.md` - Gemini planner for task validation
- **Each prompt MUST include workflow reminder section**
- Prompts follow trading platform safety focus

**FR3: Update Workflow Guides**
- Update `.claude/workflows/03-zen-review-quick.md`:
  - Replace zen tool usage with clink + codex
  - Add examples with continuation_id
  - Update duration expectations (~30 sec)
- Update `.claude/workflows/04-zen-review-deep.md`:
  - Replace with clink + gemini → codex multi-phase
  - Add examples with continuation_id preservation
  - Update duration expectations (~3-5 min)
- Create `.claude/workflows/13-task-creation-review.md`:
  - NEW workflow for validating task documents
  - Use clink + gemini planner
  - Include scope validation, requirements completeness

**FR4: Update Task Templates**
- Update `/docs/TASKS/00-TEMPLATE_TASK.md`:
  - Add task creation review checklist
  - Include reminder to request zen-mcp validation
- Update `/docs/TASKS/00-TEMPLATE_PHASE_PLANNING.md`:
  - Document clink review requirement
  - Add workflow reminder references

### Non-Functional Requirements

**NFR1: Consistency**
- All clink examples use consistent format
- Model selection explained clearly (CLI config, not API params)
- Workflow reminders have consistent structure

**NFR2: Trading Safety**
- Review prompts emphasize circuit breakers, idempotency, position limits
- Task validation checks for trading safety requirements
- Security audit prompt covers SQL injection, API keys, race conditions

**NFR3: Usability**
- Examples are copy-paste ready
- Workflow reminders prevent Claude from forgetting steps
- Clear guidance on when to use codex vs gemini

---

## Implementation Approach

### Component Breakdown

This task has **4 logical components**, each following the 4-step pattern:

**Component 1: CLAUDE.md Update**
1. Implement: Update CLAUDE.md with clink workflows
2. Test: Verify examples are accurate and copy-pasteable
3. Review: Request quick review (clink + codex codereviewer)
4. Commit: After approval

**Component 2: Review Prompts**
1. Implement: Create 4 prompt files with workflow reminders
2. Test: Test each prompt with sample files
3. Review: Request quick review (clink + codex codereviewer)
4. Commit: After approval

**Component 3: Workflow Guides**
1. Implement: Update 2 existing + create 1 new workflow guide
2. Test: Follow workflows with sample scenarios
3. Review: Request quick review (clink + codex codereviewer)
4. Commit: After approval

**Component 4: Task Templates**
1. Implement: Update 2 task templates
2. Test: Create sample task using templates
3. Review: Request quick review (clink + codex codereviewer)
4. Commit: After approval

**After all components complete:**
- Deep review: Request clink + gemini codereviewer for entire branch
- Create PR: Follow `.claude/workflows/02-git-pr.md`

### File Structure

```
CLAUDE.md                                           # Update
.claude/
  prompts/
    clink-reviews/                                  # NEW directory
      quick-safety-review.md                        # NEW
      deep-architecture-review.md                   # NEW
      security-audit.md                             # NEW
      task-creation-review.md                       # NEW
  workflows/
    03-zen-review-quick.md                          # Update
    04-zen-review-deep.md                           # Update
    13-task-creation-review.md                      # NEW
docs/
  TASKS/
    00-TEMPLATE_TASK.md                             # Update
    00-TEMPLATE_PHASE_PLANNING.md                   # Update
```

### Key Design Decisions

**Decision 1: Workflow Reminders in Every Review**
- Rationale: Claude Code tends to forget workflows after extensive work
- Implementation: Standard reminder template at end of each review prompt
- Includes: 4-step pattern, progressive commits, continuation_id usage

**Decision 2: Subscription Cost Model**
- Rationale: Using CLI tools with subscriptions, not direct API calls
- Implementation: Document fixed costs ($20-50/month) vs variable API costs
- Benefit: Predictable budgeting, unlimited reviews

**Decision 3: Gemini 2.5 Models**
- Rationale: Latest models (not outdated 1.5)
- Implementation: gemini-2.5-pro for planning, gemini-2.5-flash for quick tasks
- Configuration: Happens in gemini CLI, not clink parameters

---

## Acceptance Criteria

**AC1: Documentation Completeness**
- [ ] CLAUDE.md has NO references to direct zen tools
- [ ] CLAUDE.md includes 3 clink workflow examples (quick, deep, task review)
- [ ] Model selection strategy clearly explained
- [ ] Cost model documented ($320-350/month subscription-based)

**AC2: Review Prompts Quality**
- [ ] All 4 prompts created in `.claude/prompts/clink-reviews/`
- [ ] Each prompt includes workflow reminder section
- [ ] Trading safety focus evident in prompts
- [ ] Tested with sample files (no errors)

**AC3: Workflow Guides Updated**
- [ ] 03-zen-review-quick.md uses clink + codex (no old zen tools)
- [ ] 04-zen-review-deep.md uses clink + gemini multi-phase
- [ ] 13-task-creation-review.md created (NEW)
- [ ] All guides tested with sample scenarios

**AC4: Task Templates Enhanced**
- [ ] 00-TEMPLATE_TASK.md includes task creation review checklist
- [ ] 00-TEMPLATE_PHASE_PLANNING.md references clink workflow
- [ ] Templates tested by creating sample task

**AC5: Integration Testing**
- [ ] Quick review tested with sample commit
- [ ] Deep review tested with feature branch
- [ ] Task review tested with this task document (P1T11_TASK.md)
- [ ] Workflow reminders appear in all review responses

**AC6: No Regressions**
- [ ] All existing workflow guides still valid
- [ ] No broken links in documentation
- [ ] CLAUDE.md still readable and well-structured

---

## Testing Strategy

### Unit Testing
- **Test:** Each review prompt with sample input
- **Validate:** Prompt returns expected sections (findings, workflow reminder)
- **Coverage:** All 4 prompts tested

### Integration Testing
- **Test:** Full workflow from CLAUDE.md instructions
- **Scenario 1:** Quick commit review
  - Stage sample file
  - Follow CLAUDE.md instructions for quick review
  - Verify clink + codex works
  - Check workflow reminder appears
- **Scenario 2:** Deep branch review
  - Use feature branch with multiple commits
  - Follow CLAUDE.md instructions for deep review
  - Verify clink + gemini → codex multi-phase
  - Check continuation_id preserved
- **Scenario 3:** Task creation review
  - Use this task document (P1T11_TASK.md)
  - Follow task creation workflow
  - Verify gemini planner validation
  - Check workflow reminder guides next steps

### Edge Cases
- **Large files:** Test review prompts with >1000 line files
- **Multiple components:** Test deep review with 10+ changed files
- **Invalid task:** Test task review with incomplete task document

---

## Dependencies

**Internal:**
- Existing `.claude/workflows/` structure
- Existing `/docs/TASKS/` template structure
- CLAUDE.md format and conventions

**External:**
- Codex CLI installed and authenticated
- Gemini CLI installed and configured
- Zen-mcp server running and connected

**Blockers:**
- None (all dependencies already in place)

---

## Time Estimate Breakdown

| Component | Estimate | Notes |
|-----------|----------|-------|
| CLAUDE.md update | 1.5 hours | Remove old, add clink workflows |
| Review prompts (4 files) | 2 hours | Create + test each prompt |
| Workflow guides (3 files) | 2 hours | Update 2 existing, create 1 new |
| Task templates (2 files) | 0.5 hours | Minor updates |
| Testing (integration) | 1 hour | Test all 3 scenarios |
| Deep review + PR | 1 hour | Final validation |
| **Total** | **8 hours** | Within estimate range |

**Buffer:** Included 15% buffer for unforeseen issues

---

## Risks & Mitigations

**Risk 1: Workflow reminders too verbose**
- Impact: Users might ignore long reminders
- Probability: LOW
- Mitigation: Keep reminders concise (<10 lines), use bullet points
- Contingency: Shorten if feedback indicates too long

**Risk 2: Clink examples don't work**
- Impact: Users can't follow documentation
- Probability: LOW (already tested in proposal)
- Mitigation: Test every example before committing
- Contingency: Fix examples based on test failures

**Risk 3: Task templates too prescriptive**
- Impact: Creates friction for simple tasks
- Probability: MEDIUM
- Mitigation: Note that task review optional for trivial tasks (<2 hours)
- Contingency: Add "when to skip task review" guidance

---

## Notes

- This task implements the zen-mcp optimization proposal (see `/docs/CONCEPTS/zen-mcp-clink-optimization-proposal.md`)
- Codex review of proposal completed with all findings addressed
- Cost model is subscription-based ($320-350/month) not API-based
- Model selection via CLI configuration, NOT clink API parameters

---

## Related Documentation

- [Zen-MCP Clink Optimization Proposal](/docs/CONCEPTS/zen-mcp-clink-optimization-proposal.md)
- [Current CLAUDE.md](/CLAUDE.md)
- [Git Workflow Standards](/docs/STANDARDS/GIT_WORKFLOW.md)
- [Documentation Standards](/docs/STANDARDS/DOCUMENTATION_STANDARDS.md)

---

**Status:** 📋 Ready for Task Creation Review
**Next Step:** Request clink + gemini planner validation of this task document
