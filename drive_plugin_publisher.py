#!/usr/bin/env python3
"""Publish validated archive objects through the authenticated Codex Drive plugin.

The collector is intentionally independent from this publisher.  Collection and
manifest commits remain local and launchd-owned even when the Drive connector is
unavailable.  This process passes only manifest-bound object paths and metadata
to ``codex exec``; it never puts an archive body in a prompt, receipt, or log.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import fcntl
import hashlib
import json
import os
import plistlib
import re
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from fleet_chat_archive import (
    APPROVED_HARNESSES,
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
DEFAULT_APP_SERVER_SOCKET = (
    Path.home() / ".codex" / "app-server-control" / "app-server-control.sock"
)
DEFAULT_APP_SERVER_CLIENT_NAME = "ai-chat-archive-drive-publisher"
DEFAULT_APP_SERVER_CLIENT_VERSION = "1.0.0"
APP_SERVER_NAME = "codex_apps"
APP_SERVER_MAX_FRAME_BYTES = 8 * 1024 * 1024
WEBSOCKET_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


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


def normalize_drive_transport(value: Any) -> str:
    """Return the small, explicit set of supported publication transports."""
    if not isinstance(value, str):
        raise PublisherConfigError("drive_transport must be a string")
    normalized = value.strip().lower().replace("-", "_")
    aliases = {
        "app_server": "app_server",
        "appserver": "app_server",
        "codex_app_server": "app_server",
        "codex_exec": "codex_exec",
        "exec": "codex_exec",
        "legacy": "codex_exec",
    }
    try:
        return aliases[normalized]
    except KeyError as error:
        raise PublisherConfigError(
            "drive_transport must be app_server or codex_exec"
        ) from error


def normalize_app_server_mode(value: Any) -> str:
    """Return the supported app-server publication mode."""
    if not isinstance(value, str):
        raise PublisherConfigError("app_server_mode must be a string")
    normalized = value.strip().lower().replace("-", "_")
    aliases = {
        "direct": "direct",
        "rpc": "direct",
        "model_turn": "model_turn",
        "model": "model_turn",
    }
    try:
        return aliases[normalized]
    except KeyError as error:
        raise PublisherConfigError(
            "app_server_mode must be direct or model_turn"
        ) from error


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
        "drive_transport",
        "transport",
        "app_server_mode",
        "app_server_socket",
        "app_server_socket_path",
        "app_server_timeout_seconds",
        "app_server_client_name",
        "app_server_client_version",
        "app_server_approval_policy",
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
    minimum_batch = len(host_ids) * len(APPROVED_HARNESSES)
    if max_files < minimum_batch:
        raise PublisherConfigError(
            "max_files must provide one slot per approved harness for every host"
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
    transport_input = config.get("drive_transport", config.get("transport", "codex_exec"))
    drive_transport = normalize_drive_transport(transport_input)
    app_server_mode = normalize_app_server_mode(config.get("app_server_mode", "direct"))
    socket_input = config.get(
        "app_server_socket",
        config.get("app_server_socket_path", str(DEFAULT_APP_SERVER_SOCKET)),
    )
    if not isinstance(socket_input, str) or not socket_input.startswith("/"):
        raise PublisherConfigError("app_server_socket must be an absolute path")
    app_server_socket = assert_no_symlink_components(
        Path(socket_input), include_leaf=True
    )
    app_server_timeout = validate_positive_int(
        config.get("app_server_timeout_seconds", codex_timeout),
        "app_server_timeout_seconds",
        24 * 60 * 60,
    )
    app_server_client_name = config.get(
        "app_server_client_name", DEFAULT_APP_SERVER_CLIENT_NAME
    )
    app_server_client_version = config.get(
        "app_server_client_version", DEFAULT_APP_SERVER_CLIENT_VERSION
    )
    if (
        not isinstance(app_server_client_name, str)
        or not app_server_client_name
        or "\n" in app_server_client_name
        or "\r" in app_server_client_name
    ):
        raise PublisherConfigError("app_server_client_name is invalid")
    if (
        not isinstance(app_server_client_version, str)
        or not app_server_client_version
        or "\n" in app_server_client_version
        or "\r" in app_server_client_version
    ):
        raise PublisherConfigError("app_server_client_version is invalid")
    app_server_approval_policy = config.get("app_server_approval_policy", "never")
    if app_server_approval_policy != "never":
        raise PublisherConfigError(
            "app_server_approval_policy must be never for unattended publishing"
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
        "drive_transport": drive_transport,
        "app_server_mode": app_server_mode,
        "app_server_socket": app_server_socket,
        "app_server_timeout_seconds": app_server_timeout,
        "app_server_client_name": app_server_client_name,
        "app_server_client_version": app_server_client_version,
        "app_server_approval_policy": app_server_approval_policy,
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
    # A receiver-side bulk transfer can give every historical Studio object a
    # newer mtime than a freshly collected New object.  Global mtime ordering
    # then starves that source for dozens of six-hour batches.  Partition by
    # validated host/harness, order each queue newest-first, and take one item
    # from each queue per pass.  The group key and filename tie-breakers keep
    # retries deterministic while guaranteeing every non-empty source group a
    # bounded share of the batch.
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    last_verified_at: dict[tuple[str, str], str] = {}
    for item in unseen:
        groups.setdefault((item["host_id"], item["harness"]), []).append(item)
    for item in candidates:
        record = uploaded.get(item["file_name"])
        if not isinstance(record, dict):
            continue
        if (
            record.get("parent_folder_id") != folder_id
            or record.get("size") not in {item["size"], str(item["size"])}
            or not isinstance(record.get("file_id"), str)
            or not record["file_id"]
        ):
            continue
        verified_at = record.get("verified_at")
        if not isinstance(verified_at, str):
            continue
        group_key = (item["host_id"], item["harness"])
        if verified_at > last_verified_at.get(group_key, ""):
            last_verified_at[group_key] = verified_at
    for group in groups.values():
        group.sort(key=lambda item: (item["mtime_ns"], item["file_name"]), reverse=True)
    selected: list[dict[str, Any]] = []
    # Rotate the starting group by the last successful metadata verification.
    # This keeps a small custom batch from repeatedly serving only the first
    # lexicographic group; groups with no successful publication go first, and
    # equal timestamps retain a stable key order.
    group_keys = sorted(groups, key=lambda key: (last_verified_at.get(key, ""), key))
    while len(selected) < max_files:
        progressed = False
        for key in group_keys:
            queue = groups[key]
            if not queue:
                continue
            selected.append(queue.pop(0))
            progressed = True
            if len(selected) >= max_files:
                break
        if not progressed:
            break
    return selected


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
            # The final agent message is the preferred compact contract, but
            # Codex can terminate after a successful tool sequence without
            # emitting that message.  Drive tool results still contain the
            # bounded metadata needed to reconcile state; capture those
            # records directly so a successful upload is never retried merely
            # because the model's final response was lost.
            if item.get("type") == "mcp_tool_call":
                tool = item.get("tool")
                tool_kind = (
                    "uploaded"
                    if tool == "google_drive.upload_file"
                    else "skipped"
                    if tool in {"google_drive.search", "google_drive.get_file_metadata"}
                    else None
                )
                if tool_kind is not None:
                    for nested in _walk_json(item.get("result")):
                        if not isinstance(nested, dict):
                            continue
                        name = nested.get(
                            "file_name",
                            nested.get(
                                "name",
                                nested.get(
                                    "title", nested.get("display_title")
                                ),
                            ),
                        )
                        file_id = nested.get("file_id", nested.get("id"))
                        if (
                            isinstance(name, str)
                            and OBJECT_NAME_RE.fullmatch(name)
                            and isinstance(file_id, str)
                            and file_id
                        ):
                            records.append(
                                {
                                    "file_name": name,
                                    "file_id": file_id,
                                    "byte_size": nested.get(
                                        "byte_size", nested.get("size")
                                    ),
                                    "mime_type": nested.get(
                                        "mime_type", nested.get("mimeType")
                                    ),
                                    "parent_folder_id": nested.get(
                                        "parent_folder_id",
                                        nested.get(
                                            "parentFolderId",
                                            nested.get("parent_ids"),
                                        ),
                                    ),
                                    "_kind": tool_kind,
                                }
                            )
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
        name = record.get(
            "file_name",
            record.get("name", record.get("title", record.get("display_title"))),
        )
        if not isinstance(name, str) or not OBJECT_NAME_RE.fullmatch(name):
            continue
        parent = record.get(
            "parent_folder_id",
            record.get("parentFolderId", record.get("parent_ids")),
        )
        if isinstance(parent, list):
            parent = parent[0] if len(parent) == 1 else None
        normalized = {
            "file_name": name,
            "file_id": record.get("file_id", record.get("id")),
            "byte_size": record.get("byte_size", record.get("size")),
            "mime_type": record.get("mime_type", record.get("mimeType")),
            "parent_folder_id": parent,
            "error_code": record.get("error_code"),
            "kind": record.get("_kind", "uploaded"),
        }
        # A successful upload is followed by a metadata readback.  If the
        # final compact response is missing, do not let that readback's
        # ``skipped`` classification erase evidence that this process wrote
        # the file.
        prior = by_name.get(name)
        if prior and prior.get("kind") == "uploaded" and normalized["kind"] == "skipped":
            continue
        by_name[name] = normalized
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
        "lease_not_held",
    }
    return value if isinstance(value, str) and value in allowed else "connector_error"


class AppServerError(RuntimeError):
    """The local Codex app-server transport or RPC returned an unsafe result."""


class AppServerTimeout(AppServerError):
    """The app-server operation exceeded its bounded deadline."""


class _AppServerWebSocket:
    """Small stdlib WebSocket client for the owner-only local app-server socket."""

    def __init__(self, socket_path: Path, timeout_seconds: float):
        self.socket_path = socket_path
        self.deadline = time.monotonic() + timeout_seconds
        self.socket: socket.socket | None = None
        self._next_id = 1
        self._pending: dict[int, Any] = {}
        self._receive_buffer = bytearray()

    def _remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise AppServerTimeout("app-server deadline exceeded")
        return remaining

    def _set_timeout(self) -> None:
        if self.socket is not None:
            self.socket.settimeout(self._remaining())

    def connect(self) -> None:
        if not self.socket_path.is_absolute():
            raise AppServerError("app-server socket must be absolute")
        try:
            metadata = self.socket_path.lstat()
        except OSError as error:
            raise AppServerError("app-server socket is missing") from error
        if not stat.S_ISSOCK(metadata.st_mode):
            raise AppServerError("app-server endpoint is not a Unix socket")
        if metadata.st_uid != os.getuid():
            raise AppServerError("app-server socket owner mismatch")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise AppServerError("app-server socket permissions are too broad")
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket = client
        try:
            self._set_timeout()
            client.connect(str(self.socket_path))
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            request = (
                "GET / HTTP/1.1\r\n"
                "Host: localhost\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            ).encode("ascii")
            client.sendall(request)
            response = bytearray()
            while b"\r\n\r\n" not in response:
                if len(response) > 64 * 1024:
                    raise AppServerError("app-server handshake is too large")
                self._set_timeout()
                chunk = client.recv(4096)
                if not chunk:
                    raise AppServerError("app-server closed during handshake")
                response.extend(chunk)
            header, remainder = bytes(response).split(b"\r\n\r\n", 1)
            self._receive_buffer.extend(remainder)
            lines = header.split(b"\r\n")
            if not lines or not lines[0].startswith(b"HTTP/1.1 101"):
                raise AppServerError("app-server websocket upgrade failed")
            headers: dict[bytes, bytes] = {}
            for line in lines[1:]:
                if b":" not in line:
                    continue
                name, value = line.split(b":", 1)
                headers[name.strip().lower()] = value.strip()
            expected_accept = base64.b64encode(
                hashlib.sha1(key.encode("ascii") + WEBSOCKET_GUID).digest()
            )
            if headers.get(b"sec-websocket-accept") != expected_accept:
                raise AppServerError("app-server websocket accept mismatch")
        except socket.timeout as error:
            self.close()
            if time.monotonic() >= self.deadline:
                raise AppServerTimeout("app-server socket deadline exceeded") from error
            raise AppServerError("app-server socket connection timed out") from error
        except OSError as error:
            self.close()
            raise AppServerError("app-server socket connection failed") from error

    def _send_frame(self, payload: bytes, opcode: int = 0x1) -> None:
        if self.socket is None:
            raise AppServerError("app-server socket is not connected")
        if len(payload) > APP_SERVER_MAX_FRAME_BYTES:
            raise AppServerError("app-server frame exceeds maximum size")
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        length = len(payload)
        if length < 126:
            header = bytes((0x80 | opcode, 0x80 | length))
        elif length <= 0xFFFF:
            header = bytes((0x80 | opcode, 0x80 | 126)) + struct.pack(">H", length)
        else:
            header = bytes((0x80 | opcode, 0x80 | 127)) + struct.pack(">Q", length)
        try:
            self._set_timeout()
            self.socket.sendall(header + mask + masked)
        except socket.timeout as error:
            if time.monotonic() >= self.deadline:
                raise AppServerTimeout("app-server send deadline exceeded") from error
            raise AppServerError("app-server send timed out") from error
        except OSError as error:
            raise AppServerError("app-server send failed") from error

    def _recv_exact(self, length: int) -> bytes:
        if self.socket is None:
            raise AppServerError("app-server socket is not connected")
        data = bytearray()
        if self._receive_buffer:
            take = min(length, len(self._receive_buffer))
            data.extend(self._receive_buffer[:take])
            del self._receive_buffer[:take]
        while len(data) < length:
            try:
                self._set_timeout()
                chunk = self.socket.recv(length - len(data))
            except socket.timeout as error:
                if time.monotonic() >= self.deadline:
                    raise AppServerTimeout("app-server receive deadline exceeded") from error
                raise AppServerError("app-server receive timed out") from error
            except OSError as error:
                raise AppServerError("app-server receive failed") from error
            if not chunk:
                raise AppServerError("app-server closed the websocket")
            data.extend(chunk)
        return bytes(data)

    def _recv_frame(self) -> tuple[int, bytes]:
        first, second = self._recv_exact(2)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._recv_exact(8))[0]
        if length > APP_SERVER_MAX_FRAME_BYTES:
            raise AppServerError("app-server frame exceeds maximum size")
        mask = self._recv_exact(4) if masked else b""
        payload = self._recv_exact(length)
        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return opcode, payload

    def _notification(self, method: str, params: dict[str, Any]) -> None:
        self._send_json(
            {"method": method, "params": params},
        )

    def _send_json(self, value: dict[str, Any]) -> None:
        payload = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
        self._send_frame(payload)

    def request(self, method: str, params: dict[str, Any]) -> Any:
        request_id = self._next_id
        self._next_id += 1
        if request_id in self._pending:
            return self._pending.pop(request_id)
        payload = json.dumps(
            {"id": request_id, "method": method, "params": params},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        self._send_frame(payload)
        while True:
            opcode, raw = self._recv_frame()
            if opcode == 0x9:  # ping
                self._send_frame(raw, opcode=0xA)
                continue
            if opcode == 0x8:
                raise AppServerError("app-server sent a close frame")
            if opcode != 0x1:
                continue
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as error:
                raise AppServerError("app-server returned invalid JSON") from error
            if not isinstance(value, dict):
                continue
            # The app-server may send a request while a response is pending.
            # This unattended client implements no server-side operations, so
            # reject such requests explicitly rather than treating their id as
            # our response.  Elicitation is deliberately not advertised.
            if isinstance(value.get("method"), str):
                server_request_id = value.get("id")
                if isinstance(server_request_id, int):
                    self._send_json(
                        {
                            "id": server_request_id,
                            "error": {
                                "code": -32601,
                                "message": "server requests are unsupported",
                            },
                        }
                    )
                continue
            if not isinstance(value.get("id"), int):
                continue
            response_id = value["id"]
            if response_id != request_id:
                if value.get("error") is None:
                    self._pending[response_id] = value.get("result")
                continue
            if value.get("error") is not None:
                raise AppServerError("app-server RPC returned an error")
            return value.get("result")

    def close(self) -> None:
        client = self.socket
        if client is None:
            return
        try:
            client.settimeout(0.5)
            mask = os.urandom(4)
            client.sendall(bytes((0x88, 0x80)) + mask)
        except Exception:
            pass
        try:
            client.close()
        except OSError:
            pass
        self.socket = None

    def __enter__(self) -> "_AppServerWebSocket":
        self.connect()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


def _drive_metadata_values(value: Any) -> Iterator[dict[str, Any]]:
    """Yield only scalar Drive metadata; never retain connector bodies."""
    if isinstance(value, dict):
        name = value.get(
            "file_name",
            value.get("name", value.get("title", value.get("display_title"))),
        )
        file_id = value.get("file_id", value.get("id"))
        if isinstance(name, str) and OBJECT_NAME_RE.fullmatch(name) and isinstance(file_id, str):
            parents = value.get(
                "parent_folder_id",
                value.get(
                    "parentFolderId",
                    value.get(
                        "parent_ids",
                        value.get(
                            "parentIds",
                            value.get("parents", value.get("parent_id")),
                        ),
                    ),
                ),
            )
            if isinstance(parents, list):
                parent_ids = [item for item in parents if isinstance(item, str)]
            elif isinstance(parents, str):
                parent_ids = [parents]
            else:
                parent_ids = []
            yield {
                "file_name": name,
                "file_id": file_id,
                "byte_size": value.get("byte_size", value.get("size")),
                "mime_type": value.get("mime_type", value.get("mimeType")),
                "parent_ids": parent_ids,
            }
        for member in value.values():
            yield from _drive_metadata_values(member)
    elif isinstance(value, list):
        for member in value:
            yield from _drive_metadata_values(member)


def _app_server_result_metadata(result: Any, file_name: str) -> list[dict[str, Any]]:
    return [
        item
        for item in _drive_metadata_values(result)
        if item["file_name"] == file_name and DRIVE_ID_RE.fullmatch(item["file_id"])
    ]


def _app_server_result_ids(result: Any) -> list[str]:
    """Collect provider file IDs when an upload response omits its title."""
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key in ("file_id", "id"):
                candidate = value.get(key)
                if isinstance(candidate, str) and DRIVE_ID_RE.fullmatch(candidate):
                    found.add(candidate)
            for member in value.values():
                walk(member)
        elif isinstance(value, list):
            for member in value:
                walk(member)

    walk(result)
    return sorted(found)


def _app_server_verified_record(
    metadata: dict[str, Any],
    candidate: dict[str, Any],
    folder_id: str,
    expected_file_id: str | None = None,
) -> dict[str, Any] | None:
    if (
        (expected_file_id is not None and metadata.get("file_id") != expected_file_id)
        or
        metadata.get("byte_size") not in {candidate["size"], str(candidate["size"])}
        or metadata.get("mime_type") != DRIVE_MIME_TYPE
        or set(metadata.get("parent_ids", [])) != {folder_id}
    ):
        return None
    return {
        "file_name": candidate["file_name"],
        "file_id": metadata["file_id"],
        "byte_size": candidate["size"],
        "mime_type": DRIVE_MIME_TYPE,
        "parent_folder_id": folder_id,
    }


def _app_server_tool(
    client: _AppServerWebSocket, thread_id: str, tool: str, arguments: dict[str, Any]
) -> Any:
    result = client.request(
        "mcpServer/tool/call",
        {
            "threadId": thread_id,
            "server": APP_SERVER_NAME,
            "tool": tool,
            "arguments": arguments,
        },
    )
    if isinstance(result, dict) and result.get("isError") is True:
        raise AppServerError("Drive connector returned an error")
    return result


def _app_server_publish_one(
    client: _AppServerWebSocket,
    thread_id: str,
    candidate: dict[str, Any],
    folder_id: str,
    before_upload: Callable[[], None] | None = None,
) -> dict[str, Any]:
    name = candidate["file_name"]
    search = _app_server_tool(
        client,
        thread_id,
        "google_drive.search",
        {
            "query": name,
            "topn": 20,
            "special_filter_query_str": f"'{folder_id}' in parents and trashed = false",
            "best_effort_fetch": False,
        },
    )
    search_matches = _app_server_result_metadata(search, name)
    if search_matches:
        for match in sorted(search_matches, key=lambda item: item["file_id"]):
            metadata_result = _app_server_tool(
                client,
                thread_id,
                "google_drive.get_file_metadata",
                {"fileId": match["file_id"]},
            )
            for metadata in _app_server_result_metadata(metadata_result, name):
                verified = _app_server_verified_record(
                    metadata, candidate, folder_id, expected_file_id=match["file_id"]
                )
                if verified is not None:
                    verified["kind"] = "skipped"
                    return verified
        return {"file_name": name, "error_code": "existing_metadata_mismatch"}

    if before_upload is not None:
        try:
            before_upload()
        except LeaseNotHeld:
            return {"file_name": name, "error_code": "lease_not_held"}
    upload = _app_server_tool(
        client,
        thread_id,
        "google_drive.upload_file",
        {
            "file_uri": candidate["path"],
            "file_name": name,
            "mime_type": DRIVE_MIME_TYPE,
            "parent_folder_id": folder_id,
        },
    )
    upload_matches = _app_server_result_metadata(upload, name)
    upload_ids = [item["file_id"] for item in upload_matches]
    if not upload_ids:
        upload_ids = _app_server_result_ids(upload)
    if len(upload_ids) != 1:
        return {"file_name": name, "error_code": "upload_failed"}
    file_id = upload_ids[0]
    metadata_result = _app_server_tool(
        client,
        thread_id,
        "google_drive.get_file_metadata",
        {"fileId": file_id},
    )
    for metadata in _app_server_result_metadata(metadata_result, name):
        verified = _app_server_verified_record(
            metadata, candidate, folder_id, expected_file_id=file_id
        )
        if verified is not None:
            verified["kind"] = "uploaded"
            return verified
    return {"file_name": name, "error_code": "metadata_verification_failed"}


def _app_server_turn_upload_names(turn: Any) -> set[str]:
    """Extract only successful upload titles from a completed model turn."""
    names: set[str] = set()
    items = turn.get("items") if isinstance(turn, dict) else None
    if not isinstance(items, list):
        return names
    for item in items:
        if not isinstance(item, dict) or item.get("type") != "mcpToolCall":
            continue
        if item.get("tool") != "google_drive.upload_file":
            continue
        if item.get("status") != "completed" or item.get("error") is not None:
            continue
        arguments = item.get("arguments")
        if isinstance(arguments, str):
            arguments = _parse_json_fragment(arguments)
        if not isinstance(arguments, dict):
            continue
        name = arguments.get("file_name")
        if isinstance(name, str) and OBJECT_NAME_RE.fullmatch(name):
            names.add(name)
    return names


def _app_server_turn_tool_errors(turn: Any) -> set[str]:
    """Extract bounded tool names that ended with an error."""
    errors: set[str] = set()
    items = turn.get("items") if isinstance(turn, dict) else None
    if not isinstance(items, list):
        return errors
    for item in items:
        if not isinstance(item, dict) or item.get("type") != "mcpToolCall":
            continue
        if item.get("status") == "failed" or item.get("error") is not None:
            tool = item.get("tool")
            if isinstance(tool, str):
                errors.add(tool)
    return errors


def _app_server_model_turn(
    client: _AppServerWebSocket,
    thread_id: str,
    prompt: str,
    cwd: Path,
) -> dict[str, Any]:
    """Run one unattended model turn and retain only its bounded summary."""
    response = client.request(
        "turn/start",
        {
            "threadId": thread_id,
            "clientUserMessageId": f"archive-publisher-{uuid.uuid4().hex}",
            "input": [{"type": "text", "text": prompt, "text_elements": []}],
            "approvalPolicy": "never",
            "cwd": str(cwd),
        },
    )
    if isinstance(response, dict) and response.get("error") is not None:
        raise AppServerError("app-server turn failed to start")
    while True:
        opcode, raw = client._recv_frame()
        if opcode == 0x9:  # ping
            client._send_frame(raw, opcode=0xA)
            continue
        if opcode == 0x8:
            raise AppServerError("app-server closed during model turn")
        if opcode != 0x1:
            continue
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise AppServerError("app-server returned invalid turn JSON") from error
        if not isinstance(value, dict):
            continue
        if isinstance(value.get("method"), str):
            server_request_id = value.get("id")
            if isinstance(server_request_id, int):
                client._send_json(
                    {
                        "id": server_request_id,
                        "error": {
                            "code": -32601,
                            "message": "server requests are unsupported",
                        },
                    }
                )
            if value["method"] != "turn/completed":
                continue
            params = value.get("params")
            turn = params.get("turn") if isinstance(params, dict) else None
            if not isinstance(turn, dict):
                raise AppServerError("app-server omitted completed turn")
            return {
                "status": turn.get("status"),
                "upload_names": sorted(_app_server_turn_upload_names(turn)),
                "tool_errors": sorted(_app_server_turn_tool_errors(turn)),
            }


def _app_server_verify_one(
    client: _AppServerWebSocket,
    thread_id: str,
    candidate: dict[str, Any],
    folder_id: str,
    *,
    uploaded: bool,
) -> dict[str, Any]:
    """Re-derive one model-turn result through exact Drive metadata calls."""
    name = candidate["file_name"]
    search = _app_server_tool(
        client,
        thread_id,
        "google_drive.search",
        {
            "query": name,
            "topn": 20,
            "special_filter_query_str": f"'{folder_id}' in parents and trashed = false",
            "best_effort_fetch": False,
        },
    )
    matches = _app_server_result_metadata(search, name)
    verified: list[dict[str, Any]] = []
    for match in sorted(matches, key=lambda item: item["file_id"]):
        metadata_result = _app_server_tool(
            client,
            thread_id,
            "google_drive.get_file_metadata",
            {"fileId": match["file_id"]},
        )
        for metadata in _app_server_result_metadata(metadata_result, name):
            record = _app_server_verified_record(
                metadata,
                candidate,
                folder_id,
                expected_file_id=match["file_id"],
            )
            if record is not None:
                verified.append(record)
    if len(verified) == 1:
        verified[0]["kind"] = "uploaded" if uploaded else "skipped"
        return verified[0]
    if len(verified) > 1:
        return {"file_name": name, "error_code": "existing_metadata_mismatch"}
    return {"file_name": name, "error_code": "metadata_verification_failed"}


def run_app_server_model(
    config: dict[str, Any], candidates: list[dict[str, Any]]
) -> tuple[int, list[dict[str, Any]]]:
    """Publish through a runtime model turn, then verify through direct RPC."""
    records: list[dict[str, Any]] = []
    if not candidates:
        return 0, records
    transport_timeout = float(config["app_server_timeout_seconds"])
    before_upload: Callable[[], None] | None = None
    if {
        "deployment_lease_path",
        "lease_owner",
        "lease_host",
    }.issubset(config):
        lease = assert_deployment_lease(config)
        expires = datetime.fromisoformat(lease["expires_at"])
        remaining = (expires - utc_now()).total_seconds()
        if remaining <= 0:
            raise LeaseNotHeld("deployment lease has expired")
        transport_timeout = min(transport_timeout, remaining)
        before_upload = lambda: assert_deployment_lease(config)
    with _AppServerWebSocket(config["app_server_socket"], transport_timeout) as client:
        client.request(
            "initialize",
            {
                "clientInfo": {
                    "name": config["app_server_client_name"],
                    "version": config["app_server_client_version"],
                },
                "capabilities": {"experimentalApi": True},
            },
        )
        client._notification("initialized", {})
        thread_result = client.request(
            "thread/start",
            {"ephemeral": True, "approvalPolicy": config["app_server_approval_policy"]},
        )
        thread = thread_result.get("thread") if isinstance(thread_result, dict) else None
        thread_id = (
            thread.get("id")
            if isinstance(thread, dict)
            else thread_result.get("threadId")
            if isinstance(thread_result, dict)
            else None
        )
        if not isinstance(thread_id, str) or not thread_id:
            raise AppServerError("app-server did not return an ephemeral thread id")
        for candidate in candidates:
            try:
                if before_upload is not None:
                    before_upload()
                turn = _app_server_model_turn(
                    client,
                    thread_id,
                    make_prompt([candidate], config["drive_folder_id"]),
                    config["workspace_root"],
                )
                uploaded_names = set(turn["upload_names"])
                record = _app_server_verify_one(
                    client,
                    thread_id,
                    candidate,
                    config["drive_folder_id"],
                    uploaded=candidate["file_name"] in uploaded_names,
                )
                if record.get("error_code") and turn["tool_errors"]:
                    record["error_code"] = "connector_error"
                records.append(record)
            except AppServerTimeout:
                raise
            except (AppServerError, LeaseNotHeld):
                records.append(
                    {"file_name": candidate["file_name"], "error_code": "connector_error"}
                )
    return (
        0 if len(records) == len(candidates) and all(not record.get("error_code") for record in records) else 1,
        records,
    )


def run_app_server(
    config: dict[str, Any], candidates: list[dict[str, Any]]
) -> tuple[int, list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    if not candidates:
        return 0, records
    before_upload: Callable[[], None] | None = None
    transport_timeout = float(config["app_server_timeout_seconds"])
    if {
        "deployment_lease_path",
        "lease_owner",
        "lease_host",
    }.issubset(config):
        lease = assert_deployment_lease(config)
        expires = datetime.fromisoformat(lease["expires_at"])
        remaining = (expires - utc_now()).total_seconds()
        if remaining <= 0:
            raise LeaseNotHeld("deployment lease has expired")
        transport_timeout = min(transport_timeout, remaining)
        before_upload = lambda: assert_deployment_lease(config)
    try:
        with _AppServerWebSocket(
            config["app_server_socket"], transport_timeout
        ) as client:
            client.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": config["app_server_client_name"],
                        "version": config["app_server_client_version"],
                    },
                    "capabilities": {
                        "experimentalApi": True,
                    },
                },
            )
            client._notification("initialized", {})
            thread_result = client.request(
                "thread/start",
                {"ephemeral": True, "approvalPolicy": config["app_server_approval_policy"]},
            )
            thread = thread_result.get("thread") if isinstance(thread_result, dict) else None
            thread_id = (
                thread.get("id")
                if isinstance(thread, dict)
                else thread_result.get("threadId")
                if isinstance(thread_result, dict)
                else None
            )
            if not isinstance(thread_id, str) or not thread_id:
                raise AppServerError("app-server did not return an ephemeral thread id")
            for candidate in candidates:
                try:
                    records.append(
                        _app_server_publish_one(
                            client,
                            thread_id,
                            candidate,
                            config["drive_folder_id"],
                            before_upload=before_upload,
                        )
                    )
                except AppServerTimeout:
                    raise
                except AppServerError:
                    records.append(
                        {"file_name": candidate["file_name"], "error_code": "connector_error"}
                    )
    except AppServerTimeout:
        completed = {record.get("file_name") for record in records}
        records.extend(
            {
                "file_name": candidate["file_name"],
                "error_code": "connector_error",
            }
            for candidate in candidates
            if candidate["file_name"] not in completed
        )
        return 124, records
    except AppServerError:
        completed = {record.get("file_name") for record in records}
        records.extend(
            {
                "file_name": candidate["file_name"],
                "error_code": "connector_error",
            }
            for candidate in candidates
            if candidate["file_name"] not in completed
        )
        return 1, records
    return (
        0 if len(records) == len(candidates) and all(not record.get("error_code") for record in records) else 1,
        records,
    )


def run_codex(config: dict[str, Any], candidates: list[dict[str, Any]]) -> tuple[int, list[dict[str, Any]]]:
    if config.get("drive_transport") == "app_server":
        if config.get("app_server_mode") == "model_turn":
            return run_app_server_model(config, candidates)
        return run_app_server(config, candidates)
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
        "drive_transport": None,
        "app_server_mode": None,
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
        result["drive_transport"] = config["drive_transport"]
        result["app_server_mode"] = config["app_server_mode"]
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
                result["errors"].append(
                    {
                        "error_code": (
                            "app_server_failed"
                            if config["drive_transport"] == "app_server"
                            else "codex_exec_failed"
                        )
                    }
                )
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
