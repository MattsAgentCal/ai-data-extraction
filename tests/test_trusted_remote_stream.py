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
    receipt_path = shard / "receipts" / "healthy-codex.json"
    receipt = {
        "schema_version": 1,
        "extractor_sha256": "a" * 64,
        "config_sha256": "c" * 64,
        "run_id": "healthy-codex-test",
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
                "error_type": "ValueError",
            }
            receipt["status"] = "failed"
            receipt["errors"] = [
                {"component": "hub", "error_type": "ValueError"}
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
                    {"component": "hub", "error_type": "ValueError"}
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


if __name__ == "__main__":
    unittest.main()
