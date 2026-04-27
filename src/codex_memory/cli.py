from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .store import MemoryStore, codex_home
from .mcp_server import run_mcp_server


CONFIG_SNIPPET = """
# --- codex-memory start ---
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
# --- codex-memory end ---
""".strip()


def print_json(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def install(args: argparse.Namespace) -> None:
    home = codex_home()
    home.mkdir(parents=True, exist_ok=True)
    config = home / "config.toml"
    existing = config.read_text() if config.exists() else ""
    if "codex-memory start" not in existing:
        with config.open("a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write("\n" + CONFIG_SNIPPET + "\n")
    store = MemoryStore()
    print_json({
        "installed": True,
        "config": str(config),
        "db": str(store.db_path),
        "next": "Run: codex-memory health",
    })


def health(args: argparse.Namespace) -> None:
    print_json(MemoryStore().health())


def add(args: argparse.Namespace) -> None:
    store = MemoryStore()
    result = store.add(
        scope=args.scope,
        type_=args.type,
        title=args.title,
        content=args.content,
        tags=args.tags or [],
        source=args.source,
    )
    print_json(result)


def search(args: argparse.Namespace) -> None:
    print_json(MemoryStore().search(args.query, scope=args.scope, limit=args.limit))


def list_memories(args: argparse.Namespace) -> None:
    print_json(MemoryStore().list(scope=args.scope, limit=args.limit))


def forget(args: argparse.Namespace) -> None:
    print_json({"forgotten": MemoryStore().forget(args.id)})


def mcp(args: argparse.Namespace) -> None:
    run_mcp_server(MemoryStore())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="codex-memory")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("install")
    p.set_defaults(func=install)

    p = sub.add_parser("health")
    p.set_defaults(func=health)

    p = sub.add_parser("add")
    p.add_argument("--scope", required=True)
    p.add_argument("--type", required=True, choices=["decision", "constraint", "pattern", "task_context", "pitfall", "note"])
    p.add_argument("--title", required=True)
    p.add_argument("--content", required=True)
    p.add_argument("--tags", nargs="*", default=[])
    p.add_argument("--source")
    p.set_defaults(func=add)

    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--scope")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=search)

    p = sub.add_parser("list")
    p.add_argument("--scope")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=list_memories)

    p = sub.add_parser("forget")
    p.add_argument("id", type=int)
    p.set_defaults(func=forget)

    p = sub.add_parser("mcp")
    p.set_defaults(func=mcp)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
