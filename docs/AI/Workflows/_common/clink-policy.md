# Clink-Only Tool Usage Policy

**⚠️ MANDATORY: Use `mcp__zen__clink` EXCLUSIVELY for all zen-mcp interactions.**

## Why This Matters

- MCP server configuration is **system-level** (not project-level)
- Direct zen tools (chat, thinkdeep, debug, etc.) bypass CLI authentication
- Using wrong tools causes **API permission errors** and breaks workflows
- Cost model depends on CLI subscriptions, not direct API usage

## Correct Tool Usage

```python
# ✅ CORRECT: Use clink with appropriate CLI and role
mcp__zen__clink(
    prompt="Review this implementation for trading safety",
    cli_name="codex",  # or "gemini"
    role="codereviewer"  # or "planner" or "default"
)
```

## Incorrect Tool Usage (NEVER DO THIS)

```python
# ❌ WRONG: Direct zen-mcp tools bypass CLI authentication
mcp__zen__chat(...)           # API permission error
mcp__zen__thinkdeep(...)      # API permission error
mcp__zen__codereview(...)     # API permission error
mcp__zen__debug(...)          # API permission error
mcp__zen__consensus(...)      # API permission error
mcp__zen__planner(...)        # API permission error
```

## Technical Limitation

Tool restriction is **not enforceable at project level** because MCP config is system-level (`~/.claude/config/`). This policy relies on **documentation + workflow discipline** rather than technical gates.

## If You Catch Yourself Using Direct Tools

1. STOP immediately
2. Use `mcp__zen__clink` instead with appropriate cli_name and role
3. Check `./03-reviews.md` for correct patterns
4. See `.claude/TROUBLESHOOTING.md` for detailed error resolution

## See Also

- [CLAUDE.md - Zen-MCP + Clink Integration](../../../../CLAUDE.md#zen-mcp--clink-integration) - Complete policy
- [Quick Review Workflow](../03-reviews.md) - Clink usage examples
- [Troubleshooting Guide](../troubleshooting.md) - Wrong-tool error fixes
