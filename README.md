# Codex Memory Kit

A minimal, local-first memory layer for Codex. It gives Codex a durable
SQLite-backed project memory, an MCP server, and optional lifecycle hooks that
can recall relevant context before a turn starts and save compact checkpoints
after a turn ends.

The PyPI package is `codexmem`; the installed command is `codex-memory`.

Codex Memory Kit was inspired by
[`auto-memory`](https://github.com/dezgit2025/auto-memory), created by
[`dezgit2025`](https://github.com/dezgit2025). `auto-memory` brought automatic
memory workflows to GitHub Copilot; this package adapts that idea for a
Codex-native surface.

## What it provides

- SQLite + FTS5 durable memory storage
- Local deterministic semantic search for wording drift
- MCP stdio tools for searching, adding, listing, and maintaining memories
- `codex-memory` CLI for install, health checks, search, and maintenance
- Optional Codex hooks for automatic recall, touched-file tracking, and
  checkpoint writeback
- Bounded recall so memory helps without flooding the model context

The default semantic provider is an offline hash vectorizer. Memory content is
not sent to a remote embedding service.

## Install

Install the package with `pipx`:

```bash
pipx install codexmem
```

Then enable it for a repository:

```bash
cd /path/to/your/repo
codex-memory install
codex-memory doctor
codex-memory health
```

`codex-memory install` updates `~/.codex/config.toml` so Codex can use the MCP
server, then writes repo-local hooks to `.codex/hooks.json` in the current repo.

For local development from this checkout:

```bash
pipx install --editable .
```

## Quick Start

Add a memory:

```bash
codex-memory add \
  --scope my-project \
  --type decision \
  --title "Use Postgres as the default relational database" \
  --content "This project uses PostgreSQL as its default relational database. New persistence work should assume PostgreSQL unless explicitly stated otherwise." \
  --tags database postgres persistence
```

Search it:

```bash
codex-memory search "postgres persistence" --scope my-project --limit 5
```

Check what the automatic recall hook would inject:

```bash
printf '{"cwd":"%s","prompt":"postgres persistence"}' "$PWD" \
  | codex-memory hook user-prompt-submit
```

If there is a relevant match, the output includes
`hookSpecificOutput.additionalContext`. If there is no relevant match, it
returns:

```json
{"continue": true}
```

That empty result is intentional: unrelated memories are not added to the model
context.

## How Automatic Recall Works

When project hooks are installed, Codex can run these commands during its normal
lifecycle:

- `UserPromptSubmit`: searches local project memory before the model starts.
- `PostToolUse`: records files touched by Codex tools.
- `Stop`: saves a compact checkpoint from the final assistant response.

Recall is scoped to the current git root directory name by default. For normal
prompts, the hook only injects memories that pass relevance checks:

- exact FTS matches are allowed
- semantic-only matches must meet `min_semantic_context_score`
- recent files are included only when there is relevant memory
- continuation prompts can include recent checkpoints and touched files

Injected context is bounded:

- up to 4 memories
- up to 5 recent files
- up to 700 characters per memory excerpt
- up to 5000 characters total

These limits are deliberately conservative so automatic memory does not
unnecessarily consume the model context window.

## Configuration

`codex-memory install` manages the required Codex settings for you. The resulting
configuration in `~/.codex/config.toml` looks like this:

```toml
[features]
memories = true
codex_hooks = true

[codex_memory]
min_semantic_context_score = 0.18

[mcp_servers.codex_memory]
command = "codex-memory"
args = ["mcp"]
enabled = true
required = false
startup_timeout_sec = 10
tool_timeout_sec = 30
```

### Recall Threshold

`min_semantic_context_score` controls how strict semantic-only recall should be.
The default is `0.18`.

Lower values recall more aggressively and may add more context. Higher values
save more tokens but may miss weaker wording matches.

```toml
[codex_memory]
min_semantic_context_score = 0.12
```

For one-off testing, use an environment variable:

```bash
export CODEX_MEMORY_MIN_SEMANTIC_CONTEXT_SCORE=0.12
```

### Scope

The default scope is the current git root directory name. Override it when you
want multiple checkouts to share a memory scope:

```bash
export CODEX_MEMORY_SCOPE=my-project
```

### Database Location

The default database path is:

```text
~/.codex/codex-memory/memory.db
```

Override it with:

```bash
export CODEX_MEMORY_DB=/some/path/memory.db
```

## Install Options

```bash
codex-memory install --repo /path/to/repo
codex-memory install --no-global-config
codex-memory install --no-project-hooks
```

Project-local hooks only run when Codex trusts the project `.codex/` layer.

## CLI Reference

### `install`

Configures Codex Memory for a repository. By default it updates
`~/.codex/config.toml` and writes `.codex/hooks.json` in the current git repo.

Useful options:

- `--repo /path/to/repo`: install hooks into a specific repository.
- `--no-global-config`: skip changes to `~/.codex/config.toml`.
- `--no-project-hooks`: skip writing `.codex/hooks.json`.

### `doctor`

Runs an end-to-end installation check. It verifies the merged Codex config,
project hooks, database health, and a write/search roundtrip.

Useful options:

- `--repo /path/to/repo`: inspect a specific repository.
- `--scope my-project`: use a specific memory scope for the roundtrip.
- `--no-roundtrip`: skip the database write/search probe.

### `health`

Shows a terminal-friendly health report for the local memory database, including
schema status, corpus size, embedding coverage, and scope coverage.

Use `--json` when you want the raw machine-readable payload:

```bash
codex-memory health --json
```

### `schema-check`

Checks that the SQLite database has the expected tables, columns, metadata, and
embedding rows. It exits with a non-zero status if schema problems are found.

### `add`

Stores a memory record.

Required fields:

- `--scope`: project or repository scope.
- `--type`: one of `decision`, `constraint`, `pattern`, `task_context`,
  `pitfall`, or `note`.
- `--title`: short label for the memory.
- `--content`: the durable context to remember.

Optional fields:

- `--tags`: zero or more tags used for filtering and display.
- `--source`: optional origin identifier, useful for generated checkpoints.

### `search`

Searches memories. The default mode is `hybrid`, which combines FTS5 exact
matching with local semantic matching.

Useful options:

- `--scope my-project`: restrict results to one scope.
- `--limit 5`: limit result count.
- `--days 7`: only search recently updated memories.
- `--mode fts`: exact/project-term search.
- `--mode semantic`: wording-drift search.
- `--mode hybrid`: combined search.

### `list`

Lists recent memories, newest first.

Useful options:

- `--scope my-project`: restrict to one scope.
- `--limit 20`: limit result count.
- `--days 30`: only show recently updated memories.
- `--type decision`: filter by memory type.

### `show`

Displays one memory by numeric id:

```bash
codex-memory show 12
```

### `files`

Lists recently touched files tracked by the `PostToolUse` hook. This helps Codex
resume work with awareness of files that were recently read or edited.

Useful options:

- `--scope my-project`
- `--limit 10`
- `--days 7`

### `checkpoints`

Lists `task_context` memories written by the `Stop` hook. These are compact
continuation notes from previous Codex turns.

Useful options:

- `--scope my-project`
- `--limit 5`
- `--days 7`

### `embeddings`

Maintains local semantic-search vectors. Currently the supported action is:

```bash
codex-memory embeddings rebuild --scope my-project
```

Use this after changing embedding behavior or when `schema-check` reports
missing embedding rows.

### `forget`

Deletes a memory by numeric id:

```bash
codex-memory forget 12
```

Use this for stale, wrong, or overly noisy memories.

### `hook`

Runs a Codex lifecycle hook command. You usually do not call this manually;
`codex-memory install` wires it into `.codex/hooks.json`.

Supported hook events:

- `user-prompt-submit`: searches memory before a prompt reaches the model.
- `post-tool-use`: records touched files after Codex tool calls.
- `stop`: writes a compact checkpoint after a Codex turn.

Manual hook checks are useful when tuning recall:

```bash
printf '{"cwd":"%s","prompt":"postgres persistence"}' "$PWD" \
  | codex-memory hook user-prompt-submit
```

## Project Policy

You can add a policy block to your repo `AGENTS.md` so future Codex sessions know
how to use project memory:

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

## CLI Examples

```bash
codex-memory add \
  --scope my-project \
  --type decision \
  --title "Use handlers for business logic" \
  --content "HTTP endpoints should stay thin. Move business behavior into handlers or services." \
  --tags architecture handlers

codex-memory search "business logic should not live in endpoints" --scope my-project
codex-memory search "handler architecture" --scope my-project --mode semantic
codex-memory list --scope my-project --limit 10
codex-memory files --scope my-project --limit 10 --days 7
codex-memory checkpoints --scope my-project --limit 5
codex-memory show 1
codex-memory schema-check
codex-memory embeddings rebuild --scope my-project
```

## MCP Tools

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
