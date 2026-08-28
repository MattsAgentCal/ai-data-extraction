import json
import tempfile
import unittest
from pathlib import Path

from extract_claude_code import extract_claude_project_conversations, extract_claude_session
from extract_codex import extract_codex_session, find_all_codex_sessions
from extract_hermes import extract_hermes_export
from extract_openclaw import extract_openclaw_session, find_all_openclaw_sessions


def write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def json_line(value: object) -> str:
    return json.dumps(value)


class ExtractorIntegrityTests(unittest.TestCase):
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
            current_types = (
                "frame-link",
                "permission-mode",
                "bridge-session",
                "ai-title",
                "file-history-delta",
            )
            write_lines(
                session,
                [json_line({"type": record_type}) for record_type in current_types],
            )
            quality = {}
            conversation = extract_claude_session(session, quality_out=quality)
            self.assertEqual(quality["status"], "complete")
            self.assertEqual(
                [event["type"] for event in conversation["events"]],
                list(current_types),
            )

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
                        "tool": "shell",
                        "content": "command output",
                        "message_id": "tool-result-1",
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
