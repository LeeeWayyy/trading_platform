# Codex MCP Integration Guide

**Status:** ✅ Documented (Official OpenAI Approach)
**Created:** 2025-10-19
**Updated:** 2025-10-19
**Purpose:** Guide for integrating OpenAI Codex with Claude Code via Model Context Protocol (MCP)

---

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Installation & Setup](#installation--setup)
4. [Configuration](#configuration)
5. [Usage](#usage)
6. [Best Practices](#best-practices)
7. [Troubleshooting](#troubleshooting)

---

## Overview

**What is Codex MCP Integration?**

OpenAI Codex CLI can run as an MCP (Model Context Protocol) server, allowing Claude Code to use Codex as a tool. This provides:

- **Official integration**: Uses native `codex mcp-server` command (not third-party wrappers)
- **Code review**: Let Codex review code changes from within Claude Code
- **Code generation**: Generate code, tests, and fixes
- **Multi-model support**: Access GPT-5, O3, O3-mini, and other OpenAI models
- **Direct integration**: No separate CLI needed

**How it works:**

1. Codex runs as an MCP server (`codex mcp-server`)
2. Claude Code connects to it as an MCP client
3. You can invoke Codex from Claude Code using tool calls

**Reference:**
- Official docs: https://github.com/openai/codex/blob/main/docs/advanced.md#model-context-protocol-mcp

---

## Prerequisites

### System Requirements

- **Node.js**: v22+ (for Codex CLI)
- **npm**: v10+
- **Claude Code**: Installed and authenticated
- **OpenAI Account**: With ChatGPT Plus, Pro, Team, Edu, or Enterprise plan

### Supported Platforms

- **macOS**: ✅ Fully supported
- **Linux**: ✅ Fully supported
- **Windows**: ⚠️  Experimental (use WSL for best results)

**Note:** There are known issues with `codex mcp-server` on M4 Mac Mini systems (as of 2025-10-19). Check https://github.com/openai/codex/issues for updates.

---

## Installation & Setup

### Step 1: Install Codex CLI

```bash
# Option A: npm (recommended)
npm install -g @openai/codex

# Option B: Homebrew (macOS)
brew install codex

# Verify installation
codex --version
# Expected: 0.47.0 or newer
```

### Step 2: Authenticate Codex CLI

On first run, Codex will prompt you to authenticate:

```bash
# Run any codex command to trigger authentication
codex exec "echo hello"

# Follow prompts to sign in with your ChatGPT account
# Recommended: Use ChatGPT Plus/Pro/Team/Edu/Enterprise account
```

**Verify authentication:**
```bash
# Create test file
echo "console.log('test')" > test.js

# Run codex exec to verify it works
codex exec "explain this code" -- test.js

# If successful, Codex will explain the code
# Clean up
rm test.js
```

### Step 3: Test MCP Server

Before integrating with Claude Code, verify `codex mcp-server` works:

```bash
# Test with MCP Inspector
npx @modelcontextprotocol/inspector codex mcp-server

# This should open a browser window showing available tools
# You should see: "codex - Run a Codex session"
```

If the inspector works, you're ready to configure Claude Code!

---

## Configuration

### Add Codex to Claude Code

```bash
# Add Codex MCP server to Claude Code
claude mcp add --transport stdio codex-mcp -- codex mcp-server

# Verify it was added
claude mcp list
# Should show: codex-mcp
```

**Restart Claude Code** for changes to take effect.

### Configure Codex Behavior (Optional)

Edit `~/.codex/config.toml` to customize Codex settings:

```toml
# Default model
model = "gpt-5"

# Sandbox mode
# Options: "read-only", "workspace-write", "danger-full-access"
sandbox = "workspace-write"

# Max tokens
max_tokens = 8000

# Enable git integration
[git]
enabled = true
auto_commit = false
```

**Available models:**
- `gpt-5`: Latest GPT-5 model (default, recommended)
- `o3`: OpenAI O3 model (reasoning-optimized)
- `o3-mini`: Smaller O3 model (faster, lower cost)

**Sandbox modes:**
- `read-only`: Codex can read files but not modify anything (safest)
- `workspace-write`: Codex can read and write files in workspace (recommended)
- `danger-full-access`: Codex can execute any command (use with caution!)

---

## Usage

### In Claude Code

Once configured, you can invoke Codex from Claude Code by using tool calls.

**Example 1: Code Review**

```
User: Can you use the codex tool to review libs/risk_management/breaker.py
      for thread safety issues?

Claude Code: [Invokes Codex MCP tool with the file]

Codex: Analyzing libs/risk_management/breaker.py...

HIGH: Potential race condition in trip() method (line 142)
- The read-modify-write operation is not atomic
- Multiple concurrent calls could cause lost updates
- Recommend using Redis WATCH/MULTI/EXEC transaction

MEDIUM: Missing type hint for _get_state() return value
- Should return dict[str, Any]

[... detailed review ...]
```

**Example 2: Generate Tests**

```
User: Use codex to generate unit tests for libs/risk_management/checker.py

Claude Code: [Invokes Codex MCP tool]

Codex: Generated tests/test_checker_extended.py:

def test_concurrent_position_updates():
    """Test position limit checks with concurrent updates."""
    ...

def test_circuit_breaker_during_order_validation():
    """Test order validation when breaker trips mid-check."""
    ...

[... full test suite ...]
```

**Example 3: Explain Code**

```
User: Ask codex to explain how the circuit breaker state transitions work

Claude Code: [Invokes Codex MCP tool]

Codex: The circuit breaker has three states:

1. OPEN: Normal operation, orders allowed
2. TRIPPED: Circuit broken, orders blocked
3. QUIET_PERIOD: Cooling down after trip

State transitions:
- OPEN → TRIPPED: When trip() is called (e.g., loss limit exceeded)
- TRIPPED → QUIET_PERIOD: After quiet_period_minutes elapsed
- QUIET_PERIOD → OPEN: When reset() is called with approval

[... detailed explanation ...]
```

### Available Codex Capabilities

When you invoke the `codex` MCP tool, it can:

- **Review code**: Find bugs, security issues, style violations
- **Generate code**: Create functions, classes, modules
- **Write tests**: Generate unit tests, integration tests
- **Explain code**: Provide detailed explanations
- **Fix bugs**: Suggest or apply fixes
- **Refactor**: Improve code structure and quality

**Tool Parameters:**

The `codex` tool accepts a configuration object with parameters like:
- `prompt`: What you want Codex to do
- `files`: Which files to analyze or modify
- `model`: Which OpenAI model to use (overrides default)
- `sandbox`: Execution mode (read-only, workspace-write, full-access)

---

## Best Practices

### 1. Start with Read-Only Mode

For initial testing, use `read-only` sandbox in `~/.codex/config.toml`:

```toml
sandbox = "read-only"
```

This allows Codex to analyze code without making changes. Once comfortable, upgrade to `workspace-write`.

### 2. Provide Project Context

Create `.codex/context.md` in your project root:

```markdown
# Trading Platform Context for Codex

## Critical Safety Requirements
- ALL order placement must check circuit breaker first
- ALL timestamps must be UTC-aware
- ALL order IDs must be deterministic (hash-based)
- NO SQL injection (use parameterized queries only)

## Code Standards
- Python 3.11+ with strict type hints
- mypy --strict must pass
- pytest coverage >= 80%
- Google-style docstrings required

## See Also
- docs/STANDARDS/CODING_STANDARDS.md
- CLAUDE.md
```

Codex will automatically read this for context.

### 3. Review All Changes

**Never blindly apply Codex suggestions:**

1. Read the suggested changes carefully
2. Verify logic correctness
3. Check for security issues
4. Run tests before committing
5. Use git to track changes

### 4. Combine with Other Tools

Use Codex alongside:
- **Gemini Code Assist**: For automated PR reviews
- **CI/CD Pipeline**: For test coverage enforcement
- **Codex via Claude Code**: For focused analysis
- **Claude Code**: For broader implementation

### 5. Monitor Quota Usage

Codex uses your ChatGPT plan quota:

- **Plus ($20/month)**: Limited requests
- **Pro ($200/month)**: Higher limits
- **Team/Enterprise**: Highest limits

Use Codex for high-value tasks, rely on free tools (mypy, ruff, pytest) for basic checks.

---

## Troubleshooting

### Issue: `codex: command not found`

**Solution:**
```bash
# Reinstall Codex CLI
npm install -g @openai/codex

# Or via Homebrew
brew install codex

# Verify
codex --version
```

### Issue: MCP server not showing in Claude Code

**Solution:**
```bash
# Check MCP server is registered
claude mcp list

# If not listed, re-add
claude mcp add --transport stdio codex-mcp -- codex mcp-server

# Restart Claude Code
# (Quit and reopen application)

# Verify in Claude Code:
# Settings → Tools → Manage MCP Tools
```

### Issue: Authentication failed

**Solution:**
```bash
# Re-authenticate Codex CLI
codex exec "echo test"

# Follow prompts to sign in with ChatGPT account
```

### Issue: `codex mcp-server` fails to start

**Symptom:** MCP server doesn't respond or crashes

**Solution:**
```bash
# Test with MCP Inspector
npx @modelcontextprotocol/inspector codex mcp-server

# Check for errors in output
# Common issues:
# - Codex CLI not authenticated
# - Unsupported platform (M4 Mac Mini has known issues)
# - Missing dependencies

# Check Codex GitHub issues:
# https://github.com/openai/codex/issues
```

### Issue: Changes not being applied

**Solution:**
```bash
# Check sandbox mode in ~/.codex/config.toml
cat ~/.codex/config.toml | grep sandbox

# If set to read-only, change to workspace-write:
sandbox = "workspace-write"

# Restart Claude Code
```

### Issue: Rate limiting / quota exceeded

**Solution:**
- Wait for quota reset (varies by plan)
- Upgrade ChatGPT plan (Plus → Pro → Team)
- Use Codex sparingly for high-value tasks only

---

## Summary

**Codex MCP integration provides:**
- ✅ Official OpenAI Codex integration (not third-party)
- ✅ Code review using GPT-5/O3 models
- ✅ Code generation and test writing
- ✅ Direct integration with Claude Code
- ✅ Configurable execution modes

**Setup steps:**

1. Install Codex CLI: `npm install -g @openai/codex`
2. Authenticate: `codex exec "echo test"` (follow prompts)
3. Test MCP server: `npx @modelcontextprotocol/inspector codex mcp-server`
4. Add to Claude Code: `claude mcp add --transport stdio codex-mcp -- codex mcp-server`
5. Restart Claude Code
6. Invoke from Claude Code: "Use codex to review this file"

**Best practices:**
- Start with read-only sandbox
- Provide project context in `.codex/context.md`
- Always review changes before applying
- Combine with other automation (Gemini, CI/CD)
- Monitor quota usage

**See also:**
- Official Codex MCP docs: https://github.com/openai/codex/blob/main/docs/advanced.md#model-context-protocol-mcp
- Codex main docs: https://developers.openai.com/codex/
- MCP specification: https://modelcontextprotocol.io/
- `docs/STANDARDS/GIT_WORKFLOW.md` - PR workflow with automated reviews
- `.gemini/config.yaml` - Gemini Code Assist configuration
- `GEMINI.md` - Project context for Gemini reviews

---

**Status:** Ready for use (requires ChatGPT Plus/Pro/Team/Enterprise)
**Maintainer:** See CODEOWNERS
**Last Updated:** 2025-10-19

**Note:** This guide documents the official `codex mcp-server` command from OpenAI, not third-party wrappers. Always refer to https://github.com/openai/codex for the latest documentation.
