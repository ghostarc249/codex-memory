## Codex Memory Policy

Before making a plan or editing code, search project memory for relevant decisions, constraints, patterns, and known pitfalls.

Use the `codex_memory` MCP server:
- `memory_search` before planning work.
- `memory_add` after durable architectural decisions, implementation patterns, or project constraints are discovered.
- `memory_list` to review recent memories.
- `memory_files` to review recently touched files.
- `memory_checkpoints` to review recent task-context writebacks.
- `memory_forget` to remove stale or wrong entries.

When installed with repo-local hooks, `UserPromptSubmit` performs the pre-work
search automatically, `PostToolUse` tracks touched files, and `Stop` writes
task-context memories automatically.
Still call `memory_add` manually for high-value decisions, constraints, and
patterns that deserve a precise durable record.

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
