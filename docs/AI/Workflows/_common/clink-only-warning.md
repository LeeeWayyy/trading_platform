## 🚨 CRITICAL: Clink-Only Tool Usage

**⚠️ MANDATORY: Use `mcp__zen__clink` EXCLUSIVELY for all zen-mcp interactions.**

**❌ NEVER use direct zen-mcp tools** (chat, thinkdeep, codereview, debug, consensus, etc.) — they cause **API permission errors** and break the workflow.

**✅ CORRECT:** `mcp__zen__clink(prompt="...", cli_name="gemini", role="codereviewer")`
**❌ WRONG:** `mcp__zen__codereview(...)` ← API permission error!

See CLAUDE.md (Zen-MCP + Clink Integration section) for complete policy.
