import json
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
import plistlib
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "fleet_chat_archive.py"
sys.path.insert(0, str(REPO))

import fleet_chat_archive as fleet  # noqa: E402


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


class FleetChatArchiveTests(unittest.TestCase):
    def test_duplicate_logical_sessions_prune_superseded_staging_objects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cases = {
                "claude": [
                    {
                        "archive_schema_version": 2,
                        "source": "claude-code",
                        "session_id": "same-session",
                        "messages": [{"role": "user", "content": "first"}],
                        "project_path": "/project-one",
                        "project_name": "one",
                        "source_file": "/source/one.jsonl",
                    },
                    {
                        "archive_schema_version": 2,
                        "source": "claude-code",
                        "session_id": "same-session",
                        "messages": [{"role": "user", "content": "second"}],
                        "project_path": "/project-two",
                        "project_name": "two",
                        "source_file": "/source/two.jsonl",
                    },
                ],
                "codex": [
                    {
                        "archive_schema_version": 2,
                        "source": "codex",
                        "session_id": "same-session",
                        "messages": [{"role": "user", "content": "first"}],
                        "cwd": "/project",
                        "session_file": "/source/one.jsonl",
                        "timestamp": None,
                        "installation": "/codex",
                        "_archive_source_sha256": "1" * 64,
                    },
                    {
                        "archive_schema_version": 2,
                        "source": "codex",
                        "session_id": "same-session",
                        "messages": [{"role": "user", "content": "second"}],
                        "cwd": "/project",
                        "session_file": "/source/two.jsonl",
                        "timestamp": None,
                        "installation": "/codex",
                        "_archive_source_sha256": "2" * 64,
                    },
                ],
            }

            for harness, conversations in cases.items():
                with self.subTest(harness=harness):
                    archive_root = root / harness
                    result = fleet.archive_conversations(
                        archive_root,
                        "test-mac",
                        harness,
                        conversations,
                    )
                    host_root = archive_root / "hosts" / "test-mac"
                    files = fleet.validated_shard_files(
                        host_root,
                        "test-mac",
                        require_healthy_receipt=False,
                    )
                    index = json.loads(
                        (host_root / harness / "index.json").read_text()
                    )
                    objects = list((host_root / harness / "objects").glob("*.json"))
                    self.assertEqual(result["conversations"], 2)
                    self.assertEqual(result["index_conversations"], 1)
                    self.assertEqual(result["new_objects"], 1)
                    self.assertEqual(len(index["conversations"]), 1)
                    self.assertEqual(len(objects), 1)
                    self.assertIn(objects[0].resolve(), files)

    def test_duplicate_pruning_preserves_preexisting_unindexed_objects(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_root = Path(tmp) / "archive"
            objects_root = (
                archive_root / "hosts" / "test-mac" / "claude" / "objects"
            )
            objects_root.mkdir(parents=True)
            preexisting = objects_root / ("f" * 64 + ".json")
            preexisting.write_text("preexisting\n", encoding="utf-8")
            conversations = [
                {
                    "archive_schema_version": 2,
                    "source": "claude-code",
                    "session_id": "same-session",
                    "messages": [{"role": "user", "content": "first"}],
                    "project_path": "/project-one",
                    "project_name": "one",
                    "source_file": "/source/one.jsonl",
                },
                {
                    "archive_schema_version": 2,
                    "source": "claude-code",
                    "session_id": "same-session",
                    "messages": [{"role": "user", "content": "second"}],
                    "project_path": "/project-two",
                    "project_name": "two",
                    "source_file": "/source/two.jsonl",
                },
            ]

            fleet.archive_conversations(
                archive_root,
                "test-mac",
                "claude",
                conversations,
            )

            self.assertEqual(preexisting.read_text(encoding="utf-8"), "preexisting\n")
            self.assertEqual(len(list(objects_root.glob("*.json"))), 2)

    def test_claude_collection_is_content_addressed_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude_root = root / "claude"
            archive_root = root / "archive"
            write_jsonl(
                claude_root / "projects" / "sample-project" / "session-1.jsonl",
                [
                    {
                        "type": "user",
                        "timestamp": "2026-08-27T00:00:00Z",
                        "cwd": "/private/project",
                        "message": {"content": "Build the archive."},
                    },
                    {
                        "type": "assistant",
                        "timestamp": "2026-08-27T00:00:01Z",
                        "message": {
                            "model": "test-model",
                            "content": [{"type": "text", "text": "Done."}],
                        },
                    },
                ],
            )

            command = [
                sys.executable,
                str(CLI),
                "collect",
                "--host-id",
                "test-mac",
                "--archive-root",
                str(archive_root),
                "--claude-root",
                str(claude_root),
            ]
            first = subprocess.run(command, cwd=REPO, text=True, capture_output=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            first_receipt = json.loads(first.stdout)
            self.assertEqual(first_receipt["harnesses"]["claude"]["conversations"], 1)
            self.assertEqual(first_receipt["harnesses"]["claude"]["new_objects"], 1)

            second = subprocess.run(command, cwd=REPO, text=True, capture_output=True)
            self.assertEqual(second.returncode, 0, second.stderr)
            second_receipt = json.loads(second.stdout)
            self.assertEqual(second_receipt["harnesses"]["claude"]["new_objects"], 0)

            harness_root = archive_root / "hosts" / "test-mac" / "claude"
            objects = list((harness_root / "objects").glob("*.json"))
            self.assertEqual(len(objects), 1)
            conversation = json.loads(objects[0].read_text())
            self.assertEqual(
                [message["role"] for message in conversation["messages"]],
                ["user", "assistant"],
            )
            index = json.loads((harness_root / "index.json").read_text())
            self.assertEqual(index["conversations"][0]["session_id"], "session-1")

    def test_codex_collection_reads_modern_response_items_and_tool_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex_root = root / "codex"
            archive_root = root / "archive"
            write_jsonl(
                codex_root / "sessions" / "2026" / "08" / "27" / "rollout-test.jsonl",
                [
                    {
                        "type": "session_meta",
                        "timestamp": "2026-08-27T00:00:00Z",
                        "payload": {"id": "codex-session-1", "cwd": "/private/project"},
                    },
                    {
                        "type": "response_item",
                        "timestamp": "2026-08-27T00:00:01Z",
                        "payload": {
                            "type": "message",
                            "id": "user-message-1",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "Run the audit."}],
                        },
                    },
                    {
                        "type": "response_item",
                        "timestamp": "2026-08-27T00:00:02Z",
                        "payload": {
                            "type": "custom_tool_call",
                            "id": "tool-1",
                            "call_id": "call-1",
                            "name": "shell",
                            "input": "{}",
                        },
                    },
                    {
                        "type": "response_item",
                        "timestamp": "2026-08-27T00:00:03Z",
                        "payload": {
                            "type": "custom_tool_call_output",
                            "id": "tool-output-1",
                            "call_id": "call-1",
                            "output": "ok",
                        },
                    },
                    {
                        "type": "response_item",
                        "timestamp": "2026-08-27T00:00:04Z",
                        "payload": {
                            "type": "message",
                            "id": "assistant-message-1",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "Audit passed."}],
                        },
                    },
                ],
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "collect",
                    "--host-id",
                    "test-mac",
                    "--archive-root",
                    str(archive_root),
                    "--codex-root",
                    str(codex_root),
                ],
                cwd=REPO,
                text=True,
                capture_output=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            receipt = json.loads(completed.stdout)
            self.assertEqual(receipt["harnesses"]["codex"]["conversations"], 1)

            object_path = next(
                (archive_root / "hosts" / "test-mac" / "codex" / "objects").glob("*.json")
            )
            conversation = json.loads(object_path.read_text())
            self.assertEqual(
                [(message["role"], message["content"]) for message in conversation["messages"]],
                [("user", "Run the audit."), ("assistant", "Audit passed.")],
            )
            self.assertEqual(
                [event["type"] for event in conversation["tool_results"]],
                ["custom_tool_call", "custom_tool_call_output"],
            )

    def test_openclaw_collection_reads_v3_messages_and_tool_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            openclaw_root = root / "openclaw-sessions"
            archive_root = root / "archive"
            write_jsonl(
                openclaw_root / "openclaw-session-1.jsonl",
                [
                    {
                        "type": "session",
                        "id": "openclaw-session-1",
                        "timestamp": "2026-08-27T00:00:00Z",
                        "cwd": "/private/workspace",
                        "version": 3,
                    },
                    {
                        "type": "message",
                        "id": "message-1",
                        "timestamp": "2026-08-27T00:00:01Z",
                        "message": {"role": "user", "content": "Check production."},
                    },
                    {
                        "type": "message",
                        "id": "message-2",
                        "timestamp": "2026-08-27T00:00:02Z",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "toolCall", "name": "health", "arguments": {}},
                                {"type": "text", "text": "Production is healthy."},
                            ],
                        },
                    },
                    {
                        "type": "message",
                        "id": "message-3",
                        "timestamp": "2026-08-27T00:00:03Z",
                        "message": {
                            "role": "toolResult",
                            "toolName": "health",
                            "content": [{"type": "toolResult", "text": "ok"}],
                        },
                    },
                ],
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "collect",
                    "--host-id",
                    "mini",
                    "--archive-root",
                    str(archive_root),
                    "--openclaw-root",
                    str(openclaw_root),
                ],
                cwd=REPO,
                text=True,
                capture_output=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            receipt = json.loads(completed.stdout)
            self.assertEqual(receipt["harnesses"]["openclaw"]["conversations"], 1)
            object_path = next(
                (archive_root / "hosts" / "mini" / "openclaw" / "objects").glob("*.json")
            )
            conversation = json.loads(object_path.read_text())
            self.assertEqual(
                [(message["role"], message["content"]) for message in conversation["messages"]],
                [
                    ("user", "Check production."),
                    ("assistant", "Production is healthy."),
                ],
            )
            self.assertEqual(
                [event["type"] for event in conversation["tool_results"]],
                ["toolCall", "toolResult"],
            )

    def test_hermes_collection_reads_supported_redacted_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hermes_export = root / "hermes-export.jsonl"
            archive_root = root / "archive"
            write_jsonl(
                hermes_export,
                [
                    {
                        "id": "hermes-session-1",
                        "source": "desktop",
                        "cwd": "/private/project",
                        "started_at": 1_787_788_800,
                        "messages": [
                            {"id": 1, "role": "user", "content": "Check Hermes."},
                            {"id": 2, "role": "assistant", "content": "Hermes is ready."},
                        ],
                    }
                ],
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "collect",
                    "--host-id",
                    "studio",
                    "--archive-root",
                    str(archive_root),
                    "--hermes-export",
                    str(hermes_export),
                ],
                cwd=REPO,
                text=True,
                capture_output=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            receipt = json.loads(completed.stdout)
            self.assertEqual(receipt["harnesses"]["hermes"]["conversations"], 1)
            object_path = next(
                (archive_root / "hosts" / "studio" / "hermes" / "objects").glob("*.json")
            )
            conversation = json.loads(object_path.read_text())
            self.assertEqual(conversation["source"], "hermes")
            self.assertEqual(conversation["native_source"], "desktop")
            self.assertEqual(conversation["session_id"], "hermes-session-1")
            self.assertEqual(len(conversation["messages"]), 2)

    def test_configured_run_persists_body_free_receipt_and_reports_drive_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude_root = root / "claude"
            spool_root = root / "spool"
            config_path = root / "config.json"
            private_text = "A private instruction that must not appear in receipts."
            write_jsonl(
                claude_root / "projects" / "sample" / "session-1.jsonl",
                [
                    {
                        "type": "user",
                        "message": {"content": private_text},
                    }
                ],
            )
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "host_id": "test-mac",
                        "spool_root": str(spool_root),
                        "drive_root": None,
                        "inventory_harnesses": ["claude", "codex", "openclaw", "hermes"],
                        "sources": {"claude_roots": [str(claude_root)]},
                    }
                )
            )

            completed = subprocess.run(
                [sys.executable, str(CLI), "run", "--config", str(config_path)],
                cwd=REPO,
                text=True,
                capture_output=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            receipt = json.loads(completed.stdout)
            self.assertEqual(receipt["publication"]["status"], "blocked_no_drive_root")
            receipt_path = Path(receipt["receipt_path"])
            self.assertTrue(receipt_path.is_file())
            self.assertNotIn(private_text, receipt_path.read_text())
            self.assertEqual(receipt["harnesses"]["claude"]["conversations"], 1)
            self.assertEqual(
                receipt["harnesses"]["openclaw"]["status"],
                "not_present_on_host",
            )

    def test_configured_run_invokes_supported_redacted_hermes_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spool_root = root / "spool"
            hermes_home = root / "hermes-home"
            hermes_home.mkdir()
            fake_hermes = root / "hermes"
            fake_hermes.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, pathlib, sys\n"
                "assert sys.argv[1:3] == ['sessions', 'export']\n"
                "assert '--redact' in sys.argv and '--yes' in sys.argv\n"
                "assert os.environ['HERMES_HOME'].endswith('hermes-home')\n"
                "pathlib.Path(sys.argv[3]).write_text(json.dumps({\n"
                "  'id': 'hermes-auto-1', 'source': 'desktop',\n"
                "  'messages': [{'role': 'user', 'content': 'automatic'}]\n"
                "}) + '\\n')\n"
            )
            fake_hermes.chmod(0o755)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "host_id": "test-mac",
                        "spool_root": str(spool_root),
                        "drive_root": None,
                        "sources": {
                            "hermes_instances": [
                                {
                                    "home": str(hermes_home),
                                    "binary": str(fake_hermes),
                                }
                            ]
                        },
                    }
                )
            )

            completed = subprocess.run(
                [sys.executable, str(CLI), "run", "--config", str(config_path)],
                cwd=REPO,
                text=True,
                capture_output=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            receipt = json.loads(completed.stdout)
            self.assertEqual(receipt["harnesses"]["hermes"]["conversations"], 1)
            objects = list(
                (spool_root / "hosts" / "test-mac" / "hermes" / "objects").glob("*.json")
            )
            self.assertEqual(len(objects), 1)

    def test_launchd_install_writes_recurring_owner_only_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.json"
            agents_dir = root / "LaunchAgents"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "host_id": "test-mac",
                        "spool_root": str(root / "spool"),
                        "drive_root": None,
                        "sources": {},
                    }
                )
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "install-launchd",
                    "--config",
                    str(config_path),
                    "--launch-agents-dir",
                    str(agents_dir),
                    "--interval-seconds",
                    "900",
                    "--no-load",
                ],
                cwd=REPO,
                text=True,
                capture_output=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            plist_path = Path(result["plist_path"])
            job = plistlib.loads(plist_path.read_bytes())
            self.assertEqual(job["Label"], "com.mattrotundo.ai-chat-archive.test-mac")
            self.assertEqual(job["StartInterval"], 900)
            self.assertTrue(job["RunAtLoad"])
            self.assertEqual(job["Umask"], 0o077)
            self.assertEqual(
                job["ProgramArguments"][-2:],
                ["--config", str(config_path.resolve())],
            )
            self.assertEqual(plist_path.stat().st_mode & 0o777, 0o600)
            for log_key in ("StandardOutPath", "StandardErrorPath"):
                self.assertEqual(Path(job[log_key]).stat().st_mode & 0o777, 0o600)

    def test_launchd_install_enables_a_persistently_disabled_job_before_bootstrap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "host_id": "test-mac",
                        "spool_root": str(root / "spool"),
                        "drive_root": None,
                        "sources": {},
                    }
                )
            )
            args = SimpleNamespace(
                config=str(config_path),
                launch_agents_dir=str(root / "LaunchAgents"),
                interval_seconds=900,
                no_load=False,
            )

            with (
                mock.patch.object(fleet.Path, "home", return_value=root / "home"),
                mock.patch.object(fleet.subprocess, "run") as run,
                mock.patch("builtins.print"),
                mock.patch.object(fleet.os, "getuid", return_value=501),
            ):
                self.assertEqual(fleet.install_launchd(args), 0)

            label = "com.mattrotundo.ai-chat-archive.test-mac"
            domain = "gui/501"
            plist_path = (root / "LaunchAgents" / f"{label}.plist").resolve()
            self.assertEqual(
                [call.args[0] for call in run.call_args_list],
                [
                    ["launchctl", "bootout", f"{domain}/{label}"],
                    ["launchctl", "enable", f"{domain}/{label}"],
                    ["launchctl", "bootstrap", domain, str(plist_path)],
                ],
            )
            self.assertFalse(run.call_args_list[0].kwargs["check"])
            self.assertTrue(run.call_args_list[1].kwargs["check"])
            self.assertTrue(run.call_args_list[2].kwargs["check"])

    def test_configured_run_rejects_google_drive_shaped_plain_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude_root = root / "claude"
            spool_root = root / "spool"
            fake_home = root / "home"
            drive_root = (
                fake_home
                / "Library"
                / "CloudStorage"
                / "GoogleDrive-test@example.com"
                / "My Drive"
                / "AI Chat Archive"
            )
            drive_root.mkdir(parents=True)
            write_jsonl(
                claude_root / "projects" / "sample" / "session-1.jsonl",
                [{"type": "user", "message": {"content": "Publish this."}}],
            )
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "host_id": "test-mac",
                        "spool_root": str(spool_root),
                        "drive_root": str(drive_root),
                        "sources": {"claude_roots": [str(claude_root)]},
                    }
                )
            )

            environment = os.environ.copy()
            environment["HOME"] = str(fake_home)
            completed = subprocess.run(
                [sys.executable, str(CLI), "run", "--config", str(config_path)],
                cwd=REPO,
                text=True,
                capture_output=True,
                env=environment,
            )
            self.assertNotEqual(completed.returncode, 0, completed.stderr)
            receipt = json.loads(completed.stdout)
            self.assertEqual(
                receipt["publication"]["status"], "blocked_not_google_drive"
            )
            published_objects = list(
                (drive_root / "hosts" / "test-mac" / "claude" / "objects").glob("*.json")
            )
            self.assertEqual(len(published_objects), 0)

    def test_host_id_cannot_escape_archive_namespace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "collect",
                    "--host-id",
                    "../escape",
                    "--archive-root",
                    str(root / "archive"),
                ],
                cwd=REPO,
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse((root / "escape").exists())

    def test_config_rejects_unapproved_source_kinds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "host_id": "test-mac",
                        "spool_root": str(root / "spool"),
                        "drive_root": None,
                        "sources": {"messages_root": "/private/var/db/chat.db"},
                    }
                )
            )
            completed = subprocess.run(
                [sys.executable, str(CLI), "run", "--config", str(config_path)],
                cwd=REPO,
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("unapproved source keys", completed.stderr)

    def test_hub_merges_remote_host_shard_and_publishes_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            remote_spool = root / "remote-spool"
            remote_payload = {
                "archive_schema_version": 2,
                "source": "claude-code",
                "session_id": "remote-session",
                "messages": [{"role": "user", "content": "remote body"}],
                "project_path": None,
                "project_name": None,
                "source_file": "/synthetic/remote.jsonl",
            }
            remote_bytes = json.dumps(
                remote_payload, sort_keys=True, separators=(",", ":")
            ).encode()
            remote_digest = hashlib.sha256(remote_bytes).hexdigest()
            remote_object = (
                remote_spool
                / "hosts"
                / "mini"
                / "claude"
                / "objects"
                / f"{remote_digest}.json"
            )
            remote_object.parent.mkdir(parents=True)
            remote_object.write_bytes(remote_bytes + b"\n")
            remote_harness = remote_spool / "hosts" / "mini" / "claude"
            (remote_harness / "index.json").write_bytes(
                fleet.canonical_json(
                    {
                        "schema_version": 1,
                        "host_id": "mini",
                        "harness": "claude",
                        "conversations": [
                            {
                                "object_sha256": remote_digest,
                                "session_id": "remote-session",
                                "source": "claude-code",
                            }
                        ],
                    }
                )
                + b"\n"
            )
            remote_run_id = "20260827T000000.000000Z-deadbeef"
            remote_receipt = (
                remote_spool
                / "hosts"
                / "mini"
                / "receipts"
                / f"{remote_run_id}.json"
            )
            remote_receipt.parent.mkdir(parents=True)
            remote_receipt_value = {
                "schema_version": 1,
                "extractor_sha256": "a" * 64,
                "config_sha256": "c" * 64,
                "run_id": remote_run_id,
                "collected_at": "2026-08-27T00:00:00+00:00",
                "host_id": "mini",
                "collection_status": "completed",
                "status": "completed",
                "errors": [],
                "harnesses": {
                    "claude": {
                        "status": "collected",
                        "conversations": 1,
                        "new_objects": 1,
                        "redactions": 0,
                        "index_conversations": 1,
                        "publishable": True,
                        "quality": {
                            "discovered_lines": 1,
                            "parsed_lines": 1,
                            "failed_lines": 0,
                            "recognized_lines": 1,
                            "discovered_files": 1,
                            "processed_files": 1,
                            "skipped_unchanged_files": 0,
                            "status": "complete",
                        },
                    }
                },
                "hub": {"remotes": {}},
                "publication": {
                    "status": "blocked_no_drive_root",
                    "files_copied": 0,
                },
                "receipt_path": str(remote_receipt),
            }
            remote_receipt.write_bytes(
                fleet.canonical_json(remote_receipt_value) + b"\n"
            )
            fleet.write_publish_manifest(
                remote_spool / "hosts" / "mini",
                remote_receipt,
                remote_receipt_value,
                "c" * 64,
            )
            studio_spool = root / "studio-spool"
            config_path = root / "studio-config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "host_id": "studio",
                        "spool_root": str(studio_spool),
                        "drive_root": None,
                        "sources": {},
                        "hub": {
                            "remotes": [
                                {
                                    "host_id": "mini",
                                    "source_spool_root": str(remote_spool),
                                }
                            ]
                        },
                    }
                )
            )

            completed = subprocess.run(
                [sys.executable, str(CLI), "run", "--config", str(config_path)],
                cwd=REPO,
                text=True,
                capture_output=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            receipt = json.loads(completed.stdout)
            self.assertEqual(receipt["hub"]["remotes"]["mini"]["status"], "pulled")
            self.assertTrue(
                (
                    studio_spool
                    / "hosts"
                    / "mini"
                    / "claude"
                    / "objects"
                    / f"{remote_digest}.json"
                ).is_file()
            )

    def test_collection_redacts_credentials_before_archiving(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude_root = root / "claude"
            archive_root = root / "archive"
            secret = "sk-" + "a" * 32
            write_jsonl(
                claude_root / "projects" / "sample" / "session-1.jsonl",
                [
                    {
                        "type": "user",
                        "message": {
                            "content": f"Use api_key={secret} and password=hunter12345"
                        },
                    }
                ],
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "collect",
                    "--host-id",
                    "test-mac",
                    "--archive-root",
                    str(archive_root),
                    "--claude-root",
                    str(claude_root),
                ],
                cwd=REPO,
                text=True,
                capture_output=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            receipt = json.loads(completed.stdout)
            self.assertGreaterEqual(receipt["harnesses"]["claude"]["redactions"], 1)
            object_path = next(
                (archive_root / "hosts" / "test-mac" / "claude" / "objects").glob("*.json")
            )
            archived = object_path.read_text()
            self.assertNotIn(secret, archived)
            self.assertNotIn("hunter12345", archived)
            self.assertIn("[REDACTED]", archived)

    def test_fleet_output_is_rejected_inside_any_git_checkout(self):
        completed = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "collect",
                "--host-id",
                "test-mac",
                "--archive-root",
                str(REPO / "extracted_data" / "fleet-test"),
            ],
            cwd=REPO,
            text=True,
            capture_output=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("inside a Git checkout", completed.stderr)


if __name__ == "__main__":
    unittest.main()
