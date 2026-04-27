#!/usr/bin/env bash
set -euo pipefail

codex-memory add \
  --scope my-project \
  --type decision \
  --title "Use Postgres as the default relational database" \
  --content "This project uses Postgres as its default relational database. New persistence and query work should assume PostgreSQL unless explicitly stated otherwise." \
  --tags database postgres migration

codex-memory add \
  --scope my-project \
  --type constraint \
  --title "Prefer explicit query paths" \
  --content "Query-heavy and search-oriented paths should keep generated SQL explicit and reviewable." \
  --tags data-access performance

codex-memory add \
  --scope my-project \
  --type pattern \
  --title "Keep HTTP endpoints thin" \
  --content "Business behavior should live in handlers or services rather than being duplicated at the HTTP edge." \
  --tags api architecture handlers

codex-memory add \
  --scope my-project \
  --type pattern \
  --title "Preserve established module boundaries" \
  --content "New work should follow the repository's existing ownership and module boundaries." \
  --tags architecture modules
