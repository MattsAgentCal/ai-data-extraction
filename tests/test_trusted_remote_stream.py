import hashlib
import io
import json
import os
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


def safe_temporary_directory():
    return tempfile.TemporaryDirectory(prefix="trusted-stream-", dir=Path.home())


def make_shard(spool: Path, host_id: str = "mini") -> tuple[Path, str, bytes]:
    shard = spool / "hosts" / host_id
    conversation = {
        "schema_version": 1,
        "source": "claude-code",
        "session_id": "session-1",
        "messages": [{"role": "user", "content": "authorized body"}],
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
                    "session_id": "session-1",
                    "source": "claude-code",
                }
            ],
        },
    )
    receipt_path = shard / "receipts" / "healthy.json"
    receipt = {
        "schema_version": 1,
        "extractor_sha256": "a" * 64,
        "config_sha256": "c" * 64,
        "run_id": "healthy-test",
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

    def test_replacement_after_validation_emits_validated_snapshot(self):
        with safe_temporary_directory() as tmp:
            spool = Path(tmp) / "source"
            _shard, digest, original = make_shard(spool)
            replacement = b"UNMANIFESTED-PRIVATE-BODY\n"

            def replace(path: Path, relative: str, _payload: bytes):
                if relative == f"claude/objects/{digest}.json":
                    path.unlink()
                    path.write_bytes(replacement)

            output = io.BytesIO()
            fleet.stream_shard_to(output, spool, "mini", after_validate=replace)
            emitted = dict(frames(output.getvalue()))
            self.assertEqual(emitted[f"claude/objects/{digest}.json"], original)
            self.assertNotIn(replacement, output.getvalue())

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

    def test_index_session_id_may_be_null_but_not_a_container(self):
        base = {
            "schema_version": 1,
            "host_id": "mini",
            "harness": "hermes",
            "conversations": [
                {
                    "object_sha256": "a" * 64,
                    "session_id": None,
                    "source": "hermes",
                }
            ],
        }
        fleet.validate_index_value(base, "mini", "hermes")
        invalid = json.loads(json.dumps(base))
        invalid["conversations"][0]["session_id"] = {"private": "body"}
        with self.assertRaises(ValueError):
            fleet.validate_index_value(invalid, "mini", "hermes")

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


if __name__ == "__main__":
    unittest.main()
