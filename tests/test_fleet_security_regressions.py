import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "fleet_chat_archive.py"
sys.path.insert(0, str(REPO))

import fleet_chat_archive as fleet  # noqa: E402
import extract_claude_code as claude_extractor  # noqa: E402
import extract_codex as codex_extractor  # noqa: E402
import extract_hermes as hermes_extractor  # noqa: E402
import extract_openclaw as openclaw_extractor  # noqa: E402


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def write_archive_object(
    shard: Path, harness: str = "claude", host_id: str | None = None
) -> tuple[Path, bytes]:
    conversation = {
        "schema_version": 1,
        "source": "claude-code",
        "session_id": "cached-session",
        "messages": [{"role": "user", "content": "cached body"}],
    }
    canonical = fleet.canonical_json(conversation)
    digest = hashlib.sha256(canonical).hexdigest()
    object_path = shard / harness / "objects" / f"{digest}.json"
    object_path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical + b"\n"
    object_path.write_bytes(payload)
    index_path = shard / harness / "index.json"
    index_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "host_id": host_id or shard.name,
                "harness": harness,
                "conversations": [
                    {
                        "object_sha256": digest,
                        "session_id": conversation["session_id"],
                        "source": conversation["source"],
                    }
                ],
            },
            sort_keys=True,
        )
        + "\n"
    )
    return object_path, payload


def write_healthy_receipt(shard: Path, harness: str = "claude") -> Path:
    receipt = shard / "receipts" / "20260827T000000.000000Z-test.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    value = {
        "schema_version": 1,
        "extractor_sha256": "a" * 64,
        "config_sha256": "c" * 64,
        "run_id": "healthy-test",
        "collected_at": "2026-08-27T00:00:00+00:00",
        "host_id": shard.name,
        "collection_status": "completed",
        "status": "completed",
        "errors": [],
        "harnesses": {harness: {"status": "collected"}},
    }
    receipt.write_text(json.dumps(value, sort_keys=True) + "\n")
    fleet.write_publish_manifest(shard, receipt, value, "c" * 64)
    return receipt


def configured_args(config_path: Path) -> argparse.Namespace:
    return argparse.Namespace(config=str(config_path))


def safe_temporary_directory():
    """Avoid macOS's /var -> /private/var symlink in path-security tests."""
    return tempfile.TemporaryDirectory(prefix="fleet-security-", dir=Path.home())


def emulate_allowlisted_rsync(command: list[str], kwargs: dict, source: Path):
    """Copy only the NUL-delimited paths a remote rsync was asked to send."""
    destination = Path(command[-1].rstrip("/"))
    destination.mkdir(parents=True, exist_ok=True)
    relative_paths = [item for item in kwargs.get("input", "").split("\0") if item]
    for relative in relative_paths:
        source_path = source / relative
        if source_path.is_symlink():
            continue
        if not source_path.is_file():
            raise subprocess.CalledProcessError(23, command)
        destination_path = destination / relative
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        if destination_path.exists() or destination_path.is_symlink():
            destination_path.unlink()
        shutil.copy2(source_path, destination_path)
    return subprocess.CompletedProcess(command, 0)


class FleetSecurityRegressionTests(unittest.TestCase):
    def test_manifest_receipt_hash_and_json_use_one_byte_snapshot(self):
        with safe_temporary_directory() as tmp:
            shard = Path(tmp) / "hosts" / "mini"
            write_archive_object(shard, host_id="mini")
            receipt_path = write_healthy_receipt(shard)
            original_receipt = receipt_path.read_bytes()
            changed_receipt = json.loads(original_receipt)
            changed_receipt["unbound_marker"] = "changed after hashing"
            changed_payload = (
                json.dumps(changed_receipt, sort_keys=True, separators=(",", ":"))
                + "\n"
            ).encode()
            real_file_sha256 = fleet.file_sha256

            def mutate_receipt_after_separate_hash(path):
                digest = real_file_sha256(path)
                if Path(path) == receipt_path:
                    receipt_path.write_bytes(changed_payload)
                return digest

            with mock.patch.object(
                fleet,
                "file_sha256",
                side_effect=mutate_receipt_after_separate_hash,
            ):
                fleet.validated_shard_files(shard, "mini")

            self.assertEqual(
                receipt_path.read_bytes(),
                original_receipt,
                "receipt validation performed a separate path hash before JSON parsing",
            )

    def test_remote_shard_uses_manifest_bound_last_good_receipt_and_approved_harness(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            remote_spool = root / "remote"
            shard = remote_spool / "hosts" / "mini"
            write_archive_object(shard, host_id="mini")
            write_healthy_receipt(shard)
            failed_receipt = shard / "receipts" / "later-failed.json"
            failed_receipt.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "extractor_sha256": "b" * 64,
                        "run_id": "later-failed",
                        "collected_at": "2026-08-28T00:00:00+00:00",
                        "host_id": "mini",
                        "status": "failed",
                        "errors": [{"component": "codex"}],
                        "harnesses": {"claude": {"status": "partial"}},
                    }
                )
            )
            result = fleet.pull_hub_remotes(
                {
                    "remotes": [
                        {
                            "host_id": "mini",
                            "source_spool_root": str(remote_spool),
                        }
                    ]
                },
                root / "studio",
            )
            self.assertEqual(result["remotes"]["mini"]["status"], "pulled")
            self.assertTrue(
                (root / "studio" / "hosts" / "mini" / "publish-manifest.json").is_file()
            )

            unauthorized = root / "unauthorized" / "hosts" / "mini"
            body = fleet.canonical_json({"source": "imessage", "messages": []})
            digest = hashlib.sha256(body).hexdigest()
            target = unauthorized / "imessage" / "objects" / f"{digest}.json"
            target.parent.mkdir(parents=True)
            target.write_bytes(body + b"\n")
            with self.assertRaisesRegex(ValueError, "unauthorized harness"):
                fleet.validated_shard_files(
                    unauthorized, "mini", require_healthy_receipt=False
                )

    def test_manifest_excludes_objects_and_index_from_a_newer_unbound_run(self):
        with safe_temporary_directory() as tmp:
            shard = Path(tmp) / "hosts" / "mini"
            write_archive_object(shard, host_id="mini")
            write_healthy_receipt(shard)
            manifested_index = (shard / "claude" / "index.json").read_bytes()
            fleet.archive_conversations(
                Path(tmp),
                "mini",
                "claude",
                [
                    {
                        "source": "claude-code",
                        "session_id": "unbound-newer",
                        "messages": [{"role": "user", "content": "newer"}],
                    }
                ],
            )
            (shard / "claude" / "index.json").write_bytes(manifested_index)

            unbound_objects = {
                path
                for path in (shard / "claude" / "objects").iterdir()
                if path.stem not in json.loads(
                    (shard / "publish-manifest.json").read_text()
                )["harnesses"]["claude"]["object_sha256"]
            }
            authorized = set(fleet.validated_shard_files(shard, "mini"))
            self.assertTrue(unbound_objects)
            self.assertTrue(unbound_objects.isdisjoint(authorized))

    def test_manifest_cannot_launder_an_unapproved_object_through_claude_index(self):
        with safe_temporary_directory() as tmp:
            shard = Path(tmp) / "hosts" / "mini"
            archived = {
                "source": "imessage",
                "session_id": "body-session",
                "messages": [],
            }
            payload = fleet.canonical_json(archived)
            digest = hashlib.sha256(payload).hexdigest()
            object_path = shard / "claude" / "objects" / f"{digest}.json"
            object_path.parent.mkdir(parents=True)
            object_path.write_bytes(payload + b"\n")
            index_path = shard / "claude" / "index.json"
            index_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "host_id": "mini",
                        "harness": "claude",
                        "conversations": [
                            {
                                "object_sha256": digest,
                                "session_id": "different-session",
                                "source": "claude-code",
                            }
                        ],
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            receipt = shard / "receipts" / "forged.json"
            receipt.parent.mkdir()
            receipt_value = {
                "schema_version": 1,
                "extractor_sha256": "a" * 64,
                "config_sha256": "c" * 64,
                "run_id": "forged",
                "host_id": "mini",
                "collection_status": "completed",
                "status": "completed",
                "harnesses": {"claude": {"status": "collected"}},
            }
            receipt.write_text(json.dumps(receipt_value, sort_keys=True) + "\n")
            manifest = {
                "schema_version": 1,
                "host_id": "mini",
                "run_id": "forged",
                "generated_at": "2026-08-28T00:00:00+00:00",
                "extractor_sha256": "a" * 64,
                "config_sha256": "c" * 64,
                "receipt": {
                    "path": "receipts/forged.json",
                    "sha256": fleet.file_sha256(receipt),
                },
                "harnesses": {
                    "claude": {
                        "index_sha256": fleet.file_sha256(index_path),
                        "object_sha256": [digest],
                    }
                },
            }
            (shard / "publish-manifest.json").write_text(
                json.dumps(manifest, sort_keys=True) + "\n"
            )

            with self.assertRaisesRegex(ValueError, "provenance mismatch"):
                fleet.validated_shard_files(shard, "mini")

    def test_immutable_copy_does_not_overwrite_a_racing_destination(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            source = root / "source.json"
            destination = root / "destination" / "object.json"
            source.write_bytes(b"source\n")
            destination.parent.mkdir()
            real_link = fleet.os.link

            def competing_link(*args, **kwargs):
                destination.write_bytes(b"competitor\n")
                return real_link(*args, **kwargs)

            with mock.patch.object(fleet.os, "link", side_effect=competing_link):
                with self.assertRaisesRegex(ValueError, "immutable archive collision"):
                    fleet.copy_verified_file(source, destination, immutable=True)
            self.assertEqual(destination.read_bytes(), b"competitor\n")

    def test_interrupted_host_merge_preserves_last_good_and_retry_recovers(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            destination = root / "destination"
            destination_shard = destination / "hosts" / "mini"
            write_archive_object(destination_shard, host_id="mini")
            write_healthy_receipt(destination_shard)

            source_parent = root / "source"
            source_shard = source_parent / "hosts" / "mini"
            write_archive_object(source_shard, host_id="mini")
            fleet.archive_conversations(
                source_parent,
                "mini",
                "claude",
                [
                    {
                        "source": "claude-code",
                        "session_id": "newer-session",
                        "messages": [{"role": "user", "content": "newer"}],
                    }
                ],
            )
            write_healthy_receipt(source_shard)
            old_manifest = json.loads(
                (destination_shard / "publish-manifest.json").read_text()
            )
            old_objects = set(
                old_manifest["harnesses"]["claude"]["object_sha256"]
            )
            real_copy = fleet.copy_verified_file

            def interrupt_before_index(source, destination_path, *, immutable):
                if source.name == "index.json":
                    raise OSError("synthetic interruption")
                return real_copy(source, destination_path, immutable=immutable)

            with mock.patch.object(
                fleet, "copy_verified_file", side_effect=interrupt_before_index
            ):
                with self.assertRaisesRegex(OSError, "synthetic interruption"):
                    fleet.merge_host_shard(source_shard, destination, "mini")

            authorized_after_failure = set(
                fleet.validated_shard_files(destination_shard, "mini")
            )
            authorized_object_names = {
                path.stem for path in authorized_after_failure if path.parent.name == "objects"
            }
            self.assertEqual(authorized_object_names, old_objects)

            def interrupt_after_index(source, destination_path, *, immutable):
                changed = real_copy(source, destination_path, immutable=immutable)
                if source.name == "index.json":
                    raise KeyboardInterrupt
                return changed

            with mock.patch.object(
                fleet, "copy_verified_file", side_effect=interrupt_after_index
            ):
                with self.assertRaises(KeyboardInterrupt):
                    fleet.merge_host_shard(source_shard, destination, "mini")

            authorized_after_cancellation = set(
                fleet.validated_shard_files(destination_shard, "mini")
            )
            authorized_object_names = {
                path.stem
                for path in authorized_after_cancellation
                if path.parent.name == "objects"
            }
            self.assertEqual(authorized_object_names, old_objects)

            fleet.merge_host_shard(source_shard, destination, "mini")
            final_manifest = json.loads(
                (destination_shard / "publish-manifest.json").read_text()
            )
            self.assertEqual(
                len(final_manifest["harnesses"]["claude"]["object_sha256"]), 2
            )

    def test_shard_index_identity_references_and_additive_merge_are_enforced(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            source_shard = root / "source" / "hosts" / "mini"
            source_object, _ = write_archive_object(
                source_shard, host_id="mini"
            )
            write_healthy_receipt(source_shard)
            index_path = source_shard / "claude" / "index.json"
            bad_index = json.loads(index_path.read_text())
            bad_index["host_id"] = "wrong-host"
            index_path.write_text(json.dumps(bad_index))
            with self.assertRaisesRegex(ValueError, "index identity"):
                fleet.validated_shard_files(source_shard, "mini")

            bad_index["host_id"] = "mini"
            bad_index["conversations"][0]["object_sha256"] = "0" * 64
            index_path.write_text(json.dumps(bad_index))
            with self.assertRaisesRegex(ValueError, "missing object"):
                fleet.validated_shard_files(source_shard, "mini")

            # Restore a valid source, then prove a stale source index cannot erase
            # a newer destination row.
            write_archive_object(source_shard, host_id="mini")
            destination = root / "destination"
            fleet.merge_host_shard(source_shard, destination, "mini")
            newer = {
                "source": "claude-code",
                "session_id": "newer-session",
                "messages": [{"role": "user", "content": "newer"}],
            }
            fleet.archive_conversations(
                destination, "mini", "claude", [newer]
            )
            write_healthy_receipt(destination / "hosts" / "mini")
            fleet.merge_host_shard(source_shard, destination, "mini")
            merged = json.loads(
                (destination / "hosts" / "mini" / "claude" / "index.json").read_text()
            )
            self.assertEqual(len(merged["conversations"]), 2)
            self.assertTrue(source_object.is_file())

    def test_drive_publication_rejects_git_checkout_even_with_test_path_bypass(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            spool = root / "spool"
            shard = spool / "hosts" / "test-mac"
            write_archive_object(shard, host_id="test-mac")
            drive = root / "drive"
            (drive / ".git").mkdir(parents=True)
            result = fleet.publish_host_shard(
                spool,
                drive,
                "test-mac",
                allow_non_google_drive=True,
                require_healthy_receipt=False,
            )
            self.assertEqual(result["status"], "blocked_integrity_failure")
            self.assertFalse((drive / "hosts").exists())

    def test_incremental_state_skips_unchanged_transcript_files(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            claude = root / "claude"
            write_jsonl(
                claude / "projects" / "sample" / "session.jsonl",
                [{"type": "user", "message": {"content": "hello"}}],
            )
            first = fleet.collect_sources(
                root / "spool", "test-mac", claude_roots=[claude]
            )
            second = fleet.collect_sources(
                root / "spool", "test-mac", claude_roots=[claude]
            )
            self.assertEqual(first["claude"]["quality"]["processed_files"], 1)
            self.assertEqual(second["claude"]["quality"]["processed_files"], 0)
            self.assertEqual(
                second["claude"]["quality"]["skipped_unchanged_files"], 1
            )

    def test_tool_only_codex_session_is_archived_before_state_is_cached(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            codex = root / "codex"
            session = (
                codex
                / "sessions"
                / "2026"
                / "08"
                / "28"
                / "rollout-tool-only.jsonl"
            )
            write_jsonl(
                session,
                [
                    {"type": "session_meta", "payload": {"id": "tool-only"}},
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "exec_command_end",
                            "status": "complete",
                        },
                    },
                ],
            )
            spool = root / "spool"

            first = fleet.collect_sources(
                spool, "test-mac", codex_roots=[codex]
            )
            second = fleet.collect_sources(
                spool, "test-mac", codex_roots=[codex]
            )

            self.assertEqual(first["codex"]["conversations"], 1)
            self.assertEqual(first["codex"]["index_conversations"], 1)
            self.assertEqual(second["codex"]["quality"]["processed_files"], 0)
            self.assertEqual(
                second["codex"]["quality"]["skipped_unchanged_files"], 1
            )

    def test_codex_append_after_prehash_commits_no_snapshot_or_state(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            codex = root / "codex"
            session = (
                codex
                / "sessions"
                / "2026"
                / "08"
                / "28"
                / "rollout-growing.jsonl"
            )
            write_jsonl(
                session,
                [
                    {"type": "session_meta", "payload": {"id": "growing"}},
                    {
                        "type": "event_msg",
                        "payload": {"type": "user_message", "message": "initial"},
                    },
                ],
            )
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "host_id": "test-mac",
                        "spool_root": str(root / "spool"),
                        "drive_root": None,
                        "sources": {"codex_roots": [str(codex)]},
                    }
                )
            )
            appended_body = "must-not-archive-" + "z" * 30
            real_open = codex_extractor._open_regular_jsonl

            def open_then_append(*args, **kwargs):
                opened = real_open(*args, **kwargs)
                with session.open("a") as handle:
                    handle.write(
                        json.dumps(
                            {
                                "type": "event_msg",
                                "payload": {
                                    "type": "agent_message",
                                    "message": appended_body,
                                },
                            }
                        )
                        + "\n"
                    )
                return opened

            with mock.patch.object(
                codex_extractor,
                "_open_regular_jsonl",
                side_effect=open_then_append,
            ), mock.patch("builtins.print"):
                self.assertNotEqual(fleet.run_config(configured_args(config_path)), 0)

            spool = root / "spool"
            shard = spool / "hosts" / "test-mac"
            self.assertFalse((shard / "codex").exists())
            self.assertFalse((shard / "publish-manifest.json").exists())
            self.assertFalse((spool / "state" / "test-mac" / "codex.json").exists())
            receipts = list((shard / "receipts").glob("*.json"))
            self.assertEqual(len(receipts), 1)
            self.assertNotIn(appended_body, receipts[0].read_text())

    def test_codex_move_between_live_and_archive_paths_has_one_content_object(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            codex = root / "codex"
            live = (
                codex
                / "sessions"
                / "2026"
                / "08"
                / "28"
                / "rollout-shared.jsonl"
            )
            archived = codex / "archived_sessions" / "rollout-shared.jsonl"
            write_jsonl(
                live,
                [
                    {"type": "session_meta", "payload": {"id": "shared-session"}},
                    {
                        "type": "event_msg",
                        "payload": {"type": "user_message", "message": "same body"},
                    },
                ],
            )
            archived.parent.mkdir(parents=True)
            archived.write_bytes(live.read_bytes())

            result = fleet.collect_sources(
                root / "spool", "test-mac", codex_roots=[codex]
            )
            harness_root = root / "spool" / "hosts" / "test-mac" / "codex"
            index = json.loads((harness_root / "index.json").read_text())
            objects = list((harness_root / "objects").glob("*.json"))

            self.assertEqual(result["codex"]["quality"]["processed_files"], 2)
            self.assertEqual(len(index["conversations"]), 1)
            self.assertEqual(len(objects), 1)
            archived_value = json.loads(objects[0].read_text())
            self.assertIn("session_file", archived_value)
            self.assertIn(
                archived_value["session_file"], {str(live), str(archived)}
            )

    def test_codex_move_reuses_a_legacy_index_object_without_duplication(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            codex = root / "codex"
            live = (
                codex
                / "sessions"
                / "2026"
                / "08"
                / "28"
                / "rollout-legacy.jsonl"
            )
            archived = codex / "archived_sessions" / "rollout-legacy.jsonl"
            write_jsonl(
                live,
                [
                    {"type": "session_meta", "payload": {"id": "legacy-session"}},
                    {
                        "type": "event_msg",
                        "payload": {"type": "user_message", "message": "legacy body"},
                    },
                ],
            )
            conversation = fleet.extract_codex_session(live)
            conversation["installation"] = str(codex)
            conversation["_archive_source_sha256"] = fleet.file_sha256(live)
            spool = root / "spool"
            fleet.archive_conversations(
                spool, "test-mac", "codex", [conversation]
            )
            harness_root = spool / "hosts" / "test-mac" / "codex"
            legacy_index = json.loads((harness_root / "index.json").read_text())
            legacy_index["conversations"][0].pop("source_sha256")
            legacy_index["conversations"][0].pop("installation")
            (harness_root / "index.json").write_text(
                json.dumps(legacy_index, sort_keys=True) + "\n"
            )
            write_healthy_receipt(harness_root.parent, harness="codex")
            legacy_digest = legacy_index["conversations"][0]["object_sha256"]
            legacy_object = harness_root / "objects" / f"{legacy_digest}.json"
            legacy_inode = legacy_object.stat().st_ino
            archived.parent.mkdir(parents=True)
            live.replace(archived)

            result = fleet.collect_sources(
                spool, "test-mac", codex_roots=[codex]
            )
            current_index = json.loads((harness_root / "index.json").read_text())
            objects = list((harness_root / "objects").glob("*.json"))

            self.assertEqual(result["codex"]["new_objects"], 0)
            self.assertEqual(len(current_index["conversations"]), 1)
            self.assertEqual(
                current_index["conversations"][0]["object_sha256"], legacy_digest
            )
            self.assertEqual(objects, [legacy_object])
            self.assertEqual(legacy_object.stat().st_ino, legacy_inode)

    def test_incremental_state_detects_same_size_rewrite_with_restored_mtime(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            claude = root / "claude"
            session = claude / "projects" / "sample" / "session.jsonl"
            write_jsonl(
                session,
                [{"type": "user", "message": {"content": "hello"}}],
            )
            first = fleet.collect_sources(
                root / "spool", "test-mac", claude_roots=[claude]
            )
            original = session.stat()
            session.write_text(session.read_text().replace("hello", "world"))
            os.utime(
                session,
                ns=(original.st_atime_ns, original.st_mtime_ns),
            )
            second = fleet.collect_sources(
                root / "spool", "test-mac", claude_roots=[claude]
            )
            self.assertEqual(first["claude"]["quality"]["processed_files"], 1)
            self.assertEqual(second["claude"]["quality"]["processed_files"], 1)
            self.assertEqual(second["claude"]["quality"]["skipped_unchanged_files"], 0)

    def test_partial_extraction_is_non_publishable_and_returns_failure(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            codex_root = root / "codex"
            session = (
                codex_root
                / "sessions"
                / "2026"
                / "08"
                / "27"
                / "rollout-partial.jsonl"
            )
            session.parent.mkdir(parents=True)
            session.write_text(
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {"type": "user_message", "message": "valid"},
                    }
                )
                + "\n{malformed\n"
            )
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "host_id": "test-mac",
                        "spool_root": str(root / "spool"),
                        "drive_root": None,
                        "sources": {"codex_roots": [str(codex_root)]},
                    }
                )
            )
            with mock.patch("builtins.print"):
                result = fleet.run_config(configured_args(config_path))
            receipt_path = next(
                (root / "spool" / "hosts" / "test-mac" / "receipts").glob("*.json")
            )
            receipt = json.loads(receipt_path.read_text())
            self.assertNotEqual(result, 0)
            self.assertEqual(receipt["status"], "failed")
            self.assertEqual(receipt["harnesses"]["codex"]["status"], "partial")
            self.assertFalse(receipt["harnesses"]["codex"]["publishable"])
            self.assertEqual(
                receipt["harnesses"]["codex"]["quality"]["failed_lines"], 1
            )

    def test_partial_changed_session_preserves_last_manifested_snapshot_and_state(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            claude_root = root / "claude"
            session = claude_root / "projects" / "sample" / "session.jsonl"
            write_jsonl(
                session,
                [{"type": "user", "message": {"content": "healthy"}}],
            )
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "host_id": "test-mac",
                        "spool_root": str(root / "spool"),
                        "drive_root": None,
                        "sources": {"claude_roots": [str(claude_root)]},
                    }
                )
            )
            with mock.patch("builtins.print"):
                self.assertEqual(fleet.run_config(configured_args(config_path)), 0)
            shard = root / "spool" / "hosts" / "test-mac"
            manifest_path = shard / "publish-manifest.json"
            index_path = shard / "claude" / "index.json"
            state_path = root / "spool" / "state" / "test-mac" / "claude.json"
            before = (
                manifest_path.read_bytes(),
                index_path.read_bytes(),
                state_path.read_bytes(),
                sorted(path.name for path in (shard / "claude" / "objects").iterdir()),
            )

            with session.open("a") as handle:
                handle.write("{malformed\n")
            with mock.patch("builtins.print"):
                self.assertNotEqual(fleet.run_config(configured_args(config_path)), 0)

            after = (
                manifest_path.read_bytes(),
                index_path.read_bytes(),
                state_path.read_bytes(),
                sorted(path.name for path in (shard / "claude" / "objects").iterdir()),
            )
            self.assertEqual(after, before)
            fleet.validated_shard_files(shard, "test-mac")

    def test_failed_second_publication_is_bound_to_a_durable_local_receipt(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            claude_root = root / "claude"
            write_jsonl(
                claude_root / "projects" / "sample" / "session.jsonl",
                [{"type": "user", "message": {"content": "healthy"}}],
            )
            drive_root = root / "drive"
            drive_root.mkdir()
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "host_id": "test-mac",
                        "spool_root": str(root / "spool"),
                        "drive_root": str(drive_root),
                        "sources": {"claude_roots": [str(claude_root)]},
                    }
                )
            )
            publication_results = [
                {"status": "published", "files_copied": 3},
                {"status": "blocked_integrity_failure", "files_copied": 0},
            ]
            with mock.patch.object(
                fleet, "publish_host_shard", side_effect=publication_results
            ), mock.patch("builtins.print"):
                result = fleet.run_config(configured_args(config_path))

            shard = root / "spool" / "hosts" / "test-mac"
            manifest = json.loads((shard / "publish-manifest.json").read_text())
            receipt_path = shard / manifest["receipt"]["path"]
            receipt = json.loads(receipt_path.read_text())
            self.assertNotEqual(result, 0)
            self.assertEqual(receipt["status"], "failed")
            self.assertEqual(
                receipt["receipt_publication"]["status"],
                "blocked_integrity_failure",
            )
            self.assertEqual(
                fleet.file_sha256(receipt_path), manifest["receipt"]["sha256"]
            )

    def test_second_manifest_interruption_preserves_first_manifest_and_receipt(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            claude_root = root / "claude"
            write_jsonl(
                claude_root / "projects" / "sample" / "session.jsonl",
                [{"type": "user", "message": {"content": "healthy"}}],
            )
            drive_root = root / "drive"
            drive_root.mkdir()
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "host_id": "test-mac",
                        "spool_root": str(root / "spool"),
                        "drive_root": str(drive_root),
                        "sources": {"claude_roots": [str(claude_root)]},
                    }
                )
            )
            real_write_manifest = fleet.write_publish_manifest
            manifest_writes = 0

            def interrupt_second_manifest(*args, **kwargs):
                nonlocal manifest_writes
                manifest_writes += 1
                if manifest_writes == 2:
                    raise fleet.TerminationRequested
                return real_write_manifest(*args, **kwargs)

            with mock.patch.object(
                fleet,
                "write_publish_manifest",
                side_effect=interrupt_second_manifest,
            ), mock.patch.object(
                fleet,
                "publish_host_shard",
                return_value={
                    "status": "blocked_integrity_failure",
                    "files_copied": 0,
                },
            ), mock.patch("builtins.print"), self.assertRaises(
                fleet.TerminationRequested
            ):
                fleet.run_config(configured_args(config_path))

            shard = root / "spool" / "hosts" / "test-mac"
            manifest = json.loads((shard / "publish-manifest.json").read_bytes())
            receipt_path = shard / manifest["receipt"]["path"]
            receipt_payload = receipt_path.read_bytes()
            receipt_paths = sorted((shard / "receipts").glob("*.json"))
            self.assertEqual(manifest_writes, 2)
            self.assertEqual(len(receipt_paths), 2)
            self.assertIn(receipt_path, receipt_paths)
            self.assertEqual(
                hashlib.sha256(receipt_payload).hexdigest(),
                manifest["receipt"]["sha256"],
            )
            self.assertNotEqual(
                receipt_payload,
                next(path.read_bytes() for path in receipt_paths if path != receipt_path),
            )
            fleet.validated_shard_files(shard, "test-mac")

    def test_publication_exception_cannot_overwrite_manifest_bound_receipt(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            claude_root = root / "claude"
            write_jsonl(
                claude_root / "projects" / "sample" / "session.jsonl",
                [{"type": "user", "message": {"content": "healthy"}}],
            )
            drive_root = root / "drive"
            drive_root.mkdir()
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "host_id": "test-mac",
                        "spool_root": str(root / "spool"),
                        "drive_root": str(drive_root),
                        "sources": {"claude_roots": [str(claude_root)]},
                    }
                )
            )
            with mock.patch.object(
                fleet,
                "publish_host_shard",
                side_effect=RuntimeError("synthetic publication failure"),
            ), mock.patch("builtins.print"):
                self.assertNotEqual(fleet.run_config(configured_args(config_path)), 0)

            shard = root / "spool" / "hosts" / "test-mac"
            manifest = json.loads((shard / "publish-manifest.json").read_bytes())
            receipt_path = shard / manifest["receipt"]["path"]
            receipt_payload = receipt_path.read_bytes()
            receipt_paths = sorted((shard / "receipts").glob("*.json"))
            self.assertEqual(len(receipt_paths), 2)
            self.assertIn(receipt_path, receipt_paths)
            self.assertEqual(
                hashlib.sha256(receipt_payload).hexdigest(),
                manifest["receipt"]["sha256"],
            )
            self.assertEqual(
                json.loads(receipt_payload)["status"],
                "completed",
            )
            self.assertTrue(
                any(path.name.endswith("-publish-error.json") for path in receipt_paths)
            )
            fleet.validated_shard_files(shard, "test-mac")

    def test_sensitive_structured_fields_and_headers_are_redacted_recursively(self):
        structured_values = [
            "opaque-" + "a" * 30,
            "Bearer opaque-" + "b" * 30,
            "session=opaque-" + "c" * 30,
            "opaque-" + "d" * 30,
        ]
        value = {
            "metadata": {
                "api_key": structured_values[0],
                "headers": {
                    "Authorization": structured_values[1],
                    "Cookie": structured_values[2],
                },
                "nested": {"PASSWORD": structured_values[3]},
            }
        }

        cleaned, count = fleet.redact_value(value)
        serialized = json.dumps(cleaned, sort_keys=True)
        sensitive_values = [
            cleaned["metadata"]["api_key"],
            cleaned["metadata"]["headers"]["Authorization"],
            cleaned["metadata"]["headers"]["Cookie"],
            cleaned["metadata"]["nested"]["PASSWORD"],
        ]

        self.assertTrue(
            all(item == "[REDACTED]" for item in sensitive_values),
            "sensitive structured values must be fully redacted",
        )
        self.assertFalse(
            any(item in serialized for item in structured_values),
            "structured secret material survived redaction",
        )
        self.assertGreaterEqual(count, len(structured_values))

    def test_broad_structured_and_assignment_secret_shapes_are_blocked(self):
        samples = {
            "secretKey": "opaque-" + "a" * 30,
            "aws_secret_access_key": "opaque-" + "b" * 30,
            "credentials": {"value": "opaque-" + "c" * 30},
            "headers": {"Authorization": "ApiKey opaque-" + "d" * 30},
            "content": "MY_SECRET=opaque-" + "e" * 30,
        }
        cleaned, count = fleet.redact_value(samples)
        serialized = json.dumps(cleaned, sort_keys=True)
        self.assertGreaterEqual(count, 5)
        for marker in ("opaque-" + letter * 30 for letter in "abcde"):
            self.assertNotIn(marker, serialized)
        self.assertEqual(fleet.residual_secret_paths(cleaned), [])

    def test_multiline_escaped_and_shell_assignment_values_are_fully_redacted(self):
        sentinel = "SENTINEL-" + "z" * 24
        samples = [
            f"password: |\n  {sentinel}",
            f'secretKey: "prefix\n{sentinel}"',
            f'password=\\"prefix {sentinel}\\"',
            f"MY_SECRET=$'prefix {sentinel}'",
            f'tool input: {{"secretKey": "{sentinel}"}}',
            "password=abc123",
        ]
        for sample in samples:
            cleaned, count = fleet.redact_text(sample)
            self.assertEqual(cleaned, "[REDACTED]", sample)
            self.assertGreaterEqual(count, 1, sample)
            self.assertEqual(fleet.residual_secret_paths(cleaned), [], sample)
        for key in ("tokenizer", "max_tokens", "token_count"):
            cleaned, count = fleet.redact_value({key: 42})
            self.assertEqual(cleaned[key], 42)
            self.assertEqual(count, 0)

    def test_assignment_scanner_handles_case_prefixes_and_escaped_keys(self):
        sentinel = "SENTINEL-" + "q" * 24
        sensitive = (
            f"my.secret-access_key = {sentinel}",
            f"ReFrEsH-ToKeN: {sentinel}",
            f'{{\\"password\\": \\"{sentinel}\\"}}',
            f"PRIVATE.KEY={sentinel}",
            f"Unicode İ before PASSWORD={sentinel}",
            f"İPASSWORD={sentinel}",
            f"İsecretKey: {sentinel}",
            f"İMY_SECRET={sentinel}",
            f"PAſSWORD={sentinel}",
            f"TOKEN={sentinel}",
            f"SECRETKEY={sentinel}",
        )
        for sample in sensitive:
            cleaned, count = fleet.redact_text(sample)
            self.assertEqual(cleaned, "[REDACTED]", sample)
            self.assertEqual(count, 1, sample)
        for sample in ("monkey: 42", "token_count: 42", "tokenizer=42"):
            cleaned, count = fleet.redact_text(sample)
            self.assertEqual(cleaned, sample)
            self.assertEqual(count, 0)

        self.assertEqual(
            fleet.residual_secret_paths("bEaReR abcdefghijklmnop"), ["$"]
        )

        unicode_casefold_secrets = (
            "AUTHORIZATİON: ApiKey opaque-value",
            "BASİC abcdefghijklmnop",
            "BAſIC abcdefghijklmnop",
            "COOKIE: session=opaque-value",
        )
        for sample in unicode_casefold_secrets:
            cleaned, count = fleet.redact_text(sample)
            self.assertNotEqual(cleaned, sample)
            self.assertGreaterEqual(count, 1)
            self.assertEqual(fleet.residual_secret_paths(cleaned), [])
            self.assertEqual(fleet.residual_secret_paths(sample), ["$"])

    def test_clean_unicode_keys_and_values_do_not_invoke_secret_regex(self):
        value = {"naïve-İıſK": "café İ ı ſ K"}
        redactable = mock.Mock(wraps=fleet.REDACTABLE_SECRET_RE)
        residual = mock.Mock(wraps=fleet.RESIDUAL_SECRET_RE)

        with mock.patch.object(
            fleet, "REDACTABLE_SECRET_RE", redactable
        ), mock.patch.object(fleet, "RESIDUAL_SECRET_RE", residual):
            cleaned, count = fleet.redact_value(value)
            findings = fleet.residual_secret_paths(value)

        self.assertEqual(cleaned, value)
        self.assertEqual(count, 0)
        self.assertEqual(findings, [])
        redactable.subn.assert_not_called()
        residual.search.assert_not_called()

    def test_marker_prefilter_covers_every_secret_pattern_branch(self):
        examples = [
            "-----BEGIN PRIVATE KEY-----\nbody\n-----END PRIVATE KEY-----",
            "sk-" + "a" * 20,
            "ghp_" + "a" * 20,
            "AKIA" + "A" * 16,
            "eyJ" + "a" * 10 + "." + "b" * 10 + "." + "c" * 10,
            "Authorization: synthetic-value",
            "Set-Cookie: synthetic=value",
            "Bearer syntheticvalue",
            "Basic syntheticvalue",
            "AUTHORİZATİON: synthetic-value",
            "COOKIE: synthetic=value",
            "BAſIC syntheticvalue",
        ]

        for example in examples:
            with self.subTest(example=example[:20]):
                self.assertIsNotNone(fleet.RESIDUAL_SECRET_RE.search(example))
                lowered = fleet.regex_compatible_lower(example)
                self.assertTrue(
                    any(marker in lowered for marker in fleet.REDACTABLE_TEXT_MARKERS)
                )

    def test_archive_blocks_a_residual_structured_secret(self):
        with safe_temporary_directory() as tmp:
            archive_root = Path(tmp) / "archive"
            residual = {
                "source": "test",
                "session_id": "residual-session",
                "metadata": {"password": "opaque-" + "z" * 30},
                "messages": [],
            }
            with mock.patch.object(fleet, "redact_value", return_value=(residual, 0)):
                with self.assertRaises(ValueError):
                    fleet.archive_conversations(
                        archive_root, "test-mac", "claude", [residual]
                    )

    def test_config_rejects_a_symlink_component_in_spool_root(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            real_spool = root / "real-spool"
            real_spool.mkdir()
            spool_link = root / "spool-link"
            spool_link.symlink_to(real_spool, target_is_directory=True)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "host_id": "test-mac",
                        "spool_root": str(spool_link / "nested"),
                        "drive_root": None,
                        "sources": {},
                    }
                )
            )

            with self.assertRaisesRegex(ValueError, "symlink"):
                fleet.run_config(configured_args(config_path))

    def test_config_rejects_a_symlink_component_in_source_root(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            real_source = root / "real-source"
            write_jsonl(
                real_source / "projects" / "sample" / "session.jsonl",
                [{"type": "user", "message": {"content": "safe body"}}],
            )
            source_link = root / "source-link"
            source_link.symlink_to(real_source, target_is_directory=True)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "host_id": "test-mac",
                        "spool_root": str(root / "spool"),
                        "drive_root": None,
                        "sources": {"claude_roots": [str(source_link)]},
                    }
                )
            )

            with mock.patch("builtins.print"):
                result = fleet.run_config(configured_args(config_path))

            receipt_path = next(
                (root / "spool" / "hosts" / "test-mac" / "receipts").glob("*.json")
            )
            receipt = json.loads(receipt_path.read_text())
            self.assertNotEqual(result, 0)
            self.assertEqual(receipt["harnesses"]["claude"]["status"], "failed")
            self.assertIn("symlink", receipt["harnesses"]["claude"]["error"])
            self.assertFalse(
                (root / "spool" / "hosts" / "test-mac" / "claude" / "objects").exists()
            )

    def test_publish_rejects_a_symlink_component_in_destination(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            fake_home = root / "home"
            drive_root = (
                fake_home
                / "Library"
                / "CloudStorage"
                / "GoogleDrive-test"
                / "My Drive"
                / "Archive"
            )
            drive_root.mkdir(parents=True)
            escaped_hosts = root / "escaped-hosts"
            escaped_hosts.mkdir()
            (drive_root / "hosts").symlink_to(escaped_hosts, target_is_directory=True)
            spool_root = root / "spool"
            write_archive_object(spool_root / "hosts" / "test-mac")

            with mock.patch.object(fleet.Path, "home", return_value=fake_home):
                result = fleet.publish_host_shard(spool_root, drive_root, "test-mac")

            self.assertNotEqual(result["status"], "published")
            self.assertFalse(
                (escaped_hosts / "test-mac").exists(),
                "publication followed a destination symlink",
            )

    def test_archive_index_preserves_prior_rows_on_empty_and_partial_snapshots(self):
        with safe_temporary_directory() as tmp:
            archive_root = Path(tmp) / "archive"
            first = {
                "source": "test",
                "session_id": "session-a",
                "messages": [{"role": "user", "content": "first"}],
            }
            second = {
                "source": "test",
                "session_id": "session-b",
                "messages": [{"role": "user", "content": "second"}],
            }
            fleet.archive_conversations(
                archive_root, "test-mac", "claude", [first, second]
            )
            index_path = archive_root / "hosts" / "test-mac" / "claude" / "index.json"

            fleet.archive_conversations(archive_root, "test-mac", "claude", [])
            after_empty = json.loads(index_path.read_text())
            self.assertEqual(
                {row["session_id"] for row in after_empty["conversations"]},
                {"session-a", "session-b"},
            )

            fleet.archive_conversations(
                archive_root, "test-mac", "claude", [second]
            )
            after_partial = json.loads(index_path.read_text())
            self.assertEqual(
                {row["session_id"] for row in after_partial["conversations"]},
                {"session-a", "session-b"},
            )

    def test_interrupted_remote_pull_never_leaves_a_partial_final_shard(self):
        with safe_temporary_directory() as tmp:
            spool_root = Path(tmp) / "spool"
            remote_shard = Path(tmp) / "remote" / "hosts" / "mini"
            write_archive_object(remote_shard, host_id="mini")
            write_healthy_receipt(remote_shard)

            def interrupted_rsync(command, **kwargs):
                if command[0] == "ssh":
                    return subprocess.CompletedProcess(command, 0)
                result = emulate_allowlisted_rsync(command, kwargs, remote_shard)
                if "/objects/" in kwargs.get("input", ""):
                    raise subprocess.CalledProcessError(23, command)
                return result

            hub = {
                "remotes": [
                    {
                        "host_id": "mini",
                        "ssh_host": "mini.test",
                        "remote_spool_root": "/remote/spool",
                    }
                ]
            }
            with mock.patch.object(fleet.subprocess, "run", side_effect=interrupted_rsync):
                result = fleet.pull_hub_remotes(hub, spool_root)

            self.assertEqual(result["remotes"]["mini"]["status"], "unreachable")
            self.assertFalse(
                (spool_root / "hosts" / "mini" / "claude" / "objects").exists(),
                "a failed pull exposed a partial remote shard",
            )

    def test_remote_pull_waits_for_manifest_before_copying_objects(self):
        with safe_temporary_directory() as tmp:
            spool_root = Path(tmp) / "spool"
            calls = []

            def no_manifest(command, **_kwargs):
                calls.append(command)
                if command[0] != "ssh":
                    self.fail("rsync ran before the remote manifest existed")
                return subprocess.CompletedProcess(command, 1)

            hub = {
                "remotes": [
                    {
                        "host_id": "mini",
                        "ssh_host": "mini.test",
                        "remote_spool_root": "/remote/spool",
                    }
                ]
            }
            with mock.patch.object(fleet.subprocess, "run", side_effect=no_manifest):
                result = fleet.pull_hub_remotes(hub, spool_root)

            self.assertEqual(
                result["remotes"]["mini"],
                {"status": "pending_manifest", "files_copied": 0},
            )
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], "ssh")

    def test_remote_pull_never_stages_an_unmanifested_object(self):
        with safe_temporary_directory() as tmp:
            spool_root = Path(tmp) / "spool"
            remote_shard = Path(tmp) / "remote" / "hosts" / "mini"
            write_archive_object(remote_shard, host_id="mini")
            write_healthy_receipt(remote_shard)
            unmanifested_body = fleet.canonical_json(
                {
                    "schema_version": 1,
                    "source": "claude-code",
                    "session_id": "must-not-cross-hosts",
                    "messages": [{"role": "user", "content": "private body"}],
                }
            )
            unmanifested_digest = hashlib.sha256(unmanifested_body).hexdigest()
            unmanifested_relative = (
                f"claude/objects/{unmanifested_digest}.json"
            )
            (remote_shard / unmanifested_relative).write_bytes(
                unmanifested_body + b"\n"
            )
            requested: list[str] = []

            def allowlisted_pull(command, **kwargs):
                if command[0] == "ssh":
                    return subprocess.CompletedProcess(command, 0)
                requested.extend(
                    item for item in kwargs["input"].split("\0") if item
                )
                result = emulate_allowlisted_rsync(command, kwargs, remote_shard)
                self.assertFalse(
                    (Path(command[-1].rstrip("/")) / unmanifested_relative).exists()
                )
                return result

            hub = {
                "remotes": [
                    {
                        "host_id": "mini",
                        "ssh_host": "mini.test",
                        "remote_spool_root": "/remote/spool",
                    }
                ]
            }
            with mock.patch.object(
                fleet.subprocess, "run", side_effect=allowlisted_pull
            ):
                result = fleet.pull_hub_remotes(hub, spool_root)

            self.assertEqual(result["remotes"]["mini"]["status"], "pulled")
            self.assertNotIn(unmanifested_relative, requested)
            self.assertFalse(
                any(
                    path.name == f"{unmanifested_digest}.json"
                    for path in (spool_root / ".incoming").rglob("*.json")
                )
            )

    def test_remote_pull_rejects_manifest_mutation_before_copying_objects(self):
        with safe_temporary_directory() as tmp:
            spool_root = Path(tmp) / "spool"
            remote_shard = Path(tmp) / "remote" / "hosts" / "mini"
            write_archive_object(remote_shard, host_id="mini")
            write_healthy_receipt(remote_shard)
            remote_manifest = remote_shard / "publish-manifest.json"
            changed_manifest = json.loads(remote_manifest.read_text())
            changed_manifest["generated_at"] = "2026-08-28T12:00:00+00:00"
            rsync_calls = 0
            requested: list[str] = []

            def mutate_after_manifest(command, **kwargs):
                nonlocal rsync_calls
                if command[0] == "ssh":
                    return subprocess.CompletedProcess(command, 0)
                rsync_calls += 1
                requested.extend(
                    item for item in kwargs["input"].split("\0") if item
                )
                result = emulate_allowlisted_rsync(command, kwargs, remote_shard)
                if rsync_calls == 1:
                    remote_manifest.write_text(
                        json.dumps(changed_manifest, sort_keys=True) + "\n"
                    )
                return result

            hub = {
                "remotes": [
                    {
                        "host_id": "mini",
                        "ssh_host": "mini.test",
                        "remote_spool_root": "/remote/spool",
                    }
                ]
            }
            with mock.patch.object(
                fleet.subprocess, "run", side_effect=mutate_after_manifest
            ):
                result = fleet.pull_hub_remotes(hub, spool_root)

            self.assertEqual(
                result["remotes"]["mini"]["status"],
                "blocked_integrity_failure",
            )
            self.assertIn("changed during pull", result["remotes"]["mini"]["error"])
            self.assertFalse(any("/objects/" in path for path in requested))
            self.assertFalse((spool_root / "hosts" / "mini").exists())

    def test_remote_pull_rejects_unauthorized_manifest_paths(self):
        with safe_temporary_directory() as tmp:
            spool_root = Path(tmp) / "spool"
            remote_shard = Path(tmp) / "remote" / "hosts" / "mini"
            write_archive_object(remote_shard, host_id="mini")
            write_healthy_receipt(remote_shard)
            manifest_path = remote_shard / "publish-manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["receipt"]["path"] = "receipts/../claude/index.json"
            manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")
            requested: list[str] = []

            def record_allowlist(command, **kwargs):
                if command[0] == "ssh":
                    return subprocess.CompletedProcess(command, 0)
                requested.extend(
                    item for item in kwargs["input"].split("\0") if item
                )
                return emulate_allowlisted_rsync(command, kwargs, remote_shard)

            hub = {
                "remotes": [
                    {
                        "host_id": "mini",
                        "ssh_host": "mini.test",
                        "remote_spool_root": "/remote/spool",
                    }
                ]
            }
            with mock.patch.object(
                fleet.subprocess, "run", side_effect=record_allowlist
            ):
                result = fleet.pull_hub_remotes(hub, spool_root)

            self.assertEqual(
                result["remotes"]["mini"]["status"],
                "blocked_integrity_failure",
            )
            self.assertEqual(requested, ["publish-manifest.json"])
            self.assertFalse((spool_root / "hosts" / "mini").exists())

    def test_remote_pull_rejects_manifest_bound_symlinks_before_objects(self):
        with safe_temporary_directory() as tmp:
            spool_root = Path(tmp) / "spool"
            remote_shard = Path(tmp) / "remote" / "hosts" / "mini"
            write_archive_object(remote_shard, host_id="mini")
            receipt_path = write_healthy_receipt(remote_shard)
            outside_receipts = Path(tmp) / "outside-receipts"
            receipt_path.parent.rename(outside_receipts)
            receipt_path.parent.symlink_to(outside_receipts, target_is_directory=True)
            requested: list[str] = []

            def nofollow_pull(command, **kwargs):
                if command[0] == "ssh":
                    return subprocess.CompletedProcess(
                        command,
                        1
                        if any(str(item).endswith("/receipts") for item in command)
                        else 0,
                    )
                requested.extend(
                    item for item in kwargs["input"].split("\0") if item
                )
                return emulate_allowlisted_rsync(command, kwargs, remote_shard)

            hub = {
                "remotes": [
                    {
                        "host_id": "mini",
                        "ssh_host": "mini.test",
                        "remote_spool_root": "/remote/spool",
                    }
                ]
            }
            with mock.patch.object(
                fleet.subprocess, "run", side_effect=nofollow_pull
            ):
                result = fleet.pull_hub_remotes(hub, spool_root)

            self.assertEqual(
                result["remotes"]["mini"]["status"],
                "blocked_integrity_failure",
            )
            self.assertEqual(requested, ["publish-manifest.json"])
            self.assertFalse((spool_root / "hosts" / "mini").exists())

    def test_remote_pull_reuses_only_a_validated_cached_shard(self):
        with safe_temporary_directory() as tmp:
            spool_root = Path(tmp) / "spool"
            cached_shard = spool_root / "hosts" / "mini"
            write_archive_object(cached_shard, host_id="mini")
            write_healthy_receipt(cached_shard)
            commands = []

            def completed_pull(command, **kwargs):
                commands.append(command)
                if command[0] == "ssh":
                    return subprocess.CompletedProcess(command, 0)
                return emulate_allowlisted_rsync(command, kwargs, cached_shard)

            hub = {
                "remotes": [
                    {
                        "host_id": "mini",
                        "ssh_host": "mini.test",
                        "remote_spool_root": "/remote/spool",
                    }
                ]
            }
            with mock.patch.object(
                fleet.subprocess, "run", side_effect=completed_pull
            ):
                result = fleet.pull_hub_remotes(hub, spool_root)

            self.assertEqual(result["remotes"]["mini"]["status"], "pulled")
            rsync_commands = [
                command for command in commands if command[0] == "rsync"
            ]
            self.assertEqual(len(rsync_commands), 3)
            for rsync_command in rsync_commands:
                self.assertEqual(rsync_command[0:2], ["rsync", "-rtz"])
                self.assertIn("--checksum", rsync_command)
                self.assertIn(f"--link-dest={cached_shard}", rsync_command)

    def test_cached_remote_pull_checksums_equal_size_equal_mtime_files(self):
        with safe_temporary_directory() as tmp:
            spool_root = Path(tmp) / "spool"
            cached_shard = spool_root / "hosts" / "mini"
            write_archive_object(cached_shard, host_id="mini")
            write_healthy_receipt(cached_shard)
            manifest_path = cached_shard / "publish-manifest.json"
            original = manifest_path.read_bytes()
            changed = original.replace(b"healthy-test", b"updated-test", 1)
            self.assertEqual(len(original), len(changed))
            original_times = manifest_path.stat()
            remote_shard = Path(tmp) / "remote" / "hosts" / "mini"
            shutil.copytree(cached_shard, remote_shard)
            remote_manifest = remote_shard / "publish-manifest.json"
            remote_manifest.write_bytes(changed)
            os.utime(
                remote_manifest,
                ns=(original_times.st_atime_ns, original_times.st_mtime_ns),
            )

            commands = []

            def emulate_quick_check(command, **kwargs):
                commands.append(command)
                if command[0] == "ssh":
                    return subprocess.CompletedProcess(command, 0)
                return emulate_allowlisted_rsync(command, kwargs, remote_shard)

            hub = {
                "remotes": [
                    {
                        "host_id": "mini",
                        "ssh_host": "mini.test",
                        "remote_spool_root": "/remote/spool",
                    }
                ]
            }
            with mock.patch.object(
                fleet.subprocess, "run", side_effect=emulate_quick_check
            ):
                result = fleet.pull_hub_remotes(hub, spool_root)

            rsync_commands = [
                command for command in commands if command[0] == "rsync"
            ]
            self.assertTrue(rsync_commands)
            self.assertIn("--checksum", rsync_commands[0])
            self.assertEqual(
                result["remotes"]["mini"]["status"],
                "blocked_integrity_failure",
            )
            self.assertEqual(manifest_path.read_bytes(), original)

    def test_remote_pull_rejects_corrupt_cache_before_rsync(self):
        with safe_temporary_directory() as tmp:
            spool_root = Path(tmp) / "spool"
            cached_shard = spool_root / "hosts" / "mini"
            object_path, _payload = write_archive_object(
                cached_shard, host_id="mini"
            )
            write_healthy_receipt(cached_shard)
            object_path.write_bytes(b"{}\n")
            commands = []

            def record(command, **_kwargs):
                commands.append(command)
                return subprocess.CompletedProcess(command, 0)

            hub = {
                "remotes": [
                    {
                        "host_id": "mini",
                        "ssh_host": "mini.test",
                        "remote_spool_root": "/remote/spool",
                    }
                ]
            }
            with mock.patch.object(fleet.subprocess, "run", side_effect=record):
                result = fleet.pull_hub_remotes(hub, spool_root)

            self.assertEqual(
                result["remotes"]["mini"]["status"],
                "blocked_integrity_failure",
            )
            self.assertEqual([command[0] for command in commands], ["ssh"])

    def test_remote_pull_cannot_overwrite_an_existing_immutable_object(self):
        with safe_temporary_directory() as tmp:
            spool_root = Path(tmp) / "spool"
            existing, original_payload = write_archive_object(
                spool_root / "hosts" / "mini"
            )

            def tampering_rsync(command, **_kwargs):
                if command[0] == "ssh":
                    return subprocess.CompletedProcess(command, 0)
                destination = Path(command[-1].rstrip("/"))
                target = destination / "claude" / "objects" / existing.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"{}\n")
                return subprocess.CompletedProcess(command, 0)

            hub = {
                "remotes": [
                    {
                        "host_id": "mini",
                        "ssh_host": "mini.test",
                        "remote_spool_root": "/remote/spool",
                    }
                ]
            }
            with mock.patch.object(fleet.subprocess, "run", side_effect=tampering_rsync):
                try:
                    fleet.pull_hub_remotes(hub, spool_root)
                except ValueError:
                    pass

            self.assertTrue(
                hashlib.sha256(existing.read_bytes()).digest()
                == hashlib.sha256(original_payload).digest(),
                "remote import changed an existing immutable object",
            )

    def test_archive_lock_serializes_cross_process_import_mutations(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            spool_root = root / "spool"
            marker = root / "child-acquired-lock"
            child_program = f"""
from pathlib import Path
import sys
sys.path.insert(0, {str(REPO)!r})
import fleet_chat_archive as fleet
with fleet.archive_run_lock(Path({str(spool_root)!r})):
    Path({str(marker)!r}).touch()
"""
            process = None
            try:
                with fleet.archive_run_lock(spool_root):
                    process = subprocess.Popen(
                        [sys.executable, "-c", child_program],
                        cwd=REPO,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    with self.assertRaises(subprocess.TimeoutExpired):
                        process.wait(timeout=0.2)
                    self.assertFalse(marker.exists())
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, (stdout, stderr))
                self.assertTrue(marker.is_file())
            finally:
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait()

    def test_google_drive_path_must_be_below_the_current_home(self):
        with safe_temporary_directory() as tmp:
            outside_home = (
                Path(tmp)
                / "other-user"
                / "Library"
                / "CloudStorage"
                / "GoogleDrive-test"
                / "My Drive"
                / "Archive"
            )
            outside_home.mkdir(parents=True)
            self.assertFalse(fleet.is_google_drive_path(outside_home))

    def test_auto_drive_root_requires_exactly_one_live_provider(self):
        with safe_temporary_directory() as tmp:
            fake_home = Path(tmp) / "home"
            cloud_root = fake_home / "Library" / "CloudStorage"
            cloud_root.mkdir(parents=True)
            with mock.patch.object(fleet.Path, "home", return_value=fake_home):
                missing, missing_status = fleet.configured_drive_root("auto")
            self.assertIsNone(missing)
            self.assertEqual(missing_status, "blocked_drive_unavailable")

            first = cloud_root / "GoogleDrive-first@example.com" / "My Drive"
            first.mkdir(parents=True)
            with (
                mock.patch.object(fleet.Path, "home", return_value=fake_home),
                mock.patch.object(fleet, "is_google_drive_path", return_value=True),
            ):
                resolved, status = fleet.configured_drive_root("auto")
            self.assertEqual(status, "auto")
            self.assertEqual(resolved, first / "AI Chat Archive")
            self.assertTrue(resolved.is_dir())

            second = cloud_root / "GoogleDrive-second@example.com" / "My Drive"
            second.mkdir(parents=True)
            with (
                mock.patch.object(fleet.Path, "home", return_value=fake_home),
                mock.patch.object(fleet, "is_google_drive_path", return_value=True),
            ):
                ambiguous, ambiguous_status = fleet.configured_drive_root("auto")
            self.assertIsNone(ambiguous)
            self.assertEqual(ambiguous_status, "blocked_ambiguous_drive_root")
            self.assertFalse((second / "AI Chat Archive").exists())

    def test_auto_drive_root_skips_a_google_shaped_symlink(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            fake_home = root / "home"
            cloud_root = fake_home / "Library" / "CloudStorage"
            cloud_root.mkdir(parents=True)
            escaped = root / "escaped" / "My Drive"
            escaped.mkdir(parents=True)
            (cloud_root / "GoogleDrive-link").symlink_to(
                escaped.parent, target_is_directory=True
            )
            with (
                mock.patch.object(fleet.Path, "home", return_value=fake_home),
                mock.patch.object(fleet, "is_google_drive_path", return_value=True),
            ):
                drive_root, status = fleet.configured_drive_root("auto")
            self.assertIsNone(drive_root)
            self.assertEqual(status, "blocked_drive_unavailable")
            self.assertFalse((escaped / "AI Chat Archive").exists())

    def test_publish_blocks_a_missing_or_empty_source_shard(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            fake_home = root / "home"
            drive_root = (
                fake_home
                / "Library"
                / "CloudStorage"
                / "GoogleDrive-test"
                / "My Drive"
                / "Archive"
            )
            drive_root.mkdir(parents=True)
            spool_root = root / "spool"
            with mock.patch.object(fleet.Path, "home", return_value=fake_home):
                missing = fleet.publish_host_shard(spool_root, drive_root, "missing")
                (spool_root / "hosts" / "empty").mkdir(parents=True)
                empty = fleet.publish_host_shard(spool_root, drive_root, "empty")

            self.assertNotEqual(missing["status"], "published")
            self.assertNotEqual(empty["status"], "published")

    def test_publish_verifies_an_existing_object_against_its_hash(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            spool_root = root / "spool"
            source_object, _ = write_archive_object(
                spool_root / "hosts" / "test-mac"
            )
            drive_root = root / "drive"
            destination = (
                drive_root
                / "hosts"
                / "test-mac"
                / "claude"
                / "objects"
                / source_object.name
            )
            destination.parent.mkdir(parents=True)
            destination.write_bytes(b"{}\n")

            try:
                result = fleet.publish_host_shard(
                    spool_root,
                    drive_root,
                    "test-mac",
                    allow_non_google_drive=True,
                )

            except ValueError:
                result = None

            if result is not None:
                self.assertNotEqual(
                    result["status"],
                    "published",
                    "publication accepted a corrupt existing object",
                )

    def test_unmanifested_shard_validation_checks_each_object_once(self):
        with safe_temporary_directory() as tmp:
            shard = Path(tmp) / "hosts" / "test-mac"
            write_archive_object(shard, host_id="test-mac")
            real_validate = fleet.validate_object_file
            with mock.patch.object(
                fleet, "validate_object_file", wraps=real_validate
            ) as validate:
                fleet.validated_shard_files(
                    shard, "test-mac", require_healthy_receipt=False
                )
            self.assertEqual(validate.call_count, 1)

    def test_local_staging_merge_links_objects_without_duplicate_disk_blocks(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            source_shard = root / "staging" / "hosts" / "test-mac"
            source_object, payload = write_archive_object(
                source_shard, host_id="test-mac"
            )
            destination_parent = root / "spool"

            fleet.merge_host_shard(
                source_shard,
                destination_parent,
                "test-mac",
                require_healthy_receipt=False,
            )

            destination_object = (
                destination_parent
                / "hosts"
                / "test-mac"
                / "claude"
                / "objects"
                / source_object.name
            )
            self.assertEqual(source_object.stat().st_ino, destination_object.stat().st_ino)
            source_object.unlink()
            self.assertEqual(destination_object.read_bytes(), payload)
            fleet.validated_shard_files(
                destination_parent / "hosts" / "test-mac",
                "test-mac",
                require_healthy_receipt=False,
            )

    def test_local_object_link_parent_swap_cannot_escape_spool(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            source_shard = root / "staging" / "hosts" / "test-mac"
            source_object, payload = write_archive_object(
                source_shard, host_id="test-mac"
            )
            destination = (
                root
                / "spool"
                / "hosts"
                / "test-mac"
                / "claude"
                / "objects"
                / source_object.name
            )
            escaped = root / "escaped"
            escaped.mkdir()
            held_target = root / "held-target"
            real_link = fleet.os.link

            def swap_parent_then_link(*args, **kwargs):
                destination.parent.rename(held_target)
                destination.parent.symlink_to(escaped, target_is_directory=True)
                return real_link(*args, **kwargs)

            with mock.patch.object(
                fleet.os, "link", side_effect=swap_parent_then_link
            ):
                self.assertTrue(
                    fleet.link_verified_local_object(source_object, destination)
                )

            self.assertFalse((escaped / source_object.name).exists())
            self.assertEqual((held_target / source_object.name).read_bytes(), payload)

    def test_interrupted_local_link_then_changed_source_retry_quarantines_orphan(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            destination_parent = root / "spool"
            destination_shard = destination_parent / "hosts" / "test-mac"
            write_archive_object(destination_shard, host_id="test-mac")

            first_source_parent = root / "first-source"
            fleet.archive_conversations(
                first_source_parent,
                "test-mac",
                "claude",
                [
                    {
                        "source": "claude-code",
                        "session_id": "interrupted",
                        "messages": [{"role": "user", "content": "first new body"}],
                    }
                ],
            )
            with mock.patch.object(
                fleet, "merge_index_file", side_effect=KeyboardInterrupt
            ), self.assertRaises(KeyboardInterrupt):
                fleet.merge_host_shard(
                    first_source_parent / "hosts" / "test-mac",
                    destination_parent,
                    "test-mac",
                    require_healthy_receipt=False,
                )

            second_source_parent = root / "second-source"
            fleet.archive_conversations(
                second_source_parent,
                "test-mac",
                "claude",
                [
                    {
                        "source": "claude-code",
                        "session_id": "changed-retry",
                        "messages": [{"role": "user", "content": "changed body"}],
                    }
                ],
            )
            result = fleet.merge_host_shard(
                second_source_parent / "hosts" / "test-mac",
                destination_parent,
                "test-mac",
                require_healthy_receipt=False,
            )

            self.assertEqual(result["quarantined_unindexed_objects"], 1)
            fleet.validated_shard_files(
                destination_shard,
                "test-mac",
                require_healthy_receipt=False,
            )
            quarantine_objects = list(
                (destination_parent / "quarantine" / "test-mac").rglob("*.json")
            )
            self.assertEqual(len(quarantine_objects), 1)

    def test_quarantine_parent_swap_cannot_escape_owner_only_spool(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            destination_parent = root / "spool"
            destination_shard = destination_parent / "hosts" / "test-mac"
            orphan, payload = write_archive_object(
                destination_shard, host_id="test-mac"
            )
            (destination_shard / "claude" / "index.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "host_id": "test-mac",
                        "harness": "claude",
                        "conversations": [],
                    }
                )
                + "\n"
            )
            escaped = root / "escaped"
            escaped.mkdir()
            held_target = root / "held-target"
            real_link = fleet.os.link

            def swap_parent_then_link(*args, **kwargs):
                target_roots = list(
                    (destination_parent / "quarantine" / "test-mac").rglob("objects")
                )
                self.assertEqual(len(target_roots), 1)
                target_root = target_roots[0]
                target_root.rename(held_target)
                target_root.symlink_to(escaped, target_is_directory=True)
                return real_link(*args, **kwargs)

            with mock.patch.object(
                fleet.os, "link", side_effect=swap_parent_then_link
            ):
                self.assertEqual(
                    fleet.quarantine_unindexed_objects(
                        destination_parent, "test-mac"
                    ),
                    1,
                )

            self.assertFalse(orphan.exists())
            self.assertFalse((escaped / orphan.name).exists())
            self.assertEqual((held_target / orphan.name).read_bytes(), payload)

    def test_quarantine_never_moves_last_good_manifest_objects(self):
        with safe_temporary_directory() as tmp:
            destination_parent = Path(tmp) / "spool"
            shard = destination_parent / "hosts" / "test-mac"
            manifested_object, _ = write_archive_object(shard, host_id="test-mac")
            write_healthy_receipt(shard)
            (shard / "claude" / "index.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "host_id": "test-mac",
                        "harness": "claude",
                        "conversations": [],
                    }
                )
                + "\n"
            )

            moved = fleet.quarantine_unindexed_objects(
                destination_parent, "test-mac"
            )

            self.assertEqual(moved, 0)
            self.assertTrue(manifested_object.is_file())
            with self.assertRaisesRegex(ValueError, "manifest snapshot mismatch"):
                fleet.validated_shard_files(shard, "test-mac")

    def test_spool_preflight_happens_before_hermes_export(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            checkout = root / "checkout"
            (checkout / ".git").mkdir(parents=True)
            marker = root / "hermes-was-run"
            fake_hermes = root / "hermes"
            fake_hermes.write_text(
                "#!/usr/bin/env python3\n"
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).touch()\n"
                "raise SystemExit(1)\n"
            )
            fake_hermes.chmod(0o755)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "host_id": "test-mac",
                        "spool_root": str(checkout / "spool"),
                        "drive_root": None,
                        "sources": {
                            "hermes_instances": [
                                {"home": str(root), "binary": str(fake_hermes)}
                            ]
                        },
                    }
                )
            )

            try:
                fleet.run_config(configured_args(config_path))
            except (OSError, ValueError, subprocess.SubprocessError):
                pass

            self.assertFalse(
                marker.exists(),
                "Hermes export ran before the spool passed safety preflight",
            )

    def test_failed_run_persists_a_body_free_receipt(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            spool_root = root / "spool"
            synthetic_sensitive_text = "opaque-failure-" + "q" * 30
            fake_hermes = root / "hermes"
            fake_hermes.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                f"sys.stderr.write({synthetic_sensitive_text!r})\n"
                "raise SystemExit(1)\n"
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
                                {"home": str(root), "binary": str(fake_hermes)}
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
            receipts = list(
                (spool_root / "hosts" / "test-mac" / "receipts").glob("*.json")
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(len(receipts), 1, "failed run did not leave one receipt")
            self.assertFalse(
                any(synthetic_sensitive_text in path.read_text() for path in receipts),
                "failure receipt retained command output or body text",
            )

    def test_interrupted_run_cannot_write_a_healthy_manifest_or_cache_state(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "host_id": "test-mac",
                        "spool_root": str(root / "spool"),
                        "drive_root": None,
                        "sources": {"claude_roots": [str(root / "claude")]},
                    }
                )
            )
            with mock.patch.object(
                fleet, "collect_sources", side_effect=KeyboardInterrupt
            ), self.assertRaises(KeyboardInterrupt):
                fleet.run_config(configured_args(config_path))

            shard = root / "spool" / "hosts" / "test-mac"
            receipt_path = next((shard / "receipts").glob("*.json"))
            receipt = json.loads(receipt_path.read_text())
            self.assertEqual(receipt["collection_status"], "failed")
            self.assertEqual(receipt["status"], "failed")
            self.assertFalse((shard / "publish-manifest.json").exists())
            self.assertFalse(
                (root / "spool" / "state" / "test-mac" / "claude.json").exists()
            )

    def test_termination_during_later_harness_restores_prior_manifest_and_state(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            claude_root = root / "claude"
            session = claude_root / "projects" / "sample" / "session.jsonl"
            write_jsonl(
                session,
                [{"type": "user", "message": {"content": "first snapshot"}}],
            )
            config_path = root / "config.json"
            config = {
                "schema_version": 1,
                "host_id": "test-mac",
                "spool_root": str(root / "spool"),
                "drive_root": None,
                "sources": {"claude_roots": [str(claude_root)]},
            }
            config_path.write_text(json.dumps(config))
            with mock.patch("builtins.print"):
                self.assertEqual(fleet.run_config(configured_args(config_path)), 0)

            shard = root / "spool" / "hosts" / "test-mac"
            old_manifest = (shard / "publish-manifest.json").read_bytes()
            old_index = (shard / "claude" / "index.json").read_bytes()
            claude_state_path = (
                root / "spool" / "state" / "test-mac" / "claude.json"
            )
            old_claude_state = claude_state_path.read_bytes()
            with session.open("a") as handle:
                handle.write(
                    json.dumps(
                        {"type": "assistant", "message": {"content": "second snapshot"}}
                    )
                    + "\n"
                )
            config["sources"]["hermes_instances"] = [{"name": "synthetic"}]
            config_path.write_text(json.dumps(config))

            with mock.patch.object(
                fleet,
                "export_hermes_instances",
                side_effect=fleet.TerminationRequested,
            ), mock.patch("builtins.print"), self.assertRaises(
                fleet.TerminationRequested
            ):
                fleet.run_config(configured_args(config_path))

            self.assertEqual(
                (shard / "publish-manifest.json").read_bytes(), old_manifest
            )
            self.assertEqual((shard / "claude" / "index.json").read_bytes(), old_index)
            fleet.validated_shard_files(shard, "test-mac")
            self.assertEqual(claude_state_path.read_bytes(), old_claude_state)
            self.assertFalse(
                (root / "spool" / "state" / "test-mac" / "hermes.json").exists()
            )

    def test_termination_during_first_manifest_write_restores_snapshot_and_retries(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            claude_root = root / "claude"
            session = claude_root / "projects" / "sample" / "session.jsonl"
            write_jsonl(
                session,
                [{"type": "user", "message": {"content": "first snapshot"}}],
            )
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "host_id": "test-mac",
                        "spool_root": str(root / "spool"),
                        "drive_root": None,
                        "sources": {"claude_roots": [str(claude_root)]},
                    }
                )
            )
            with mock.patch("builtins.print"):
                self.assertEqual(fleet.run_config(configured_args(config_path)), 0)

            shard = root / "spool" / "hosts" / "test-mac"
            manifest_path = shard / "publish-manifest.json"
            index_path = shard / "claude" / "index.json"
            state_path = root / "spool" / "state" / "test-mac" / "claude.json"
            old_manifest = manifest_path.read_bytes()
            old_index = index_path.read_bytes()
            old_state = state_path.read_bytes()
            with session.open("a") as handle:
                handle.write(
                    json.dumps(
                        {"type": "assistant", "message": {"content": "second snapshot"}}
                    )
                    + "\n"
                )

            with mock.patch.object(
                fleet,
                "write_publish_manifest",
                side_effect=fleet.TerminationRequested,
            ), mock.patch("builtins.print"), self.assertRaises(
                fleet.TerminationRequested
            ):
                fleet.run_config(configured_args(config_path))

            self.assertEqual(manifest_path.read_bytes(), old_manifest)
            self.assertEqual(index_path.read_bytes(), old_index)
            self.assertEqual(state_path.read_bytes(), old_state)
            fleet.validated_shard_files(shard, "test-mac")

            with mock.patch("builtins.print"):
                self.assertEqual(fleet.run_config(configured_args(config_path)), 0)
            current_manifest = json.loads(manifest_path.read_text())
            current_receipt = json.loads(
                (shard / current_manifest["receipt"]["path"]).read_text()
            )
            self.assertEqual(
                current_receipt["harnesses"]["claude"]["quality"]["processed_files"],
                1,
            )

    def test_termination_during_first_receipt_write_restores_prior_transaction(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            claude_root = root / "claude"
            session = claude_root / "projects" / "sample" / "session.jsonl"
            write_jsonl(
                session,
                [{"type": "user", "message": {"content": "first snapshot"}}],
            )
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "host_id": "test-mac",
                        "spool_root": str(root / "spool"),
                        "drive_root": None,
                        "sources": {"claude_roots": [str(claude_root)]},
                    }
                )
            )
            with mock.patch("builtins.print"):
                self.assertEqual(fleet.run_config(configured_args(config_path)), 0)
            shard = root / "spool" / "hosts" / "test-mac"
            manifest_path = shard / "publish-manifest.json"
            index_path = shard / "claude" / "index.json"
            state_path = root / "spool" / "state" / "test-mac" / "claude.json"
            before = (
                manifest_path.read_bytes(),
                index_path.read_bytes(),
                state_path.read_bytes(),
            )
            before_object_names = sorted(
                path.name for path in (shard / "claude" / "objects").glob("*.json")
            )
            with session.open("a") as handle:
                handle.write(
                    json.dumps(
                        {"type": "assistant", "message": {"content": "changed"}}
                    )
                    + "\n"
                )
            real_atomic_write_json = fleet.atomic_write_json

            def terminate_receipt(path, value):
                if Path(path).parent.name == "receipts":
                    raise fleet.TerminationRequested
                return real_atomic_write_json(path, value)

            with mock.patch.object(
                fleet,
                "atomic_write_json",
                side_effect=terminate_receipt,
            ), mock.patch("builtins.print"), self.assertRaises(
                fleet.TerminationRequested
            ):
                fleet.run_config(configured_args(config_path))

            self.assertEqual(
                (
                    manifest_path.read_bytes(),
                    index_path.read_bytes(),
                    state_path.read_bytes(),
                ),
                before,
            )
            self.assertEqual(
                sorted(
                    path.name
                    for path in (shard / "claude" / "objects").glob("*.json")
                ),
                before_object_names,
            )
            fleet.validated_shard_files(shard, "test-mac")

    def test_failed_collection_rolls_back_successful_harness_changes(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            claude_root = root / "claude"
            session = claude_root / "projects" / "sample" / "session.jsonl"
            write_jsonl(
                session,
                [{"type": "user", "message": {"content": "first snapshot"}}],
            )
            config_path = root / "config.json"
            config = {
                "schema_version": 1,
                "host_id": "test-mac",
                "spool_root": str(root / "spool"),
                "drive_root": None,
                "sources": {"claude_roots": [str(claude_root)]},
            }
            config_path.write_text(json.dumps(config))
            with mock.patch("builtins.print"):
                self.assertEqual(fleet.run_config(configured_args(config_path)), 0)

            shard = root / "spool" / "hosts" / "test-mac"
            manifest_path = shard / "publish-manifest.json"
            index_path = shard / "claude" / "index.json"
            state_path = root / "spool" / "state" / "test-mac" / "claude.json"
            before = (
                manifest_path.read_bytes(),
                index_path.read_bytes(),
                state_path.read_bytes(),
                sorted(
                    path.name
                    for path in (shard / "claude" / "objects").glob("*.json")
                ),
            )
            with session.open("a") as handle:
                handle.write(
                    json.dumps(
                        {"type": "assistant", "message": {"content": "changed"}}
                    )
                    + "\n"
                )
            config["sources"]["openclaw_roots"] = [str(root / "missing-openclaw")]
            config_path.write_text(json.dumps(config))
            with mock.patch("builtins.print"):
                self.assertNotEqual(fleet.run_config(configured_args(config_path)), 0)

            self.assertEqual(
                (
                    manifest_path.read_bytes(),
                    index_path.read_bytes(),
                    state_path.read_bytes(),
                    sorted(
                        path.name
                        for path in (shard / "claude" / "objects").glob("*.json")
                    ),
                ),
                before,
            )
            fleet.validated_shard_files(shard, "test-mac")

    def test_first_added_codex_harness_rollback_removes_only_its_live_index(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            claude_root = root / "claude"
            write_jsonl(
                claude_root / "projects" / "sample" / "session.jsonl",
                [{"type": "user", "message": {"content": "healthy claude"}}],
            )
            config_path = root / "config.json"
            config = {
                "schema_version": 1,
                "host_id": "test-mac",
                "spool_root": str(root / "spool"),
                "drive_root": None,
                "sources": {"claude_roots": [str(claude_root)]},
            }
            config_path.write_text(json.dumps(config))
            with mock.patch("builtins.print"):
                self.assertEqual(fleet.run_config(configured_args(config_path)), 0)
            shard = root / "spool" / "hosts" / "test-mac"
            manifest_path = shard / "publish-manifest.json"
            claude_index = shard / "claude" / "index.json"
            claude_state = root / "spool" / "state" / "test-mac" / "claude.json"
            before = (
                manifest_path.read_bytes(),
                claude_index.read_bytes(),
                claude_state.read_bytes(),
            )

            codex_root = root / "codex"
            write_jsonl(
                codex_root
                / "sessions"
                / "2026"
                / "08"
                / "28"
                / "rollout-new-harness.jsonl",
                [
                    {"type": "session_meta", "payload": {"id": "new-harness"}},
                    {
                        "type": "event_msg",
                        "payload": {"type": "user_message", "message": "new body"},
                    },
                ],
            )
            config["sources"]["codex_roots"] = [str(codex_root)]
            config_path.write_text(json.dumps(config))
            with mock.patch.object(
                fleet,
                "write_publish_manifest",
                side_effect=fleet.TerminationRequested,
            ), mock.patch("builtins.print"), self.assertRaises(
                fleet.TerminationRequested
            ):
                fleet.run_config(configured_args(config_path))

            codex_harness = shard / "codex"
            codex_state = root / "spool" / "state" / "test-mac" / "codex.json"
            self.assertEqual(
                (
                    manifest_path.read_bytes(),
                    claude_index.read_bytes(),
                    claude_state.read_bytes(),
                ),
                before,
            )
            self.assertFalse((codex_harness / "index.json").exists())
            self.assertFalse(codex_state.exists())
            self.assertFalse(any((codex_harness / "objects").glob("*.json")))
            fleet.validated_shard_files(shard, "test-mac")

            del config["sources"]["codex_roots"]
            config_path.write_text(json.dumps(config))
            with mock.patch("builtins.print"):
                self.assertEqual(fleet.run_config(configured_args(config_path)), 0)
            manifest = json.loads(manifest_path.read_text())
            self.assertEqual(set(manifest["harnesses"]), {"claude"})
            fleet.validated_shard_files(shard, "test-mac")

    def test_manifest_validation_failure_restores_prior_snapshot_and_state(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            claude_root = root / "claude"
            session = claude_root / "projects" / "sample" / "session.jsonl"
            write_jsonl(
                session,
                [{"type": "user", "message": {"content": "first snapshot"}}],
            )
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "host_id": "test-mac",
                        "spool_root": str(root / "spool"),
                        "drive_root": None,
                        "sources": {"claude_roots": [str(claude_root)]},
                    }
                )
            )
            with mock.patch("builtins.print"):
                self.assertEqual(fleet.run_config(configured_args(config_path)), 0)
            shard = root / "spool" / "hosts" / "test-mac"
            manifest_path = shard / "publish-manifest.json"
            index_path = shard / "claude" / "index.json"
            state_path = root / "spool" / "state" / "test-mac" / "claude.json"
            before = (
                manifest_path.read_bytes(),
                index_path.read_bytes(),
                state_path.read_bytes(),
            )
            with session.open("a") as handle:
                handle.write(
                    json.dumps(
                        {"type": "assistant", "message": {"content": "changed"}}
                    )
                    + "\n"
                )

            with mock.patch.object(
                fleet,
                "write_publish_manifest",
                side_effect=ValueError("synthetic manifest validation failure"),
            ), mock.patch("builtins.print"):
                self.assertNotEqual(fleet.run_config(configured_args(config_path)), 0)

            self.assertEqual(
                (
                    manifest_path.read_bytes(),
                    index_path.read_bytes(),
                    state_path.read_bytes(),
                ),
                before,
            )
            fleet.validated_shard_files(shard, "test-mac")

    def test_first_run_manifest_cancellation_leaves_no_committed_state(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            claude_root = root / "claude"
            write_jsonl(
                claude_root / "projects" / "sample" / "session.jsonl",
                [{"type": "user", "message": {"content": "first snapshot"}}],
            )
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "host_id": "test-mac",
                        "spool_root": str(root / "spool"),
                        "drive_root": None,
                        "sources": {"claude_roots": [str(claude_root)]},
                    }
                )
            )
            with mock.patch.object(
                fleet,
                "write_publish_manifest",
                side_effect=fleet.TerminationRequested,
            ), mock.patch("builtins.print"), self.assertRaises(
                fleet.TerminationRequested
            ):
                fleet.run_config(configured_args(config_path))

            shard = root / "spool" / "hosts" / "test-mac"
            state_path = root / "spool" / "state" / "test-mac" / "claude.json"
            self.assertFalse((shard / "publish-manifest.json").exists())
            self.assertFalse(state_path.exists())
            self.assertFalse(any((shard / "claude" / "objects").glob("*.json")))

            with mock.patch("builtins.print"):
                self.assertEqual(fleet.run_config(configured_args(config_path)), 0)
            manifest = json.loads((shard / "publish-manifest.json").read_text())
            receipt = json.loads((shard / manifest["receipt"]["path"]).read_text())
            self.assertEqual(
                receipt["harnesses"]["claude"]["quality"]["processed_files"], 1
            )

    def test_source_and_object_byte_guards_fail_before_parsing(self):
        self.assertGreaterEqual(fleet.MAX_SOURCE_BYTES, 566_191_187)
        self.assertGreaterEqual(fleet.MAX_SOURCE_BYTES, 1_249_061_185)
        self.assertGreaterEqual(fleet.MAX_OBJECT_BYTES, 552_957_718)
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            codex = root / "codex"
            oversized_source = (
                codex
                / "sessions"
                / "2026"
                / "08"
                / "28"
                / "rollout-oversized.jsonl"
            )
            oversized_source.parent.mkdir(parents=True)
            with oversized_source.open("wb") as handle:
                handle.truncate(fleet.MAX_SOURCE_BYTES + 1)

            with mock.patch.object(
                fleet, "extract_codex_session"
            ) as extract_session, self.assertRaisesRegex(
                ValueError, "source exceeds maximum"
            ):
                fleet.collect_sources(
                    root / "spool", "test-mac", codex_roots=[codex]
                )
            extract_session.assert_not_called()

            oversized_object = (
                root / "objects" / ("a" * 64 + ".json")
            )
            oversized_object.parent.mkdir()
            with oversized_object.open("wb") as handle:
                handle.truncate(fleet.MAX_OBJECT_BYTES + 1)
            with mock.patch.object(fleet.json, "load") as parse_json, self.assertRaisesRegex(
                ValueError, "object exceeds maximum"
            ):
                fleet.validate_object_file(oversized_object)
            parse_json.assert_not_called()

            changed_source = codex / "archived_sessions" / "rollout-changed.jsonl"
            write_jsonl(
                changed_source,
                [{"type": "session_meta", "payload": {"id": "changed"}}],
            )
            with self.assertRaisesRegex(ValueError, "source changed before extraction"):
                fleet.extract_codex_session(
                    changed_source,
                    expected_source_sha256="0" * 64,
                    max_source_bytes=fleet.MAX_SOURCE_BYTES,
                )

    def test_all_jsonl_extractors_reject_append_on_the_open_descriptor(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            cases = [
                (
                    "claude",
                    claude_extractor,
                    claude_extractor.extract_claude_session,
                    [{"type": "user", "message": {"content": "claude-before"}}],
                    {"type": "assistant", "message": {"content": "claude-after"}},
                ),
                (
                    "openclaw",
                    openclaw_extractor,
                    openclaw_extractor.extract_openclaw_session,
                    [
                        {"type": "session", "version": 3, "id": "openclaw-session"},
                        {
                            "type": "message",
                            "message": {"role": "user", "content": "openclaw-before"},
                        },
                    ],
                    {
                        "type": "message",
                        "message": {"role": "assistant", "content": "openclaw-after"},
                    },
                ),
                (
                    "hermes",
                    hermes_extractor,
                    hermes_extractor.extract_hermes_export,
                    [
                        {
                            "id": "hermes-before",
                            "messages": [{"role": "user", "content": "before"}],
                        }
                    ],
                    {
                        "id": "hermes-after",
                        "messages": [{"role": "assistant", "content": "after"}],
                    },
                ),
            ]
            for name, module, extractor, initial_rows, appended_row in cases:
                with self.subTest(extractor=name):
                    source = root / f"{name}.jsonl"
                    write_jsonl(source, initial_rows)
                    fingerprint = fleet.source_fingerprint(source)
                    real_open = module._open_regular_jsonl

                    def append_after_open(path, **kwargs):
                        opened = real_open(path, **kwargs)
                        with Path(path).open("a") as handle:
                            handle.write(json.dumps(appended_row) + "\n")
                        return opened

                    with mock.patch.object(
                        module,
                        "_open_regular_jsonl",
                        side_effect=append_after_open,
                    ), self.assertRaisesRegex(
                        ValueError, "source changed during extraction"
                    ) as raised:
                        extractor(
                            source,
                            max_source_bytes=fleet.MAX_SOURCE_BYTES,
                            expected_fingerprint=fingerprint,
                        )
                    self.assertNotIn("after", str(raised.exception))

    def test_object_growth_after_json_load_is_rejected(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            conversation = {
                "schema_version": 1,
                "source": "claude-code",
                "session_id": "object-race",
                "messages": [{"role": "user", "content": "safe body"}],
            }
            canonical = fleet.canonical_json(conversation)
            digest = hashlib.sha256(canonical).hexdigest()
            object_path = root / f"{digest}.json"
            object_path.write_bytes(canonical + b"\n")
            real_json_load = fleet.json.load

            def grow_after_load(handle):
                value = real_json_load(handle)
                with object_path.open("r+b") as writer:
                    writer.truncate(fleet.MAX_OBJECT_BYTES + 1)
                return value

            with mock.patch.object(
                fleet.json,
                "load",
                side_effect=grow_after_load,
            ), self.assertRaisesRegex(
                ValueError, "object changed while parsing"
            ) as raised:
                fleet.validate_object_file(object_path)
            self.assertNotIn("safe body", str(raised.exception))

    def test_cached_remote_shard_publishes_while_remote_is_unreachable(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            spool_root = root / "spool"
            cached_object, cached_payload = write_archive_object(
                spool_root / "hosts" / "mini"
            )
            write_healthy_receipt(spool_root / "hosts" / "mini")
            fake_home = root / "home"
            drive_root = (
                fake_home
                / "Library"
                / "CloudStorage"
                / "GoogleDrive-test"
                / "My Drive"
                / "Archive"
            )
            drive_root.mkdir(parents=True)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "host_id": "studio",
                        "spool_root": str(spool_root),
                        "drive_root": str(drive_root),
                        "sources": {},
                        "hub": {
                            "remotes": [
                                {
                                    "host_id": "mini",
                                    "ssh_host": "mini.test",
                                    "remote_spool_root": "/remote/spool",
                                }
                            ]
                        },
                    }
                )
            )
            failure = subprocess.CalledProcessError(255, ["rsync"])
            with mock.patch.object(fleet.Path, "home", return_value=fake_home), mock.patch.object(
                fleet, "is_google_drive_path", return_value=True
            ), mock.patch.object(
                fleet.subprocess, "run", side_effect=failure
            ), mock.patch("builtins.print"):
                fleet.run_config(configured_args(config_path))

            published = (
                drive_root
                / "hosts"
                / "mini"
                / "claude"
                / "objects"
                / cached_object.name
            )
            self.assertTrue(
                published.is_file(),
                "cached remote shard was skipped because the remote was unreachable",
            )
            self.assertTrue(
                hashlib.sha256(published.read_bytes()).digest()
                == hashlib.sha256(cached_payload).digest(),
                "published cached object did not match the verified spool object",
            )


if __name__ == "__main__":
    unittest.main()
