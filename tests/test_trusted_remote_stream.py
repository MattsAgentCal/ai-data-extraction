import hashlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
CLI = REPO / "fleet_chat_archive.py"
sys.path.insert(0, str(REPO))

import fleet_chat_archive as fleet  # noqa: E402


TEST_RUN_ID = "20260828T000000.000000Z-cafebabe"
CODEX_RUN_ID = "20260828T000001.000000Z-cafebabe"
FALLBACK_RUN_ID = "20260828T000002.000000Z-cafebabe"


def safe_temporary_directory():
    return tempfile.TemporaryDirectory(prefix="trusted-stream-", dir=Path.home())


def make_shard(
    spool: Path, host_id: str = "mini", session_id: str = "session-1"
) -> tuple[Path, str, bytes]:
    shard = spool / "hosts" / host_id
    conversation = {
        "archive_schema_version": 2,
        "source": "claude-code",
        "session_id": session_id,
        "messages": [{"role": "user", "content": "authorized body"}],
        "project_path": None,
        "project_name": None,
        "source_file": "/synthetic/session.jsonl",
    }
    canonical = fleet.canonical_json(conversation)
    digest = hashlib.sha256(canonical).hexdigest()
    object_path = shard / "claude" / "objects" / f"{digest}.json"
    object_path.parent.mkdir(parents=True)
    object_payload = canonical + b"\n"
    object_path.write_bytes(object_payload)
    fleet.atomic_write_json(
        shard / "claude" / "index.json",
        {
            "schema_version": 1,
            "host_id": host_id,
            "harness": "claude",
            "conversations": [
                {
                    "object_sha256": digest,
                    "session_id": session_id,
                    "source": "claude-code",
                }
            ],
        },
    )
    receipt_path = shard / "receipts" / f"{TEST_RUN_ID}.json"
    receipt = {
        "schema_version": 1,
        "extractor_sha256": "a" * 64,
        "config_sha256": "c" * 64,
        "run_id": TEST_RUN_ID,
        "collected_at": "2026-08-28T00:00:00+00:00",
        "host_id": host_id,
        "collection_status": "completed",
        "status": "completed",
        "harnesses": {
            "claude": {
                "status": "collected",
                "conversations": 1,
                "new_objects": 1,
                "publishable": True,
                "redactions": 0,
                "index_conversations": 1,
                "quality": {
                    "status": "complete",
                    "discovered_lines": 1,
                    "parsed_lines": 1,
                    "failed_lines": 0,
                    "recognized_lines": 1,
                    "discovered_files": 1,
                    "processed_files": 1,
                    "skipped_unchanged_files": 0,
                },
            }
        },
        "hub": {"remotes": {}},
        "publication": {"status": "blocked_no_drive_root", "files_copied": 0},
        "errors": [],
        "receipt_path": str(receipt_path),
    }
    fleet.atomic_write_json(receipt_path, receipt)
    fleet.write_publish_manifest(shard, receipt_path, receipt, "c" * 64)
    return shard, digest, object_payload


def make_superset_shard(spool: Path, host_id: str = "mini") -> Path:
    shard, _digest, _payload = make_shard(spool, host_id)
    fleet.archive_conversations(
        spool,
        host_id,
        "claude",
        [
            {
                "archive_schema_version": 2,
                "source": "claude-code",
                "session_id": "session-2",
                "messages": [{"role": "user", "content": "second authorized body"}],
                "project_path": None,
                "project_name": None,
                "source_file": "/synthetic/session-2.jsonl",
            }
        ],
    )
    receipt_path = shard / "receipts" / f"{TEST_RUN_ID}.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["harnesses"]["claude"]["conversations"] = 2
    receipt["harnesses"]["claude"]["new_objects"] = 2
    receipt["harnesses"]["claude"]["index_conversations"] = 2
    fleet.atomic_write_json(receipt_path, receipt)
    fleet.write_publish_manifest(shard, receipt_path, receipt, "c" * 64)
    return shard


def tree_snapshot(root: Path) -> dict[str, tuple]:
    snapshot = {}
    for path in [root, *sorted(root.rglob("*"))]:
        relative = "." if path == root else path.relative_to(root).as_posix()
        metadata = path.lstat()
        if path.is_dir():
            snapshot[relative] = ("directory", metadata.st_mode & 0o777)
        elif path.is_file():
            snapshot[relative] = (
                "file",
                metadata.st_mode & 0o777,
                path.read_bytes(),
            )
        else:
            snapshot[relative] = ("other", metadata.st_mode)
    return snapshot


def replace_only_object(shard: Path, value: dict, *, old_digest: str) -> str:
    canonical = fleet.canonical_json(value)
    digest = hashlib.sha256(canonical).hexdigest()
    old_path = shard / "claude" / "objects" / f"{old_digest}.json"
    if old_path.exists():
        old_path.unlink()
    (shard / "claude" / "objects" / f"{digest}.json").write_bytes(canonical + b"\n")
    index_path = shard / "claude" / "index.json"
    index = json.loads(index_path.read_text())
    index["conversations"][0].update(
        {
            "object_sha256": digest,
            "session_id": value.get("session_id"),
            "source": value.get("source"),
        }
    )
    fleet.atomic_write_json(index_path, index)
    manifest_path = shard / "publish-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["harnesses"]["claude"] = {
        "index_sha256": fleet.file_sha256(index_path),
        "object_sha256": [digest],
    }
    fleet.atomic_write_json(manifest_path, manifest)
    return digest


def make_codex_shard(
    spool: Path, *, session_id: str, installation: str
) -> tuple[Path, str]:
    conversation = {
        "archive_schema_version": 2,
        "source": "codex",
        "session_id": session_id,
        "messages": [{"role": "user", "content": "codex body"}],
        "cwd": "/synthetic/project",
        "session_file": "/synthetic/rollout.jsonl",
        "timestamp": "2026-08-28T00:00:00Z",
        "installation": installation,
        "_archive_source_sha256": "d" * 64,
    }
    fleet.archive_conversations(spool, "mini", "codex", [conversation])
    shard = spool / "hosts" / "mini"
    receipt_path = shard / "receipts" / f"{CODEX_RUN_ID}.json"
    receipt = {
        "schema_version": 1,
        "extractor_sha256": "a" * 64,
        "config_sha256": "c" * 64,
        "run_id": CODEX_RUN_ID,
        "collected_at": "2026-08-28T00:00:00+00:00",
        "host_id": "mini",
        "collection_status": "completed",
        "status": "completed",
        "harnesses": {"codex": {"status": "collected"}},
        "hub": {"remotes": {}},
        "publication": {"status": "blocked_no_drive_root", "files_copied": 0},
        "errors": [],
        "receipt_path": str(receipt_path),
    }
    fleet.atomic_write_json(receipt_path, receipt)
    fleet.write_publish_manifest(shard, receipt_path, receipt, "c" * 64)
    digest = json.loads((shard / "codex" / "index.json").read_text())[
        "conversations"
    ][0]["object_sha256"]
    return shard, digest


def frames(payload: bytes) -> list[tuple[str, bytes]]:
    stream = io.BytesIO(payload)
    if stream.read(len(fleet.STREAM_MAGIC)) != fleet.STREAM_MAGIC:
        raise AssertionError("missing stream magic")
    result = []
    while True:
        header = stream.read(fleet.STREAM_FRAME_HEADER.size)
        path_length, payload_length = fleet.STREAM_FRAME_HEADER.unpack(header)
        if path_length == 0:
            break
        path = stream.read(path_length).decode()
        result.append((path, stream.read(payload_length)))
    if stream.read():
        raise AssertionError("trailing stream bytes")
    return result


def one_frame(path: str, payload: bytes) -> bytes:
    encoded = path.encode()
    return (
        fleet.STREAM_FRAME_HEADER.pack(len(encoded), len(payload))
        + encoded
        + payload
    )


class TrustedRemoteStreamTests(unittest.TestCase):
    def test_stream_holds_archive_run_lock_through_preflight_and_emission(self):
        with safe_temporary_directory() as tmp:
            spool = Path(tmp) / "spool"
            make_shard(spool)
            locked = False

            @fleet.contextlib.contextmanager
            def tracked_lock(_spool_root):
                nonlocal locked
                self.assertFalse(locked)
                locked = True
                try:
                    yield
                finally:
                    locked = False

            class LockCheckingOutput(io.BytesIO):
                def write(self, payload):
                    self.assert_locked()
                    return super().write(payload)

                def assert_locked(self):
                    if not locked:
                        raise AssertionError("stream output escaped archive run lock")

            output = LockCheckingOutput()

            def after_validate(*_args):
                self.assertTrue(locked)

            with mock.patch.object(fleet, "archive_run_lock", tracked_lock):
                fleet.stream_shard_to(
                    output, spool, "mini", after_validate=after_validate
                )

            self.assertFalse(locked)
            self.assertGreater(len(output.getvalue()), len(fleet.STREAM_MAGIC))

    def test_fresh_merge_cancellation_leaves_no_partial_and_retry_succeeds(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            source = make_shard(root / "source")[0]
            destination = root / "destination"
            real_copy = fleet.copy_verified_file

            def cancel_after_manifest(source_path, destination_path, *, immutable):
                changed = real_copy(source_path, destination_path, immutable=immutable)
                if source_path.name == "publish-manifest.json":
                    raise fleet.TerminationRequested
                return changed

            with mock.patch.object(
                fleet, "copy_verified_file", side_effect=cancel_after_manifest
            ), self.assertRaises(fleet.TerminationRequested):
                fleet.merge_host_shard(source, destination, "mini")

            final_shard = destination / "hosts" / "mini"
            self.assertFalse(final_shard.exists())
            result = fleet.merge_host_shard(source, destination, "mini")
            self.assertEqual(result["status"], "published")
            fleet.validated_shard_files(final_shard, "mini")

    def test_merge_cancellation_preserves_exact_last_good_and_retry_succeeds(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            destination = root / "destination"
            final_shard = make_shard(destination)[0]
            source = make_superset_shard(root / "source")
            prior_tree = tree_snapshot(final_shard)
            real_copy = fleet.copy_verified_file

            def cancel_after_manifest(source_path, destination_path, *, immutable):
                changed = real_copy(source_path, destination_path, immutable=immutable)
                if source_path.name == "publish-manifest.json":
                    raise KeyboardInterrupt
                return changed

            with mock.patch.object(
                fleet, "copy_verified_file", side_effect=cancel_after_manifest
            ), self.assertRaises(KeyboardInterrupt):
                fleet.merge_host_shard(source, destination, "mini")

            self.assertEqual(tree_snapshot(final_shard), prior_tree)
            result = fleet.merge_host_shard(source, destination, "mini")
            self.assertEqual(result["status"], "published")
            manifest = json.loads((final_shard / "publish-manifest.json").read_text())
            self.assertEqual(
                len(manifest["harnesses"]["claude"]["object_sha256"]), 2
            )

    def test_rollback_context_blocks_and_restores_sigint_and_sigterm(self):
        if not hasattr(signal, "pthread_sigmask"):
            self.skipTest("pthread signal masks are unavailable")
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        try:
            with fleet.defer_termination_signals_during_rollback():
                current = signal.pthread_sigmask(signal.SIG_BLOCK, set())
                self.assertIn(signal.SIGINT, current)
                self.assertIn(signal.SIGTERM, current)
            restored = signal.pthread_sigmask(signal.SIG_BLOCK, set())
            self.assertEqual(restored, previous)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)

    def test_failed_metadata_restore_keeps_new_immutable_files(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            destination = root / "destination"
            final_shard = destination / "hosts" / "mini"
            fleet.archive_conversations(
                destination,
                "mini",
                "claude",
                [{
                    "archive_schema_version": 2,
                    "source": "claude-code",
                    "session_id": "original",
                    "messages": [{"role": "user", "content": "original"}],
                    "project_path": None,
                    "project_name": None,
                    "source_file": "/synthetic/original.jsonl",
                }],
            )
            prior_index = (final_shard / "claude" / "index.json").read_bytes()
            source_parent = root / "source"
            fleet.archive_conversations(
                source_parent,
                "mini",
                "claude",
                [{
                    "archive_schema_version": 2,
                    "source": "claude-code",
                    "session_id": "new",
                    "messages": [{"role": "user", "content": "new"}],
                    "project_path": None,
                    "project_name": None,
                    "source_file": "/synthetic/new.jsonl",
                }],
            )
            source = source_parent / "hosts" / "mini"
            new_digest = json.loads((source / "claude" / "index.json").read_text())[
                "conversations"
            ][0]["object_sha256"]
            real_merge_index = fleet.merge_index_file
            real_atomic_write = fleet.atomic_write_bytes

            def merge_then_fail(source_path, destination_path):
                real_merge_index(source_path, destination_path)
                raise ValueError("initiating merge failure")

            def fail_original_restore(path, payload):
                if path.name == "index.json" and payload == prior_index:
                    raise KeyboardInterrupt
                return real_atomic_write(path, payload)

            with mock.patch.object(
                fleet, "merge_index_file", side_effect=merge_then_fail
            ), mock.patch.object(
                fleet, "atomic_write_bytes", side_effect=fail_original_restore
            ), self.assertRaisesRegex(ValueError, "initiating merge failure"):
                fleet.merge_host_shard(
                    source,
                    destination,
                    "mini",
                    require_healthy_receipt=False,
                )

            self.assertTrue(
                (final_shard / "claude" / "objects" / f"{new_digest}.json").is_file()
            )

    def test_cancellation_during_rollback_preserves_original_and_exact_tree(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            destination = root / "destination"
            fleet.archive_conversations(
                destination,
                "mini",
                "claude",
                [{
                    "archive_schema_version": 2,
                    "source": "claude-code",
                    "session_id": "original",
                    "messages": [{"role": "user", "content": "original"}],
                    "project_path": None,
                    "project_name": None,
                    "source_file": "/synthetic/original.jsonl",
                }],
            )
            final_shard = destination / "hosts" / "mini"
            prior_tree = tree_snapshot(final_shard)
            source_parent = root / "source"
            fleet.archive_conversations(
                source_parent,
                "mini",
                "claude",
                [{
                    "archive_schema_version": 2,
                    "source": "claude-code",
                    "session_id": "new",
                    "messages": [{"role": "user", "content": "new"}],
                    "project_path": None,
                    "project_name": None,
                    "source_file": "/synthetic/new.jsonl",
                }],
            )
            source = source_parent / "hosts" / "mini"
            real_merge_index = fleet.merge_index_file
            real_atomic_write = fleet.atomic_write_bytes
            rollback_phase = False
            interrupted_rollback = False

            def merge_then_interrupt(source_path, destination_path):
                nonlocal rollback_phase
                real_merge_index(source_path, destination_path)
                rollback_phase = True
                raise KeyboardInterrupt

            def cancel_during_restore(path, payload):
                nonlocal interrupted_rollback
                real_atomic_write(path, payload)
                if rollback_phase and not interrupted_rollback:
                    interrupted_rollback = True
                    raise fleet.TerminationRequested

            with mock.patch.object(
                fleet, "merge_index_file", side_effect=merge_then_interrupt
            ), mock.patch.object(
                fleet, "atomic_write_bytes", side_effect=cancel_during_restore
            ), self.assertRaises(KeyboardInterrupt):
                fleet.merge_host_shard(
                    source,
                    destination,
                    "mini",
                    require_healthy_receipt=False,
                )

            self.assertTrue(interrupted_rollback)
            self.assertEqual(tree_snapshot(final_shard), prior_tree)
            result = fleet.merge_host_shard(
                source,
                destination,
                "mini",
                require_healthy_receipt=False,
            )
            self.assertEqual(result["status"], "published")

    def test_actual_sigint_before_rollback_write_is_deferred_until_restored(self):
        if not hasattr(signal, "pthread_sigmask"):
            self.skipTest("pthread signal masks are unavailable")
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            destination = root / "destination"
            fleet.archive_conversations(
                destination,
                "mini",
                "claude",
                [{
                    "archive_schema_version": 2,
                    "source": "claude-code",
                    "session_id": "original",
                    "messages": [{"role": "user", "content": "original"}],
                    "project_path": None,
                    "project_name": None,
                    "source_file": "/synthetic/original.jsonl",
                }],
            )
            final_shard = destination / "hosts" / "mini"
            prior_tree = tree_snapshot(final_shard)
            source_parent = root / "source"
            fleet.archive_conversations(
                source_parent,
                "mini",
                "claude",
                [{
                    "archive_schema_version": 2,
                    "source": "claude-code",
                    "session_id": "new",
                    "messages": [{"role": "user", "content": "new"}],
                    "project_path": None,
                    "project_name": None,
                    "source_file": "/synthetic/new.jsonl",
                }],
            )
            source = source_parent / "hosts" / "mini"
            real_merge_index = fleet.merge_index_file
            real_atomic_write = fleet.atomic_write_bytes
            rollback_phase = False
            sent_sigint = False

            def merge_then_fail(source_path, destination_path):
                nonlocal rollback_phase
                real_merge_index(source_path, destination_path)
                rollback_phase = True
                raise ValueError("initiating merge failure")

            def signal_before_restore(path, payload):
                nonlocal sent_sigint
                if rollback_phase and not sent_sigint:
                    sent_sigint = True
                    os.kill(os.getpid(), signal.SIGINT)
                    self.assertIn(signal.SIGINT, signal.sigpending())
                real_atomic_write(path, payload)

            with mock.patch.object(
                fleet, "merge_index_file", side_effect=merge_then_fail
            ), mock.patch.object(
                fleet, "atomic_write_bytes", side_effect=signal_before_restore
            ), self.assertRaisesRegex(ValueError, "initiating merge failure"):
                fleet.merge_host_shard(
                    source,
                    destination,
                    "mini",
                    require_healthy_receipt=False,
                )

            self.assertTrue(sent_sigint)
            self.assertEqual(tree_snapshot(final_shard), prior_tree)

    def test_two_codex_rollouts_without_session_meta_have_stable_private_identities(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            codex_root = root / "private-codex-installation"
            sessions = codex_root / "sessions" / "2026" / "08" / "28"
            first_file = (
                sessions
                / "rollout-2026-08-28T10-00-00-019f0000-0000-7000-8000-000000000001.jsonl"
            )
            second_file = (
                sessions
                / "rollout-2026-08-28T10-00-01-019f0000-0000-7000-8000-000000000002.jsonl"
            )
            sessions.mkdir(parents=True)
            first_file.write_text(
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {"type": "user_message", "message": "first"},
                    }
                )
                + "\n"
            )
            spool = root / "spool"

            first_result = fleet.collect_sources(
                spool, "mini", codex_roots=[codex_root]
            )
            index_path = spool / "hosts" / "mini" / "codex" / "index.json"
            first_index = json.loads(index_path.read_text())
            first_rows = {
                row["session_id"]: row["object_sha256"]
                for row in first_index["conversations"]
            }
            self.assertEqual(first_result["codex"]["conversations"], 1)
            self.assertEqual(len(first_rows), 1)
            self.assertTrue(
                all(value.startswith("codex-fallback-") for value in first_rows)
            )
            first_identity = fleet.extract_codex_session(
                first_file, installation_identity=codex_root
            )["session_id"]

            # Simulate a fresh process with no incremental cache and prove the
            # fallback identity/object mapping is deterministic.
            (spool / "state" / "mini" / "codex.json").unlink()
            fleet.collect_sources(spool, "mini", codex_roots=[codex_root])
            restarted_index = json.loads(index_path.read_text())
            self.assertEqual(
                {
                    row["session_id"]: row["object_sha256"]
                    for row in restarted_index["conversations"]
                },
                first_rows,
            )

            shard = spool / "hosts" / "mini"
            receipt_path = shard / "receipts" / f"{FALLBACK_RUN_ID}.json"
            receipt = {
                "schema_version": 1,
                "extractor_sha256": "a" * 64,
                "config_sha256": "c" * 64,
                "run_id": FALLBACK_RUN_ID,
                "collected_at": "2026-08-28T00:00:00+00:00",
                "host_id": "mini",
                "collection_status": "completed",
                "status": "completed",
                "harnesses": {"codex": {"status": "collected"}},
                "hub": {"remotes": {}},
                "publication": {"status": "blocked_no_drive_root", "files_copied": 0},
                "errors": [],
                "receipt_path": str(receipt_path),
            }
            fleet.atomic_write_json(receipt_path, receipt)
            fleet.write_publish_manifest(shard, receipt_path, receipt, "c" * 64)
            fleet.finalize_manifested_object_set(spool, "mini")

            archived_file = codex_root / "archived_sessions" / first_file.name
            archived_file.parent.mkdir()
            first_file.replace(archived_file)
            fleet.collect_sources(spool, "mini", codex_roots=[codex_root])
            moved_index = json.loads(index_path.read_text())
            moved_rows = {
                row["session_id"]: row["object_sha256"]
                for row in moved_index["conversations"]
            }
            self.assertEqual(moved_rows, first_rows)
            self.assertEqual(
                fleet.extract_codex_session(
                    archived_file, installation_identity=codex_root
                )["session_id"],
                first_identity,
            )
            self.assertEqual(
                {
                    path.stem
                    for path in (shard / "codex" / "objects").glob("*.json")
                },
                set(first_rows.values()),
            )

            with archived_file.open("a") as handle:
                handle.write(
                    json.dumps(
                        {
                            "type": "event_msg",
                            "payload": {
                                "type": "agent_message",
                                "message": "changed",
                            },
                        }
                    )
                    + "\n"
                )
            fleet.collect_sources(spool, "mini", codex_roots=[codex_root])
            changed_index = json.loads(index_path.read_text())
            changed_rows = {
                row["session_id"]: row["object_sha256"]
                for row in changed_index["conversations"]
            }
            self.assertEqual(len(changed_rows), 1)
            self.assertEqual(set(changed_rows), set(first_rows))
            self.assertNotEqual(
                changed_rows[first_identity], first_rows[first_identity]
            )

            fleet.atomic_write_json(receipt_path, receipt)
            fleet.write_publish_manifest(shard, receipt_path, receipt, "c" * 64)
            fleet.finalize_manifested_object_set(spool, "mini")
            self.assertEqual(
                {
                    path.stem
                    for path in (shard / "codex" / "objects").glob("*.json")
                },
                set(changed_rows.values()),
            )

            second_file.write_text(
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {"type": "user_message", "message": "second"},
                    }
                )
                + "\n"
            )
            fleet.collect_sources(spool, "mini", codex_roots=[codex_root])
            final_index = json.loads(index_path.read_text())
            final_rows = {
                row["session_id"]: row["object_sha256"]
                for row in final_index["conversations"]
            }
            second_identity = fleet.extract_codex_session(
                second_file, installation_identity=codex_root
            )["session_id"]
            self.assertNotEqual(first_identity, second_identity)
            self.assertEqual(len(final_rows), 2)
            self.assertEqual(final_rows[first_identity], changed_rows[first_identity])
            self.assertIn(second_identity, final_rows)

            fleet.atomic_write_json(receipt_path, receipt)
            fleet.write_publish_manifest(shard, receipt_path, receipt, "c" * 64)
            fleet.finalize_manifested_object_set(spool, "mini")
            self.assertEqual(
                {
                    path.stem
                    for path in (shard / "codex" / "objects").glob("*.json")
                },
                set(final_rows.values()),
            )
            output = io.BytesIO()
            fleet.stream_shard_to(output, spool, "mini")
            metadata = b"".join(
                payload
                for path, payload in frames(output.getvalue())
                if "/objects/" not in path
            )
            for private_value in (
                str(codex_root),
                str(first_file),
                str(archived_file),
                str(second_file),
                *final_rows.keys(),
            ):
                self.assertNotIn(private_value.encode(), metadata)

    def test_v2_codex_base_row_rejected_before_magic(self):
        with safe_temporary_directory() as tmp:
            spool = Path(tmp) / "source"
            shard, _digest = make_codex_shard(
                spool,
                session_id="codex-base-row",
                installation="/synthetic/codex",
            )
            index_path = shard / "codex" / "index.json"
            index = json.loads(index_path.read_text())
            index["conversations"][0].pop("source_sha256")
            index["conversations"][0].pop("installation")
            fleet.atomic_write_json(index_path, index)
            manifest_path = shard / "publish-manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["harnesses"]["codex"]["index_sha256"] = fleet.file_sha256(
                index_path
            )
            fleet.atomic_write_json(manifest_path, manifest)

            output = io.BytesIO()
            with self.assertRaises((fleet.LegacyArchiveSchemaError, ValueError)):
                fleet.stream_shard_to(output, spool, "mini")
            self.assertEqual(output.getvalue(), b"")

    def test_installation_mismatch_has_zero_output(self):
        with safe_temporary_directory() as tmp:
            spool = Path(tmp) / "source"
            shard, _digest = make_codex_shard(
                spool,
                session_id="codex-installation-mismatch",
                installation="/synthetic/codex-a",
            )
            index_path = shard / "codex" / "index.json"
            index = json.loads(index_path.read_text())
            index["conversations"][0]["installation"] = "/synthetic/codex-b"
            fleet.atomic_write_json(index_path, index)
            manifest_path = shard / "publish-manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["harnesses"]["codex"]["index_sha256"] = fleet.file_sha256(
                index_path
            )
            fleet.atomic_write_json(manifest_path, manifest)

            output = io.BytesIO()
            with self.assertRaisesRegex(ValueError, "installation identity mismatch"):
                fleet.stream_shard_to(output, spool, "mini")
            self.assertEqual(output.getvalue(), b"")

    def test_reversed_index_has_zero_output(self):
        with safe_temporary_directory() as tmp:
            spool = Path(tmp) / "source"
            shard, first_digest, first_payload = make_shard(
                spool, session_id="session-a"
            )
            second = json.loads(first_payload)
            second["session_id"] = "session-b"
            second["messages"][0]["content"] = "second body"
            canonical = fleet.canonical_json(second)
            second_digest = hashlib.sha256(canonical).hexdigest()
            (shard / "claude" / "objects" / f"{second_digest}.json").write_bytes(
                canonical + b"\n"
            )
            index_path = shard / "claude" / "index.json"
            index = json.loads(index_path.read_text())
            index["conversations"].append(
                {
                    "object_sha256": second_digest,
                    "session_id": "session-b",
                    "source": "claude-code",
                }
            )
            index["conversations"].sort(
                key=lambda row: (str(row["session_id"]), row["object_sha256"])
            )
            index["conversations"].reverse()
            fleet.atomic_write_json(index_path, index)
            manifest_path = shard / "publish-manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["harnesses"]["claude"] = {
                "index_sha256": fleet.file_sha256(index_path),
                "object_sha256": sorted([first_digest, second_digest]),
            }
            fleet.atomic_write_json(manifest_path, manifest)

            output = io.BytesIO()
            with self.assertRaisesRegex(ValueError, "row order"):
                fleet.stream_shard_to(output, spool, "mini")
            self.assertEqual(output.getvalue(), b"")

    def test_context_and_tool_container_unknown_envelopes_fail_before_magic(self):
        mutations = (
            {
                "context": {
                    "type": "context",
                    "payload": {"cwd": "/safe"},
                    "timestamp": None,
                    "future": "not allowed",
                }
            },
            {
                "tool_use": {
                    "type": "future_tool",
                    "payload": {"name": "shell"},
                    "timestamp": None,
                }
            },
            {
                "tool_uses": [
                    {
                        "type": "tool_use",
                        "payload": {"name": "shell"},
                        "timestamp": None,
                        "future": "not allowed",
                    }
                ]
            },
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation), safe_temporary_directory() as tmp:
                spool = Path(tmp) / "source"
                shard, digest, payload = make_shard(spool)
                value = json.loads(payload)
                value["messages"][0].update(mutation)
                replace_only_object(shard, value, old_digest=digest)
                output = io.BytesIO()
                with self.assertRaises(ValueError):
                    fleet.stream_shard_to(output, spool, "mini")
                self.assertEqual(output.getvalue(), b"")

    def test_transport_projection_hides_native_id_and_round_trips_it(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            native_id = "native-session-id-never-in-metadata"
            source_spool = root / "source"
            make_shard(source_spool, session_id=native_id)
            output = io.BytesIO()
            fleet.stream_shard_to(output, source_spool, "mini")
            streamed = frames(output.getvalue())
            metadata = b"".join(
                payload for path, payload in streamed if "/objects/" not in path
            )
            self.assertNotIn(native_id.encode(), metadata)
            self.assertTrue(any(path == "transport-index/claude.json" for path, _ in streamed))
            self.assertFalse(any(path == "claude/index.json" for path, _ in streamed))

            incoming = root / "incoming"
            incoming.mkdir()
            fleet.receive_stream_to_directory(
                io.BytesIO(output.getvalue()),
                incoming,
                "mini",
                str(source_spool),
                time.monotonic() + 10,
            )
            index = json.loads((incoming / "claude" / "index.json").read_text())
            self.assertEqual(index["conversations"][0]["session_id"], native_id)

    def test_boolean_transport_schema_fails_before_magic_and_at_receiver(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            source_spool = root / "source"
            make_shard(source_spool)
            original_builder = fleet.build_transport_projection

            def boolean_schema(index: dict, harness: str) -> dict:
                projection = original_builder(index, harness)
                projection["schema_version"] = True
                return projection

            output = io.BytesIO()
            with mock.patch.object(
                fleet, "build_transport_projection", side_effect=boolean_schema
            ):
                with self.assertRaises(ValueError):
                    fleet.stream_shard_to(output, source_spool, "mini")
            self.assertEqual(output.getvalue(), b"")

            valid = io.BytesIO()
            fleet.stream_shard_to(valid, source_spool, "mini")
            tampered = bytearray(fleet.STREAM_MAGIC)
            for relative, payload in frames(valid.getvalue()):
                if relative == "transport-index/claude.json":
                    projection = json.loads(payload)
                    projection["schema_version"] = True
                    payload = fleet.canonical_json(projection) + b"\n"
                tampered.extend(one_frame(relative, payload))
            tampered.extend(fleet.STREAM_FRAME_HEADER.pack(0, 0))

            incoming = root / "incoming"
            incoming.mkdir()
            with self.assertRaises(fleet.LocalStreamIntegrityError):
                fleet.receive_stream_to_directory(
                    io.BytesIO(tampered),
                    incoming,
                    "mini",
                    str(source_spool),
                    time.monotonic() + 10,
                )
            self.assertFalse((incoming / "claude" / "index.json").exists())

    def test_codex_projection_hashes_and_binds_installation(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            native_id = "codex-native-private-id"
            installation = "/Users/synthetic/.codex-private-installation"
            source_spool = root / "source"
            make_codex_shard(
                source_spool, session_id=native_id, installation=installation
            )
            output = io.BytesIO()
            fleet.stream_shard_to(output, source_spool, "mini")
            streamed = frames(output.getvalue())
            metadata = b"".join(
                payload for path, payload in streamed if "/objects/" not in path
            )
            self.assertNotIn(native_id.encode(), metadata)
            self.assertNotIn(installation.encode(), metadata)
            projection = json.loads(
                dict(streamed)["transport-index/codex.json"]
            )
            self.assertEqual(
                set(projection["conversations"][0]),
                {
                    "transport_schema",
                    "object_sha256",
                    "source",
                    "session_id_sha256",
                    "source_sha256",
                    "installation_sha256",
                },
            )

            incoming = root / "incoming"
            incoming.mkdir()
            fleet.receive_stream_to_directory(
                io.BytesIO(output.getvalue()),
                incoming,
                "mini",
                str(source_spool),
                time.monotonic() + 10,
            )
            row = json.loads((incoming / "codex" / "index.json").read_text())[
                "conversations"
            ][0]
            self.assertEqual(row["session_id"], native_id)
            self.assertEqual(row["installation"], installation)

    def test_legacy_v1_and_unknown_v2_shapes_fail_before_magic(self):
        mutations = (
            lambda value: {**value, "archive_schema_version": 1},
            lambda value: {**value, "archive_schema_version": 2.0},
            lambda value: {**value, "future_metadata": "private"},
            lambda value: {
                **value,
                "messages": [
                    {"role": "user", "content": "authorized body", "future": "private"}
                ],
            },
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate), safe_temporary_directory() as tmp:
                spool = Path(tmp) / "source"
                shard, digest, payload = make_shard(spool)
                replace_only_object(
                    shard, mutate(json.loads(payload)), old_digest=digest
                )
                output = io.BytesIO()
                with self.assertRaises((fleet.LegacyArchiveSchemaError, ValueError)):
                    fleet.stream_shard_to(output, spool, "mini")
                self.assertEqual(output.getvalue(), b"")

    def test_nonfinite_and_excessively_deep_objects_fail_before_magic(self):
        with safe_temporary_directory() as tmp:
            spool = Path(tmp) / "source"
            shard, digest, payload = make_shard(spool)
            value = json.loads(payload)
            nested = "body"
            for _ in range(70):
                nested = [nested]
            value["messages"][0]["content"] = nested
            replace_only_object(shard, value, old_digest=digest)
            output = io.BytesIO()
            with self.assertRaises(ValueError):
                fleet.stream_shard_to(output, spool, "mini")
            self.assertEqual(output.getvalue(), b"")

        with self.assertRaisesRegex(ValueError, "non-finite"):
            fleet.exact_json_loads(b'{"value":NaN}', label="synthetic")

    def test_later_invalid_object_keeps_entire_stdout_empty(self):
        with safe_temporary_directory() as tmp:
            spool = Path(tmp) / "source"
            shard, first_digest, first_payload = make_shard(spool)
            second = json.loads(first_payload)
            second["session_id"] = "session-2"
            second_canonical = fleet.canonical_json(second)
            second_digest = hashlib.sha256(second_canonical).hexdigest()
            object_root = shard / "claude" / "objects"
            (object_root / f"{second_digest}.json").write_bytes(second_canonical + b"\n")
            index_path = shard / "claude" / "index.json"
            index = json.loads(index_path.read_text())
            index["conversations"].append(
                {
                    "object_sha256": second_digest,
                    "session_id": "session-2",
                    "source": "claude-code",
                }
            )
            index["conversations"].sort(
                key=lambda row: (str(row["session_id"]), row["object_sha256"])
            )
            fleet.atomic_write_json(index_path, index)
            invalid_digest = max(first_digest, second_digest)
            (object_root / f"{invalid_digest}.json").write_bytes(
                b"private invalid later object\n"
            )
            manifest_path = shard / "publish-manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["harnesses"]["claude"] = {
                "index_sha256": fleet.file_sha256(index_path),
                "object_sha256": sorted([first_digest, second_digest]),
            }
            fleet.atomic_write_json(manifest_path, manifest)
            output = io.BytesIO()
            with self.assertRaises(ValueError):
                fleet.stream_shard_to(output, spool, "mini")
            self.assertEqual(output.getvalue(), b"")

    def test_valid_stream_round_trip(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            source_spool = root / "source"
            make_shard(source_spool)
            output = io.BytesIO()
            fleet.stream_shard_to(output, source_spool, "mini")
            incoming = root / "incoming"
            incoming.mkdir(mode=0o700)
            stats = fleet.receive_stream_to_directory(
                io.BytesIO(output.getvalue()),
                incoming,
                "mini",
                str(source_spool),
                time.monotonic() + 10,
            )
            self.assertEqual(stats["stream_objects_received"], 1)
            fleet.validated_shard_files(incoming, "mini")

    def test_unknown_manifest_receipt_and_index_fields_fail_before_body_emission(self):
        for target in ("manifest", "receipt", "index"):
            with self.subTest(target=target), safe_temporary_directory() as tmp:
                spool = Path(tmp) / "source"
                shard, _digest, _payload = make_shard(spool)
                manifest_path = shard / "publish-manifest.json"
                manifest = json.loads(manifest_path.read_text())
                if target == "manifest":
                    manifest["private_body"] = "must not cross"
                    fleet.atomic_write_json(manifest_path, manifest)
                elif target == "receipt":
                    receipt_path = shard / manifest["receipt"]["path"]
                    receipt = json.loads(receipt_path.read_text())
                    receipt["private_body"] = "must not cross"
                    fleet.atomic_write_json(receipt_path, receipt)
                    manifest["receipt"]["sha256"] = fleet.file_sha256(receipt_path)
                    fleet.atomic_write_json(manifest_path, manifest)
                else:
                    index_path = shard / "claude" / "index.json"
                    index = json.loads(index_path.read_text())
                    index["conversations"][0]["private_body"] = "must not cross"
                    fleet.atomic_write_json(index_path, index)
                    manifest["harnesses"]["claude"]["index_sha256"] = fleet.file_sha256(
                        index_path
                    )
                    fleet.atomic_write_json(manifest_path, manifest)
                output = io.BytesIO()
                with self.assertRaises(ValueError):
                    fleet.stream_shard_to(output, spool, "mini")
                self.assertEqual(output.getvalue(), b"")

    def test_free_text_receipt_error_is_not_transferable(self):
        with safe_temporary_directory() as tmp:
            spool = Path(tmp) / "source"
            shard, _digest, _payload = make_shard(spool)
            manifest_path = shard / "publish-manifest.json"
            manifest = json.loads(manifest_path.read_text())
            receipt_path = shard / manifest["receipt"]["path"]
            receipt = json.loads(receipt_path.read_text())
            receipt["publication"]["error"] = "private conversation body"
            fleet.atomic_write_json(receipt_path, receipt)
            manifest["receipt"]["sha256"] = fleet.file_sha256(receipt_path)
            fleet.atomic_write_json(manifest_path, manifest)
            output = io.BytesIO()
            with self.assertRaises(ValueError):
                fleet.stream_shard_to(output, spool, "mini")
            self.assertEqual(output.getvalue(), b"")

    def test_exact_body_free_hub_failure_receipt_is_transferable(self):
        with safe_temporary_directory() as tmp:
            spool = Path(tmp) / "source"
            shard, _digest, _payload = make_shard(spool)
            manifest_path = shard / "publish-manifest.json"
            manifest = json.loads(manifest_path.read_text())
            receipt_path = shard / manifest["receipt"]["path"]
            receipt = json.loads(receipt_path.read_text())
            receipt["hub"] = {
                "remotes": {},
                "status": "failed",
                "error_code": "HubFailure",
            }
            receipt["status"] = "failed"
            receipt["errors"] = [
                {"component": "hub", "error_code": "HubFailure"}
            ]
            fleet.atomic_write_json(receipt_path, receipt)
            manifest["receipt"]["sha256"] = fleet.file_sha256(receipt_path)
            fleet.atomic_write_json(manifest_path, manifest)

            output = io.BytesIO()
            fleet.stream_shard_to(output, spool, "mini")
            self.assertTrue(output.getvalue().startswith(fleet.STREAM_MAGIC))

    def test_hub_failure_receipt_rejects_extras_and_free_text(self):
        invalid_hubs = (
            {
                "remotes": {},
                "status": "failed",
                "error_type": "ValueError",
                "error": "private body",
            },
            {
                "remotes": {"mini": {"status": "unreachable", "files_copied": 0}},
                "status": "failed",
                "error_type": "ValueError",
            },
            {"remotes": {}, "status": "failed", "error_type": "private body"},
            {"remotes": {}, "status": "failed", "error_type": ""},
        )
        for hub in invalid_hubs:
            with self.subTest(hub=hub), safe_temporary_directory() as tmp:
                spool = Path(tmp) / "source"
                shard, _digest, _payload = make_shard(spool)
                manifest_path = shard / "publish-manifest.json"
                manifest = json.loads(manifest_path.read_text())
                receipt_path = shard / manifest["receipt"]["path"]
                receipt = json.loads(receipt_path.read_text())
                receipt["hub"] = hub
                receipt["status"] = "failed"
                receipt["errors"] = [
                    {"component": "hub", "error_code": "HubFailure"}
                ]
                fleet.atomic_write_json(receipt_path, receipt)
                manifest["receipt"]["sha256"] = fleet.file_sha256(receipt_path)
                fleet.atomic_write_json(manifest_path, manifest)
                output = io.BytesIO()
                with self.assertRaises(ValueError):
                    fleet.stream_shard_to(output, spool, "mini")
                self.assertEqual(output.getvalue(), b"")

    def test_symlink_parent_and_leaf_never_emit_outside_body(self):
        for kind in ("parent", "leaf"):
            with self.subTest(kind=kind), safe_temporary_directory() as tmp:
                root = Path(tmp)
                spool = root / "source"
                shard, digest, _payload = make_shard(spool)
                object_path = shard / "claude" / "objects" / f"{digest}.json"
                private = b"UNMANIFESTED-PRIVATE-BODY\n"
                outside = root / "outside"
                outside.mkdir()
                if kind == "parent":
                    (outside / object_path.name).write_bytes(private)
                    original = object_path.parent.with_name("objects-original")
                    object_path.parent.rename(original)
                    object_path.parent.symlink_to(outside, target_is_directory=True)
                else:
                    outside_file = outside / object_path.name
                    outside_file.write_bytes(private)
                    object_path.unlink()
                    object_path.symlink_to(outside_file)
                output = io.BytesIO()
                with self.assertRaises((OSError, ValueError)):
                    fleet.stream_shard_to(output, spool, "mini")
                self.assertNotIn(private, output.getvalue())

    def test_replacement_after_preflight_is_revalidated_before_body_emission(self):
        with safe_temporary_directory() as tmp:
            spool = Path(tmp) / "source"
            _shard, digest, original = make_shard(spool)
            replacement = b"UNMANIFESTED-PRIVATE-BODY\n"

            def replace(path: Path, relative: str, _payload: bytes):
                if relative == f"claude/objects/{digest}.json":
                    path.unlink()
                    path.write_bytes(replacement)

            output = io.BytesIO()
            with self.assertRaises(ValueError):
                fleet.stream_shard_to(output, spool, "mini", after_validate=replace)
            self.assertNotIn(replacement, output.getvalue())
            self.assertNotIn(original, output.getvalue())

    def test_unmanifested_object_is_never_emitted(self):
        with safe_temporary_directory() as tmp:
            spool = Path(tmp) / "source"
            shard, _digest, _payload = make_shard(spool)
            private = fleet.canonical_json(
                {
                    "schema_version": 1,
                    "source": "claude-code",
                    "session_id": "unmanifested",
                    "messages": [{"role": "user", "content": "private body"}],
                }
            ) + b"\n"
            digest = hashlib.sha256(private[:-1]).hexdigest()
            (shard / "claude" / "objects" / f"{digest}.json").write_bytes(private)
            output = io.BytesIO()
            fleet.stream_shard_to(output, spool, "mini")
            self.assertNotIn(private, output.getvalue())
            self.assertNotIn(digest, {Path(path).stem for path, _ in frames(output.getvalue())})

    def test_hinted_objects_are_still_fully_validated_before_skip(self):
        with safe_temporary_directory() as tmp:
            spool = Path(tmp) / "source"
            shard, digest, _payload = make_shard(spool)
            private = b"UNMANIFESTED-PRIVATE-BODY\n"
            (shard / "claude" / "objects" / f"{digest}.json").write_bytes(private)
            output = io.BytesIO()
            with self.assertRaises(ValueError):
                fleet.stream_shard_to(
                    output,
                    spool,
                    "mini",
                    skip_object_digests={digest},
                )
            self.assertNotIn(private, output.getvalue())

    def test_all_v2_harnesses_reject_nullable_session_identity(self):
        objects = {
            "claude": {
                "archive_schema_version": 2,
                "source": "claude-code",
                "session_id": None,
                "messages": [],
                "project_path": None,
                "project_name": None,
                "source_file": "/synthetic/claude.jsonl",
            },
            "codex": {
                "archive_schema_version": 2,
                "source": "codex",
                "session_id": None,
                "messages": [],
                "cwd": None,
                "session_file": "/synthetic/codex.jsonl",
                "timestamp": None,
                "installation": "/synthetic/codex",
            },
            "openclaw": {
                "archive_schema_version": 2,
                "source": "openclaw",
                "session_id": None,
                "messages": [],
                "cwd": None,
                "session_file": "/synthetic/openclaw.jsonl",
                "timestamp": None,
                "source_schema": "openclaw-jsonl-v3",
            },
            "hermes": {
                "archive_schema_version": 2,
                "source": "hermes",
                "session_id": None,
                "messages": [],
                "native_source": "hermes-cli",
                "source_schema": "hermes-sessions-export-jsonl-v1",
            },
        }
        for harness, value in objects.items():
            with self.subTest(harness=harness):
                with self.assertRaisesRegex(ValueError, "non-empty string"):
                    fleet.validate_archive_object(value, harness=harness)
                row = {
                    "object_sha256": "a" * 64,
                    "session_id": None,
                    "source": value["source"],
                }
                if harness == "codex":
                    row.update(
                        {
                            "source_sha256": "b" * 64,
                            "installation": "/synthetic/codex",
                        }
                    )
                index = {
                    "schema_version": 1,
                    "host_id": "mini",
                    "harness": harness,
                    "conversations": [row],
                }
                with self.assertRaises(fleet.LegacyArchiveSchemaError):
                    fleet.validate_index_value(index, "mini", harness)

    def test_local_merge_replaces_legacy_nullable_index(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            source_spool = root / "source"
            source_shard, _digest, _payload = make_shard(
                source_spool, session_id="stable-session"
            )
            source_index_path = source_shard / "claude" / "index.json"
            source_index = json.loads(source_index_path.read_text())

            destination_index_path = root / "destination" / "claude" / "index.json"
            destination_index_path.parent.mkdir(parents=True)
            legacy_index = json.loads(source_index_path.read_text())
            legacy_index["conversations"][0]["session_id"] = None
            fleet.atomic_write_json(destination_index_path, legacy_index)

            self.assertTrue(
                fleet.merge_index_file(source_index_path, destination_index_path)
            )
            self.assertEqual(
                json.loads(destination_index_path.read_text()), source_index
            )

    def test_receiver_rejects_malformed_traversal_duplicate_and_oversized_frames(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            source = root / "source"
            shard, _digest, _payload = make_shard(source)
            manifest = (shard / "publish-manifest.json").read_bytes()
            cases = {
                "bad-magic": b"bad",
                "traversal": fleet.STREAM_MAGIC + one_frame("../private", b"x"),
                "duplicate": (
                    fleet.STREAM_MAGIC
                    + one_frame("publish-manifest.json", manifest)
                    + one_frame("publish-manifest.json", manifest)
                ),
                "oversized-path": (
                    fleet.STREAM_MAGIC
                    + fleet.STREAM_FRAME_HEADER.pack(fleet.MAX_STREAM_PATH_BYTES + 1, 0)
                ),
                "oversized-payload": (
                    fleet.STREAM_MAGIC
                    + fleet.STREAM_FRAME_HEADER.pack(
                        len(b"publish-manifest.json"),
                        fleet.MAX_STREAM_METADATA_BYTES + 1,
                    )
                    + b"publish-manifest.json"
                ),
            }
            for name, payload in cases.items():
                with self.subTest(name=name):
                    incoming = root / f"incoming-{name}"
                    incoming.mkdir(mode=0o700)
                    with self.assertRaises(
                        (fleet.LocalStreamIntegrityError, EOFError)
                    ):
                        fleet.receive_stream_to_directory(
                            io.BytesIO(payload),
                            incoming,
                            "mini",
                            str(source),
                            time.monotonic() + 10,
                        )

    def test_cache_hints_are_closed_bounded_and_duplicate_free(self):
        digest = "a" * 64
        self.assertEqual(fleet.parse_cache_hints(io.BytesIO((digest + "\n").encode())), {digest})
        for payload in (
            (digest + "\n" + digest + "\n").encode(),
            b"../private\n",
            b"a" * (fleet.MAX_CACHE_HINT_BYTES + 1),
            digest.encode(),
        ):
            with self.assertRaises(ValueError):
                fleet.parse_cache_hints(io.BytesIO(payload))

    def test_valid_initial_and_cached_pull_uses_popen_and_skips_object_bytes(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            source_spool = root / "source"
            _shard, digest, _payload = make_shard(source_spool)
            destination = root / "destination"
            remote = {
                "host_id": "mini",
                "ssh_host": "mini.test",
                "remote_spool_root": str(source_spool),
                "remote_pipeline_path": str(CLI),
                "timeout_seconds": 10,
            }
            real_popen = subprocess.Popen

            def run_helper_locally(command, **kwargs):
                remote_python = command.index("python3")
                return real_popen(
                    [sys.executable, *command[remote_python + 1 :]], **kwargs
                )

            with mock.patch.object(fleet.subprocess, "Popen", side_effect=run_helper_locally):
                first = fleet.pull_remote_stream(
                    remote, destination, "mini", None, set()
                )
                cached = destination / "hosts" / "mini"
                fleet.validated_shard_files(cached, "mini")
                second = fleet.pull_remote_stream(
                    remote, destination, "mini", cached, {digest}
                )
            self.assertEqual(first["stream_objects_received"], 1)
            self.assertEqual(second["stream_objects_received"], 0)
            self.assertEqual(second["status"], "published")

    def test_manifest_absent_partial_cache_is_quarantined_and_retried_fresh(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            spool = root / "destination"
            cached = make_shard(spool)[0]
            (cached / "publish-manifest.json").unlink()
            remote = {
                "host_id": "mini",
                "ssh_host": "mini.test",
                "remote_spool_root": "/remote/spool",
                "remote_pipeline_path": "/safe/helper.py",
            }
            with mock.patch.object(
                fleet,
                "pull_remote_stream",
                return_value={
                    "status": "published",
                    "files_copied": 4,
                    "files_verified": 4,
                },
            ) as pull:
                result = fleet.pull_hub_remotes({"remotes": [remote]}, spool)

            self.assertEqual(result["remotes"]["mini"]["status"], "pulled")
            self.assertFalse(cached.exists())
            self.assertEqual(
                len(list((spool / "quarantine" / "mini").glob("incomplete-cache-*"))),
                1,
            )
            self.assertIsNone(pull.call_args.args[3])
            self.assertEqual(pull.call_args.args[4], set())

    def test_corrupt_manifest_cache_is_quarantined_and_retried_fresh(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            source_spool = root / "source"
            make_shard(source_spool)
            spool = root / "destination"
            cached = make_shard(spool)[0]
            (cached / "publish-manifest.json").write_text("{}\n")
            remote = {
                "host_id": "mini",
                "ssh_host": "mini.test",
                "remote_spool_root": str(source_spool),
                "remote_pipeline_path": str(CLI),
                "timeout_seconds": 10,
            }
            real_popen = subprocess.Popen

            def run_helper_locally(command, **kwargs):
                remote_python = command.index("python3")
                return real_popen(
                    [sys.executable, *command[remote_python + 1 :]], **kwargs
                )

            with mock.patch.object(
                fleet.subprocess, "Popen", side_effect=run_helper_locally
            ):
                result = fleet.pull_hub_remotes({"remotes": [remote]}, spool)

            self.assertEqual(result["remotes"]["mini"]["status"], "pulled")
            quarantined = list(
                (spool / "quarantine" / "mini").glob("invalid-cache-*")
            )
            self.assertEqual(len(quarantined), 1)
            self.assertEqual(
                (quarantined[0] / "publish-manifest.json").read_text(), "{}\n"
            )
            fleet.validated_shard_files(spool / "hosts" / "mini", "mini")

    def test_interrupted_cache_index_is_restored_to_last_good_manifest(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            spool = root / "destination"
            cached = make_shard(spool)[0]
            fleet.archive_conversations(
                spool,
                "mini",
                "claude",
                [{
                    "archive_schema_version": 2,
                    "source": "claude-code",
                    "session_id": "interrupted-row",
                    "messages": [{"role": "user", "content": "uncommitted"}],
                    "project_path": None,
                    "project_name": None,
                    "source_file": "/synthetic/interrupted.jsonl",
                }],
            )
            remote = {
                "host_id": "mini",
                "ssh_host": "mini.test",
                "remote_spool_root": "/remote/spool",
                "remote_pipeline_path": "/safe/helper.py",
            }
            with mock.patch.object(
                fleet,
                "pull_remote_stream",
                return_value={
                    "status": "published",
                    "files_copied": 0,
                    "files_verified": 4,
                },
            ) as pull:
                result = fleet.pull_hub_remotes({"remotes": [remote]}, spool)

            self.assertEqual(result["remotes"]["mini"]["status"], "pulled")
            self.assertEqual(pull.call_args.args[3], cached)
            fleet.validated_shard_files(cached, "mini")
            self.assertFalse((spool / "quarantine").exists())

    def test_interrupted_and_timeout_streams_leave_no_final_shard(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            source_spool = root / "source"
            make_shard(source_spool)
            remote = {
                "host_id": "mini",
                "ssh_host": "mini.test",
                "remote_spool_root": str(source_spool),
                "remote_pipeline_path": str(CLI),
                "timeout_seconds": 1,
            }
            real_popen = subprocess.Popen

            programs = {
                "interrupted": (
                    "import sys; sys.stdin.buffer.read(); "
                    f"sys.stdout.buffer.write({(fleet.STREAM_MAGIC + b'half')!r}); "
                    "sys.stdout.buffer.flush()"
                ),
                "timeout": "import sys,time; sys.stdin.buffer.read(); time.sleep(5)",
            }
            for name, program in programs.items():
                with self.subTest(name=name):
                    destination = root / f"destination-{name}"

                    def synthetic_process(_command, **kwargs):
                        return real_popen([sys.executable, "-c", program], **kwargs)

                    expected = (
                        fleet.LocalStreamIntegrityError
                        if name == "interrupted"
                        else fleet.RemoteTimeoutError
                    )
                    with mock.patch.object(
                        fleet.subprocess, "Popen", side_effect=synthetic_process
                    ), self.assertRaises(expected):
                        fleet.pull_remote_stream(
                            remote, destination, "mini", None, set()
                        )
                    self.assertFalse((destination / "hosts" / "mini").exists())

    def test_local_validation_failure_kills_and_reaps_process_group(self):
        class FakeProcess:
            def __init__(self):
                self.stdin = mock.Mock()
                self.stdout = mock.Mock()
                self.pid = 4321
                self.waited = False

            def poll(self):
                return None

            def wait(self, timeout):
                self.waited = True
                return -signal.SIGKILL

        with safe_temporary_directory() as tmp:
            process = FakeProcess()
            remote = {
                "host_id": "mini",
                "ssh_host": "mini.test",
                "remote_spool_root": "/remote/spool",
                "remote_pipeline_path": "/safe/helper.py",
                "timeout_seconds": 10,
            }
            with mock.patch.object(
                fleet.subprocess, "Popen", return_value=process
            ), mock.patch.object(
                fleet, "write_before_deadline"
            ), mock.patch.object(
                fleet,
                "receive_stream_to_directory",
                side_effect=ValueError("synthetic local validation failure"),
            ), mock.patch.object(fleet.os, "killpg") as killpg, self.assertRaisesRegex(
                ValueError, "synthetic local validation failure"
            ):
                fleet.pull_remote_stream(
                    remote, Path(tmp) / "destination", "mini", None, set()
                )

            process.stdin.close.assert_called()
            killpg.assert_called_once_with(process.pid, signal.SIGKILL)
            self.assertTrue(process.waited)
            process.stdout.close.assert_called()

    def test_cleanup_preserves_original_when_kill_and_bounded_wait_fail(self):
        class FakeProcess:
            def __init__(self):
                self.stdin = mock.Mock()
                self.stdout = mock.Mock()
                self.pid = 4321
                self.kill = mock.Mock(side_effect=PermissionError("kill denied"))
                self.wait_timeouts = []

            def poll(self):
                return None

            def wait(self, timeout):
                self.wait_timeouts.append(timeout)
                raise subprocess.TimeoutExpired("ssh", timeout)

        with safe_temporary_directory() as tmp:
            process = FakeProcess()
            remote = {
                "host_id": "mini",
                "ssh_host": "mini.test",
                "remote_spool_root": "/remote/spool",
                "remote_pipeline_path": "/safe/helper.py",
                "timeout_seconds": 10,
            }
            original = OSError("original local failure")
            with mock.patch.object(
                fleet.subprocess, "Popen", return_value=process
            ), mock.patch.object(
                fleet, "write_before_deadline"
            ), mock.patch.object(
                fleet, "receive_stream_to_directory", side_effect=original
            ), mock.patch.object(
                fleet.os,
                "killpg",
                side_effect=PermissionError("killpg denied"),
            ) as killpg, self.assertRaises(OSError) as raised:
                fleet.pull_remote_stream(
                    remote, Path(tmp) / "destination", "mini", None, set()
                )

            self.assertIs(raised.exception, original)
            killpg.assert_called_once_with(process.pid, signal.SIGKILL)
            process.kill.assert_called_once_with()
            self.assertEqual(len(process.wait_timeouts), 1)
            self.assertGreater(process.wait_timeouts[0], 0)
            self.assertLessEqual(
                process.wait_timeouts[0], fleet.REMOTE_PROCESS_REAP_SECONDS
            )
            process.stdin.close.assert_called()
            process.stdout.close.assert_called()

    def test_baseexceptions_after_popen_are_cleaned_and_preserved(self):
        class FakeProcess:
            def __init__(self):
                self.stdin = mock.Mock()
                self.stdout = mock.Mock()
                self.pid = 4321
                self.waited = False

            def poll(self):
                return None

            def wait(self, timeout):
                self.waited = True
                return -signal.SIGKILL

        remote = {
            "host_id": "mini",
            "ssh_host": "mini.test",
            "remote_spool_root": "/remote/spool",
            "remote_pipeline_path": "/safe/helper.py",
            "timeout_seconds": 10,
        }
        for original in (fleet.TerminationRequested(), KeyboardInterrupt()):
            with self.subTest(
                error=type(original).__name__
            ), safe_temporary_directory() as tmp:
                process = FakeProcess()
                with mock.patch.object(
                    fleet.subprocess, "Popen", return_value=process
                ), mock.patch.object(
                    fleet, "write_before_deadline"
                ), mock.patch.object(
                    fleet, "receive_stream_to_directory", side_effect=original
                ), mock.patch.object(fleet.os, "killpg"), self.assertRaises(
                    type(original)
                ) as raised:
                    fleet.pull_remote_stream(
                        remote, Path(tmp) / "destination", "mini", None, set()
                    )

                self.assertIs(raised.exception, original)
                self.assertTrue(process.waited)
                process.stdin.close.assert_called()
                process.stdout.close.assert_called()

    def test_local_source_failure_does_not_abort_other_remotes(self):
        with safe_temporary_directory() as tmp:
            root = Path(tmp)
            unsafe_target = root / "unsafe-target"
            unsafe_target.mkdir()
            unsafe_source = root / "unsafe-source"
            unsafe_source.symlink_to(unsafe_target, target_is_directory=True)
            valid_source = root / "valid-source"
            make_shard(valid_source, "studio")

            result = fleet.pull_hub_remotes(
                {
                    "remotes": [
                        {"host_id": "mini", "source_spool_root": str(unsafe_source)},
                        {"host_id": "studio", "source_spool_root": str(valid_source)},
                    ]
                },
                root / "destination",
            )

            self.assertEqual(
                result["remotes"]["mini"],
                {"status": "local_integrity_rejection", "files_copied": 0},
            )
            self.assertEqual(result["remotes"]["studio"]["status"], "pulled")

    def test_invalid_timeout_is_invalid_remote_before_popen(self):
        for timeout_seconds in (True, 0, 86401, "10"):
            with self.subTest(
                timeout_seconds=timeout_seconds
            ), safe_temporary_directory() as tmp:
                hub = {
                    "remotes": [
                        {
                            "host_id": "mini",
                            "ssh_host": "mini.test",
                            "remote_spool_root": "/remote/spool",
                            "remote_pipeline_path": "/safe/helper.py",
                            "timeout_seconds": timeout_seconds,
                        }
                    ]
                }
                with mock.patch.object(fleet.subprocess, "Popen") as popen:
                    status = fleet.pull_hub_remotes(hub, Path(tmp) / "spool")
                self.assertEqual(
                    status["remotes"]["mini"],
                    {"status": "invalid_remote", "files_copied": 0},
                )
                popen.assert_not_called()

    def test_argv_path_injection_is_rejected_before_popen(self):
        with safe_temporary_directory() as tmp:
            hub = {
                "remotes": [
                    {
                        "host_id": "mini",
                        "ssh_host": "mini.test",
                        "remote_spool_root": "/remote/spool",
                        "remote_pipeline_path": "/safe/helper.py;touch-private",
                    }
                ]
            }
            with mock.patch.object(fleet.subprocess, "Popen") as popen:
                status = fleet.pull_hub_remotes(hub, Path(tmp) / "spool")
            self.assertEqual(status["remotes"]["mini"]["status"], "invalid_remote")
            popen.assert_not_called()

    def test_one_monotonic_deadline_is_shared_by_all_remote_phases(self):
        class FakeProcess:
            def __init__(self):
                self.stdin = io.BytesIO()
                self.stdout = io.BytesIO()
                self.pid = 123

            def poll(self):
                return 0

        with safe_temporary_directory() as tmp:
            deadlines = []
            remote = {
                "host_id": "mini",
                "ssh_host": "mini.test",
                "remote_spool_root": "/remote/spool",
                "remote_pipeline_path": "/safe/helper.py",
                "timeout_seconds": 10,
            }

            def record_write(_stream, _payload, deadline):
                deadlines.append(deadline)

            def record_receive(*args, **kwargs):
                deadlines.append(args[4])
                return {
                    "stream_files_received": 0,
                    "stream_objects_received": 0,
                    "stream_bytes_received": 0,
                }

            def record_wait(_process, deadline):
                deadlines.append(deadline)
                return 0

            with mock.patch.object(fleet.subprocess, "Popen", return_value=FakeProcess()), mock.patch.object(
                fleet, "write_before_deadline", side_effect=record_write
            ), mock.patch.object(
                fleet, "receive_stream_to_directory", side_effect=record_receive
            ), mock.patch.object(
                fleet, "wait_remote_process", side_effect=record_wait
            ), mock.patch.object(
                fleet,
                "merge_host_shard",
                return_value={"status": "published", "files_copied": 0, "files_verified": 0},
            ):
                fleet.pull_remote_stream(
                    remote, Path(tmp) / "destination", "mini", None, set()
                )
            self.assertEqual(len(set(deadlines)), 1)

    def test_pull_status_classification_is_specific(self):
        cases = {
            fleet.PendingManifestError("pending"): "pending_manifest",
            fleet.LegacyArchiveSchemaError("legacy"): "legacy_schema",
            fleet.RemoteUnreachableError("down"): "unreachable",
            fleet.RemoteTimeoutError("slow"): "timeout",
            fleet.RemoteIntegrityError("remote"): "remote_integrity_rejection",
            fleet.LocalStreamIntegrityError("local"): "local_integrity_rejection",
        }
        with safe_temporary_directory() as tmp:
            for error, expected in cases.items():
                with self.subTest(expected=expected), mock.patch.object(
                    fleet, "pull_remote_stream", side_effect=error
                ):
                    result = fleet.pull_hub_remotes(
                        {
                            "remotes": [
                                {
                                    "host_id": "mini",
                                    "ssh_host": "mini.test",
                                    "remote_spool_root": "/remote/spool",
                                    "remote_pipeline_path": "/safe/helper.py",
                                }
                            ]
                        },
                        Path(tmp) / expected,
                    )
                self.assertEqual(result["remotes"]["mini"]["status"], expected)
                projected = fleet.project_remote_receipt_metadata(
                    result["remotes"]["mini"]
                )
                self.assertEqual(projected["status"], expected)
                fleet.validate_remote_receipt_metadata(projected)


if __name__ == "__main__":
    unittest.main()
