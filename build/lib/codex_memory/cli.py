from __future__ import annotations

import argparse
import json
import os
import sys
import subprocess
import time
from pathlib import Path

from .store import MemoryStore, codex_home
from .mcp_server import run_mcp_server
from . import hooks


CONFIG_SNIPPET = """
# --- codex-memory start ---
[mcp_servers.codex_memory]
command = "codex-memory"
args = ["mcp"]
enabled = true
required = false
startup_timeout_sec = 10
tool_timeout_sec = 30
# --- codex-memory end ---
""".strip()

FEATURE_VALUES = {
    "memories": "true",
    "codex_hooks": "true",
}

MCP_VALUES = {
    "command": '"codex-memory"',
    "args": '["mcp"]',
    "enabled": "true",
    "required": "false",
    "startup_timeout_sec": "10",
    "tool_timeout_sec": "30",
}


PROJECT_HOOKS = {
    "hooks": {
        "UserPromptSubmit": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": "codex-memory hook user-prompt-submit",
                        "timeout": 10,
                        "statusMessage": "Searching Codex memory",
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
                        "statusMessage": "Tracking Codex memory context",
                    }
                ],
            }
        ],
        "Stop": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": "codex-memory hook stop",
                        "timeout": 10,
                        "statusMessage": "Saving Codex memory",
                    }
                ]
            }
        ],
    }
}


def print_json(value: object) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def install(args: argparse.Namespace) -> None:
    home = codex_home()
    home.mkdir(parents=True, exist_ok=True)
    config = home / "config.toml"
    existing = config.read_text() if config.exists() else ""
    global_config_updated = False
    if not args.no_global_config:
        updated = merge_codex_config(existing)
        if updated != existing:
            config.write_text(updated, encoding="utf-8")
            global_config_updated = True

    project_hooks_path = None
    project_hooks_updated = False
    if not args.no_project_hooks:
        repo_root = resolve_install_root(args.repo)
        project_hooks_path = repo_root / ".codex" / "hooks.json"
        project_hooks_updated = install_project_hooks(project_hooks_path)

    store = MemoryStore()
    print_json({
        "installed": True,
        "config": str(config),
        "global_config_updated": global_config_updated,
        "project_hooks": str(project_hooks_path) if project_hooks_path else None,
        "project_hooks_updated": project_hooks_updated,
        "db": str(store.db_path),
        "next": "Run: codex-memory health",
    })


def health(args: argparse.Namespace) -> None:
    print_json(MemoryStore().health())


def schema_check(args: argparse.Namespace) -> None:
    problems = MemoryStore().schema_check()
    print_json({"ok": not problems, "problems": problems})
    if problems:
        raise SystemExit(2)


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
    store = MemoryStore()
    if args.mode == "semantic":
        print_json(store.semantic_search(args.query, scope=args.scope, limit=args.limit, days=args.days))
    elif args.mode == "hybrid":
        print_json(store.hybrid_search(args.query, scope=args.scope, limit=args.limit, days=args.days))
    else:
        print_json(store.search(args.query, scope=args.scope, limit=args.limit, days=args.days))


def list_memories(args: argparse.Namespace) -> None:
    print_json(MemoryStore().list(scope=args.scope, limit=args.limit, days=args.days, type_=args.type))


def show(args: argparse.Namespace) -> None:
    print_json(MemoryStore().get(args.id))


def files(args: argparse.Namespace) -> None:
    print_json(MemoryStore().list_files(scope=args.scope, limit=args.limit, days=args.days))


def checkpoints(args: argparse.Namespace) -> None:
    print_json(MemoryStore().checkpoints(scope=args.scope, limit=args.limit, days=args.days))


def embeddings(args: argparse.Namespace) -> None:
    print_json(MemoryStore().rebuild_embeddings(scope=args.scope))


def forget(args: argparse.Namespace) -> None:
    print_json({"forgotten": MemoryStore().forget(args.id)})


def mcp(args: argparse.Namespace) -> None:
    run_mcp_server(MemoryStore())


def hook(args: argparse.Namespace) -> None:
    if args.event == "user-prompt-submit":
        hooks.user_prompt_submit()
        return
    if args.event == "post-tool-use":
        hooks.post_tool_use()
        return
    if args.event == "stop":
        hooks.stop()
        return
    raise ValueError(f"Unknown hook event: {args.event}")


def resolve_install_root(repo: str | None) -> Path:
    cwd = Path(repo or os.getcwd()).resolve()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(cwd),
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:
        result = None
    if result and result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip()).resolve()
    return cwd


def install_project_hooks(path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_json_object(path)
    merged = merge_hooks(existing, PROJECT_HOOKS)
    if existing == merged:
        return False
    path.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return True


def merge_codex_config(existing: str) -> str:
    text = remove_managed_block(existing)
    text = upsert_table_values(text, "features", FEATURE_VALUES, managed=False)
    text = upsert_table_values(text, "mcp_servers.codex_memory", MCP_VALUES, managed=True)
    return text


def remove_managed_block(text: str) -> str:
    lines = text.splitlines()
    result: list[str] = []
    skipping = False
    for line in lines:
        if line.strip() == "# --- codex-memory start ---":
            skipping = True
            continue
        if skipping and line.strip() == "# --- codex-memory end ---":
            skipping = False
            continue
        if not skipping:
            result.append(line)
    return "\n".join(result).rstrip() + ("\n" if result else "")


def upsert_table_values(text: str, table: str, values: dict[str, str], managed: bool) -> str:
    lines = text.splitlines()
    header_index = find_table_header(lines, table)
    if header_index is None:
        block = format_table_block(table, values, managed=managed)
        prefix = "\n" if text.strip() else ""
        return text.rstrip() + prefix + block + "\n"

    end_index = find_table_end(lines, header_index)
    section = lines[header_index + 1:end_index]
    seen: set[str] = set()
    new_section: list[str] = []
    for line in section:
        key = toml_key(line)
        if key in values:
            new_section.append(f"{key} = {values[key]}")
            seen.add(key)
        else:
            new_section.append(line)
    for key, value in values.items():
        if key not in seen:
            new_section.append(f"{key} = {value}")

    merged_lines = lines[:header_index + 1] + new_section + lines[end_index:]
    return "\n".join(merged_lines).rstrip() + "\n"


def format_table_block(table: str, values: dict[str, str], managed: bool) -> str:
    lines: list[str] = []
    if managed:
        lines.append("# --- codex-memory start ---")
    lines.append(f"[{table}]")
    lines.extend(f"{key} = {value}" for key, value in values.items())
    if managed:
        lines.append("# --- codex-memory end ---")
    return "\n".join(lines)


def find_table_header(lines: list[str], table: str) -> int | None:
    target = f"[{table}]"
    for index, line in enumerate(lines):
        if line.strip().split("#", 1)[0].strip() == target:
            return index
    return None


def find_table_end(lines: list[str], header_index: int) -> int:
    for index in range(header_index + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("[") and stripped.split("#", 1)[0].strip().endswith("]"):
            return index
    return len(lines)


def toml_key(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    return stripped.split("=", 1)[0].strip()


def load_json_object(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Cannot update invalid JSON at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"Cannot update {path}: expected a JSON object")
    return value


def merge_hooks(existing: dict[str, object], desired: dict[str, object]) -> dict[str, object]:
    result = json.loads(json.dumps(existing))
    result_hooks = result.setdefault("hooks", {})
    if not isinstance(result_hooks, dict):
        raise SystemExit("Cannot update hooks.json: `hooks` must be a JSON object")

    desired_hooks = desired["hooks"]
    assert isinstance(desired_hooks, dict)
    for event_name, groups in desired_hooks.items():
        existing_groups = result_hooks.setdefault(event_name, [])
        if not isinstance(existing_groups, list):
            raise SystemExit(f"Cannot update hooks.json: hooks.{event_name} must be an array")
        assert isinstance(groups, list)
        for group in groups:
            if not any(hook_group_has_command(existing_group, group) for existing_group in existing_groups):
                existing_groups.append(group)
    return result


def hook_group_has_command(existing_group: object, desired_group: object) -> bool:
    if not isinstance(existing_group, dict) or not isinstance(desired_group, dict):
        return False
    desired_commands = group_commands(desired_group)
    return bool(desired_commands.intersection(group_commands(existing_group)))


def group_commands(group: dict[str, object]) -> set[str]:
    hooks_value = group.get("hooks")
    if not isinstance(hooks_value, list):
        return set()
    commands = set()
    for hook_value in hooks_value:
        if isinstance(hook_value, dict) and isinstance(hook_value.get("command"), str):
            commands.add(hook_value["command"])
    return commands


def main(argv: list[str] | None = None) -> None:
    started = time.monotonic()
    parser = argparse.ArgumentParser(prog="codex-memory")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("install")
    p.add_argument("--repo", help="Repository path to install project hooks into. Defaults to the current working directory.")
    p.add_argument("--no-global-config", action="store_true", help="Do not update ~/.codex/config.toml.")
    p.add_argument("--no-project-hooks", action="store_true", help="Do not write <repo>/.codex/hooks.json.")
    p.set_defaults(func=install)

    p = sub.add_parser("health")
    p.set_defaults(func=health)

    p = sub.add_parser("schema-check")
    p.set_defaults(func=schema_check)

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
    p.add_argument("--days", type=int)
    p.add_argument("--mode", choices=["fts", "semantic", "hybrid"], default="hybrid")
    p.set_defaults(func=search)

    p = sub.add_parser("list")
    p.add_argument("--scope")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--days", type=int)
    p.add_argument("--type", choices=["decision", "constraint", "pattern", "task_context", "pitfall", "note"])
    p.set_defaults(func=list_memories)

    p = sub.add_parser("show")
    p.add_argument("id", type=int)
    p.set_defaults(func=show)

    p = sub.add_parser("files")
    p.add_argument("--scope")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--days", type=int)
    p.set_defaults(func=files)

    p = sub.add_parser("checkpoints")
    p.add_argument("--scope")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--days", type=int)
    p.set_defaults(func=checkpoints)

    p = sub.add_parser("embeddings")
    p.add_argument("action", choices=["rebuild"])
    p.add_argument("--scope")
    p.set_defaults(func=embeddings)

    p = sub.add_parser("forget")
    p.add_argument("id", type=int)
    p.set_defaults(func=forget)

    p = sub.add_parser("mcp")
    p.set_defaults(func=mcp)

    p = sub.add_parser("hook")
    p.add_argument("event", choices=["user-prompt-submit", "post-tool-use", "stop"])
    p.set_defaults(func=hook)

    args = parser.parse_args(argv)
    exit_code = 0
    try:
        args.func(args)
    except SystemExit as exc:
        exit_code = int(exc.code) if isinstance(exc.code, int) else 1
        raise
    except Exception:
        exit_code = 1
        raise
    finally:
        if getattr(args, "command", None) not in {None, "hook", "mcp"}:
            duration_ms = int((time.monotonic() - started) * 1000)
            scope = getattr(args, "scope", None)
            MemoryStore().record_invocation(args.command, duration_ms, exit_code, scope=scope)


if __name__ == "__main__":
    main()
