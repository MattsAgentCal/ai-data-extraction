import json
import tempfile
import unittest
from pathlib import Path

from extract_claude_code import extract_claude_project_conversations, extract_claude_session
from extract_codex import extract_codex_session, find_all_codex_sessions
from extract_hermes import (
    MAX_HERMES_THOUGHT_SIGNATURE_BYTES,
    extract_hermes_export,
)
from extract_openclaw import extract_openclaw_session, find_all_openclaw_sessions
from archive_object_contract import validate_archive_object


def write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def json_line(value: object) -> str:
    return json.dumps(value)


class ExtractorIntegrityTests(unittest.TestCase):
    def test_current_producers_normalize_context_and_message_tool_containers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude_session = root / "claude.jsonl"
            write_lines(
                claude_session,
                [
                    json_line(
                        {
                            "type": "user",
                            "message": {"content": "run it"},
                            "toolUse": {"name": "shell", "input": "pwd"},
                        }
                    ),
                    json_line(
                        {
                            "type": "assistant",
                            "message": {
                                "content": [
                                    {"type": "text", "text": "running"},
                                    {
                                        "type": "tool_use",
                                        "name": "shell",
                                        "input": "pwd",
                                    },
                                ]
                            },
                        }
                    ),
                    json_line(
                        {
                            "type": "tool_result",
                            "toolResult": {"output": "/repo"},
                        }
                    ),
                ],
            )
            claude = extract_claude_session(claude_session)
            self.assertEqual(claude["messages"][0]["tool_use"]["type"], "tool_use")
            self.assertEqual(
                claude["messages"][1]["tool_uses"][0]["type"], "tool_use"
            )
            self.assertEqual(
                claude["messages"][1]["tool_results"][0]["type"], "tool_result"
            )
            validate_archive_object(claude, harness="claude")

            codex_session = root / "codex.jsonl"
            write_lines(
                codex_session,
                [
                    json_line(
                        {"type": "session_meta", "payload": {"id": "context-1"}}
                    ),
                    json_line(
                        {
                            "type": "event_msg",
                            "payload": {
                                "type": "user_message",
                                "message": "continue",
                                "context": {"cwd": "/repo", "mode": "default"},
                            },
                        }
                    ),
                ],
            )
            codex = extract_codex_session(codex_session)
            self.assertEqual(codex["messages"][0]["context"]["type"], "context")
            self.assertEqual(
                codex["messages"][0]["context"]["payload"]["cwd"], "/repo"
            )
            validate_archive_object(codex, harness="codex")

    def test_claude_reports_partial_parse_and_rejects_contained_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "claude"
            session = root / "projects" / "sample" / "session.jsonl"
            write_lines(
                session,
                [
                    json_line(
                        {
                            "type": "user",
                            "message": {"content": "valid"},
                        }
                    ),
                    "{malformed",
                ],
            )
            quality = {}
            conversations = extract_claude_project_conversations(
                root, quality_out=quality
            )
            self.assertEqual(len(conversations), 1)
            self.assertEqual(quality["status"], "partial")
            self.assertEqual(quality["failed_lines"], 1)

            outside = Path(tmp) / "outside.jsonl"
            outside.write_text("{}\n")
            (session.parent / "linked.jsonl").symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "symlink"):
                extract_claude_project_conversations(root)

    def test_claude_preserves_current_auxiliary_record_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session.jsonl"
            current_records = (
                {"type": "frame-link"},
                {"type": "permission-mode"},
                {"type": "bridge-session"},
                {"type": "ai-title"},
                {"type": "file-history-delta"},
                {
                    "type": "agent-name",
                    "agentName": "reviewer",
                    "sessionId": "session-1",
                },
                {
                    "type": "artifact-comment-monitor",
                    "v": 1,
                    "sessionId": "session-1",
                    "artifacts": {
                        "artifact-1": {
                            "state": "written",
                            "title": "Review",
                            "writtenAtMs": 1,
                        }
                    },
                },
            )
            write_lines(
                session,
                [json_line(record) for record in current_records],
            )
            quality = {}
            conversation = extract_claude_session(session, quality_out=quality)
            self.assertEqual(quality["status"], "complete")
            self.assertEqual(
                [event["type"] for event in conversation["events"]],
                [record["type"] for record in current_records],
            )
            self.assertEqual(
                conversation["events"][-1]["payload"]["artifacts"]["artifact-1"]["state"],
                "written",
            )
            validate_archive_object(conversation, harness="claude")

    def test_claude_accepts_only_exact_relocation_and_worktree_metadata_shapes(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session.jsonl"
            worktree_session = {
                "originalBranch": "main",
                "originalCwd": "/repo",
                "originalHeadCommit": "abc123",
                "preEnterOriginalCwd": "/repo",
                "sessionId": "session-1",
                "worktreeBranch": "feature",
                "worktreeName": "feature-worktree",
                "worktreePath": "/repo-worktree",
            }
            records = [
                {
                    "type": "relocated",
                    "sessionId": "session-1",
                    "relocatedCwd": "/repo-moved",
                },
                {
                    "type": "worktree-state",
                    "sessionId": "session-1",
                    "worktreeSession": worktree_session,
                },
            ]
            write_lines(session, [json_line(record) for record in records])
            quality = {}

            conversation = extract_claude_session(session, quality_out=quality)

            self.assertEqual(quality["status"], "complete")
            self.assertEqual(quality["recognized_lines"], 2)
            self.assertEqual(quality["failed_lines"], 0)
            self.assertEqual(
                [event["payload"]["sessionId"] for event in conversation["events"]],
                ["session-1", "session-1"],
            )

    def test_claude_relocation_metadata_rejects_shape_drift_and_content_smuggling(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session.jsonl"
            valid_worktree = {
                "originalBranch": "main",
                "originalCwd": "/repo",
                "originalHeadCommit": "abc123",
                "preEnterOriginalCwd": "/repo",
                "sessionId": "session-1",
                "worktreeBranch": "feature",
                "worktreeName": "feature-worktree",
                "worktreePath": "/repo-worktree",
            }
            invalid_records = [
                {
                    "type": "relocated",
                    "sessionId": "session-1",
                    "relocatedCwd": "/repo-moved",
                    "content": "smuggled body",
                },
                {
                    "type": "relocated",
                    "sessionId": "session-1",
                    "relocatedCwd": {"future": "/repo-moved"},
                },
                {
                    "type": "worktree-state",
                    "sessionId": "session-1",
                    "worktreeSession": {**valid_worktree, "text": "smuggled body"},
                },
                {
                    "type": "worktree-state",
                    "sessionId": "different-session",
                    "worktreeSession": valid_worktree,
                },
                {
                    "type": "worktree-state",
                    "sessionId": "session-1",
                    "worktreeSession": {
                        key: value
                        for key, value in valid_worktree.items()
                        if key != "worktreePath"
                    },
                },
            ]
            write_lines(
                session,
                [json_line(record) for record in invalid_records],
            )
            quality = {}

            conversation = extract_claude_session(session, quality_out=quality)

            self.assertIsNone(conversation)
            self.assertEqual(quality["status"], "partial")
            self.assertEqual(quality["recognized_lines"], 0)
            self.assertEqual(quality["failed_lines"], len(invalid_records))

    def test_codex_merges_and_deduplicates_legacy_and_modern_messages_in_source_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "rollout-mixed.jsonl"
            write_lines(
                session,
                [
                    json_line(
                        {
                            "type": "session_meta",
                            "payload": {"id": "mixed-session"},
                        }
                    ),
                    json_line(
                        {
                            "type": "event_msg",
                            "timestamp": "2026-08-27T00:00:01Z",
                            "payload": {
                                "type": "user_message",
                                "message": "same user message",
                            },
                        }
                    ),
                    json_line(
                        {
                            "type": "response_item",
                            "timestamp": "2026-08-27T00:00:02Z",
                            "payload": {
                                "type": "message",
                                "id": "modern-copy",
                                "role": "user",
                                "content": [
                                    {"type": "input_text", "text": "same user message"}
                                ],
                            },
                        }
                    ),
                    json_line(
                        {
                            "type": "event_msg",
                            "timestamp": "2026-08-27T00:00:03Z",
                            "payload": {
                                "type": "agent_message",
                                "message": "legacy-only answer",
                            },
                        }
                    ),
                    json_line(
                        {
                            "type": "response_item",
                            "timestamp": "2026-08-27T00:00:04Z",
                            "payload": {
                                "type": "message",
                                "id": "modern-only",
                                "role": "assistant",
                                "content": [
                                    {"type": "output_text", "text": "modern-only answer"}
                                ],
                            },
                        }
                    ),
                ],
            )

            conversation = extract_codex_session(session)

            self.assertEqual(
                [(message["role"], message["content"]) for message in conversation["messages"]],
                [
                    ("user", "same user message"),
                    ("assistant", "legacy-only answer"),
                    ("assistant", "modern-only answer"),
                ],
            )
            self.assertEqual(conversation["messages"][0]["message_id"], "modern-copy")
            self.assertEqual(conversation.get("extraction_quality"), None)

    def test_codex_malformed_json_is_reported_as_partial_without_body_leakage(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "rollout-partial.jsonl"
            private_body = "private malformed body"
            write_lines(
                session,
                [
                    json_line(
                        {
                            "type": "event_msg",
                            "payload": {"type": "user_message", "message": "valid"},
                        }
                    ),
                    '{"broken": "' + private_body,
                ],
            )
            quality = {}

            conversation = extract_codex_session(session, quality_out=quality)

            expected = {
                "status": "partial",
                "discovered_lines": 2,
                "parsed_lines": 1,
                "failed_lines": 1,
            }
            for key, value in expected.items():
                self.assertEqual(quality[key], value)
            self.assertNotIn("extraction_quality", conversation)
            self.assertNotIn(private_body, json.dumps(conversation))
            self.assertNotIn(private_body, json.dumps(quality))

    def test_codex_rejects_non_utf8_and_utf8_bom_json(self):
        row = {
            "type": "event_msg",
            "payload": {"type": "user_message", "message": "must not parse"},
        }
        encoded_rows = {
            "utf-16le": (json.dumps(row) + "\n").encode("utf-16le"),
            "utf-8-bom": b"\xef\xbb\xbf" + (json.dumps(row) + "\n").encode("utf-8"),
        }
        for label, payload in encoded_rows.items():
            with self.subTest(encoding=label), tempfile.TemporaryDirectory() as tmp:
                session = Path(tmp) / "rollout-encoded.jsonl"
                session.write_bytes(payload)
                quality = {}

                conversation = extract_codex_session(session, quality_out=quality)

                self.assertIsNone(conversation)
                self.assertEqual(quality["status"], "partial")
                self.assertGreater(quality["failed_lines"], 0)
                self.assertEqual(quality["recognized_lines"], 0)

    def test_codex_preserves_current_tool_image_and_lifecycle_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "rollout-current.jsonl"
            event_types = (
                "exec_command_end",
                "error",
                "view_image_tool_call",
                "thread_name_updated",
                "thread_rolled_back",
                "image_generation_end",
                "collab_waiting_end",
                "collab_agent_spawn_end",
                "collab_close_end",
                "dynamic_tool_call_request",
                "dynamic_tool_call_response",
                "collab_agent_interaction_end",
                "entered_review_mode",
                "exited_review_mode",
            )
            response_types = (
                "web_search_call",
                "tool_search_call",
                "tool_search_output",
                "image_generation_call",
            )
            rows = [
                {
                    "type": "event_msg",
                    "payload": {"type": event_type, "status": "complete"},
                }
                for event_type in event_types
            ]
            rows.extend(
                {
                    "type": "response_item",
                    "payload": {"type": response_type, "status": "complete"},
                }
                for response_type in response_types
            )
            rows.extend(
                [
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {"type": "input_text", "text": "image attached"},
                                {
                                    "type": "input_image",
                                    "image_url": "data:image/png;base64,AAAA",
                                    "detail": "high",
                                },
                            ],
                        },
                    },
                    {
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": ""}],
                        },
                    },
                ]
            )
            write_lines(session, [json_line(row) for row in rows])
            quality = {}

            conversation = extract_codex_session(session, quality_out=quality)

            self.assertEqual(quality["status"], "complete")
            self.assertEqual(quality["failed_lines"], 0)
            self.assertEqual(quality["recognized_lines"], len(rows))
            self.assertEqual(
                conversation["messages"][0]["content_items"][0]["type"],
                "input_image",
            )
            tool_types = {item["type"] for item in conversation["tool_results"]}
            self.assertIn("event_msg:exec_command_end", tool_types)
            self.assertIn("response_item:web_search_call", tool_types)
            self.assertIn("response_item:message_empty", tool_types)

    def test_codex_returns_tool_only_session_instead_of_silently_caching_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "rollout-tool-only.jsonl"
            rows = [
                {"type": "session_meta", "payload": {"id": "tool-only"}},
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": ""}],
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "exec_command_end", "status": "complete"},
                },
            ]
            write_lines(session, [json_line(row) for row in rows])
            quality = {}

            conversation = extract_codex_session(session, quality_out=quality)

            self.assertIsNotNone(conversation)
            self.assertEqual(conversation["messages"], [])
            self.assertEqual(quality["status"], "complete")
            self.assertEqual(quality["recognized_lines"], len(rows))
            self.assertEqual(
                {item["type"] for item in conversation["tool_results"]},
                {"response_item:message_empty", "event_msg:exec_command_end"},
            )

    def test_openclaw_preserves_plain_text_tool_result_and_reports_partial_parse(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "openclaw.jsonl"
            write_lines(
                session,
                [
                    json_line({"type": "session", "id": "openclaw-1", "version": 3}),
                    json_line(
                        {
                            "type": "message",
                            "id": "tool-result-1",
                            "message": {
                                "role": "toolResult",
                                "toolName": "shell",
                                "content": [{"type": "text", "text": "command output"}],
                            },
                        }
                    ),
                    "{malformed",
                ],
            )
            quality = {}

            conversation = extract_openclaw_session(session, quality_out=quality)

            self.assertEqual(
                conversation["tool_results"],
                [
                    {
                        "type": "toolResult",
                        "payload": {
                            "tool": "shell",
                            "content": "command output",
                            "message_id": "tool-result-1",
                        },
                        "timestamp": None,
                    }
                ],
            )
            self.assertEqual(quality["status"], "partial")
            self.assertEqual(quality["discovered_lines"], 3)
            self.assertEqual(quality["parsed_lines"], 2)
            self.assertEqual(quality["failed_lines"], 1)
            self.assertNotIn("extraction_quality", conversation)

    def test_hermes_skips_malformed_rows_and_reports_partial_parse(self):
        with tempfile.TemporaryDirectory() as tmp:
            export = Path(tmp) / "hermes.jsonl"
            write_lines(
                export,
                [
                    json_line(
                        {
                            "id": "hermes-1",
                            "source": "desktop",
                            "messages": [{"role": "user", "content": "valid"}],
                        }
                    ),
                    "{malformed",
                    json_line({"id": "missing-messages"}),
                ],
            )
            quality = {}

            conversations = extract_hermes_export(export, quality_out=quality)

            self.assertEqual(len(conversations), 1)
            self.assertEqual(quality["status"], "partial")
            self.assertEqual(quality["discovered_lines"], 3)
            self.assertEqual(quality["parsed_lines"], 1)
            self.assertEqual(quality["failed_lines"], 2)
            self.assertNotIn("extraction_quality", conversations[0])

    def test_hermes_preserves_session_metadata_outside_chat_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            export = Path(tmp) / "hermes.jsonl"
            session_metadata = {
                "id": 1,
                "role": "session_meta",
                "content": None,
                "created_at": 1,
                "is_meta": 1,
                "tool_name": None,
            }
            write_lines(
                export,
                [
                    json_line(
                        {
                            "id": "hermes-1",
                            "source": "desktop",
                            "messages": [
                                session_metadata,
                                {"role": "user", "content": "hello"},
                                {"role": "assistant", "content": "ready"},
                            ],
                        }
                    )
                ],
            )
            quality = {}

            conversations = extract_hermes_export(export, quality_out=quality)

            self.assertEqual(quality["status"], "complete")
            self.assertEqual(quality["failed_lines"], 0)
            self.assertEqual(
                [message["role"] for message in conversations[0]["messages"]],
                ["user", "assistant"],
            )
            self.assertEqual(
                conversations[0]["events"],
                [
                    {
                        "type": "session_meta",
                        "payload": {
                            "id": 1,
                            "content": None,
                            "created_at": 1,
                            "is_meta": 1,
                            "tool_name": None,
                        },
                        "timestamp": 1,
                    }
                ],
            )

    def test_hermes_020_exact_observed_schemas_preserve_validated_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            export = Path(tmp) / "hermes.jsonl"
            private_ignored = "ignored-private-metadata"
            string_row_keys = {
                "billing_base_url", "billing_mode", "billing_provider", "chat_id",
                "chat_type", "compression_failure_error", "cost_source", "cost_status",
                "display_name", "end_reason", "git_branch", "git_repo_root",
                "handoff_error", "handoff_platform", "handoff_state",
                "last_activity_description", "last_activity_provenance", "model_config",
                "origin_json", "parent_session_id", "pricing_version", "profile_name",
                "session_key", "system_prompt", "system_prompt_hash", "thread_id",
                "title_source", "user_id",
            }
            number_row_keys = {
                "actual_cost_usd", "compression_failure_cooldown_until",
                "estimated_cost_usd", "last_active", "last_activity_at", "last_read_at",
            }
            integer_row_keys = {
                "api_call_count", "archived", "cache_read_tokens", "cache_write_tokens",
                "compression_fallback_streak", "compression_ineffective_count",
                "expiry_finalized", "git_metadata_generation", "hidden", "input_tokens",
                "message_count", "output_tokens", "pinned", "reasoning_tokens",
                "rewind_count", "tool_call_count",
            }
            message_keys = {
                "active", "api_content", "codex_message_items",
                "codex_reasoning_items", "compacted", "content", "display_kind",
                "display_metadata", "effect_disposition", "finish_reason", "id",
                "observed", "platform_message_id", "reasoning", "reasoning_content",
                "reasoning_details", "role", "session_id", "timestamp", "token_count",
                "tool_call_id", "tool_calls", "tool_name",
            }

            def current_message(**values):
                message = dict.fromkeys(message_keys)
                message.update(
                    {"active": 0, "compacted": 0, "observed": 0, "token_count": 0}
                )
                message.update(values)
                return message

            row = {
                **{key: private_ignored for key in string_row_keys},
                **{key: 1.5 for key in number_row_keys},
                **{key: 0 for key in integer_row_keys},
                "id": "hermes-current",
                "source": "desktop",
                "cwd": "/workspace",
                "started_at": "2026-08-28T00:00:00Z",
                "ended_at": "2026-08-28T00:01:00Z",
                "title": "session",
                "model": "model",
                "billing_provider": "provider",
                "chat_type": "dm",
                "system_prompt": private_ignored,
                "messages": [
                    current_message(
                        id=1,
                        role="user",
                        content="retained chat body",
                        session_id="hermes-current",
                        timestamp=1.0,
                        display_metadata={"ignored": private_ignored},
                    ),
                    current_message(
                        id=2,
                        role="assistant",
                        content="running",
                        session_id="hermes-current",
                        timestamp=2.0,
                        tool_calls=[
                            {
                                "call_id": "call-1",
                                "function": {"arguments": "{}", "name": "shell"},
                                "id": "tool-1",
                                "response_item_id": "response-1",
                                "type": "function",
                                "extra_content": {
                                    "google": {
                                        "thought_signature": private_ignored,
                                    }
                                },
                            }
                        ],
                    ),
                ],
            }
            base_row = {
                key: value
                for key, value in row.items()
                if key not in {"git_metadata_generation", "hidden"}
            }
            base_row["id"] = "hermes-current-base"
            base_row["messages"] = [
                {**message, "session_id": "hermes-current-base"}
                for message in row["messages"]
            ]
            filtered_row = {
                key: value for key, value in row.items() if key != "last_active"
            }
            filtered_row["id"] = "hermes-current-filtered"
            filtered_row["messages"] = [
                {**message, "session_id": "hermes-current-filtered"}
                for message in row["messages"]
            ]
            filtered_base_row = {
                key: value for key, value in base_row.items() if key != "last_active"
            }
            filtered_base_row["id"] = "hermes-current-base-filtered"
            filtered_base_row["messages"] = [
                {**message, "session_id": "hermes-current-base-filtered"}
                for message in row["messages"]
            ]
            write_lines(
                export,
                [
                    json_line(row),
                    json_line(base_row),
                    json_line(filtered_row),
                    json_line(filtered_base_row),
                ],
            )
            quality = {}

            conversations = extract_hermes_export(export, quality_out=quality)

            self.assertEqual(quality["status"], "complete")
            self.assertEqual(quality["failed_lines"], 0)
            self.assertEqual(len(conversations), 4)
            conversation = conversations[0]
            self.assertEqual(conversation["provider"], "provider")
            self.assertEqual(conversation["session_type"], "dm")
            self.assertEqual(
                conversation["messages"][1]["tool_uses"][0],
                {
                    "type": "tool_use",
                    "payload": {
                        "call_id": "call-1",
                        "function": {"arguments": "{}", "name": "shell"},
                        "id": "tool-1",
                        "response_item_id": "response-1",
                        "type": "function",
                    },
                    "timestamp": 2.0,
                },
            )
            serialized = json.dumps(conversations, sort_keys=True)
            self.assertNotIn(private_ignored, serialized)
            self.assertNotIn("git_metadata_generation", serialized)
            self.assertNotIn("hidden", serialized)
            self.assertNotIn("extra_content", serialized)

            without_hidden = dict(row)
            without_hidden.pop("hidden")
            without_generation = dict(row)
            without_generation.pop("git_metadata_generation")
            invalid_rows = [
                without_hidden,
                without_generation,
                {
                    key: value
                    for key, value in row.items()
                    if key not in {"api_call_count", "last_active"}
                },
                {**row, "future_session_field": "blocked"},
                {**row, "api_call_count": "not-an-integer"},
                {**row, "events": [{"type": "smuggled", "system_prompt": private_ignored}]},
                {**row, "messages": [{"role": "user", "content": "hybrid"}]},
                {
                    **row,
                    "messages": [
                        current_message(
                            id="not-an-integer",
                            role="user",
                            content="body",
                            session_id="hermes-current",
                            timestamp=1.0,
                        )
                    ],
                },
                {
                    **row,
                    "messages": [
                        current_message(
                            id=1,
                            role="user",
                            content="body",
                            session_id="hermes-current",
                            timestamp=1,
                        )
                    ],
                },
                {
                    **row,
                    "messages": [
                        current_message(
                            id=1,
                            role="user",
                            content="body",
                            session_id="hermes-current",
                            timestamp=1.0,
                            active=2,
                        )
                    ],
                },
                {
                    **row,
                    "messages": [
                        current_message(
                            id=1,
                            role="user",
                            content="body",
                            session_id="different-session",
                            timestamp=1.0,
                        )
                    ],
                },
                {
                    **row,
                    "messages": [
                        current_message(
                            id=1,
                            role="assistant",
                            content="body",
                            session_id="hermes-current",
                            timestamp=1.0,
                            tool_calls=[{"type": "function"}],
                        )
                    ],
                },
                {
                    **row,
                    "messages": [
                        current_message(
                            id=1,
                            role="assistant",
                            content="body",
                            session_id="hermes-current",
                            timestamp=1.0,
                            tool_calls=[
                                {
                                    "call_id": "call-1",
                                    "function": {
                                        "arguments": "{}",
                                        "name": "shell",
                                    },
                                    "id": "tool-1",
                                    "response_item_id": "response-1",
                                    "type": "function",
                                    "extra_content": [],
                                }
                            ],
                        )
                    ],
                },
                {
                    **row,
                    "messages": [
                        current_message(
                            id=1,
                            role="assistant",
                            content="body",
                            session_id="hermes-current",
                            timestamp=1.0,
                            tool_calls=[
                                {
                                    "call_id": "call-1",
                                    "function": {
                                        "arguments": "{}",
                                        "name": "shell",
                                    },
                                    "id": "tool-1",
                                    "response_item_id": "response-1",
                                    "type": "function",
                                    "extra_content": {
                                        "body": "blocked",
                                    },
                                }
                            ],
                        )
                    ],
                },
                {
                    **row,
                    "messages": [
                        current_message(
                            id=1,
                            role="assistant",
                            content="body",
                            session_id="hermes-current",
                            timestamp=1.0,
                            tool_calls=[
                                {
                                    "call_id": "call-1",
                                    "function": {
                                        "arguments": "{}",
                                        "name": "shell",
                                    },
                                    "id": "tool-1",
                                    "response_item_id": "response-1",
                                    "type": "function",
                                    "extra_content": {
                                        "google": {
                                            "thought_signature": "signature",
                                            "system_prompt": "blocked",
                                        }
                                    },
                                }
                            ],
                        )
                    ],
                },
                {
                    **row,
                    "messages": [
                        current_message(
                            id=1,
                            role="assistant",
                            content="body",
                            session_id="hermes-current",
                            timestamp=1.0,
                            tool_calls=[
                                {
                                    "call_id": "call-1",
                                    "function": {
                                        "arguments": "{}",
                                        "name": "shell",
                                    },
                                    "id": "tool-1",
                                    "response_item_id": "response-1",
                                    "type": "function",
                                    "extra_content": {
                                        "google": {"thought_signature": ""}
                                    },
                                }
                            ],
                        )
                    ],
                },
                {
                    **row,
                    "messages": [
                        current_message(
                            id=1,
                            role="assistant",
                            content="body",
                            session_id="hermes-current",
                            timestamp=1.0,
                            tool_calls=[
                                {
                                    "call_id": "call-1",
                                    "function": {
                                        "arguments": "{}",
                                        "name": "shell",
                                    },
                                    "id": "tool-1",
                                    "response_item_id": "response-1",
                                    "type": "function",
                                    "extra_content": {
                                        "google": {
                                            "thought_signature": "x"
                                            * (
                                                MAX_HERMES_THOUGHT_SIGNATURE_BYTES
                                                + 1
                                            )
                                        }
                                    },
                                }
                            ],
                        )
                    ],
                },
                {
                    **row,
                    "messages": [
                        current_message(
                            id=1,
                            role="user",
                            content="body",
                            session_id="hermes-current",
                            timestamp=1.0,
                            display_metadata=[],
                        )
                    ],
                },
            ]
            invalid_export = Path(tmp) / "hermes-invalid.jsonl"
            write_lines(invalid_export, [json_line(value) for value in invalid_rows])
            invalid_quality = {}

            self.assertEqual(
                extract_hermes_export(invalid_export, quality_out=invalid_quality),
                [],
            )
            self.assertEqual(invalid_quality["status"], "partial")
            self.assertEqual(invalid_quality["failed_lines"], len(invalid_rows))

    def test_hermes_retained_message_metadata_type_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            export = Path(tmp) / "hermes.jsonl"
            invalid_messages = [
                {"role": "user", "content": "body", "id": {}},
                {"role": "user", "content": "body", "timestamp": []},
                {"role": "user", "content": "body", "created_at": {}},
                {"role": "user", "content": "body", "is_meta": {}},
                {"role": "user", "content": "body", "name": {}},
                {"role": "user", "content": "body", "tool_name": []},
                {"role": "session_meta", "content": None, "name": {}},
            ]
            write_lines(
                export,
                [
                    json_line(
                        {
                            "id": f"invalid-{index}",
                            "source": "desktop",
                            "messages": [message],
                        }
                    )
                    for index, message in enumerate(invalid_messages)
                ],
            )
            quality = {}

            conversations = extract_hermes_export(export, quality_out=quality)

            self.assertEqual(conversations, [])
            self.assertEqual(quality["status"], "partial")
            self.assertEqual(quality["failed_lines"], len(invalid_messages))

    def test_all_four_producers_emit_closed_v2_objects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude = root / "claude.jsonl"
            codex = root / "codex.jsonl"
            openclaw = root / "openclaw.jsonl"
            hermes = root / "hermes.jsonl"
            write_lines(claude, [json_line({"type": "user", "message": {"content": "c"}})])
            write_lines(codex, [json_line({"type": "event_msg", "payload": {"type": "user_message", "message": "x"}})])
            write_lines(openclaw, [
                json_line({"type": "session", "id": "o", "version": 3}),
                json_line({"type": "message", "id": "m", "message": {"role": "user", "content": "o"}}),
            ])
            write_lines(hermes, [json_line({"id": "h", "source": "desktop", "messages": [{"role": "user", "content": "h"}]})])
            values = (
                (extract_claude_session(claude), "claude"),
                (extract_codex_session(codex), "codex"),
                (extract_openclaw_session(openclaw), "openclaw"),
                (extract_hermes_export(hermes)[0], "hermes"),
            )
            for value, harness in values:
                with self.subTest(harness=harness):
                    self.assertEqual(value["archive_schema_version"], 2)
                    validate_archive_object(value, harness=harness)

    def test_closed_v2_contract_rejects_unknown_top_and_envelope_fields(self):
        value = {
            "archive_schema_version": 2,
            "source": "claude-code",
            "session_id": "s",
            "messages": [{"role": "user", "content": "body"}],
            "project_path": None,
            "project_name": None,
            "source_file": "/synthetic/session.jsonl",
        }
        for mutated in (
            {**value, "future": "metadata"},
            {**value, "messages": [{"role": "user", "content": "body", "future": 1}]},
            {**value, "events": [{"type": "known", "payload": {}, "future": 1}]},
        ):
            with self.assertRaises(ValueError):
                validate_archive_object(mutated, harness="claude")

    def test_hermes_other_unknown_or_null_content_shapes_still_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            export = Path(tmp) / "hermes.jsonl"
            write_lines(
                export,
                [
                    json_line(
                        {
                            "id": "unknown-role",
                            "messages": [{"role": "future", "content": None}],
                        }
                    ),
                    json_line(
                        {
                            "id": "null-user",
                            "messages": [{"role": "user", "content": None}],
                        }
                    ),
                ],
            )
            quality = {}

            conversations = extract_hermes_export(export, quality_out=quality)

            self.assertEqual(conversations, [])
            self.assertEqual(quality["status"], "partial")
            self.assertEqual(quality["failed_lines"], 2)

    def test_current_record_types_with_invalid_nested_shapes_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            claude = root / "claude.jsonl"
            write_lines(
                claude,
                [json_line({"type": "user", "message": {"content": {"future": 1}}})],
            )
            claude_quality = {}
            self.assertIsNone(extract_claude_session(claude, quality_out=claude_quality))
            self.assertEqual(claude_quality["status"], "partial")

            codex = root / "codex.jsonl"
            write_lines(
                codex,
                [
                    json_line({"type": "session_meta", "payload": {"id": "c"}}),
                    json_line(
                        {
                            "type": "response_item",
                            "payload": {
                                "type": "message",
                                "role": "assistant",
                                "content": {"future": 1},
                            },
                        }
                    ),
                ],
            )
            codex_quality = {}
            self.assertIsNone(extract_codex_session(codex, quality_out=codex_quality))
            self.assertEqual(codex_quality["status"], "partial")

            openclaw = root / "openclaw.jsonl"
            write_lines(
                openclaw,
                [
                    json_line({"type": "session", "id": "o", "version": 3}),
                    json_line(
                        {
                            "type": "message",
                            "message": {"role": "future", "content": {"future": 1}},
                        }
                    ),
                ],
            )
            openclaw_quality = {}
            self.assertIsNone(
                extract_openclaw_session(openclaw, quality_out=openclaw_quality)
            )
            self.assertEqual(openclaw_quality["status"], "partial")

            hermes = root / "hermes.jsonl"
            write_lines(
                hermes,
                [json_line({"id": "h", "messages": [{"future": 1}]})],
            )
            hermes_quality = {}
            self.assertEqual(
                extract_hermes_export(hermes, quality_out=hermes_quality), []
            )
            self.assertEqual(hermes_quality["status"], "partial")

    def test_unknown_codex_schema_and_openclaw_version_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex = root / "rollout-unknown.jsonl"
            write_lines(codex, [json_line({"type": "future_record", "payload": {}})])
            codex_quality = {}
            self.assertIsNone(extract_codex_session(codex, quality_out=codex_quality))
            self.assertEqual(codex_quality["status"], "partial")

            openclaw = root / "openclaw-unknown.jsonl"
            write_lines(
                openclaw,
                [
                    json_line({"type": "session", "id": "future", "version": 99}),
                    json_line(
                        {
                            "type": "message",
                            "message": {"role": "user", "content": "hello"},
                        }
                    ),
                ],
            )
            openclaw_quality = {}
            extract_openclaw_session(openclaw, quality_out=openclaw_quality)
            self.assertEqual(openclaw_quality["status"], "partial")

            missing_header = root / "openclaw-missing-header.jsonl"
            write_lines(
                missing_header,
                [
                    json_line(
                        {
                            "type": "message",
                            "message": {"role": "user", "content": "hello"},
                        }
                    )
                ],
            )
            missing_header_quality = {}
            extract_openclaw_session(
                missing_header, quality_out=missing_header_quality
            )
            self.assertEqual(missing_header_quality["status"], "partial")

    def test_codex_discovers_archived_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archived = root / "archived_sessions" / "rollout-archived.jsonl"
            archived.parent.mkdir(parents=True)
            archived.write_text("{}\n")
            self.assertIn(archived, find_all_codex_sessions(root))

    def test_extractors_reject_symlinked_jsonl_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "real.jsonl"
            target.write_text("{}\n", encoding="utf-8")
            link = root / "linked.jsonl"
            link.symlink_to(target)

            for extractor in (
                extract_codex_session,
                extract_openclaw_session,
                extract_hermes_export,
            ):
                with self.subTest(extractor=extractor.__name__):
                    with self.assertRaisesRegex(ValueError, "symlink"):
                        extractor(link)

    def test_extractors_reject_jsonl_files_reached_through_symlinked_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real_directory = root / "real-directory"
            real_directory.mkdir()
            target = real_directory / "session.jsonl"
            target.write_text("{}\n", encoding="utf-8")
            linked_directory = root / "linked-directory"
            linked_directory.symlink_to(real_directory, target_is_directory=True)
            linked_file = linked_directory / target.name

            for extractor in (
                extract_codex_session,
                extract_openclaw_session,
                extract_hermes_export,
            ):
                with self.subTest(extractor=extractor.__name__):
                    with self.assertRaisesRegex(ValueError, "symlink"):
                        extractor(linked_file)

    def test_discovery_rejects_symlinked_roots_and_contained_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real_codex = root / "real-codex"
            (real_codex / "sessions").mkdir(parents=True)
            linked_codex = root / "linked-codex"
            linked_codex.symlink_to(real_codex, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                find_all_codex_sessions(linked_codex)

            openclaw = root / "openclaw"
            openclaw.mkdir()
            target = root / "outside.jsonl"
            target.write_text("{}\n", encoding="utf-8")
            (openclaw / "linked.jsonl").symlink_to(target)
            with self.assertRaisesRegex(ValueError, "symlink"):
                find_all_openclaw_sessions(openclaw)


if __name__ == "__main__":
    unittest.main()
