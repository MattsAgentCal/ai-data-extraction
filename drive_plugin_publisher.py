#!/usr/bin/env python3
"""Publish validated archive objects through the authenticated Codex Drive plugin.

The collector is intentionally independent from this publisher.  Collection and
manifest commits remain local and launchd-owned even when the Drive connector is
unavailable.  This process passes only manifest-bound object paths and metadata
to ``codex exec``; it never puts an archive body in a prompt, receipt, or log.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import plistlib
import re
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from fleet_chat_archive import (
    OBJECT_NAME_RE,
    assert_no_symlink_components,
    atomic_write_bytes,
    canonical_json,
    secure_mkdir,
    validate_host_id,
    validate_index_metadata,
    validate_publish_manifest,
    validate_publish_manifest_metadata,
    validated_object_provenance,
)


PUBLISHER_SCHEMA_VERSION = 1
DEFAULT_INTERVAL_SECONDS = 21600
DEFAULT_MAX_FILES = 24
DEFAULT_LOCK_TIMEOUT_SECONDS = 1800
DEFAULT_CODEX_TIMEOUT_SECONDS = 1800
DRIVE_MIME_TYPE = "text/plain"
DRIVE_ID_RE = re.compile(r"[A-Za-z0-9_-]{10,256}\Z")
RECEIPT_NAME_RE = re.compile(r"[0-9]{8}T[0-9]{6}\.[0-9]{6}Z-[0-9a-f]{8}\Z")
LEASE_OWNER_RE = re.compile(r"[A-Za-z0-9:_-]{1,128}\Z")


class PublisherConfigError(ValueError):
    """The publisher configuration is not safe to execute."""


class LeaseNotHeld(RuntimeError):
    """The deployment lease is absent, expired, or held by another owner."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso() -> str:
    return utc_now().isoformat()


def read_json(path: Path, *, label: str, max_bytes: int = 4 * 1024 * 1024) -> Any:
    path = assert_no_symlink_components(path)
    if not path.is_file():
        raise FileNotFoundError(f"{label} is missing: {path}")
    metadata = path.stat()
    if metadata.st_size > max_bytes:
        raise PublisherConfigError(f"{label} exceeds maximum size")
    with path.open("rb") as handle:
        payload = handle.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise PublisherConfigError(f"{label} exceeds maximum size")
    try:
        value = json.loads(payload)
    except (TypeError, ValueError, UnicodeError) as error:
        raise PublisherConfigError(f"{label} is not JSON") from error
    return value


def atomic_write_json(path: Path, value: Any) -> None:
    path = assert_no_symlink_components(path, include_leaf=False)
    secure_mkdir(path.parent)
    payload = canonical_json(value) + b"\n"
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def validate_drive_id(value: Any) -> str:
    if not isinstance(value, str) or not DRIVE_ID_RE.fullmatch(value):
        raise PublisherConfigError("drive_folder_id must be a valid Drive id")
    return value


def validate_positive_int(value: Any, label: str, maximum: int) -> int:
    if type(value) is not int or value < 1 or value > maximum:
        raise PublisherConfigError(f"{label} must be an integer from 1 to {maximum}")
    return value


def load_config(path: Path) -> dict[str, Any]:
    config = read_json(path, label="publisher config")
    if not isinstance(config, dict) or config.get("schema_version") != PUBLISHER_SCHEMA_VERSION:
        raise PublisherConfigError("unsupported publisher config schema_version")
    required = {"schema_version", "host_id", "spool_root", "drive_folder_id"}
    if set(config) - {
        *required,
        "host_ids",
        "codex_command",
        "workspace_root",
        "max_files",
        "lock_timeout_seconds",
        "codex_timeout_seconds",
        "state_path",
        "receipt_root",
        "deployment_lease_path",
        "lease_owner",
        "lease_host",
        "interval_seconds",
    }:
        raise PublisherConfigError("publisher config contains an unknown key")
    if not required.issubset(config):
        raise PublisherConfigError("publisher config is missing a required key")
    host_id = validate_host_id(config["host_id"])
    spool_input = Path(config["spool_root"])
    if not spool_input.is_absolute():
        raise PublisherConfigError("spool_root must be absolute")
    spool_root = assert_no_symlink_components(spool_input)
    host_ids_value = config.get("host_ids", [host_id])
    if (
        not isinstance(host_ids_value, list)
        or not host_ids_value
        or len(host_ids_value) != len(set(host_ids_value))
    ):
        raise PublisherConfigError("host_ids must be a non-empty unique list")
    host_ids = [validate_host_id(value) for value in host_ids_value]
    if host_id not in host_ids:
        raise PublisherConfigError("host_ids must include host_id")
    drive_folder_id = validate_drive_id(config["drive_folder_id"])
    codex_command = config.get("codex_command", "codex")
    if not isinstance(codex_command, str) or not codex_command:
        raise PublisherConfigError("codex_command must be a non-empty string")
    workspace_root = config.get("workspace_root", "/tmp")
    if not isinstance(workspace_root, str) or not workspace_root.startswith("/"):
        raise PublisherConfigError("workspace_root must be an absolute path")
    max_files = validate_positive_int(
        config.get("max_files", DEFAULT_MAX_FILES), "max_files", 200
    )
    lock_timeout = validate_positive_int(
        config.get("lock_timeout_seconds", DEFAULT_LOCK_TIMEOUT_SECONDS),
        "lock_timeout_seconds",
        24 * 60 * 60,
    )
    codex_timeout = validate_positive_int(
        config.get("codex_timeout_seconds", DEFAULT_CODEX_TIMEOUT_SECONDS),
        "codex_timeout_seconds",
        24 * 60 * 60,
    )
    interval = validate_positive_int(
        config.get("interval_seconds", DEFAULT_INTERVAL_SECONDS),
        "interval_seconds",
        7 * 24 * 60 * 60,
    )
    default_state = spool_root / "plugin-publisher" / f"{host_id}.json"
    state_path = assert_no_symlink_components(
        Path(config.get("state_path", str(default_state))), include_leaf=False
    )
    default_receipts = spool_root / "plugin-publisher" / "receipts"
    receipt_root = assert_no_symlink_components(
        Path(config.get("receipt_root", str(default_receipts))), include_leaf=False
    )
    lease_path = assert_no_symlink_components(
        Path(config.get("deployment_lease_path", str(Path(__file__).resolve().parent / ".deployment-lease.json")))
    )
    lease_owner = config.get("lease_owner", "codex:macbook")
    lease_host = config.get("lease_host", "Mac.lan")
    if not isinstance(lease_owner, str) or not LEASE_OWNER_RE.fullmatch(lease_owner):
        raise PublisherConfigError("lease_owner is invalid")
    if not isinstance(lease_host, str) or not lease_host or "\n" in lease_host:
        raise PublisherConfigError("lease_host is invalid")
    return {
        **config,
        "host_id": host_id,
        "host_ids": host_ids,
        "spool_root": spool_root,
        "drive_folder_id": drive_folder_id,
        "codex_command": codex_command,
        "workspace_root": Path(workspace_root),
        "max_files": max_files,
        "lock_timeout_seconds": lock_timeout,
        "codex_timeout_seconds": codex_timeout,
        "interval_seconds": interval,
        "state_path": state_path,
        "receipt_root": receipt_root,
        "deployment_lease_path": lease_path,
        "lease_owner": lease_owner,
        "lease_host": lease_host,
    }


def assert_deployment_lease(config: dict[str, Any], *, now: datetime | None = None) -> dict:
    lease = read_json(config["deployment_lease_path"], label="deployment lease")
    if not isinstance(lease, dict):
        raise LeaseNotHeld("deployment lease is not an object")
    if (
        lease.get("schema_version") != 1
        or lease.get("status") != "active"
        or lease.get("owner") != config["lease_owner"]
        or lease.get("owner_host") != config["lease_host"]
    ):
        raise LeaseNotHeld("deployment lease owner mismatch")
    try:
        expires = datetime.fromisoformat(str(lease["expires_at"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as error:
        raise LeaseNotHeld("deployment lease expiry is invalid") from error
    if expires.tzinfo is None or expires.utcoffset() is None:
        raise LeaseNotHeld("deployment lease expiry must include a timezone")
    current = now or utc_now()
    if expires <= current:
        raise LeaseNotHeld("deployment lease has expired")
    return {
        "owner": lease["owner"],
        "owner_host": lease["owner_host"],
        "expires_at": expires.isoformat(),
    }


@contextlib.contextmanager
def publisher_snapshot_lock(spool_root: Path, timeout_seconds: int) -> Iterator[None]:
    """Wait for the collector transaction, then hold its lock for validation."""
    secure_mkdir(spool_root)
    lock_path = assert_no_symlink_components(spool_root / ".run.lock", include_leaf=False)
    descriptor = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("collector lock wait timed out")
                time.sleep(min(1.0, max(0.05, deadline - time.monotonic())))
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def sha256_file(path: Path) -> str:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    try:
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def iter_object_candidates(
    source_root: Path,
    host_id: str,
    *,
    validation_cache: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Derive manifest-bound paths without re-reading every historical body.

    The collector has already run the canonical full validator before committing
    its manifest.  The publisher re-validates that manifest, receipt, and every
    index under the collector lock, then runs the same object/provenance
    validator only for the bounded batch selected for publication.  This keeps a
    six-hour run bounded even when a host has a large historical archive.
    """
    source_root = assert_no_symlink_components(source_root)
    manifest = validate_publish_manifest_metadata(source_root, host_id)
    receipts_root = source_root / "receipts"
    receipt_paths = (
        sorted(receipts_root.glob("*.json")) if receipts_root.is_dir() else []
    )
    present_harnesses = set(manifest["harnesses"])
    validate_publish_manifest(
        source_root,
        host_id,
        present_harnesses,
        receipt_paths,
        require_objects=False,
        validation_proofs=validation_cache,
    )
    candidates: list[dict[str, Any]] = []
    for harness, binding in manifest["harnesses"].items():
        index_path = source_root / harness / "index.json"
        index = validate_index_metadata(index_path, host_id, harness)
        rows_by_digest = {row["object_sha256"]: row for row in index["conversations"]}
        for digest in binding["object_sha256"]:
            row = rows_by_digest.get(digest)
            if row is None:
                raise ValueError(f"manifest/index object mismatch: {digest}")
            path = source_root / harness / "objects" / f"{digest}.json"
            assert_no_symlink_components(path)
            if not path.is_file():
                raise FileNotFoundError(f"manifest object is missing: {path}")
            metadata = path.stat()
            candidates.append(
                {
                    "host_id": host_id,
                    "harness": harness,
                    "file_name": path.name,
                    "path": str(path),
                    "size": metadata.st_size,
                    "sha256": digest,
                    "mtime_ns": metadata.st_mtime_ns,
                    "row": row,
                }
            )
    return candidates


def load_state(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    value = read_json(path, label="publisher state")
    if not isinstance(value, dict) or value.get("schema_version") != PUBLISHER_SCHEMA_VERSION:
        raise PublisherConfigError("publisher state schema is invalid")
    uploaded = value.get("uploaded", {})
    if not isinstance(uploaded, dict):
        raise PublisherConfigError("publisher state uploaded map is invalid")
    result: dict[str, dict[str, Any]] = {}
    for name, record in uploaded.items():
        if not isinstance(name, str) or not OBJECT_NAME_RE.fullmatch(name):
            raise PublisherConfigError("publisher state contains an invalid object name")
        if not isinstance(record, dict):
            raise PublisherConfigError("publisher state contains an invalid record")
        result[name] = {
            "file_id": record.get("file_id"),
            "size": record.get("size"),
            "parent_folder_id": record.get("parent_folder_id"),
            "verified_at": record.get("verified_at"),
        }
    return result


def save_state(path: Path, uploaded: dict[str, dict[str, Any]]) -> None:
    atomic_write_json(
        path,
        {
            "schema_version": PUBLISHER_SCHEMA_VERSION,
            "updated_at": utc_iso(),
            "uploaded": uploaded,
        },
    )


def select_candidates(
    candidates: list[dict[str, Any]],
    uploaded: dict[str, dict[str, Any]],
    max_files: int,
    folder_id: str,
) -> list[dict[str, Any]]:
    unseen = [
        item
        for item in candidates
        if not (
            isinstance(uploaded.get(item["file_name"]), dict)
            and uploaded[item["file_name"]].get("parent_folder_id") == folder_id
            and uploaded[item["file_name"]].get("size")
            in {item["size"], str(item["size"])}
            and isinstance(uploaded[item["file_name"]].get("file_id"), str)
            and bool(uploaded[item["file_name"]]["file_id"])
        )
    ]
    # Recent files are first so a newly-created chat is not hidden behind the
    # initial historical backlog.  The stable name tie-breaker keeps retries
    # deterministic and prevents a foreground session from changing the set.
    unseen.sort(key=lambda item: (-item["mtime_ns"], item["host_id"], item["harness"], item["file_name"]))
    return unseen[:max_files]


def make_prompt(candidates: list[dict[str, Any]], folder_id: str) -> str:
    task = {
        "target_folder_id": folder_id,
        "candidates": [
            {
                "file_name": item["file_name"],
                "file_uri": item["path"],
                "byte_size": item["size"],
            }
            for item in candidates
        ],
    }
    return (
        "You are the bounded Google Drive publication worker. Use only the "
        "authenticated Google Drive plugin tools. Do not use shell, read, open, "
        "print, summarize, or transmit any candidate file contents. Do not call "
        "any tool other than google_drive.search, google_drive.upload_file, and "
        "google_drive.get_file_metadata. For each candidate, search the exact "
        "file_name inside target_folder_id. If an exact item already exists, "
        "get its metadata and verify its parent folder, MIME type text/plain, "
        "and byte size; then mark it skipped. Otherwise upload using file_uri as "
        "the local path string, the exact file_name, MIME type text/plain, and "
        "target_folder_id; then read metadata back and verify the same three "
        "fields. Never modify, move, delete, or create anything else. Return "
        "only compact JSON with keys uploaded, skipped, failed. Each uploaded "
        "or skipped item must contain file_name, file_id, byte_size, mime_type, "
        "and parent_folder_id; failed items contain file_name and a fixed "
        "error_code only. Do not include bodies, snippets, or tool transcripts.\n"
        + json.dumps(task, sort_keys=True, separators=(",", ":"))
    )


def _walk_json(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for member in value.values():
            yield from _walk_json(member)
    elif isinstance(value, list):
        for member in value:
            yield from _walk_json(member)


def _parse_json_fragment(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def parse_connector_output(stdout: str) -> list[dict[str, Any]]:
    """Extract body-free Drive metadata from Codex JSONL events."""
    records: list[dict[str, Any]] = []

    def add_records(values: Any, kind: str | None = None) -> None:
        if not isinstance(values, list):
            return
        for member in values:
            if isinstance(member, dict):
                copied = dict(member)
                if kind is not None:
                    copied["_kind"] = kind
                records.append(copied)

    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except (TypeError, ValueError):
            continue
        for item in _walk_json(event):
            if item.get("type") == "agent_message":
                fragments = [item.get("text"), item.get("content"), item.get("message")]
                for fragment in fragments:
                    parsed = _parse_json_fragment(fragment)
                    if isinstance(parsed, dict):
                        for key in ("uploaded", "skipped", "failed"):
                            add_records(parsed.get(key), key)
            for key in ("output", "result", "structuredContent", "structured_content"):
                parsed = _parse_json_fragment(item.get(key))
                if isinstance(parsed, dict):
                    for nested in _walk_json(parsed):
                        if "file_name" in nested and (
                            "file_id" in nested or "error_code" in nested
                        ):
                            records.append(nested)
    # Preserve the last metadata record for each title and keep only bounded
    # scalar fields.  Connector payloads are untrusted input.
    by_name: dict[str, dict[str, Any]] = {}
    for record in records:
        name = record.get("file_name", record.get("name"))
        if not isinstance(name, str) or not OBJECT_NAME_RE.fullmatch(name):
            continue
        parent = record.get(
            "parent_folder_id",
            record.get("parentFolderId", record.get("parent_ids")),
        )
        if isinstance(parent, list):
            parent = parent[0] if len(parent) == 1 else None
        by_name[name] = {
            "file_name": name,
            "file_id": record.get("file_id", record.get("id")),
            "byte_size": record.get("byte_size", record.get("size")),
            "mime_type": record.get("mime_type", record.get("mimeType")),
            "parent_folder_id": parent,
            "error_code": record.get("error_code"),
            "kind": record.get("_kind", "uploaded"),
        }
    return list(by_name.values())


def _valid_connector_record(
    record: dict[str, Any], candidate: dict[str, Any], folder_id: str
) -> bool:
    return (
        record.get("file_name") == candidate["file_name"]
        and isinstance(record.get("file_id"), str)
        and record["file_id"]
        and record.get("byte_size") in {candidate["size"], str(candidate["size"])}
        and record.get("mime_type") == DRIVE_MIME_TYPE
        and record.get("parent_folder_id") == folder_id
    )


def fixed_error_code(value: Any) -> str:
    allowed = {
        "not_found",
        "upload_failed",
        "metadata_verification_failed",
        "existing_metadata_mismatch",
        "connector_error",
    }
    return value if isinstance(value, str) and value in allowed else "connector_error"


def run_codex(config: dict[str, Any], candidates: list[dict[str, Any]]) -> tuple[int, list[dict[str, Any]]]:
    prompt = make_prompt(candidates, config["drive_folder_id"])
    environment = os.environ.copy()
    environment.setdefault("HOME", str(Path.home()))
    environment.setdefault("CODEX_HOME", str(Path.home() / ".codex"))
    environment["PYTHONUNBUFFERED"] = "1"
    path_entries = [
        str(Path(config["codex_command"]).parent),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        environment.get("PATH", ""),
    ]
    environment["PATH"] = os.pathsep.join(item for item in path_entries if item)
    command = [
        config["codex_command"],
        "exec",
        "--ephemeral",
        "--approve-for-me",
        "--skip-git-repo-check",
        "--json",
        "--cd",
        str(config["workspace_root"]),
        prompt,
    ]
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=config["codex_timeout_seconds"],
            env=environment,
        )
    except subprocess.TimeoutExpired:
        return 124, []
    except OSError:
        return 127, []
    return completed.returncode, parse_connector_output(completed.stdout)


def receipt_path(root: Path, run_id: str) -> Path:
    if not RECEIPT_NAME_RE.fullmatch(run_id):
        raise ValueError("invalid publisher run id")
    return root / f"{run_id}.json"


def run_publisher(config_path: Path) -> int:
    started = utc_iso()
    config: dict[str, Any] | None = None
    run_id = utc_now().strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid.uuid4().hex[:8]
    result: dict[str, Any] = {
        "schema_version": PUBLISHER_SCHEMA_VERSION,
        "run_id": run_id,
        "started_at": started,
        "finished_at": None,
        "host_id": None,
        "host_ids": [],
        "drive_folder_id": None,
        "status": "failed",
        "candidate_count": 0,
        "uploaded": [],
        "skipped": [],
        "failed": [],
        "shards": {},
        "errors": [],
    }
    uploaded_state: dict[str, dict[str, Any]] = {}
    try:
        config = load_config(config_path)
        result["host_id"] = config["host_id"]
        result["host_ids"] = config["host_ids"]
        result["drive_folder_id"] = config["drive_folder_id"]
        lease = assert_deployment_lease(config)
        result["lease"] = lease
        uploaded_state = load_state(config["state_path"])
        validation_cache: dict[str, Any] = {}
        candidates: list[dict[str, Any]] = []
        unavailable_shard = False
        with publisher_snapshot_lock(
            config["spool_root"], config["lock_timeout_seconds"]
        ):
            for host_id in config["host_ids"]:
                source_root = config["spool_root"] / "hosts" / host_id
                try:
                    shard_candidates = iter_object_candidates(
                        source_root,
                        host_id,
                        validation_cache=validation_cache,
                    )
                    result["shards"][host_id] = {
                        "status": "validated",
                        "objects": len(shard_candidates),
                    }
                    candidates.extend(shard_candidates)
                except (FileNotFoundError, OSError, ValueError) as error:
                    # A remote shard may legitimately be pending/offline; local
                    # candidates still publish and the next six-hour run retries.
                    result["shards"][host_id] = {
                        "status": "unavailable",
                        "error_code": type(error).__name__,
                    }
                    unavailable_shard = True
        selected = select_candidates(
            candidates,
            uploaded_state,
            config["max_files"],
            config["drive_folder_id"],
        )
        result["candidate_count"] = len(selected)
        if not selected:
            result["status"] = "partial" if unavailable_shard else "idle"
        else:
            # Re-run the canonical body/provenance check only for this bounded
            # batch, while the collector transaction is excluded.
            with publisher_snapshot_lock(
                config["spool_root"], config["lock_timeout_seconds"]
            ):
                for candidate in selected:
                    validated_object_provenance(
                        Path(candidate["path"]),
                        candidate["harness"],
                        candidate["row"],
                        validation_cache,
                    )
            return_code, records = run_codex(config, selected)
            by_name = {record["file_name"]: record for record in records}
            for candidate in selected:
                record = by_name.get(candidate["file_name"])
                if record and _valid_connector_record(
                    record, candidate, config["drive_folder_id"]
                ):
                    normalized = {
                        "file_id": record["file_id"],
                        "size": candidate["size"],
                        "parent_folder_id": config["drive_folder_id"],
                        "verified_at": utc_iso(),
                    }
                    uploaded_state[candidate["file_name"]] = normalized
                    destination = result["skipped"] if record.get("kind") == "skipped" else result["uploaded"]
                    destination.append(
                        {
                            "file_name": candidate["file_name"],
                            "sha256": candidate["sha256"],
                            "byte_size": candidate["size"],
                            "file_id": record["file_id"],
                        }
                    )
                elif record and record.get("error_code"):
                    result["failed"].append(
                        {
                            "file_name": candidate["file_name"],
                            "error_code": fixed_error_code(record["error_code"]),
                        }
                    )
                else:
                    result["failed"].append(
                        {
                            "file_name": candidate["file_name"],
                            "error_code": "metadata_verification_failed",
                        }
                    )
            if return_code != 0:
                result["errors"].append({"error_code": "codex_exec_failed"})
            result["status"] = (
                "completed"
                if not result["failed"] and return_code == 0 and not unavailable_shard
                else "partial"
            )
            save_state(config["state_path"], uploaded_state)
    except LeaseNotHeld as error:
        result["status"] = "stand_down_lease"
        result["errors"].append({"error_code": type(error).__name__})
    except (FileNotFoundError, OSError, TimeoutError, PublisherConfigError, ValueError) as error:
        result["errors"].append({"error_code": type(error).__name__})
    finally:
        result["finished_at"] = utc_iso()
        try:
            receipt_root = (
                config["receipt_root"]
                if config is not None
                else Path(tempfile.gettempdir()) / "ai-chat-archive-publisher-receipts"
            )
            path = receipt_path(receipt_root, run_id)
            atomic_write_json(path, result)
        except Exception:
            # A receipt failure must still be visible to launchd without
            # printing connector output or conversation content.
            result["errors"] = [{"error_code": "receipt_write_failed"}]
            result["finished_at"] = utc_iso()
            try:
                fallback = Path(tempfile.gettempdir()) / f"ai-chat-archive-publisher-{run_id}.json"
                atomic_write_json(fallback, result)
            except Exception:
                pass
    print(json.dumps({
        "run_id": result["run_id"],
        "status": result["status"],
        "candidate_count": result["candidate_count"],
        "uploaded_count": len(result["uploaded"]),
        "skipped_count": len(result["skipped"]),
        "failed_count": len(result["failed"]),
        "error_codes": [item.get("error_code") for item in result["errors"]],
    }, sort_keys=True))
    return 0 if result["status"] in {"completed", "idle", "stand_down_lease"} else 1


def install_launchd(args: argparse.Namespace) -> int:
    config_path = assert_no_symlink_components(Path(args.config).expanduser().resolve())
    config = load_config(config_path)
    label = "com.mattrotundo.ai-chat-archive.drive-publisher"
    agents_dir = (
        Path(args.launch_agents_dir).expanduser().resolve()
        if args.launch_agents_dir
        else Path.home() / "Library" / "LaunchAgents"
    )
    logs_dir = Path.home() / "Library" / "Logs" / "AIChatArchive"
    secure_mkdir(logs_dir)
    logs = [
        logs_dir / "drive-publisher.out.log",
        logs_dir / "drive-publisher.err.log",
    ]
    for path in logs:
        secure_mkdir(path.parent)
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
    job = {
        "Label": label,
        "ProgramArguments": [
            sys.executable,
            str(Path(__file__).resolve()),
            "run",
            "--config",
            str(config_path),
        ],
        "WorkingDirectory": str(Path(__file__).resolve().parent),
        "RunAtLoad": True,
        "StartInterval": config["interval_seconds"],
        "ProcessType": "Background",
        "StandardOutPath": str(logs[0]),
        "StandardErrorPath": str(logs[1]),
        "Umask": 0o077,
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
    }
    secure_mkdir(agents_dir)
    plist_path = agents_dir / f"{label}.plist"
    payload = plistlib.dumps(job, fmt=plistlib.FMT_XML)
    atomic_write_bytes(plist_path, payload)
    loaded = False
    if not args.no_load:
        domain = f"gui/{os.getuid()}"
        subprocess.run(
            ["launchctl", "bootout", f"{domain}/{label}"],
            check=False,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["launchctl", "enable", f"{domain}/{label}"],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["launchctl", "bootstrap", domain, str(plist_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        loaded = True
    print(json.dumps({"label": label, "loaded": loaded, "plist_path": str(plist_path)}))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--config", required=True)
    run.set_defaults(func=lambda args: run_publisher(Path(args.config).expanduser().resolve()))
    install = commands.add_parser("install-launchd")
    install.add_argument("--config", required=True)
    install.add_argument("--launch-agents-dir")
    install.add_argument("--no-load", action="store_true")
    install.set_defaults(func=install_launchd)
    return root


def main() -> int:
    args = parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
