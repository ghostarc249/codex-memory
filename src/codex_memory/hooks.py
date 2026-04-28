from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from .store import MemoryStore


MAX_CONTEXT_MEMORIES = 4
MAX_CONTEXT_FILES = 5
MAX_CONTEXT_MEMORY_CHARS = 700
MAX_TOTAL_CONTEXT_CHARS = 5000
MAX_STORED_CONTENT_CHARS = 4000
MIN_SEMANTIC_CONTEXT_SCORE = 0.18
CONTINUATION_MARKERS = (
    "continue",
    "pick up",
    "resume",
    "carry on",
    "where were we",
    "context compression",
    "compaction",
)

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
    try:
        payload = read_hook_input()
        prompt = str(payload.get("prompt") or "").strip()
        cwd = str(payload.get("cwd") or os.getcwd())
        scope = resolve_scope(cwd)

        store = MemoryStore()
        continuation_prompt = is_continuation_prompt(prompt)
        if continuation_prompt:
            memories = continuation_memories(store, scope, prompt)
        else:
            memories = relevant_memories(store, scope, prompt)
        files = store.list_files(scope=scope, limit=MAX_CONTEXT_FILES) if continuation_prompt or memories else []

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
    except Exception as exc:
        write_json({"continue": True, "suppressOutput": True, "metadata": {"codex_memory_warning": str(exc)}})


def post_tool_use() -> None:
    try:
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
    except Exception as exc:
        write_json({"continue": True, "suppressOutput": True, "metadata": {"codex_memory_warning": str(exc)}})


def stop() -> None:
    try:
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
        source = make_source(session_id, turn_id)

        store = MemoryStore()
        if source and any(memory.get("source") == source for memory in store.list(scope=scope, limit=25)):
            write_json({"continue": True})
            return

        files = store.list_files(scope=scope, limit=MAX_CONTEXT_FILES)
        title = make_title(assistant_message)
        content = make_checkpoint_content(assistant_message, files)

        store.add(
            scope=scope,
            type_="task_context",
            title=title,
            content=content,
            tags=["codex-hook", "auto-memory", "checkpoint"],
            source=source,
        )

        write_json({"continue": True})
    except Exception as exc:
        write_json({"continue": True, "suppressOutput": True, "metadata": {"codex_memory_warning": str(exc)}})


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
        content = single_line(sanitize_terminal(str(memory['content'])))
        lines.append(f"  {truncate_text(content, MAX_CONTEXT_MEMORY_CHARS)}")
    return truncate_lines(lines, MAX_TOTAL_CONTEXT_CHARS)


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


def is_continuation_prompt(prompt: str) -> bool:
    lowered = prompt.lower()
    return any(marker in lowered for marker in CONTINUATION_MARKERS)


def relevant_memories(store: MemoryStore, scope: str, prompt: str) -> list[dict[str, Any]]:
    prompt = prompt.strip()
    if not prompt:
        return []
    memories = store.hybrid_search(prompt, scope=scope, limit=MAX_CONTEXT_MEMORIES)
    return [memory for memory in memories if is_relevant_memory(memory)][:MAX_CONTEXT_MEMORIES]


def is_relevant_memory(memory: dict[str, Any]) -> bool:
    if "fts_rank" in memory:
        return True
    return float(memory.get("semantic_score") or 0.0) >= MIN_SEMANTIC_CONTEXT_SCORE


def continuation_memories(store: MemoryStore, scope: str, prompt: str) -> list[dict[str, Any]]:
    memories: list[dict[str, Any]] = []
    memories.extend(store.checkpoints(scope=scope, limit=MAX_CONTEXT_MEMORIES))
    if prompt:
        memories.extend(store.hybrid_search(prompt, scope=scope, limit=MAX_CONTEXT_MEMORIES))
    seen: set[int] = set()
    result: list[dict[str, Any]] = []
    for memory in memories:
        memory_id = int(memory["id"])
        if memory_id in seen:
            continue
        seen.add(memory_id)
        result.append(memory)
        if len(result) >= MAX_CONTEXT_MEMORIES:
            break
    return result


def make_checkpoint_content(assistant_message: str, files: list[dict[str, Any]]) -> str:
    cleaned = sanitize_for_storage(assistant_message[:MAX_STORED_CONTENT_CHARS])
    lines = [
        "Checkpoint summary:",
        f"- Outcome: {single_line(cleaned)}",
    ]
    if files:
        lines.append("- Recent files:")
        for file in files[:MAX_CONTEXT_FILES]:
            lines.append(f"  - {file['file_path']}")
    lines.extend([
        "- Validation: Review the latest assistant message for commands/tests that were run.",
        "- Next step: Continue from this checkpoint and verify drift-prone details in the repository.",
    ])
    return "\n".join(lines)


def make_title(message: str) -> str:
    first_line = next((line.strip() for line in message.splitlines() if line.strip()), "Codex turn summary")
    first_line = re.sub(r"^[#*\-\s]+", "", first_line)
    return single_line(first_line)[:90] or "Codex turn summary"


def single_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def truncate_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max(0, max_chars - 3)].rstrip() + "..."


def truncate_lines(lines: list[str], max_chars: int) -> str:
    output: list[str] = []
    used = 0
    marker = "[Codex Memory context truncated]"
    for line in lines:
        line_cost = len(line) + (1 if output else 0)
        if used + line_cost > max_chars:
            marker_cost = len(marker) + (1 if output else 0)
            if used + marker_cost <= max_chars:
                output.append(marker)
            elif output:
                output[-1] = truncate_text(output[-1], max(0, len(output[-1]) - (used + marker_cost - max_chars)))
            break
        output.append(line)
        used += line_cost
    return "\n".join(output)


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
