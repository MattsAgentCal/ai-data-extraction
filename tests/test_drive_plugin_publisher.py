import contextlib
import fcntl
import io
import json
import os
import plistlib
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import drive_plugin_publisher as publisher


class DrivePluginPublisherTests(unittest.TestCase):
    def test_publisher_waiter_acquires_after_collector_releases_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            spool = Path(directory) / "spool"
            spool.mkdir()
            lock_path = spool / ".run.lock"
            holder = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(holder, fcntl.LOCK_EX)
            acquired = threading.Event()
            failures = []

            def wait_for_snapshot():
                try:
                    with publisher.publisher_snapshot_lock(spool, 5):
                        acquired.set()
                except Exception as error:  # pragma: no cover - assertion below
                    failures.append(error)

            waiter = threading.Thread(target=wait_for_snapshot)
            waiter.start()
            time.sleep(0.1)
            fcntl.flock(holder, fcntl.LOCK_UN)
            os.close(holder)
            self.assertTrue(acquired.wait(2))
            waiter.join(2)
            self.assertFalse(waiter.is_alive())
            self.assertEqual(failures, [])

    def test_production_publisher_wait_matches_one_schedule_interval(self):
        config_path = Path(__file__).resolve().parents[1] / "configs" / "new-macbook-drive-publisher.json"
        config = json.loads(config_path.read_text())
        self.assertEqual(config["lock_timeout_seconds"], config["interval_seconds"])
        self.assertGreaterEqual(config["lock_timeout_seconds"], 21600)

    def test_config_requires_one_batch_slot_per_host_harness(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "publisher.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "host_id": "new-macbook",
                        "host_ids": ["new-macbook", "mac-studio"],
                        "spool_root": str(root / "spool"),
                        "drive_folder_id": "1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV",
                        "max_files": 7,
                    }
                )
            )
            with self.assertRaisesRegex(ValueError, "one slot per approved harness"):
                publisher.load_config(config_path)

    def test_select_candidates_prioritizes_newest_and_caps_batch(self):
        rows = [
            {
                "file_name": f"{index:064x}.json",
                "mtime_ns": index,
                "host_id": "new-macbook",
                "harness": "codex",
                "size": 4,
            }
            for index in range(4)
        ]
        folder = "1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV"
        selected = publisher.select_candidates(rows, {}, 2, folder)
        self.assertEqual([row["mtime_ns"] for row in selected], [3, 2])
        selected = publisher.select_candidates(
            rows,
            {
                rows[3]["file_name"]: {
                    "file_id": "drive-file-123456",
                    "size": 4,
                    "parent_folder_id": folder,
                }
            },
            2,
            folder,
        )
        self.assertEqual([row["mtime_ns"] for row in selected], [2, 1])

    def test_select_candidates_shares_batch_across_source_groups(self):
        folder = "1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV"
        rows = [
            {
                "file_name": f"{index:064x}.json",
                "mtime_ns": mtime,
                "host_id": host,
                "harness": "hermes",
                "size": 4,
            }
            for index, mtime, host in [
                (1, 202, "mac-studio"),
                (2, 201, "mac-studio"),
                (3, 200, "mac-studio"),
                (4, 1, "new-macbook"),
                (5, 0, "new-macbook"),
            ]
        ]
        selected = publisher.select_candidates(rows, {}, 1, folder)
        self.assertEqual(selected[0]["host_id"], "mac-studio")
        selected = publisher.select_candidates(rows, {}, 2, folder)
        self.assertEqual(
            {row["host_id"] for row in selected}, {"mac-studio", "new-macbook"}
        )
        self.assertIn(1, [row["mtime_ns"] for row in selected])

    def test_select_candidates_rotates_small_batches_by_verified_state(self):
        folder = "1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV"
        rows = [
            {
                "file_name": f"{index:064x}.json",
                "mtime_ns": index,
                "host_id": host,
                "harness": "codex",
                "size": 4,
            }
            for index, host in [(1, "mac-studio"), (2, "new-macbook")]
        ]
        uploaded = {
            rows[0]["file_name"]: {
                "file_id": "drive-file-123456",
                "size": 4,
                "parent_folder_id": folder,
                "verified_at": "2026-09-01T00:00:00+00:00",
            }
        }
        selected = publisher.select_candidates(rows, uploaded, 1, folder)
        self.assertEqual(selected[0]["host_id"], "new-macbook")

    def test_prompt_contains_metadata_but_not_body(self):
        body = "a private conversation body that must not be copied"
        name = "a" * 64 + ".json"
        prompt = publisher.make_prompt(
            [{"file_name": name, "path": "/private/object.json", "size": 17}],
            "1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV",
        )
        self.assertIn(name, prompt)
        self.assertIn("/private/object.json", prompt)
        self.assertNotIn(body, prompt)
        self.assertIn("Do not use shell", prompt)

    def test_parse_agent_message_keeps_kind(self):
        name = "b" * 64 + ".json"
        line = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": json.dumps(
                        {
                            "uploaded": [
                                {
                                    "file_name": name,
                                    "file_id": "drive-file-123456",
                                    "byte_size": 9,
                                    "mime_type": "text/plain",
                                    "parent_folder_id": "1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV",
                                }
                            ],
                            "skipped": [],
                            "failed": [],
                        }
                    ),
                },
            }
        )
        self.assertEqual(publisher.parse_connector_output(line)[0]["kind"], "uploaded")

    def test_parse_drive_tool_metadata_when_final_message_is_missing(self):
        name = "d" * 64 + ".json"
        folder = "1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV"
        upload = {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "tool": "google_drive.upload_file",
                "status": "completed",
                "result": {
                    "structured_content": {
                        "title": name,
                        "id": "drive-file-123456",
                        "mime_type": "text/plain",
                        "size": "9",
                        "parent_ids": [folder],
                    }
                },
            },
        }
        readback = {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "tool": "google_drive.get_file_metadata",
                "status": "completed",
                "result": {
                    "structured_content": {
                        "name": name,
                        "id": "drive-file-123456",
                        "mimeType": "text/plain",
                        "size": 9,
                        "parents": [folder],
                    }
                },
            },
        }
        records = publisher.parse_connector_output(
            "\n".join(json.dumps(item) for item in (upload, readback))
        )
        self.assertEqual(records, [
            {
                "file_name": name,
                "file_id": "drive-file-123456",
                "byte_size": "9",
                "mime_type": "text/plain",
                "parent_folder_id": folder,
                "error_code": None,
                "kind": "uploaded",
            }
        ])

    def test_valid_connector_record_requires_exact_metadata(self):
        candidate = {
            "file_name": "c" * 64 + ".json",
            "size": 12,
        }
        record = {
            "file_name": candidate["file_name"],
            "file_id": "drive-file-123456",
            "byte_size": "12",
            "mime_type": "text/plain",
            "parent_folder_id": "1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV",
        }
        self.assertTrue(
            publisher._valid_connector_record(
                record, candidate, "1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV"
            )
        )
        record["parent_folder_id"] = "wrong-parent"
        self.assertFalse(
            publisher._valid_connector_record(
                record, candidate, "1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV"
            )
        )

    def test_app_server_route_searches_folder_uploads_and_reads_back(self):
        folder = "1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV"
        name = "e" * 64 + ".json"
        candidate = {
            "file_name": name,
            "path": "/private/object.json",
            "size": 23,
        }

        class FakeAppServer:
            def __init__(self):
                self.calls = []

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def _notification(self, method, params):
                self.calls.append((method, params))

            def request(self, method, params):
                self.calls.append((method, params))
                if method == "initialize":
                    return {}
                if method == "thread/start":
                    return {"thread": {"id": "thread-123456"}}
                if method != "mcpServer/tool/call":
                    raise AssertionError(method)
                tool = params["tool"]
                arguments = params["arguments"]
                if tool == "google_drive.search":
                    return {"structuredContent": {"results": []}}
                if tool == "google_drive.upload_file":
                    # Exercise the provider shape that returns only an ID.
                    return {"structuredContent": {"id": "drive-file-123456"}}
                if tool == "google_drive.get_file_metadata":
                    return {
                        "structuredContent": {
                            "title": name,
                            "id": arguments["fileId"],
                            "mime_type": "text/plain",
                            "size": "23",
                            "parent_ids": [folder],
                        }
                    }
                raise AssertionError(tool)

        fake = FakeAppServer()
        config = {
            "app_server_socket": Path("/tmp/app-server.sock"),
            "app_server_timeout_seconds": 20,
            "app_server_client_name": "test-publisher",
            "app_server_client_version": "test",
            "app_server_approval_policy": "never",
            "drive_folder_id": folder,
        }
        with mock.patch.object(publisher, "_AppServerWebSocket", return_value=fake):
            return_code, records = publisher.run_app_server(config, [candidate])
        self.assertEqual(return_code, 0, records)
        self.assertEqual(records[0]["kind"], "uploaded")
        initialize_args = next(
            params
            for method, params in fake.calls
            if method == "initialize"
        )
        self.assertEqual(initialize_args["capabilities"], {"experimentalApi": True})
        search_args = next(
            params["arguments"]
            for method, params in fake.calls
            if method == "mcpServer/tool/call" and params["tool"] == "google_drive.search"
        )
        self.assertEqual(
            search_args["special_filter_query_str"],
            f"'{folder}' in parents and trashed = false",
        )
        upload_args = next(
            params["arguments"]
            for method, params in fake.calls
            if method == "mcpServer/tool/call" and params["tool"] == "google_drive.upload_file"
        )
        self.assertEqual(upload_args["file_uri"], candidate["path"])
        self.assertEqual(upload_args["parent_folder_id"], folder)

    def test_app_server_model_turn_materializes_local_file_and_reverifies(self):
        folder = "1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV"
        name = "a" * 64 + ".json"
        candidate = {
            "file_name": name,
            "path": "/private/object.json",
            "size": 23,
        }

        class FakeAppServer:
            def __init__(self):
                self.calls = []
                self.frames = []

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def _notification(self, method, params):
                self.calls.append((method, params))

            def _send_json(self, value):
                self.calls.append(("send", value))

            def _recv_frame(self):
                if not self.frames:
                    raise AssertionError("model turn did not complete")
                return 0x1, self.frames.pop(0)

            def request(self, method, params):
                self.calls.append((method, params))
                if method == "initialize":
                    self.asserted_capabilities = params["capabilities"]
                    return {}
                if method == "thread/start":
                    return {"thread": {"id": "thread-123456"}}
                if method == "turn/start":
                    event = {
                        "method": "turn/completed",
                        "params": {
                            "turn": {
                                "status": "completed",
                                "items": [
                                    {
                                        "type": "mcpToolCall",
                                        "tool": "google_drive.upload_file",
                                        "status": "completed",
                                        "error": None,
                                        "arguments": {"file_name": name},
                                    }
                                ],
                            }
                        },
                    }
                    payload = json.dumps(event, separators=(",", ":")).encode()
                    self.frames.append(payload)
                    return {}
                if method != "mcpServer/tool/call":
                    raise AssertionError(method)
                tool = params["tool"]
                if tool == "google_drive.search":
                    return {
                        "structuredContent": {
                            "results": [{"name": name, "id": "drive-file-123456"}]
                        }
                    }
                if tool == "google_drive.get_file_metadata":
                    return {
                        "structuredContent": {
                            "name": name,
                            "id": "drive-file-123456",
                            "mime_type": "text/plain",
                            "size": 23,
                            "parent_ids": [folder],
                        }
                    }
                raise AssertionError(tool)

        fake = FakeAppServer()
        config = {
            "app_server_socket": Path("/tmp/app-server.sock"),
            "app_server_timeout_seconds": 20,
            "app_server_client_name": "test-publisher",
            "app_server_client_version": "test",
            "app_server_approval_policy": "never",
            "workspace_root": Path("/tmp"),
            "drive_folder_id": folder,
        }
        with mock.patch.object(publisher, "_AppServerWebSocket", return_value=fake):
            return_code, records = publisher.run_app_server_model(config, [candidate])
        self.assertEqual(return_code, 0, records)
        self.assertEqual(records[0]["kind"], "uploaded")
        self.assertEqual(fake.asserted_capabilities, {"experimentalApi": True})
        turn = next(params for method, params in fake.calls if method == "turn/start")
        self.assertIn(candidate["path"], turn["input"][0]["text"])

    def test_app_server_metadata_rejects_extra_parent(self):
        candidate = {"file_name": "f" * 64 + ".json", "size": 4}
        metadata = {
            "file_id": "drive-file-123456",
            "byte_size": 4,
            "mime_type": "text/plain",
            "parent_ids": [
                "1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV",
                "unexpected-parent",
            ],
        }
        self.assertIsNone(
            publisher._app_server_verified_record(
                metadata, candidate, "1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV"
            )
        )

    def test_app_server_metadata_binds_readback_to_expected_id(self):
        candidate = {"file_name": "g" * 64 + ".json", "size": 4}
        metadata = {
            "file_id": "different-drive-file-123456",
            "byte_size": 4,
            "mime_type": "text/plain",
            "parent_ids": ["1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV"],
        }
        self.assertIsNone(
            publisher._app_server_verified_record(
                metadata,
                candidate,
                "1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV",
                expected_file_id="expected-drive-file-123456",
            )
        )

    def test_app_server_rejects_server_request_instead_of_treating_it_as_response(self):
        class FakeSocket:
            def __init__(self, incoming):
                self.incoming = bytearray(incoming)
                self.sent = bytearray()

            def settimeout(self, _):
                return None

            def sendall(self, payload):
                self.sent.extend(payload)

            def recv(self, length):
                if not self.incoming:
                    return b""
                result = bytes(self.incoming[:length])
                del self.incoming[:length]
                return result

        def server_frame(value):
            payload = json.dumps(value, separators=(",", ":")).encode()
            return bytes((0x81, len(payload))) + payload

        incoming = b"".join(
            [
                server_frame({"id": 1, "method": "item/commandExecution/requestApproval"}),
                server_frame({"id": 1, "result": {"ok": True}}),
            ]
        )
        client = publisher._AppServerWebSocket(Path("/tmp/app-server.sock"), 10)
        fake = FakeSocket(incoming)
        client.socket = fake
        with mock.patch.object(client, "_send_json", wraps=client._send_json) as send_json:
            result = client.request("test", {})
        self.assertEqual(result, {"ok": True})
        send_json.assert_called_once_with(
            {
                "id": 1,
                "error": {
                    "code": -32601,
                    "message": "server requests are unsupported",
                },
            }
        )

    def test_app_server_rechecks_lease_before_upload(self):
        folder = "1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV"
        name = "h" * 64 + ".json"
        candidate = {"file_name": name, "path": "/private/object.json", "size": 23}

        class FakeAppServer:
            def __init__(self):
                self.tools = []

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def _notification(self, *_):
                return None

            def request(self, method, params):
                if method == "initialize":
                    return {}
                if method == "thread/start":
                    return {"thread": {"id": "thread-123456"}}
                self.tools.append(params["tool"])
                if params["tool"] == "google_drive.search":
                    return {"structuredContent": {"results": []}}
                raise AssertionError("upload must not run after lease expiry")

        fake = FakeAppServer()
        config = {
            "app_server_socket": Path("/tmp/app-server.sock"),
            "app_server_timeout_seconds": 20,
            "app_server_client_name": "test-publisher",
            "app_server_client_version": "test",
            "app_server_approval_policy": "never",
            "drive_folder_id": folder,
            "deployment_lease_path": Path("/tmp/lease.json"),
            "lease_owner": "codex:macbook",
            "lease_host": "Mac.lan",
        }
        with (
            mock.patch.object(publisher, "_AppServerWebSocket", return_value=fake),
            mock.patch.object(
                publisher,
                "assert_deployment_lease",
                side_effect=[
                    {"expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()},
                    publisher.LeaseNotHeld("expired"),
                ],
            ),
        ):
            return_code, records = publisher.run_app_server(config, [candidate])
        self.assertEqual(return_code, 1)
        self.assertEqual(records, [{"file_name": name, "error_code": "lease_not_held"}])
        self.assertEqual(fake.tools, ["google_drive.search"])

    def test_lease_rejects_different_owner_and_expired_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lease = root / "lease.json"
            config = {
                "deployment_lease_path": lease,
                "lease_owner": "codex:macbook",
                "lease_host": "Mac.lan",
            }
            lease.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "status": "active",
                        "owner": "other",
                        "owner_host": "Mac.lan",
                        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                    }
                )
            )
            with self.assertRaises(publisher.LeaseNotHeld):
                publisher.assert_deployment_lease(config)
            lease.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "status": "active",
                        "owner": "codex:macbook",
                        "owner_host": "Mac.lan",
                        "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
                    }
                )
            )
            with self.assertRaises(publisher.LeaseNotHeld):
                publisher.assert_deployment_lease(config)

    def test_run_without_shard_is_partial_and_writes_body_free_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lease = root / "lease.json"
            lease.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "status": "active",
                        "owner": "codex:macbook",
                        "owner_host": "Mac.lan",
                        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                    }
                )
            )
            config = {
                "schema_version": 1,
                "host_id": "new-macbook",
                "host_ids": ["new-macbook"],
                "spool_root": str(root / "spool"),
                "drive_folder_id": "1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV",
                "codex_command": "/bin/false",
                "deployment_lease_path": str(lease),
                "lease_owner": "codex:macbook",
                "lease_host": "Mac.lan",
            }
            config_path = root / "publisher.json"
            config_path.write_text(json.dumps(config))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(publisher.run_publisher(config_path), 1)
            summary = json.loads(output.getvalue())
            self.assertEqual(summary["status"], "partial")
            receipts = list((root / "spool" / "plugin-publisher" / "receipts").glob("*.json"))
            self.assertEqual(len(receipts), 1)
            receipt = json.loads(receipts[0].read_text())
            self.assertEqual(receipt["status"], "partial")
            self.assertNotIn("body", receipt)

    def test_install_launchd_no_load_writes_background_interval_plist(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lease = root / "lease.json"
            lease.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "status": "active",
                        "owner": "codex:macbook",
                        "owner_host": "Mac.lan",
                        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
                    }
                )
            )
            config = {
                "schema_version": 1,
                "host_id": "new-macbook",
                "spool_root": str(root / "spool"),
                "drive_folder_id": "1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV",
                "deployment_lease_path": str(lease),
                "lease_owner": "codex:macbook",
                "lease_host": "Mac.lan",
            }
            config_path = root / "publisher.json"
            config_path.write_text(json.dumps(config))
            agents = root / "LaunchAgents"
            args = mock.Mock(
                config=str(config_path), launch_agents_dir=str(agents), no_load=True
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(publisher.install_launchd(args), 0)
            plist_path = agents / "com.mattrotundo.ai-chat-archive.drive-publisher.plist"
            with plist_path.open("rb") as handle:
                job = plistlib.load(handle)
            self.assertEqual(job["StartInterval"], 21600)
            self.assertTrue(job["RunAtLoad"])
            self.assertEqual(job["ProcessType"], "Background")
            self.assertEqual(job["Umask"], 0o077)
            self.assertEqual(os.stat(plist_path).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
