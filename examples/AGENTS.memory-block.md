## Codex Memory Policy

Before making a plan or editing code, search project memory for relevant decisions, constraints, patterns, and known pitfalls.

Use the `codex_memory` MCP server:
- `memory_search` before planning work.
- `memory_add` after durable architectural decisions, implementation patterns, or project constraints are discovered.
- `memory_list` to review recent memories.
- `memory_forget` to remove stale or wrong entries.

Never store:
- secrets, tokens, connection strings, private keys, credentials
- personal/sensitive data
- temporary debugging noise
- low-confidence assumptions

Prefer storing:
- architectural decisions
- codebase conventions
- migration status
- performance/security constraints
- reusable implementation patterns
