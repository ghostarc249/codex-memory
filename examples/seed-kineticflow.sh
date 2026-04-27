#!/usr/bin/env bash
set -euo pipefail

codex-memory add \
  --scope kineticflow \
  --type decision \
  --title "KineticFlow uses Postgres as the default relational database" \
  --content "KineticFlow has migrated from MSSQL to Postgres. New persistence and query work should assume PostgreSQL unless explicitly stated otherwise." \
  --tags database postgres migration

codex-memory add \
  --scope kineticflow \
  --type constraint \
  --title "Prefer Dapper over EF Core for query paths" \
  --content "The project prefers Dapper for query-heavy and search-oriented paths because generated SQL should remain explicit and reviewable." \
  --tags dapper data-access performance

codex-memory add \
  --scope kineticflow \
  --type pattern \
  --title "FastEndpoints processors handle auditing" \
  --content "Auditing should be centralized through FastEndpoints Pre/Post Processors rather than duplicated per endpoint." \
  --tags fastendpoints auditing processors

codex-memory add \
  --scope kineticflow \
  --type pattern \
  --title "Use vertical slice architecture and modular monolith boundaries" \
  --content "Backend work should preserve vertical slice organization and modular monolith boundaries." \
  --tags architecture vsa modular-monolith
