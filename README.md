# Codex Memory Kit

A minimal, local-first Codex memory setup inspired by `auto-memory`, but designed for Codex:

- SQLite + FTS5 durable memory store
- MCP stdio server exposing memory tools
- CLI for `install`, `health`, `schema-check`, `add`, `search`, `list`, `show`, `files`, `checkpoints`, `embeddings`, `forget`, `hook`
- Codex lifecycle hooks for automatic recall, touched-file tracking, and turn writeback
- Optional Codex config snippets for `~/.codex/config.toml`
- Optional project `AGENTS.md` policy block

## Why this exists

Codex has native memories, but native memories are generated state under `~/.codex/memories/`.
This kit gives you a more explicit engineering memory layer:

- architecture decisions
- implementation constraints
- reusable patterns
- durable project context
- known pitfalls

Search is hybrid by default: SQLite FTS5 for exact/project terminology plus
local semantic embeddings for wording drift. The default embedding provider is a
deterministic, offline hash vectorizer, so installs stay zero-dependency and do
not send memory content to a remote service.

## Install locally with pipx

From this folder:

```bash
pipx install .
```

Or for development:

```bash
pipx install --editable .
```

Then:

```bash
codex-memory install
codex-memory health
```

Run `codex-memory install` from each repository where you want automatic memory.
It updates the user Codex config so the MCP server is available, then writes
repo-local hooks to `<repo>/.codex/hooks.json`.

## Manual Codex config

Add this to `~/.codex/config.toml`:

```toml
[features]
memories = true
codex_hooks = true

[mcp_servers.codex_memory]
command = "codex-memory"
args = ["mcp"]
enabled = true
required = false
startup_timeout_sec = 10
tool_timeout_sec = 30
```

## Automatic memory hooks

`codex-memory install` writes repo-local hooks equivalent to:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "codex-memory hook user-prompt-submit",
            "timeout": 10,
            "statusMessage": "Searching Codex memory"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "codex-memory hook post-tool-use",
            "timeout": 10,
            "statusMessage": "Tracking Codex memory context"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "codex-memory hook stop",
            "timeout": 10,
            "statusMessage": "Saving Codex memory"
          }
        ]
      }
    ]
  }
}
```

The `UserPromptSubmit` hook searches the current repository scope before the
model starts work and injects matching memories plus recently touched files as
extra developer context. The `PostToolUse` hook records files seen in Codex tool
calls. The `Stop` hook stores the final assistant response as a `task_context`
memory with `codex-hook` and `auto-memory` tags.

The scope defaults to the git root directory name. Override it per environment:

```bash
export CODEX_MEMORY_SCOPE=kineticflow
```

Useful install options:

```bash
codex-memory install --repo /path/to/repo
codex-memory install --no-global-config
codex-memory install --no-project-hooks
```

Project-local hooks only load when Codex trusts the project `.codex/` layer.

## Project AGENTS.md policy block

Add this to your repo `AGENTS.md`:

```md
## Codex Memory Policy

Before making a plan or editing code, search project memory for relevant decisions, constraints, patterns, and known pitfalls.

Use the `codex_memory` MCP server:
- `memory_search` before planning work.
- `memory_add` after durable architectural decisions, implementation patterns, or project constraints are discovered.
- `memory_list` to review recent memories.
- `memory_files` to review recently touched files.
- `memory_checkpoints` to review recent task-context writebacks.
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
```

## Example usage

```bash
codex-memory add \
  --scope kineticflow \
  --type decision \
  --title "Use Postgres as the default relational database" \
  --content "KineticFlow has migrated from MSSQL to Postgres. New persistence work should assume PostgreSQL unless explicitly stated otherwise." \
  --tags database postgres migration

codex-memory search "postgres dapper pgvector" --scope kineticflow --limit 5
codex-memory search "business logic should not live in endpoints" --scope kineticflow --mode semantic
codex-memory files --scope kineticflow --limit 10 --days 7
codex-memory checkpoints --scope kineticflow --limit 5
codex-memory show 1
codex-memory schema-check
codex-memory embeddings rebuild --scope kineticflow
```

## MCP tools exposed

- `memory_search`
- `memory_add`
- `memory_list`
- `memory_show`
- `memory_files`
- `memory_checkpoints`
- `memory_forget`
- `memory_health`
- `memory_schema_check`
- `memory_embeddings_rebuild`

## Data location

Default:

```text
~/.codex/codex-memory/memory.db
```

Override:

```bash
export CODEX_MEMORY_DB=/some/path/memory.db
```

## Notes

This starter MCP implementation is intentionally small and stdlib-only.
It implements enough JSON-RPC over stdio for local MCP tool usage.
If you later want richer MCP protocol support, replace `mcp_server.py`
with the official MCP Python SDK implementation.
