from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from codex_memory import hooks
from codex_memory.cli import format_health_report, install_project_hooks, merge_codex_config, validate_project_hooks
from codex_memory.store import MemoryStore


def context_chars(output: dict[str, object]) -> int:
    hook_output = output.get("hookSpecificOutput")
    if not isinstance(hook_output, dict):
        return 0
    return len(str(hook_output.get("additionalContext") or ""))


def legacy_eager_context_chars(store: MemoryStore, scope: str, prompt: str) -> int:
    memories = store.hybrid_search(prompt, scope=scope, limit=5) if prompt else []
    if not memories:
        memories = store.list(scope=scope, limit=3)
    files = store.list_files(scope=scope, limit=10)
    return len(legacy_format_memory_context(scope, memories, files))


def legacy_format_memory_context(scope: str, memories: list[dict[str, object]], files: list[dict[str, object]]) -> str:
    lines = [
        f"Codex Memory auto-search ran before this turn for scope `{scope}`.",
        "Use these local memories if relevant; they may be stale, so verify drift-prone facts.",
        "",
    ]
    if files:
        lines.append("Recent files:")
        for file in files:
            lines.append(f"- {file['file_path']} (last tool={file.get('tool_name') or 'unknown'}, seen={file.get('seen_count', 1)})")
        lines.append("")
    if memories:
        lines.append("Relevant memories:")
    for memory in memories:
        tags = ", ".join(memory.get("tags") or [])
        tag_text = f" tags={tags}" if tags else ""
        lines.append(f"- [{memory['type']}] {memory['title']} (id={memory['id']}{tag_text})")
        lines.append(f"  {hooks.single_line(hooks.sanitize_terminal(str(memory['content'])))}")
    return "\n".join(lines)


def run_user_prompt_submit(db_path: Path, repo: Path, prompt: str) -> dict[str, object]:
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    env["CODEX_MEMORY_DB"] = str(db_path)

    result = subprocess.run(
        [sys.executable, "-m", "codex_memory.cli", "hook", "user-prompt-submit"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        input=json.dumps({"hook_event_name": "UserPromptSubmit", "cwd": str(repo), "prompt": prompt}),
        text=True,
        check=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


class MemoryStoreTests(unittest.TestCase):
    def test_search_list_files_and_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(Path(tmp) / "memory.db")
            decision = store.add(
                scope="repo",
                type_="decision",
                title="Use Codex hooks",
                content="Codex lifecycle hooks provide automatic memory recall.",
                tags=["hooks"],
            )
            task_context = store.add(
                scope="repo",
                type_="task_context",
                title="Finished hook parity",
                content="Added file recall and checkpoint commands.",
                tags=["auto-memory"],
            )
            store.record_file("repo", "src/codex_memory/hooks.py", "apply_patch", "session:s turn:t")

            self.assertEqual(decision["id"], 1)
            self.assertEqual(task_context["id"], 2)
            self.assertEqual(store.search("Codex hooks", scope="repo", limit=1)[0]["id"], 1)
            self.assertEqual(store.semantic_search("lifecycle automatic recall", scope="repo", limit=1)[0]["id"], 1)
            self.assertEqual(store.hybrid_search("checkpoint commands", scope="repo", limit=1)[0]["id"], 2)
            self.assertEqual(store.list_files(scope="repo", limit=1)[0]["file_path"], "src/codex_memory/hooks.py")
            self.assertEqual(store.checkpoints(scope="repo", limit=1)[0]["type"], "task_context")
            self.assertEqual(store.schema_check(), [])

    def test_embeddings_can_be_rebuilt_for_existing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(Path(tmp) / "memory.db")
            store.add(
                scope="repo",
                type_="pattern",
                title="Use handlers for business logic",
                content="Keep HTTP endpoints thin and move behavior into handlers.",
                tags=["architecture"],
            )
            with store.connect() as conn:
                conn.execute("DELETE FROM memory_embeddings")

            self.assertTrue(any("memory_embeddings" in problem for problem in store.schema_check()))
            result = store.rebuild_embeddings(scope="repo")
            self.assertEqual(result["rebuilt"], 1)
            self.assertEqual(store.schema_check(), [])

    def test_install_project_hooks_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            hooks_path = Path(tmp) / ".codex" / "hooks.json"
            self.assertTrue(install_project_hooks(hooks_path))
            self.assertFalse(install_project_hooks(hooks_path))
            data = json.loads(hooks_path.read_text(encoding="utf-8"))
            self.assertIn("UserPromptSubmit", data["hooks"])
            self.assertIn("PostToolUse", data["hooks"])
            self.assertIn("Stop", data["hooks"])

    def test_merge_codex_config_updates_existing_features_without_duplicate_table(self) -> None:
        existing = """
model = "gpt-5.4"

[features]
web_search = true
codex_hooks = false

[mcp_servers.docs]
command = "docs-server"
""".lstrip()

        merged = merge_codex_config(existing)

        self.assertEqual(merged.count("[features]"), 1)
        self.assertIn("web_search = true", merged)
        self.assertIn("codex_hooks = true", merged)
        self.assertIn("memories = true", merged)
        self.assertIn("[mcp_servers.docs]", merged)
        self.assertIn("[mcp_servers.codex_memory]", merged)

    def test_merge_codex_config_removes_old_managed_block_before_merging(self) -> None:
        existing = """
[features]
web_search = true

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
""".lstrip()

        merged = merge_codex_config(existing)

        self.assertEqual(merged.count("[features]"), 1)
        self.assertEqual(merged.count("[mcp_servers.codex_memory]"), 1)
        self.assertIn("web_search = true", merged)
        self.assertIn("memories = true", merged)
        self.assertIn("codex_hooks = true", merged)

    def test_validate_project_hooks_requires_all_auto_memory_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            hooks_path = Path(tmp) / ".codex" / "hooks.json"
            install_project_hooks(hooks_path)
            data = json.loads(hooks_path.read_text(encoding="utf-8"))
            ok, problems = validate_project_hooks(data)
            self.assertTrue(ok)
            self.assertEqual(problems, [])

    def test_checkpoint_content_is_structured_for_continuation(self) -> None:
        content = hooks.make_checkpoint_content(
            "Implemented retry behavior and verified unit tests.",
            [{"file_path": "src/payments/retry_policy.py"}],
        )

        self.assertIn("Checkpoint summary:", content)
        self.assertIn("- Outcome:", content)
        self.assertIn("src/payments/retry_policy.py", content)
        self.assertIn("- Next step:", content)

    def test_continuation_prompt_prioritizes_latest_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(Path(tmp) / "memory.db")
            store.add(
                scope="repo",
                type_="decision",
                title="Older decision",
                content="Older context",
                tags=[],
            )
            checkpoint = store.add(
                scope="repo",
                type_="task_context",
                title="Latest checkpoint",
                content="Checkpoint summary:\n- Outcome: Continue payment retry work.",
                tags=["checkpoint"],
            )

            memories = hooks.continuation_memories(store, "repo", "continue after compaction")

            self.assertEqual(memories[0]["id"], checkpoint["id"])

    def test_relevant_memories_filters_weak_semantic_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(Path(tmp) / "memory.db")
            store.add(
                scope="repo",
                type_="decision",
                title="Use Postgres",
                content="Use PostgreSQL with Dapper and pgvector for persistence.",
                tags=["database"],
            )

            self.assertEqual(hooks.relevant_memories(store, "repo", "what time is it"), [])
            self.assertEqual(len(hooks.relevant_memories(store, "repo", "postgres dapper pgvector")), 1)

    def test_format_memory_context_has_total_budget(self) -> None:
        context = hooks.format_memory_context(
            "repo",
            [
                {
                    "id": index,
                    "type": "task_context",
                    "title": f"Large memory {index}",
                    "content": "word " * 1000,
                    "tags": ["checkpoint"],
                }
                for index in range(20)
            ],
            [{"file_path": f"src/file_{index}.py", "tool_name": "read", "seen_count": index} for index in range(20)],
        )

        self.assertLessEqual(len(context), hooks.MAX_TOTAL_CONTEXT_CHARS)
        self.assertIn("Codex Memory auto-search", context)

    def test_budget_savings_for_unrelated_prompt_are_material(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "memory.db"
            repo = Path(tmp) / "repo"
            repo.mkdir()
            store = MemoryStore(db_path)
            for index in range(5):
                store.add(
                    scope="repo",
                    type_="decision",
                    title=f"Architecture decision {index}",
                    content="PostgreSQL Dapper pgvector " + ("database persistence policy " * 120),
                    tags=["database", "architecture"],
                )
                store.record_file("repo", f"src/module_{index}.py", "read", f"session:s turn:{index}")

            old_chars = legacy_eager_context_chars(store, "repo", "what time is it")
            output = run_user_prompt_submit(db_path, repo, "what time is it")

            self.assertGreater(old_chars, 1000)
            self.assertEqual(context_chars(output), 0)

    def test_budget_for_relevant_prompt_keeps_signal_while_capping_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "memory.db"
            repo = Path(tmp) / "repo"
            repo.mkdir()
            store = MemoryStore(db_path)
            for index in range(8):
                store.add(
                    scope="repo",
                    type_="decision",
                    title=f"Postgres decision {index}",
                    content="PostgreSQL Dapper pgvector " + ("relevant database migration detail " * 180),
                    tags=["database"],
                )
                store.record_file("repo", f"src/db_{index}.py", "read", f"session:s turn:{index}")

            old_chars = legacy_eager_context_chars(store, "repo", "postgres dapper pgvector")
            output = run_user_prompt_submit(db_path, repo, "postgres dapper pgvector")
            new_chars = context_chars(output)
            context = output["hookSpecificOutput"]["additionalContext"]

            self.assertGreater(old_chars, hooks.MAX_TOTAL_CONTEXT_CHARS)
            self.assertGreater(new_chars, 0)
            self.assertLessEqual(new_chars, hooks.MAX_TOTAL_CONTEXT_CHARS)
            self.assertLess(new_chars, old_chars)
            self.assertIn("Postgres decision", context)
            self.assertIn("src/db_", context)

    def test_retry_retries_locked_operations(self) -> None:
        attempts = {"count": 0}

        def flaky() -> str:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise __import__("sqlite3").OperationalError("database is locked")
            return "ok"

        self.assertEqual(MemoryStore._retry(flaky), "ok")
        self.assertEqual(attempts["count"], 2)

    def test_health_report_uses_terminal_table(self) -> None:
        report = format_health_report(
            {
                "memory_count": 0,
                "schema_problems": [],
                "dimensions": [
                    {
                        "name": "schema_integrity",
                        "zone": "GREEN",
                        "score": 10,
                        "detail": "All expected tables/columns OK",
                    },
                    {
                        "name": "corpus_size",
                        "zone": "YELLOW",
                        "score": 0,
                        "detail": "0 memories",
                    },
                ],
            }
        )

        self.assertIn("Dim  Name", report)
        self.assertIn("Schema Integrity", report)
        self.assertIn("🟡 AMBER", report)
        self.assertIn("Overall", report)
        self.assertIn("Cold start - will improve as memories are saved", report)


class HookCliTests(unittest.TestCase):
    def test_post_tool_use_tracks_patch_files_and_prompt_injects_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "memory.db"
            repo = Path(tmp) / "repo"
            repo.mkdir()
            env = os.environ.copy()
            env["PYTHONPATH"] = "src"
            env["CODEX_MEMORY_DB"] = str(db_path)

            payload = {
                "hook_event_name": "PostToolUse",
                "cwd": str(repo),
                "tool_name": "apply_patch",
                "tool_input": {
                    "command": "*** Begin Patch\n*** Update File: src/app.py\n*** End Patch\n"
                },
                "session_id": "s1",
                "turn_id": "t1",
            }
            subprocess.run(
                [sys.executable, "-m", "codex_memory.cli", "hook", "post-tool-use"],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                input=json.dumps(payload),
                text=True,
                check=True,
                capture_output=True,
            )

            prompt_payload = {
                "hook_event_name": "UserPromptSubmit",
                "cwd": str(repo),
                "prompt": "continue",
                "session_id": "s1",
                "turn_id": "t2",
            }
            result = subprocess.run(
                [sys.executable, "-m", "codex_memory.cli", "hook", "user-prompt-submit"],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                input=json.dumps(prompt_payload),
                text=True,
                check=True,
                capture_output=True,
            )
            output = json.loads(result.stdout)
            context = output["hookSpecificOutput"]["additionalContext"]
            self.assertIn("src/app.py", context)

    def test_user_prompt_submit_does_not_inject_unrelated_recent_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "memory.db"
            repo = Path(tmp) / "repo"
            repo.mkdir()
            store = MemoryStore(db_path)
            store.add(
                scope="repo",
                type_="decision",
                title="Use Postgres",
                content="Use PostgreSQL with Dapper and pgvector for persistence.",
                tags=["database"],
            )
            self.assertEqual(run_user_prompt_submit(db_path, repo, "what time is it"), {"continue": True})

    def test_stop_hook_writes_structured_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "memory.db"
            repo = Path(tmp) / "repo"
            repo.mkdir()
            env = os.environ.copy()
            env["PYTHONPATH"] = "src"
            env["CODEX_MEMORY_DB"] = str(db_path)

            payload = {
                "hook_event_name": "Stop",
                "cwd": str(repo),
                "last_assistant_message": "Implemented retry behavior and verified unit tests.",
                "session_id": "s1",
                "turn_id": "t1",
            }
            subprocess.run(
                [sys.executable, "-m", "codex_memory.cli", "hook", "stop"],
                cwd=Path(__file__).resolve().parents[1],
                env=env,
                input=json.dumps(payload),
                text=True,
                check=True,
                capture_output=True,
            )

            store = MemoryStore(db_path)
            checkpoint = store.checkpoints(scope="repo", limit=1)[0]
            self.assertIn("Checkpoint summary:", checkpoint["content"])
            self.assertIn("checkpoint", checkpoint["tags"])


if __name__ == "__main__":
    unittest.main()
