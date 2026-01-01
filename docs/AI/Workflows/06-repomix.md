# Repomix Integration Workflow

**Purpose:** AI-assisted codebase analysis and context management using Repomix
**Tools:** Repomix MCP Server, Claude Code Plugins, GitHub Actions

---

## Overview

Repomix packages codebases into AI-optimized formats with ~70% token reduction through Tree-sitter compression. This enables efficient context sharing for reviews, analysis, and delegation.

---

## Available Tools

### MCP Server Tools (via `repomix --mcp`)

| Tool | Purpose |
|------|---------|
| `pack_codebase` | Package local directories into consolidated XML |
| `pack_remote_repository` | Clone and analyze GitHub repositories |
| `read_repomix_output` | Retrieve previously generated outputs (partial reading supported) |
| `grep_repomix_output` | Search outputs with regex patterns |
| `file_system_read_file` | Secure file access with Secretlint validation |

### Claude Code Plugins

| Plugin | Command | Purpose |
|--------|---------|---------|
| `repomix-mcp` | Foundation | AI-powered analysis, security scanning |
| `repomix-commands` | `/repomix-commands:pack-local` | Quick repository packing |
| `repomix-explorer` | `/repomix-explorer:explore-local` | Natural language exploration |

---

## When to Use Repomix

### 1. Pre-Analysis Phase (00-analysis-checklist.md)

Before implementing a feature, pack relevant directories for comprehensive analysis:

```bash
# Pack specific directories for focused analysis
/repomix-commands:pack-local ./libs/secrets ./apps/execution_gateway

# Or use explorer for natural language queries
/repomix-explorer:explore-local ./apps "How does the circuit breaker integrate with order execution?"
```

**Benefits:**
- Understand all impacted components before coding
- Identify patterns and dependencies
- 70% token reduction preserves context budget

### 2. Pre-Review Context (MANDATORY)

Before requesting zen-mcp reviews, pack the changes for structured context:

```bash
# Pack changed directories
/repomix-commands:pack-local ./libs/tax ./tests/libs/tax
```

**Note:** This is mandatory per [03-reviews.md](./03-reviews.md) Step 0. Packing provides reviewers with compressed, structured context for better analysis.

### 3. Subagent Delegation (16-subagent-delegation.md)

When delegating work to subagents, pack relevant context:

```bash
# Pack context for delegation
/repomix-commands:pack-local ./apps/signal_service --output delegation-context.xml
```

### 4. Remote Repository Analysis

Analyze external dependencies or reference implementations:

```bash
# Pack a GitHub repository
/repomix-commands:pack-remote alpacahq/alpaca-py
```

---

## GitHub Actions Integration

Repomix runs automatically on PRs to generate AI-ready context:

```yaml
# .github/workflows/repomix-context.yml
# Generates packed codebase context on PRs for AI analysis
```

**Outputs:**
- `repomix-full.xml` - Full codebase context
- `repomix-changed.xml` - Changed files only (for PR reviews)

Artifacts are available for 7 days and can be downloaded for offline analysis.

---

## Configuration

Project configuration in `repomix.config.json`:

```json
{
  "output": {
    "style": "xml",
    "compress": true
  },
  "ignore": {
    "customPatterns": [
      "*.pyc", "__pycache__", ".venv",
      "artifacts/", "data/", "htmlcov/"
    ]
  },
  "security": {
    "enableSecurityCheck": true
  }
}
```

**Key settings:**
- `compress: true` - Enable Tree-sitter compression (~70% token reduction)
- `style: xml` - XML format is most efficient for AI parsing
- `enableSecurityCheck: true` - Secretlint validation prevents credential exposure

---

## Best Practices

### DO:
- Pack focused directories rather than entire codebase
- Use explorer for understanding unfamiliar code areas
- Leverage compression for large codebases
- Check security warnings before sharing packed output

### DON'T:
- Pack the entire repo when only analyzing one component
- Ignore Secretlint security warnings
- Share packed output containing credentials
- Skip repomix for simple, single-file changes

---

## Common Commands

```bash
# Pack local directory with compression
/repomix-commands:pack-local ./libs/secrets

# Explore with natural language
/repomix-explorer:explore-local ./apps "What endpoints handle order placement?"

# Pack remote repository
/repomix-commands:pack-remote owner/repo

# Pack with specific patterns
/repomix-commands:pack-local ./src --include "*.py" --ignore "test_*"
```

---

## Troubleshooting

### MCP Server Not Responding

```bash
# Verify MCP server is configured
claude mcp list

# Re-add if missing
claude mcp add repomix -- npx -y repomix --mcp
```

### Plugin Commands Not Available

```bash
# Check installed plugins
claude plugin marketplace list

# Reinstall plugins
claude plugin install repomix-mcp@repomix
claude plugin install repomix-commands@repomix
claude plugin install repomix-explorer@repomix
```

### Security Check Failures

If Secretlint blocks packing due to detected secrets:
1. Review flagged files
2. Add patterns to `.gitignore` or `repomix.config.json` ignore
3. Never disable security checks to bypass warnings

---

## Integration with Other Workflows

| Workflow | Repomix Usage |
|----------|---------------|
| 00-analysis-checklist | Pack directories before analysis |
| 03-reviews | Mandatory pre-review context |
| 16-subagent-delegation | Pack context for delegation |
| GitHub Actions | Automated context generation on PRs |

---

**Last Updated:** 2025-12-31
