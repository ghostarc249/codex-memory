from __future__ import annotations

import json
import sys
from typing import Any

from .store import MemoryStore


TOOLS = [
    {
        "name": "memory_search",
        "description": "Search durable local Codex memory records using SQLite FTS5.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "scope": {"type": "string"},
                "limit": {"type": "integer", "default": 10}
            },
            "required": ["query"]
        }
    },
    {
        "name": "memory_add",
        "description": "Add a durable local Codex memory record.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string"},
                "type": {"type": "string", "enum": ["decision", "constraint", "pattern", "task_context", "pitfall", "note"]},
                "title": {"type": "string"},
                "content": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "source": {"type": "string"}
            },
            "required": ["scope", "type", "title", "content"]
        }
    },
    {
        "name": "memory_list",
        "description": "List recent durable local Codex memory records.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {"type": "string"},
                "limit": {"type": "integer", "default": 20}
            }
        }
    },
    {
        "name": "memory_forget",
        "description": "Delete a durable local Codex memory record by id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"}
            },
            "required": ["id"]
        }
    },
    {
        "name": "memory_health",
        "description": "Check durable local Codex memory database health.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    }
]


def make_text_result(value: Any) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(value, indent=2, ensure_ascii=False)
            }
        ]
    }


def handle_tool_call(store: MemoryStore, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "memory_search":
        return make_text_result(store.search(arguments["query"], arguments.get("scope"), int(arguments.get("limit", 10))))
    if name == "memory_add":
        return make_text_result(store.add(
            scope=arguments["scope"],
            type_=arguments["type"],
            title=arguments["title"],
            content=arguments["content"],
            tags=arguments.get("tags") or [],
            source=arguments.get("source")
        ))
    if name == "memory_list":
        return make_text_result(store.list(arguments.get("scope"), int(arguments.get("limit", 20))))
    if name == "memory_forget":
        return make_text_result({"forgotten": store.forget(int(arguments["id"]))})
    if name == "memory_health":
        return make_text_result(store.health())
    raise ValueError(f"Unknown tool: {name}")


def send(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def run_mcp_server(store: MemoryStore) -> None:
    # Minimal JSON-RPC over stdio MCP-style server.
    # This intentionally stays small and dependency-free.
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
            method = msg.get("method")
            msg_id = msg.get("id")

            if method == "initialize":
                send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "codex-memory", "version": "0.1.0"}
                    }
                })
            elif method == "notifications/initialized":
                continue
            elif method == "tools/list":
                send({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}})
            elif method == "tools/call":
                params = msg.get("params") or {}
                result = handle_tool_call(store, params["name"], params.get("arguments") or {})
                send({"jsonrpc": "2.0", "id": msg_id, "result": result})
            else:
                send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Method not found: {method}"}})
        except Exception as exc:
            send({"jsonrpc": "2.0", "id": msg.get("id") if isinstance(msg, dict) else None, "error": {"code": -32000, "message": str(exc)}})
