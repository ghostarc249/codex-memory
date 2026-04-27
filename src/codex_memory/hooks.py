from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from .store import MemoryStore


MAX_CONTEXT_MEMORIES = 5
MAX_CONTEXT_FILES = 10
MAX_STORED_CONTENT_CHARS = 4000

SECRET_PATTERNS = [
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password|connection[_-]?string)\b\s*[:=]\s*['\"]?[^'\"\s]+"),
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"),
    re.compile(r"\b[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b"),
]
CONTROL_PATTERN = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b[@-Z\\-_]"
    r"|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"
    r"|[\x80-\x9f]"
)
PATCH_FILE_PATTERN = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)
PATH_KEYS = {"path", "file_path", "filename", "file", "target_file"}


def read_hook_input() -> dict[str, Any]:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def write_json(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def resolve_scope(cwd: str | None) -> str:
    env_scope = os.environ.get("CODEX_MEMORY_SCOPE")
    if env_scope:
        return env_scope

    root = resolve_git_root(cwd)
    return root.name if root else Path(cwd or os.getcwd()).resolve().name


def resolve_git_root(cwd: str | None) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd or None,
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return Path(value).resolve() if value else None


def user_prompt_submit() -> None:
    payload = read_hook_input()
    prompt = str(payload.get("prompt") or "").strip()
    cwd = str(payload.get("cwd") or os.getcwd())
    scope = resolve_scope(cwd)

    store = MemoryStore()
    memories = store.search(prompt, scope=scope, limit=MAX_CONTEXT_MEMORIES) if prompt else []
    if not memories:
        memories = store.list(scope=scope, limit=min(MAX_CONTEXT_MEMORIES, 3))
    files = store.list_files(scope=scope, limit=MAX_CONTEXT_FILES)

    if not memories and not files:
        write_json({"continue": True})
        return

    write_json(
        {
            "continue": True,
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": format_memory_context(scope, memories, files),
            },
        }
    )


def post_tool_use() -> None:
    payload = read_hook_input()
    cwd = str(payload.get("cwd") or os.getcwd())
    scope = resolve_scope(cwd)
    tool_name = str(payload.get("tool_name") or "")
    source = make_source(str(payload.get("session_id") or ""), str(payload.get("turn_id") or ""))
    paths = extract_paths(payload)

    if paths:
        store = MemoryStore()
        for path in paths[:25]:
            store.record_file(scope=scope, file_path=normalize_path(path, cwd), tool_name=tool_name, source=source)

    write_json({"continue": True})


def stop() -> None:
    payload = read_hook_input()
    if payload.get("stop_hook_active"):
        write_json({"continue": True})
        return

    assistant_message = str(payload.get("last_assistant_message") or "").strip()
    if should_skip_memory_write(assistant_message):
        write_json({"continue": True})
        return

    cwd = str(payload.get("cwd") or os.getcwd())
    scope = resolve_scope(cwd)
    session_id = str(payload.get("session_id") or "")
    turn_id = str(payload.get("turn_id") or "")
    title = make_title(assistant_message)
    content = sanitize_for_storage(assistant_message[:MAX_STORED_CONTENT_CHARS])
    source = make_source(session_id, turn_id)

    store = MemoryStore()
    if source and any(memory.get("source") == source for memory in store.list(scope=scope, limit=25)):
        write_json({"continue": True})
        return

    store.add(
        scope=scope,
        type_="task_context",
        title=title,
        content=content,
        tags=["codex-hook", "auto-memory"],
        source=source,
    )

    write_json({"continue": True})


def format_memory_context(scope: str, memories: list[dict[str, Any]], files: list[dict[str, Any]]) -> str:
    lines = [
        f"Codex Memory auto-search ran before this turn for scope `{scope}`.",
        "Use these local memories if relevant; they may be stale, so verify drift-prone facts.",
        "",
    ]
    if files:
        lines.append("Recent files:")
        for file in files:
            seen = file.get("seen_count", 1)
            lines.append(f"- {file['file_path']} (last tool={file.get('tool_name') or 'unknown'}, seen={seen})")
        lines.append("")
    if memories:
        lines.append("Relevant memories:")
    for memory in memories:
        tags = ", ".join(memory.get("tags") or [])
        tag_text = f" tags={tags}" if tags else ""
        lines.append(f"- [{memory['type']}] {memory['title']} (id={memory['id']}{tag_text})")
        lines.append(f"  {single_line(sanitize_terminal(str(memory['content'])))}")
    return "\n".join(lines)


def should_skip_memory_write(message: str) -> bool:
    if len(message) < 40:
        return True
    lowered = message.lower()
    noisy_markers = [
        "i wasn't able to",
        "i’m sorry",
        "i'm sorry",
        "what would you like",
    ]
    return any(marker in lowered for marker in noisy_markers)


def make_title(message: str) -> str:
    first_line = next((line.strip() for line in message.splitlines() if line.strip()), "Codex turn summary")
    first_line = re.sub(r"^[#*\-\s]+", "", first_line)
    return single_line(first_line)[:90] or "Codex turn summary"


def single_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def redact_sensitive(value: str) -> str:
    redacted = value
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def sanitize_terminal(value: str) -> str:
    return CONTROL_PATTERN.sub("", value)


def sanitize_for_storage(value: str) -> str:
    return redact_sensitive(sanitize_terminal(value))


def extract_paths(payload: dict[str, Any]) -> list[str]:
    tool_input = payload.get("tool_input")
    candidates: list[str] = []
    collect_paths(tool_input, candidates)
    if isinstance(tool_input, dict):
        command = tool_input.get("command")
        if isinstance(command, str):
            candidates.extend(extract_patch_paths(command))
            candidates.extend(extract_command_paths(command))
    return dedupe_paths(candidates)


def collect_paths(value: Any, candidates: list[str]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in PATH_KEYS and isinstance(nested, str):
                candidates.append(nested)
            collect_paths(nested, candidates)
    elif isinstance(value, list):
        for nested in value:
            collect_paths(nested, candidates)


def extract_patch_paths(command: str) -> list[str]:
    return [match.strip() for match in PATCH_FILE_PATTERN.findall(command)]


def extract_command_paths(command: str) -> list[str]:
    paths: list[str] = []
    for token in re.split(r"\s+", command):
        token = token.strip("'\"")
        if "/" not in token and "." not in token:
            continue
        if token.startswith("-") or "://" in token:
            continue
        if re.search(r"[A-Za-z0-9_\-./]+\.[A-Za-z0-9_\-]+$", token):
            paths.append(token.rstrip(",:;"))
    return paths


def dedupe_paths(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for path in paths:
        cleaned = path.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def normalize_path(path: str, cwd: str) -> str:
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        try:
            return str(candidate.resolve().relative_to(Path(cwd).resolve()))
        except ValueError:
            return str(candidate)
    return str(candidate)


def make_source(session_id: str, turn_id: str) -> str | None:
    pieces = []
    if session_id:
        pieces.append(f"session:{session_id}")
    if turn_id:
        pieces.append(f"turn:{turn_id}")
    return " ".join(pieces) or None
