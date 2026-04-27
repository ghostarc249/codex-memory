from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from codex_memory.cli import install_project_hooks, merge_codex_config
from codex_memory.store import MemoryStore


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


if __name__ == "__main__":
    unittest.main()
