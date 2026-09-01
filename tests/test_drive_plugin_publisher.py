import contextlib
import io
import json
import os
import plistlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import drive_plugin_publisher as publisher


class DrivePluginPublisherTests(unittest.TestCase):
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
