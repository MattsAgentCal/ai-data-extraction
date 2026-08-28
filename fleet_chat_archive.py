#!/usr/bin/env python3
"""Content-addressed, host-namespaced chat archive collector."""

from __future__ import annotations

import argparse
import contextlib
import errno
import fcntl
import hashlib
import json
import os
import plistlib
import re
import select
import shutil
import signal
import struct
import subprocess
import stat
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from archive_object_contract import (
    ARCHIVE_OBJECT_SCHEMA_VERSION,
    validate_archive_object,
)

from extract_claude_code import extract_claude_session, find_all_claude_sessions
from extract_codex import extract_codex_session, find_all_codex_sessions
from extract_hermes import extract_hermes_export, iter_hermes_export
from extract_openclaw import extract_openclaw_session, find_all_openclaw_sessions


class TerminationRequested(BaseException):
    """Cancellation signal that operational exception handlers must not swallow."""


_RUN_CONFIG_TRANSACTION = object()


HOST_ID_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62})\Z")
SSH_HOST_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._@-]{0,254})\Z")
REMOTE_SPOOL_ROOT_RE = re.compile(
    r"/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+\Z"
)
APPROVED_SOURCE_KEYS = {
    "claude_roots",
    "codex_roots",
    "openclaw_roots",
    "hermes_exports",
    "hermes_instances",
}
PRIVATE_KEY_PATTERN = (
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----"
)
PROVIDER_TOKEN_PATTERN = (
    r"\b(?:sk-[A-Za-z0-9_-]{20,}|gh[opusr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})\b"
)
JWT_PATTERN = (
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
)
AUTH_HEADER_PATTERN = r"\b(?:authorization|proxy-authorization)\s*:\s*[^\r\n]+"
COOKIE_HEADER_PATTERN = r"\b(?:cookie|set-cookie)\s*:\s*[^\r\n]+"
GENERIC_BEARER_PATTERN = r"\b(?:bearer|basic)\s+[A-Za-z0-9+/=_-]{8,}"
PRIVATE_KEY_RE = re.compile(
    PRIVATE_KEY_PATTERN,
    re.IGNORECASE | re.DOTALL,
)
PROVIDER_TOKEN_RE = re.compile(
    PROVIDER_TOKEN_PATTERN
)
JWT_RE = re.compile(JWT_PATTERN)
AUTH_HEADER_RE = re.compile(AUTH_HEADER_PATTERN, re.IGNORECASE)
COOKIE_HEADER_RE = re.compile(COOKIE_HEADER_PATTERN, re.IGNORECASE)
GENERIC_BEARER_RE = re.compile(GENERIC_BEARER_PATTERN, re.IGNORECASE)
REDACTABLE_SECRET_PATTERN = "|".join(
    (
        f"(?is:{PRIVATE_KEY_PATTERN})",
        f"(?:{PROVIDER_TOKEN_PATTERN})",
        f"(?:{JWT_PATTERN})",
        f"(?i:{AUTH_HEADER_PATTERN})",
        f"(?i:{COOKIE_HEADER_PATTERN})",
        f"(?i:{GENERIC_BEARER_PATTERN})",
    )
)
REDACTABLE_SECRET_RE = re.compile(REDACTABLE_SECRET_PATTERN)
RESIDUAL_SECRET_RE = re.compile(REDACTABLE_SECRET_PATTERN)
SENSITIVE_KEY_PARTS = {
    "apikey",
    "accesstoken",
    "authtoken",
    "authorization",
    "clientsecret",
    "cookie",
    "credential",
    "credentials",
    "password",
    "passwd",
    "privatekey",
    "proxyauthorization",
    "refreshtoken",
    "secret",
    "secretaccesskey",
    "secretkey",
    "setcookie",
    "token",
}
ASSIGNMENT_KEY_TERMINALS = (
    "authorization",
    "credentials",
    "credential",
    "password",
    "passwd",
    "secret",
    "token",
    "cookie",
    "key",
)
REDACTABLE_TEXT_MARKERS = (
    "-----begin",
    "authorization",
    "cookie",
    "bearer",
    "basic",
    "sk-",
    "gho_",
    "ghp_",
    "ghr_",
    "ghs_",
    "ghu_",
    "akia",
    "eyj",
)
REGEX_CASE_TRANSLATION = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZİıſK", "abcdefghijklmnopqrstuvwxyziisk"
)
OBJECT_NAME_RE = re.compile(r"[0-9a-f]{64}\.json\Z")
RECEIPT_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.json\Z")
APPROVED_HARNESSES = {"claude", "codex", "openclaw", "hermes"}
HARNESS_SOURCES = {
    "claude": {"claude-code"},
    "codex": {"codex"},
    "openclaw": {"openclaw"},
    "hermes": {"hermes"},
}
EXTRACTOR_FILES = (
    "fleet_chat_archive.py",
    "archive_object_contract.py",
    "extract_claude_code.py",
    "extract_codex.py",
    "extract_openclaw.py",
    "extract_hermes.py",
)
MAX_SOURCE_BYTES = 1280 * 1024 * 1024
MAX_OBJECT_BYTES = 1280 * 1024 * 1024
CODEX_PATH_PROVENANCE_FIELDS = frozenset({"session_file"})

# The SSH transport authenticates an owned source host through its pinned
# known_hosts entry, then invokes this exact deployed helper.  The helper is the
# trust boundary; shard files themselves do not need a second signing key.
STREAM_MAGIC = b"FLEET-CHAT-SHARD\x00\x02"
STREAM_FRAME_HEADER = struct.Struct(">IQ")
MAX_STREAM_PATH_BYTES = 512
MAX_STREAM_FILES = 1_000_000
MAX_STREAM_METADATA_BYTES = 16 * 1024 * 1024
MAX_STREAM_METADATA_TOTAL_BYTES = 32 * 1024 * 1024
MAX_STREAM_TOTAL_BYTES = 4 * 1024 * 1024 * 1024 * 1024
MAX_CACHE_HINT_BYTES = 65 * MAX_STREAM_FILES
STREAM_EXIT_PENDING_MANIFEST = 3
STREAM_EXIT_INTEGRITY_REJECTION = 4
STREAM_EXIT_LEGACY_SCHEMA = 5


class PendingManifestError(Exception):
    """The source host has not committed a transferable manifest yet."""


class LegacyArchiveSchemaError(ValueError):
    """The source shard predates the trusted v2 archive-object contract."""


class RemoteUnreachableError(Exception):
    """The authenticated SSH transport could not reach the source host."""


class RemoteTimeoutError(Exception):
    """The source exceeded its one end-to-end monotonic deadline."""


class RemoteIntegrityError(Exception):
    """The trusted source helper rejected its local shard."""


class LocalStreamIntegrityError(Exception):
    """The receiver rejected framing or staged shard integrity."""


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def extractor_sha256() -> str:
    digest = hashlib.sha256()
    root = Path(__file__).resolve().parent
    for name in EXTRACTOR_FILES:
        digest.update(name.encode("utf-8") + b"\0")
        digest.update((root / name).read_bytes())
    return digest.hexdigest()


def lexical_absolute(path: Path) -> Path:
    absolute = Path(os.path.abspath(os.path.expanduser(str(path))))
    # macOS exposes these fixed system aliases as root-owned symlinks. Normalize
    # only the aliases themselves; user-controlled components remain untrusted.
    system_aliases = {
        Path("/var"): Path("/private/var"),
        Path("/tmp"): Path("/private/tmp"),
        Path("/etc"): Path("/private/etc"),
    }
    for alias, target in system_aliases.items():
        try:
            return target / absolute.relative_to(alias)
        except ValueError:
            continue
    return absolute


def assert_no_symlink_components(path: Path, *, include_leaf: bool = True) -> Path:
    """Reject symlinks in every existing component without resolving through them."""
    absolute = lexical_absolute(path)
    parts = absolute.parts
    current = Path(parts[0])
    for part in parts[1:-1]:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        if current.is_symlink():
            raise ValueError(f"refusing symlink path component: {current}")
        if not current.is_dir():
            raise ValueError(f"path component is not a directory: {current}")
    if include_leaf and (absolute.exists() or absolute.is_symlink()):
        if absolute.is_symlink():
            raise ValueError(f"refusing symlink path component: {absolute}")
    return absolute


def open_directory_fd(path: Path, *, create: bool = False) -> tuple[Path, int]:
    absolute = lexical_absolute(path)
    descriptor = os.open(
        absolute.anchor,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        for part in absolute.parts[1:]:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            next_descriptor = os.open(
                part,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        return absolute, descriptor
    except Exception:
        os.close(descriptor)
        raise


def open_regular_fd(path: Path) -> int:
    absolute = lexical_absolute(path)
    _, parent_fd = open_directory_fd(absolute.parent)
    try:
        descriptor = os.open(
            absolute.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
    finally:
        os.close(parent_fd)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ValueError(f"archive input must be a regular file: {absolute}")
    return descriptor


def read_json_nofollow(
    path: Path,
    *,
    max_bytes: int | None = None,
    size_label: str = "JSON file",
):
    descriptor = open_regular_fd(path)
    try:
        initial_metadata = os.fstat(descriptor)
        if max_bytes is not None and initial_metadata.st_size > max_bytes:
            raise ValueError(f"{size_label} exceeds maximum of {max_bytes} bytes: {path}")
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            value = json.load(handle)
            final_metadata = os.fstat(handle.fileno())
            initial_identity = (
                initial_metadata.st_dev,
                initial_metadata.st_ino,
                initial_metadata.st_size,
                initial_metadata.st_mtime_ns,
                initial_metadata.st_ctime_ns,
            )
            final_identity = (
                final_metadata.st_dev,
                final_metadata.st_ino,
                final_metadata.st_size,
                final_metadata.st_mtime_ns,
                final_metadata.st_ctime_ns,
            )
            if (
                initial_identity != final_identity
                or (max_bytes is not None and final_metadata.st_size > max_bytes)
                or os.lseek(handle.fileno(), 0, os.SEEK_CUR) != final_metadata.st_size
            ):
                raise ValueError(f"{size_label} changed while parsing: {path}")
            return value
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def read_bytes_nofollow(path: Path) -> bytes:
    descriptor = open_regular_fd(path)
    with os.fdopen(descriptor, "rb") as handle:
        return handle.read()


def read_bytes_snapshot_nofollow(
    path: Path,
    *,
    max_bytes: int,
    size_label: str,
) -> bytes:
    """Read one stable regular-file snapshot through a no-follow descriptor."""
    descriptor = open_regular_fd(path)
    try:
        initial = os.fstat(descriptor)
        if initial.st_size > max_bytes:
            raise ValueError(
                f"{size_label} exceeds maximum of {max_bytes} bytes: {path.name}"
            )
        payload = bytearray()
        while len(payload) <= max_bytes:
            chunk = os.read(descriptor, min(1024 * 1024, max_bytes + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        final = os.fstat(descriptor)
        identity = lambda metadata: (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )
        if (
            len(payload) > max_bytes
            or len(payload) != initial.st_size
            or identity(initial) != identity(final)
        ):
            raise ValueError(f"{size_label} changed while reading: {path.name}")
        return bytes(payload)
    finally:
        os.close(descriptor)


def secure_mkdir(path: Path) -> None:
    try:
        _, descriptor = open_directory_fd(path, create=True)
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ValueError(f"refusing unsafe directory: {lexical_absolute(path)}") from error
        raise
    try:
        os.fchmod(descriptor, 0o700)
    finally:
        os.close(descriptor)


def normalized_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", regex_compatible_lower(str(value)))


def regex_compatible_lower(value: str) -> str:
    """Apply Python re.I's ASCII case equivalents without changing offsets."""
    return value.translate(REGEX_CASE_TRANSLATION)


def is_sensitive_key(value: object) -> bool:
    key = normalized_key(value)
    return key in SENSITIVE_KEY_PARTS or any(
        key.endswith(part) for part in SENSITIVE_KEY_PARTS
    )


def has_residual_assignment(value: str, lowered: str | None = None) -> bool:
    if value == "[REDACTED]" or (":" not in value and "=" not in value):
        return False
    lowered = regex_compatible_lower(value) if lowered is None else lowered
    length = len(value)
    key_characters = "abcdefghijklmnopqrstuvwxyz0123456789_.-"
    for terminal in ASSIGNMENT_KEY_TERMINALS:
        start = 0
        while True:
            terminal_start = lowered.find(terminal, start)
            if terminal_start < 0:
                break
            terminal_end = terminal_start + len(terminal)
            cursor = terminal_end
            while cursor < length and value[cursor] == "\\":
                cursor += 1
            if cursor < length and value[cursor] in "\"'":
                cursor += 1
            while cursor < length and value[cursor].isspace():
                cursor += 1
            if cursor < length and value[cursor] in ":=":
                key_start = terminal_start
                while (
                    key_start > 0
                    and lowered[key_start - 1] in key_characters
                ):
                    key_start -= 1
                if is_sensitive_key(value[key_start:terminal_end]):
                    return True
            start = terminal_end
    return False


def redact_text(value: str) -> tuple[str, int]:
    lowered = regex_compatible_lower(value)
    # Assignment values can be multiline YAML blocks, shell $'...' strings, or
    # escaped serialized JSON. Redact the containing string as one unit rather
    # than risk preserving a suffix the parser cannot safely delimit.
    if has_residual_assignment(value, lowered):
        return "[REDACTED]", 1
    if not any(marker in lowered for marker in REDACTABLE_TEXT_MARKERS):
        return value, 0
    return REDACTABLE_SECRET_RE.subn("[REDACTED]", value)


def redact_value(value):
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        result = []
        redactions = 0
        for item in value:
            cleaned, count = redact_value(item)
            result.append(cleaned)
            redactions += count
        return result, redactions
    if isinstance(value, dict):
        result = {}
        redactions = 0
        for key, item in value.items():
            archived_key = key
            if isinstance(key, str):
                cleaned_key, key_count = redact_text(key)
                if key_count:
                    archived_key = f"[REDACTED_KEY_{len(result)}]"
                    redactions += key_count
            if is_sensitive_key(key) and item not in (None, "", "[REDACTED]"):
                cleaned, count = "[REDACTED]", 1
            else:
                cleaned, count = redact_value(item)
            result[archived_key] = cleaned
            redactions += count
        return result, redactions
    return value, 0


def residual_secret_paths(value: object, path: str = "$") -> list[str]:
    """Return body-free locations of recognized secrets left after redaction."""
    findings: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_has_secret = False
            if isinstance(key, str):
                lowered_key = regex_compatible_lower(key)
                key_has_secret = has_residual_assignment(key, lowered_key) or (
                    any(
                        marker in lowered_key
                        for marker in REDACTABLE_TEXT_MARKERS
                    )
                    and bool(RESIDUAL_SECRET_RE.search(key))
                )
            child_path = f"{path}.<sensitive-key>" if key_has_secret else f"{path}.{key}"
            if key_has_secret:
                findings.append(child_path)
            if is_sensitive_key(key) and item not in (None, "", "[REDACTED]"):
                findings.append(child_path)
            findings.extend(residual_secret_paths(item, child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(residual_secret_paths(item, f"{path}[{index}]"))
    elif isinstance(value, str):
        lowered = regex_compatible_lower(value)
        if has_residual_assignment(value, lowered) or (
            any(marker in lowered for marker in REDACTABLE_TEXT_MARKERS)
            and RESIDUAL_SECRET_RE.search(value)
        ):
            findings.append(path)
    return findings


def atomic_write_json(path: Path, value: object) -> None:
    atomic_write_bytes(path, canonical_json(value) + b"\n")


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path = lexical_absolute(path)
    secure_mkdir(path.parent)
    _, directory_fd = open_directory_fd(path.parent)
    temporary_name = f".{path.name}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(
        temporary_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=directory_fd,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.fchmod(descriptor, 0o600)
        os.close(descriptor)
        descriptor = -1
        os.rename(
            temporary_name,
            path.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)


def remove_file_if_present(path: Path) -> None:
    absolute = lexical_absolute(path)
    try:
        _, directory_fd = open_directory_fd(absolute.parent)
    except FileNotFoundError:
        return
    try:
        try:
            os.unlink(absolute.name, dir_fd=directory_fd)
        except FileNotFoundError:
            return
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


@contextlib.contextmanager
def defer_sigterm_during_rollback():
    previous_mask = None
    if hasattr(signal, "pthread_sigmask"):
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
    try:
        yield
    finally:
        if previous_mask is not None:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)


def load_healthy_manifest_snapshot(
    source_root: Path,
    host_id: str,
) -> tuple[bytes, dict[Path, bytes], dict[str, dict]] | None:
    """Load exact manifest-bound indexes without trusting unbound live rows."""
    manifest_path = source_root / "publish-manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        return None
    manifest_payload = read_bytes_nofollow(manifest_path)
    manifest = json.loads(manifest_payload)
    harnesses = manifest.get("harnesses")
    receipt_binding = manifest.get("receipt")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("host_id") != host_id
        or not isinstance(manifest.get("run_id"), str)
        or not isinstance(harnesses, dict)
        or not set(harnesses).issubset(APPROVED_HARNESSES)
        or not isinstance(receipt_binding, dict)
    ):
        raise ValueError("invalid prior publish manifest identity")

    receipt_relative = Path(str(receipt_binding.get("path", "")))
    if (
        len(receipt_relative.parts) != 2
        or receipt_relative.parts[0] != "receipts"
        or not receipt_relative.name.endswith(".json")
    ):
        raise ValueError("invalid prior manifest receipt path")
    receipt_path = source_root / receipt_relative
    if not receipt_path.is_file() or receipt_path.is_symlink():
        raise ValueError("prior manifest receipt is missing")
    receipt_payload = read_bytes_nofollow(receipt_path)
    if (
        not re.fullmatch(r"[0-9a-f]{64}", str(receipt_binding.get("sha256", "")))
        or hashlib.sha256(receipt_payload).hexdigest() != receipt_binding["sha256"]
    ):
        raise ValueError("prior manifest receipt hash mismatch")
    receipt = json.loads(receipt_payload)
    receipt_harnesses = receipt.get("harnesses")
    collection_status = receipt.get("collection_status", receipt.get("status"))
    if (
        receipt.get("schema_version") != 1
        or receipt.get("host_id") != host_id
        or receipt.get("run_id") != manifest.get("run_id")
        or receipt.get("extractor_sha256") != manifest.get("extractor_sha256")
        or receipt.get("config_sha256") != manifest.get("config_sha256")
        or collection_status not in {"completed", "completed_with_absent_harnesses"}
        or not isinstance(receipt_harnesses, dict)
    ):
        raise ValueError("prior manifest receipt is not healthy")

    index_payloads: dict[Path, bytes] = {}
    index_values: dict[str, dict] = {}
    for harness, binding in harnesses.items():
        result = receipt_harnesses.get(harness)
        object_digests = binding.get("object_sha256") if isinstance(binding, dict) else None
        expected_index_hash = binding.get("index_sha256") if isinstance(binding, dict) else None
        if (
            not isinstance(result, dict)
            or result.get("status")
            in {"failed", "partial", "source_missing", "not_present_on_host"}
            or not isinstance(object_digests, list)
            or len(object_digests) != len(set(object_digests))
            or any(not re.fullmatch(r"[0-9a-f]{64}", str(item)) for item in object_digests)
            or not re.fullmatch(r"[0-9a-f]{64}", str(expected_index_hash or ""))
        ):
            raise ValueError(f"invalid prior manifest binding for {harness}")
        index_path = source_root / harness / "index.json"
        if not index_path.is_file() or index_path.is_symlink():
            raise ValueError(f"prior manifest index is missing for {harness}")
        index_payload = read_bytes_nofollow(index_path)
        if hashlib.sha256(index_payload).hexdigest() != expected_index_hash:
            raise ValueError(f"prior manifest index hash mismatch for {harness}")
        index = json.loads(index_payload)
        rows = index.get("conversations")
        if (
            index.get("schema_version") != 1
            or index.get("host_id") != host_id
            or index.get("harness") != harness
            or not isinstance(rows, list)
        ):
            raise ValueError(f"invalid prior manifest index for {harness}")
        indexed_digests = []
        for row in rows:
            digest = row.get("object_sha256") if isinstance(row, dict) else None
            if (
                not OBJECT_NAME_RE.fullmatch(f"{digest}.json")
                or row.get("source") not in HARNESS_SOURCES[harness]
                or (
                    row.get("source_sha256") is not None
                    and not re.fullmatch(r"[0-9a-f]{64}", str(row["source_sha256"]))
                )
            ):
                raise ValueError(f"invalid prior manifest index row for {harness}")
            object_path = source_root / harness / "objects" / f"{digest}.json"
            if not object_path.is_file() or object_path.is_symlink():
                raise ValueError(f"prior manifest object is missing for {harness}: {digest}")
            indexed_digests.append(digest)
        if sorted(indexed_digests) != sorted(object_digests):
            raise ValueError(f"prior manifest object binding mismatch for {harness}")
        index_payloads[index_path] = index_payload
        index_values[harness] = index
    return manifest_payload, index_payloads, index_values


def stable_codex_conversation_sha256(conversation: dict) -> str:
    stable = {
        key: value
        for key, value in conversation.items()
        if key not in CODEX_PATH_PROVENANCE_FIELDS
    }
    return hashlib.sha256(canonical_json(stable)).hexdigest()


def archive_conversations(
    archive_root: Path,
    host_id: str,
    harness: str,
    conversations,
    *,
    collection_complete: bool = True,
    reuse_harness_root: Path | None = None,
    reuse_index: dict | None = None,
) -> dict:
    harness_root = archive_root / "hosts" / host_id / harness
    objects_root = harness_root / "objects"
    secure_mkdir(archive_root / "hosts")
    secure_mkdir(archive_root / "hosts" / host_id)
    secure_mkdir(harness_root)
    secure_mkdir(objects_root)

    new_objects = 0
    conversation_count = 0
    redaction_count = 0
    index_path = harness_root / "index.json"
    prior_rows: list[dict] = []
    if index_path.exists():
        assert_no_symlink_components(index_path)
        prior_index = read_json_nofollow(index_path)
        prior_rows = list(prior_index.get("conversations", []))
        for row in prior_rows:
            digest = row.get("object_sha256") if isinstance(row, dict) else None
            candidate = objects_root / f"{digest}.json"
            try:
                prior_value = read_json_nofollow(candidate)
            except (FileNotFoundError, ValueError):
                # Regeneration is replacement, not a migration of arbitrary v1
                # bodies. A run transaction preserves the old manifested index
                # until the new v2 manifest commits.
                prior_rows = []
                break
            if prior_value.get("archive_schema_version") != ARCHIVE_OBJECT_SCHEMA_VERSION:
                prior_rows = []
                break
    index_by_digest = {
        row["object_sha256"]: row
        for row in prior_rows
        if isinstance(row, dict) and OBJECT_NAME_RE.fullmatch(f"{row.get('object_sha256', '')}.json")
    }
    reusable_by_session: dict[object, list[tuple[dict, Path]]] = {}
    if harness == "codex" and reuse_harness_root is not None and reuse_index is not None:
        for row in reuse_index.get("conversations", []):
            if not isinstance(row, dict):
                continue
            digest = row.get("object_sha256")
            session_id = row.get("session_id")
            if (
                not OBJECT_NAME_RE.fullmatch(f"{digest}.json")
                or not isinstance(session_id, str)
                or not session_id
            ):
                continue
            reusable_by_session.setdefault(session_id, []).append(
                (row, reuse_harness_root / "objects" / f"{digest}.json")
            )
    current_by_raw_identity: dict[tuple[str, str], tuple[str, str]] = {}
    reusable_stable_sha256: dict[Path, tuple[str, object, object, object]] = {}

    for conversation in conversations:
        conversation_count += 1
        source_sha256 = conversation.pop("_archive_source_sha256", None)
        validate_archive_object(conversation, harness=harness)
        conversation, conversation_redactions = redact_value(conversation)
        redaction_count += conversation_redactions
        if conversation_redactions:
            conversation["archive_redaction_count"] = conversation_redactions
        residuals = residual_secret_paths(conversation)
        if residuals:
            raise ValueError(
                "recognized credential remained after redaction at "
                + ", ".join(residuals[:5])
            )
        validate_archive_object(conversation, harness=harness)
        digest = None
        stable_sha256 = None
        if harness == "codex":
            installation = conversation.get("installation")
            if (
                not isinstance(installation, str)
                or not re.fullmatch(r"[0-9a-f]{64}", str(source_sha256 or ""))
            ):
                raise ValueError("Codex conversation is missing stable source identity")
            stable_sha256 = stable_codex_conversation_sha256(conversation)
            raw_identity = (installation, source_sha256)
            current = current_by_raw_identity.get(raw_identity)
            if current is not None and current[1] == stable_sha256:
                digest = current[0]
            if digest is None:
                for row, candidate_path in reusable_by_session.get(
                    conversation.get("session_id"), []
                ):
                    prior_source_sha256 = row.get("source_sha256")
                    if (
                        prior_source_sha256 is not None
                        and prior_source_sha256 != source_sha256
                    ):
                        continue
                    cached = reusable_stable_sha256.get(candidate_path)
                    if cached is None:
                        try:
                            archived = validate_object_file(candidate_path)
                        except (FileNotFoundError, ValueError):
                            # Extractor hash drift triggers regeneration. Legacy
                            # v1 bodies are not migrated or trusted for reuse.
                            continue
                        cached = (
                            stable_codex_conversation_sha256(archived),
                            archived.get("source"),
                            archived.get("session_id"),
                            archived.get("installation"),
                        )
                        reusable_stable_sha256[candidate_path] = cached
                    (
                        candidate_stable_sha256,
                        archived_source,
                        archived_session_id,
                        archived_installation,
                    ) = cached
                    if (
                        archived_source == "codex"
                        and archived_session_id == conversation.get("session_id")
                        and archived_installation == installation
                        and candidate_stable_sha256 == stable_sha256
                    ):
                        digest = row["object_sha256"]
                        object_path = objects_root / f"{digest}.json"
                        if not object_path.exists():
                            link_verified_local_object(candidate_path, object_path)
                        break

        if digest is None:
            payload = canonical_json(conversation)
            if len(payload) + 1 > MAX_OBJECT_BYTES:
                raise ValueError(
                    f"object exceeds maximum of {MAX_OBJECT_BYTES} bytes before write"
                )
            digest = hashlib.sha256(payload).hexdigest()
        else:
            payload = None
        object_path = objects_root / f"{digest}.json"
        if not object_path.exists():
            if payload is None:
                raise ValueError(f"reused Codex object is missing: {digest}")
            atomic_write_bytes(object_path, payload + b"\n")
            new_objects += 1
        index_row = {
            "object_sha256": digest,
            "session_id": conversation.get("session_id"),
            "source": conversation.get("source"),
        }
        if harness == "codex":
            index_row["source_sha256"] = source_sha256
            index_row["installation"] = conversation["installation"]
            current_by_raw_identity[(conversation["installation"], source_sha256)] = (
                digest,
                stable_sha256,
            )
        index_by_digest[digest] = index_row

    index_rows = list(index_by_digest.values())
    index_rows.sort(key=lambda row: (str(row["session_id"]), row["object_sha256"]))
    atomic_write_json(
        harness_root / "index.json",
        {
            "schema_version": 1,
            "host_id": host_id,
            "harness": harness,
            "conversations": index_rows,
        },
    )
    return {
        "status": (
            "partial"
            if not collection_complete
            else "collected" if conversation_count else "no_conversations"
        ),
        "conversations": conversation_count,
        "new_objects": new_objects,
        "redactions": redaction_count,
        "index_conversations": len(index_rows),
        "publishable": collection_complete,
    }


def source_fingerprint(path: Path, *, include_content_hash: bool = False) -> dict:
    descriptor = open_regular_fd(path)
    try:
        metadata = os.fstat(descriptor)
        if metadata.st_size > MAX_SOURCE_BYTES:
            raise ValueError(
                f"source exceeds maximum of {MAX_SOURCE_BYTES} bytes: {path}"
            )
        content_sha256 = descriptor_sha256(descriptor) if include_content_hash else None
        final_metadata = os.fstat(descriptor)
        if (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        ) != (
            final_metadata.st_dev,
            final_metadata.st_ino,
            final_metadata.st_size,
            final_metadata.st_mtime_ns,
            final_metadata.st_ctime_ns,
        ):
            raise ValueError(f"source changed while hashing: {path}")
    finally:
        os.close(descriptor)
    fingerprint = {
        "size": metadata.st_size,
        "mtime_ns": metadata.st_mtime_ns,
        "ctime_ns": metadata.st_ctime_ns,
        "inode": metadata.st_ino,
    }
    if include_content_hash:
        fingerprint["sha256"] = content_sha256
    return fingerprint


def load_incremental_state(archive_root: Path, host_id: str, harness: str) -> tuple[Path, dict]:
    state_path = archive_root / "state" / host_id / f"{harness}.json"
    current_extractor = extractor_sha256()
    if not state_path.is_file():
        return state_path, {}
    assert_no_symlink_components(state_path)
    state = read_json_nofollow(state_path)
    if state.get("extractor_sha256") != current_extractor:
        return state_path, {}
    sources = state.get("sources", {})
    return state_path, sources if isinstance(sources, dict) else {}


def save_incremental_state(state_path: Path, sources: dict) -> None:
    atomic_write_json(
        state_path,
        {
            "schema_version": 1,
            "extractor_sha256": extractor_sha256(),
            "sources": sources,
        },
    )


def collect_sources(
    archive_root: Path,
    host_id: str,
    *,
    claude_roots=(),
    codex_roots=(),
    openclaw_roots=(),
    hermes_exports=(),
    _transaction_token: object | None = None,
) -> dict:
    if (
        _transaction_token is not None
        and _transaction_token is not _RUN_CONFIG_TRANSACTION
    ):
        raise ValueError("invalid collection transaction token")
    archive_root = validate_output_root(archive_root)
    secure_mkdir(archive_root)
    harnesses = {}
    codex_reuse_harness_root = None
    codex_reuse_index = None
    if codex_roots:
        live_host_root = archive_root / "hosts" / host_id
        snapshot = load_healthy_manifest_snapshot(live_host_root, host_id)
        if snapshot is not None:
            _, _, manifest_indexes = snapshot
            codex_reuse_index = manifest_indexes.get("codex")
            if codex_reuse_index is not None:
                codex_reuse_harness_root = live_host_root / "codex"

    def aggregate_quality(items: list[dict], *, source_missing: bool = False) -> dict:
        keys = ("discovered_lines", "parsed_lines", "failed_lines", "recognized_lines")
        quality = {key: sum(int(item.get(key, 0)) for item in items) for key in keys}
        quality["discovered_files"] = sum(
            int(item.get("discovered_files", 1)) for item in items
        )
        quality["status"] = (
            "source_missing"
            if source_missing
            else "partial"
            if any(item.get("status") == "partial" for item in items)
            else "complete"
        )
        return quality

    def finish_harness(
        harness: str,
        conversations,
        qualities: list[dict],
        *,
        source_missing=False,
        file_counts: dict | None = None,
        state_commit: tuple[Path, dict] | None = None,
    ) -> None:
        work_parent = archive_root / ".work"
        secure_mkdir(work_parent)
        with tempfile.TemporaryDirectory(
            dir=work_parent, prefix=f"{host_id}-{harness}-"
        ) as staged_directory:
            staged_root = Path(staged_directory)
            result = archive_conversations(
                staged_root,
                host_id,
                harness,
                conversations,
                collection_complete=True,
                reuse_harness_root=(
                    codex_reuse_harness_root if harness == "codex" else None
                ),
                reuse_index=codex_reuse_index if harness == "codex" else None,
            )
            missing = source_missing() if callable(source_missing) else source_missing
            quality = aggregate_quality(qualities, source_missing=missing)
            if file_counts is not None:
                quality["discovered_files"] = file_counts["discovered"]
                quality["processed_files"] = file_counts["processed"]
                quality["skipped_unchanged_files"] = file_counts["skipped"]
            complete = quality["status"] == "complete"
            result["publishable"] = complete
            if not complete:
                result["status"] = "partial"
                result["staged_objects_discarded"] = result["new_objects"]
                result["new_objects"] = 0
            if quality["status"] == "source_missing":
                result["status"] = "source_missing"
            result["quality"] = quality

            if complete:
                staged_objects = staged_root / "hosts" / host_id / harness / "objects"
                live_objects = archive_root / "hosts" / host_id / harness / "objects"
                result["new_objects"] = sum(
                    1
                    for path in staged_objects.iterdir()
                    if not (live_objects / path.name).is_file()
                )
                merge_host_shard(
                    staged_root / "hosts" / host_id,
                    archive_root,
                    host_id,
                    require_healthy_receipt=False,
                    _transaction_token=_transaction_token,
                )
                live_index = read_json_nofollow(
                    archive_root / "hosts" / host_id / harness / "index.json"
                )
                result["index_conversations"] = len(live_index["conversations"])
                if state_commit is not None:
                    save_incremental_state(*state_commit)
            harnesses[harness] = result

    if claude_roots:
        qualities = []
        source_missing = False
        file_counts = {"discovered": 0, "processed": 0, "skipped": 0}
        state_path, prior_state = load_incremental_state(
            archive_root, host_id, "claude"
        )
        next_state = dict(prior_state)
        def claude_conversations():
            nonlocal source_missing
            for root in claude_roots:
                if not root.is_dir():
                    source_missing = True
                    continue
                for session_file in find_all_claude_sessions(root):
                    file_counts["discovered"] += 1
                    state_key = str(lexical_absolute(session_file))
                    fingerprint = source_fingerprint(session_file)
                    if prior_state.get(state_key) == fingerprint:
                        file_counts["skipped"] += 1
                        continue
                    file_counts["processed"] += 1
                    quality = {}
                    conversation = extract_claude_session(
                        session_file,
                        installation=root,
                        quality_out=quality,
                        max_source_bytes=MAX_SOURCE_BYTES,
                        expected_fingerprint=fingerprint,
                    )
                    qualities.append(quality)
                    if conversation:
                        yield conversation
                    if quality.get("status") == "complete":
                        next_state[state_key] = fingerprint
        finish_harness(
            "claude",
            claude_conversations(),
            qualities,
            source_missing=lambda: source_missing,
            file_counts=file_counts,
            state_commit=(state_path, next_state),
        )

    if codex_roots:
        qualities = []
        source_missing = False
        file_counts = {"discovered": 0, "processed": 0, "skipped": 0}
        state_path, prior_state = load_incremental_state(
            archive_root, host_id, "codex"
        )
        next_state = dict(prior_state)
        def codex_conversations():
            nonlocal source_missing
            for codex_root in codex_roots:
                if not codex_root.is_dir():
                    source_missing = True
                    continue
                for session_file in find_all_codex_sessions(codex_root):
                    file_counts["discovered"] += 1
                    state_key = str(lexical_absolute(session_file))
                    fingerprint = source_fingerprint(
                        session_file, include_content_hash=True
                    )
                    if prior_state.get(state_key) == fingerprint:
                        file_counts["skipped"] += 1
                        continue
                    file_counts["processed"] += 1
                    quality = {}
                    conversation = extract_codex_session(
                        session_file,
                        quality_out=quality,
                        expected_source_sha256=fingerprint["sha256"],
                        max_source_bytes=MAX_SOURCE_BYTES,
                    )
                    qualities.append(quality)
                    if conversation:
                        conversation["installation"] = str(codex_root)
                        conversation["_archive_source_sha256"] = fingerprint["sha256"]
                        yield conversation
                    if quality.get("status") == "complete":
                        next_state[state_key] = fingerprint
        finish_harness(
            "codex",
            codex_conversations(),
            qualities,
            source_missing=lambda: source_missing,
            file_counts=file_counts,
            state_commit=(state_path, next_state),
        )

    if openclaw_roots:
        qualities = []
        source_missing = False
        file_counts = {"discovered": 0, "processed": 0, "skipped": 0}
        state_path, prior_state = load_incremental_state(
            archive_root, host_id, "openclaw"
        )
        next_state = dict(prior_state)
        def openclaw_conversations():
            nonlocal source_missing
            for openclaw_root in openclaw_roots:
                if not openclaw_root.is_dir():
                    source_missing = True
                    continue
                for session_file in find_all_openclaw_sessions(openclaw_root):
                    file_counts["discovered"] += 1
                    state_key = str(lexical_absolute(session_file))
                    fingerprint = source_fingerprint(session_file)
                    if prior_state.get(state_key) == fingerprint:
                        file_counts["skipped"] += 1
                        continue
                    file_counts["processed"] += 1
                    quality = {}
                    conversation = extract_openclaw_session(
                        session_file,
                        quality_out=quality,
                        max_source_bytes=MAX_SOURCE_BYTES,
                        expected_fingerprint=fingerprint,
                    )
                    qualities.append(quality)
                    if conversation:
                        conversation["installation"] = str(openclaw_root)
                        yield conversation
                    if quality.get("status") == "complete":
                        next_state[state_key] = fingerprint
        finish_harness(
            "openclaw",
            openclaw_conversations(),
            qualities,
            source_missing=lambda: source_missing,
            file_counts=file_counts,
            state_commit=(state_path, next_state),
        )

    if hermes_exports:
        qualities = []
        source_missing = False
        file_counts = {"discovered": 0, "processed": 0, "skipped": 0}
        state_path, prior_state = load_incremental_state(
            archive_root, host_id, "hermes"
        )
        next_state = dict(prior_state)
        def hermes_conversations():
            nonlocal source_missing
            for export_file in hermes_exports:
                if not export_file.is_file():
                    source_missing = True
                    continue
                file_counts["discovered"] += 1
                state_key = f"hermes-export:{export_file.name}"
                parse_fingerprint = source_fingerprint(
                    export_file, include_content_hash=True
                )
                fingerprint = dict(parse_fingerprint)
                fingerprint.pop("mtime_ns", None)
                fingerprint.pop("ctime_ns", None)
                fingerprint.pop("inode", None)
                if prior_state.get(state_key) == fingerprint:
                    file_counts["skipped"] += 1
                    continue
                file_counts["processed"] += 1
                quality = {}
                yield from iter_hermes_export(
                    export_file,
                    quality_out=quality,
                    max_source_bytes=MAX_SOURCE_BYTES,
                    expected_fingerprint=parse_fingerprint,
                )
                qualities.append(quality)
                if quality.get("status") == "complete":
                    next_state[state_key] = fingerprint
        finish_harness(
            "hermes",
            hermes_conversations(),
            qualities,
            source_missing=lambda: source_missing,
            file_counts=file_counts,
            state_commit=(state_path, next_state),
        )

    return harnesses


def path_list(values) -> list[Path]:
    return [assert_no_symlink_components(Path(value)) for value in values if value]


def validate_host_id(value: str) -> str:
    if not HOST_ID_RE.fullmatch(value):
        raise ValueError("host_id must contain only lowercase letters, digits, and hyphens")
    return value


def validate_output_root(path: Path) -> Path:
    resolved = assert_no_symlink_components(path)
    for candidate in (resolved, *resolved.parents):
        git_marker = candidate / ".git"
        if git_marker.exists() or git_marker.is_symlink():
            raise ValueError(f"fleet output cannot be inside a Git checkout: {resolved}")
    return resolved


def export_hermes_instances(instances: list[dict], work_root: Path) -> list[Path]:
    exports = []
    for index, instance in enumerate(instances):
        hermes_home = assert_no_symlink_components(Path(instance["home"]))
        binary = assert_no_symlink_components(
            Path(instance.get("binary", "~/.local/bin/hermes"))
        )
        output = work_root / f"hermes-{index}.jsonl"
        environment = os.environ.copy()
        environment["HERMES_HOME"] = str(hermes_home)
        subprocess.run(
            [
                str(binary),
                "sessions",
                "export",
                str(output),
                "--format",
                "jsonl",
                "--redact",
                "--yes",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=int(instance.get("timeout_seconds", 600)),
            env=environment,
        )
        exports.append(output)
    return exports


def is_google_drive_path(path: Path) -> bool:
    candidate = assert_no_symlink_components(path)
    cloud_root = lexical_absolute(Path.home() / "Library" / "CloudStorage")
    try:
        relative = candidate.relative_to(cloud_root)
    except ValueError:
        return False
    if not relative.parts or not relative.parts[0].startswith("GoogleDrive-"):
        return False
    provider_root = cloud_root / relative.parts[0]
    fileproviderctl = shutil.which("fileproviderctl")
    if not fileproviderctl or not provider_root.is_dir():
        return False
    try:
        completed = subprocess.run(
            [fileproviderctl, "evaluate", str(provider_root)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    output = (completed.stdout + completed.stderr).lower()
    return completed.returncode == 0 and "no item for url" not in output


def configured_drive_root(value: object) -> tuple[Path | None, str]:
    """Resolve an explicit Drive path or exactly one live Google provider."""
    if value is None:
        return None, "blocked_no_drive_root"
    if value != "auto":
        if not isinstance(value, str) or not value:
            raise ValueError("drive_root must be an absolute path, 'auto', or null")
        return assert_no_symlink_components(Path(value)), "configured"

    cloud_root = lexical_absolute(Path.home() / "Library" / "CloudStorage")
    if not cloud_root.is_dir() or cloud_root.is_symlink():
        return None, "blocked_drive_unavailable"
    assert_no_symlink_components(cloud_root)
    providers: list[Path] = []
    for candidate in sorted(cloud_root.iterdir(), key=lambda path: path.name):
        if not candidate.name.startswith("GoogleDrive-"):
            continue
        try:
            candidate = assert_no_symlink_components(candidate)
        except ValueError:
            continue
        my_drive = candidate / "My Drive"
        if (
            candidate.is_dir()
            and my_drive.is_dir()
            and is_google_drive_path(candidate)
        ):
            providers.append(my_drive)
    if not providers:
        return None, "blocked_drive_unavailable"
    if len(providers) != 1:
        return None, "blocked_ambiguous_drive_root"
    archive_root = providers[0] / "AI Chat Archive"
    secure_mkdir(archive_root)
    return assert_no_symlink_components(archive_root), "auto"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    descriptor = open_regular_fd(path)
    with os.fdopen(descriptor, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def descriptor_sha256(descriptor: int) -> str:
    digest = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    for chunk in iter(lambda: os.read(descriptor, 1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def exact_json_loads(payload: bytes, *, label: str):
    """Parse UTF-8 JSON while rejecting duplicates and non-finite numbers."""

    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key in {label}: {key}")
            result[key] = value
        return result

    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant in {label}: {constant}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid JSON in {label}") from error


def require_redaction_idempotence(value: object) -> None:
    """Prove the current redactor would not change any part of an object."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            cleaned, count = redact_text(item)
            if count or cleaned != item:
                raise ValueError("archive object is not redaction-idempotent")
        elif isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, dict):
            for key, child in item.items():
                if isinstance(key, str):
                    cleaned_key, key_count = redact_text(key)
                    if key_count or cleaned_key != key:
                        raise ValueError("archive object key is not redaction-idempotent")
                if is_sensitive_key(key) and child not in (None, "", "[REDACTED]"):
                    raise ValueError("archive object is not redaction-idempotent")
                stack.append(child)


def require_exact_keys(
    value: object,
    *,
    required: set[str],
    optional: set[str] = frozenset(),
    label: str,
) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    keys = set(value)
    unknown = keys - required - optional
    missing = required - keys
    if unknown or missing:
        raise ValueError(f"invalid {label} schema")
    return value


def require_bounded_string(
    value: object,
    *,
    label: str,
    maximum: int = 1024,
    pattern: re.Pattern | None = None,
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum
        or "\x00" in value
        or (pattern is not None and not pattern.fullmatch(value))
    ):
        raise ValueError(f"invalid {label}")
    return value


def require_nonnegative_int(value: object, *, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"invalid {label}")
    return value


def reject_metadata_secrets(value: object, *, label: str) -> None:
    residuals = residual_secret_paths(value)
    if residuals:
        raise ValueError(f"recognized credential in {label} metadata")


def validate_manifest_value(manifest: object, host_id: str) -> dict:
    manifest = require_exact_keys(
        manifest,
        required={
            "schema_version",
            "host_id",
            "run_id",
            "generated_at",
            "extractor_sha256",
            "config_sha256",
            "receipt",
            "harnesses",
        },
        label="publish manifest",
    )
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        raise ValueError(f"invalid publish manifest identity: {host_id}")
    if manifest["host_id"] != host_id:
        raise ValueError(f"invalid publish manifest identity: {host_id}")
    require_bounded_string(
        manifest["run_id"],
        label="manifest run_id",
        maximum=256,
        pattern=re.compile(r"[A-Za-z0-9._-]+\Z"),
    )
    require_bounded_string(
        manifest["generated_at"],
        label="manifest generated_at",
        maximum=128,
        pattern=re.compile(r"[0-9T:+.Z-]+\Z"),
    )
    digest_pattern = re.compile(r"[0-9a-f]{64}\Z")
    require_bounded_string(
        manifest["extractor_sha256"],
        label="manifest extractor digest",
        maximum=64,
        pattern=digest_pattern,
    )
    require_bounded_string(
        manifest["config_sha256"],
        label="manifest config digest",
        maximum=64,
        pattern=digest_pattern,
    )
    receipt = require_exact_keys(
        manifest["receipt"],
        required={"path", "sha256"},
        label="manifest receipt binding",
    )
    receipt_path = require_bounded_string(
        receipt["path"], label="manifest receipt path", maximum=MAX_STREAM_PATH_BYTES
    )
    relative = Path(receipt_path)
    if (
        relative.as_posix() != receipt_path
        or len(relative.parts) != 2
        or relative.parts[0] != "receipts"
        or not RECEIPT_NAME_RE.fullmatch(relative.name)
    ):
        raise ValueError(f"invalid manifest receipt path: {host_id}")
    require_bounded_string(
        receipt["sha256"],
        label="manifest receipt digest",
        maximum=64,
        pattern=digest_pattern,
    )
    harnesses = manifest["harnesses"]
    if not isinstance(harnesses, dict) or not set(harnesses).issubset(APPROVED_HARNESSES):
        raise ValueError(f"invalid publish manifest identity: {host_id}")
    total_objects = 0
    for harness, raw_binding in harnesses.items():
        binding = require_exact_keys(
            raw_binding,
            required={"index_sha256", "object_sha256"},
            label=f"{harness} manifest binding",
        )
        require_bounded_string(
            binding["index_sha256"],
            label=f"{harness} index digest",
            maximum=64,
            pattern=digest_pattern,
        )
        digests = binding["object_sha256"]
        if not isinstance(digests, list):
            raise ValueError(f"invalid publish manifest binding for {harness}")
        total_objects += len(digests)
        if total_objects + len(harnesses) + 2 > MAX_STREAM_FILES:
            raise ValueError("publish manifest authorizes too many files")
        for digest in digests:
            require_bounded_string(
                digest,
                label=f"{harness} object digest",
                maximum=64,
                pattern=digest_pattern,
            )
        if digests != sorted(set(digests)):
            raise ValueError(f"invalid publish manifest binding for {harness}")
    reject_metadata_secrets(manifest, label="manifest")
    return manifest


def validate_index_value(index: object, host_id: str, harness: str) -> dict:
    index = require_exact_keys(
        index,
        required={"schema_version", "host_id", "harness", "conversations"},
        label=f"{harness} archive index",
    )
    if (
        type(index["schema_version"]) is not int
        or index["schema_version"] != 1
        or index["host_id"] != host_id
        or index["harness"] != harness
        or not isinstance(index["conversations"], list)
    ):
        raise ValueError("invalid archive index identity: index.json")
    if len(index["conversations"]) > MAX_STREAM_FILES:
        raise ValueError(f"{harness} archive index has too many rows")
    seen: set[str] = set()
    for row in index["conversations"]:
        allowed_schemas = [{"object_sha256", "session_id", "source"}]
        if harness == "codex":
            allowed_schemas.append(
                {
                    "object_sha256",
                    "session_id",
                    "source",
                    "source_sha256",
                    "installation",
                }
            )
        if not isinstance(row, dict) or set(row) not in allowed_schemas:
            raise ValueError(f"invalid archive index row schema for {harness}")
        digest = row["object_sha256"]
        if not isinstance(digest, str) or not OBJECT_NAME_RE.fullmatch(f"{digest}.json"):
            raise ValueError("invalid archive index row: index.json")
        if digest in seen:
            raise ValueError(f"duplicate archive index row: {digest}")
        seen.add(digest)
        if row["session_id"] is not None and not isinstance(row["session_id"], str):
            raise ValueError("invalid index session_id")
        if row["source"] not in HARNESS_SOURCES[harness]:
            raise ValueError(f"unauthorized source in {harness} index")
        if "source_sha256" in row:
            require_bounded_string(
                row["source_sha256"],
                label="Codex source digest",
                maximum=64,
                pattern=re.compile(r"[0-9a-f]{64}\Z"),
            )
            installation = require_bounded_string(
                row["installation"], label="Codex installation", maximum=4096
            )
            if not Path(installation).is_absolute():
                raise ValueError("invalid Codex installation identity: index.json")
    return index


def validate_quality_value(value: object, harness: str) -> None:
    quality = require_exact_keys(
        value,
        required={
            "status",
            "discovered_lines",
            "parsed_lines",
            "failed_lines",
            "recognized_lines",
            "discovered_files",
        },
        optional={"processed_files", "skipped_unchanged_files"},
        label=f"{harness} receipt quality",
    )
    if quality["status"] not in {"complete", "partial", "source_missing"}:
        raise ValueError(f"invalid {harness} receipt quality status")
    for key, item in quality.items():
        if key != "status":
            require_nonnegative_int(item, label=f"{harness} quality {key}")


REMOTE_RECEIPT_STATUSES = {
    "pulled",
    "published",
    "pending_validation",
    "pending_manifest",
    "legacy_schema",
    "unreachable",
    "timeout",
    "remote_integrity_rejection",
    "local_integrity_rejection",
    "blocked_integrity_failure",
    "blocked_source_missing",
    "invalid_remote",
    "cached",
}
PUBLICATION_RECEIPT_STATUSES = {
    "not_attempted",
    "pending_manifest",
    "pending_validation",
    "published",
    "failed",
    "blocked_incomplete_collection",
    "blocked_no_drive_root",
    "blocked_drive_unavailable",
    "blocked_ambiguous_drive_root",
    "blocked_not_google_drive",
    "blocked_source_missing",
    "blocked_integrity_failure",
}


def validate_status_record(
    value: object,
    *,
    label: str,
    allowed_statuses: set[str],
) -> None:
    record = require_exact_keys(
        value,
        required={"status", "files_copied"},
        optional={
            "files_verified",
            "quarantined_unindexed_objects",
            "publication",
        },
        label=label,
    )
    if record["status"] not in allowed_statuses:
        raise ValueError(f"invalid {label} status")
    require_nonnegative_int(record["files_copied"], label=f"{label} files_copied")
    for key in ("files_verified", "quarantined_unindexed_objects"):
        if key in record:
            require_nonnegative_int(record[key], label=f"{label} {key}")
    if "publication" in record:
        validate_status_record(
            record["publication"],
            label=f"{label} publication",
            allowed_statuses=PUBLICATION_RECEIPT_STATUSES,
        )


def validate_receipt_value(
    receipt: object,
    host_id: str,
    manifest: dict,
    *,
    expected_receipt_path: Path | None = None,
) -> dict:
    receipt = require_exact_keys(
        receipt,
        required={
            "schema_version",
            "extractor_sha256",
            "config_sha256",
            "run_id",
            "host_id",
            "harnesses",
        },
        optional={
            "collected_at",
            "collection_status",
            "status",
            "hub",
            "publication",
            "errors",
            "receipt_path",
            "receipt_publication",
        },
        label="manifest-bound receipt",
    )
    collection_status = receipt.get("collection_status", receipt.get("status"))
    if (
        type(receipt["schema_version"]) is not int
        or receipt["schema_version"] != 1
        or receipt["host_id"] != host_id
        or receipt["run_id"] != manifest["run_id"]
        or receipt["extractor_sha256"] != manifest["extractor_sha256"]
        or receipt["config_sha256"] != manifest["config_sha256"]
        or collection_status not in {"completed", "completed_with_absent_harnesses"}
    ):
        raise ValueError(f"manifest-bound receipt is not healthy: {host_id}")
    for key in ("collected_at", "status"):
        if key in receipt:
            require_bounded_string(
                receipt[key],
                label=f"receipt {key}",
                maximum=128,
                pattern=(
                    re.compile(r"[0-9T:+.Z-]+\Z")
                    if key == "collected_at"
                    else re.compile(r"(?:completed|completed_with_absent_harnesses|failed)\Z")
                ),
            )
    if "receipt_path" in receipt:
        receipt_path = require_bounded_string(
            receipt["receipt_path"], label="receipt_path", maximum=4096
        )
        if not Path(receipt_path).is_absolute():
            raise ValueError("invalid receipt_path")
        if (
            expected_receipt_path is not None
            and lexical_absolute(Path(receipt_path))
            != lexical_absolute(expected_receipt_path)
        ):
            raise ValueError("receipt_path does not match manifest binding")
    harnesses = receipt["harnesses"]
    if not isinstance(harnesses, dict) or not set(harnesses).issubset(APPROVED_HARNESSES):
        raise ValueError(f"manifest-bound receipt is not healthy: {host_id}")
    if not set(manifest["harnesses"]).issubset(harnesses):
        raise ValueError(f"manifest-bound receipt is not healthy: {host_id}")
    for harness, raw_result in harnesses.items():
        result = require_exact_keys(
            raw_result,
            required={"status"},
            optional={
                "conversations",
                "new_objects",
                "publishable",
                "redactions",
                "index_conversations",
                "quality",
                "inventory_only",
                "staged_objects_discarded",
            },
            label=f"{harness} receipt result",
        )
        if result["status"] not in {
            "collected",
            "no_conversations",
            "not_present_on_host",
        }:
            raise ValueError(f"manifest-bound receipt has incomplete {harness} extraction")
        for key in (
            "conversations",
            "new_objects",
            "redactions",
            "index_conversations",
            "staged_objects_discarded",
        ):
            if key in result:
                require_nonnegative_int(result[key], label=f"{harness} receipt {key}")
        for key in ("publishable", "inventory_only"):
            if key in result and type(result[key]) is not bool:
                raise ValueError(f"invalid {harness} receipt {key}")
        if "quality" in result:
            validate_quality_value(result["quality"], harness)
    if "hub" in receipt:
        hub = receipt["hub"]
        if not isinstance(hub, dict) or set(hub) not in (
            {"remotes"},
            {"remotes", "status", "error_type"},
        ):
            raise ValueError("invalid receipt hub schema")
        if not isinstance(hub["remotes"], dict):
            raise ValueError("invalid receipt hub remotes")
        if set(hub) == {"remotes", "status", "error_type"}:
            if hub["remotes"] or hub["status"] != "failed":
                raise ValueError("invalid receipt hub failure schema")
            require_bounded_string(
                hub["error_type"],
                label="receipt hub failure error_type",
                maximum=128,
                pattern=re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z"),
            )
        for remote_id, status in hub["remotes"].items():
            validate_host_id(remote_id)
            validate_status_record(
                status,
                label=f"receipt remote {remote_id}",
                allowed_statuses=REMOTE_RECEIPT_STATUSES,
            )
    for key in ("publication", "receipt_publication"):
        if key in receipt:
            validate_status_record(
                receipt[key],
                label=f"receipt {key}",
                allowed_statuses=PUBLICATION_RECEIPT_STATUSES,
            )
    if "errors" in receipt:
        if not isinstance(receipt["errors"], list) or len(receipt["errors"]) > 1024:
            raise ValueError("invalid body-free receipt errors")
        allowed_components = APPROVED_HARNESSES | {
            "hub",
            "publication",
            "receipt_publication",
            "publish_manifest",
            "run",
        }
        for raw_error in receipt["errors"]:
            error = require_exact_keys(
                raw_error,
                required={"component", "error_type"},
                label="body-free receipt error",
            )
            if error["component"] not in allowed_components:
                raise ValueError("invalid body-free receipt error component")
            require_bounded_string(
                error["error_type"],
                label="body-free receipt error type",
                maximum=128,
                pattern=re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z"),
            )
    reject_metadata_secrets(receipt, label="receipt")
    return receipt


def validate_object_payload(
    payload: bytes,
    *,
    digest: str,
    harness: str,
    row: dict | None,
) -> dict:
    value = exact_json_loads(payload, label=f"archive object {digest}")
    if (
        not isinstance(value, dict)
        or value.get("archive_schema_version") != ARCHIVE_OBJECT_SCHEMA_VERSION
    ):
        raise LegacyArchiveSchemaError("archive object requires v2 regeneration")
    validate_archive_object(value, harness=harness)
    canonical_body = canonical_json(value)
    if payload != canonical_body + b"\n" or hashlib.sha256(canonical_body).hexdigest() != digest:
        raise ValueError(f"archive object hash/canonical JSON mismatch: {digest}.json")
    if value.get("source") not in HARNESS_SOURCES[harness] or (
        row is not None
        and (
            value.get("source") != row["source"]
            or value.get("session_id") != row["session_id"]
        )
    ):
        raise ValueError(f"archive object provenance mismatch for {harness}: {digest}")
    residuals = residual_secret_paths(value)
    if residuals:
        raise ValueError(
            "recognized credential in archive object at " + ", ".join(residuals[:5])
        )
    require_redaction_idempotence(value)
    return value


def validate_object_file(path: Path) -> dict:
    assert_no_symlink_components(path)
    if not OBJECT_NAME_RE.fullmatch(path.name):
        raise ValueError(f"invalid archive object filename: {path.name}")
    payload = read_bytes_snapshot_nofollow(
        path,
        max_bytes=MAX_OBJECT_BYTES,
        size_label="object",
    )
    value = exact_json_loads(payload, label=f"archive object {path.name}")
    validate_archive_object(value)
    expected = path.stem
    if (
        payload != (canonical_body := canonical_json(value)) + b"\n"
        or hashlib.sha256(canonical_body).hexdigest() != expected
    ):
        raise ValueError(f"archive object hash/canonical JSON mismatch: {path.name}")
    residuals = residual_secret_paths(value)
    if residuals:
        raise ValueError(
            "recognized credential in archive object at " + ", ".join(residuals[:5])
        )
    require_redaction_idempotence(value)
    return value


def validate_index_metadata(index_path: Path, host_id: str, harness: str) -> dict:
    """Validate an index without opening any conversation object bodies."""
    payload = read_bytes_snapshot_nofollow(
        index_path,
        max_bytes=MAX_STREAM_METADATA_BYTES,
        size_label="index",
    )
    return validate_index_value(
        exact_json_loads(payload, label=f"{harness} archive index"),
        host_id,
        harness,
    )


def validate_index_file(
    index_path: Path,
    source_root: Path,
    host_id: str,
    harness: str,
    *,
    require_exact_object_set: bool = True,
) -> dict:
    index = validate_index_metadata(index_path, host_id, harness)
    referenced: set[str] = set()
    for row in index["conversations"]:
        digest = row["object_sha256"]
        object_path = source_root / harness / "objects" / f"{digest}.json"
        if not object_path.is_file() or object_path.is_symlink():
            raise ValueError(f"archive index references missing object: {digest}")
        archived = validate_object_file(object_path)
        if (
            archived.get("source") not in HARNESS_SOURCES[harness]
            or archived.get("source") != row.get("source")
            or archived.get("session_id") != row.get("session_id")
        ):
            raise ValueError(f"archive object provenance mismatch for {harness}: {digest}")
        referenced.add(digest)
    object_root = source_root / harness / "objects"
    object_digests = (
        {path.stem for path in object_root.iterdir() if path.is_file()}
        if object_root.is_dir()
        else set()
    )
    if require_exact_object_set and object_digests != referenced:
        raise ValueError(f"archive index/object set mismatch for {harness}")
    return index


def write_publish_manifest(
    source_root: Path,
    receipt_path: Path,
    receipt: dict,
    config_sha256: str,
) -> dict:
    """Atomically bind a healthy receipt to one exact transferable snapshot."""
    collection_status = receipt.get("collection_status", receipt.get("status"))
    if collection_status not in {"completed", "completed_with_absent_harnesses"}:
        raise ValueError("refusing to manifest an incomplete collection")
    present_harnesses = sorted(
        path.name
        for path in source_root.iterdir()
        if (
            path.is_dir()
            and not path.is_symlink()
            and path.name in APPROVED_HARNESSES
            and (
                (path / "index.json").exists()
                or (path / "index.json").is_symlink()
            )
        )
    )
    manifest_harnesses = {}
    for harness in present_harnesses:
        index_path = source_root / harness / "index.json"
        index = validate_index_file(
            index_path,
            source_root,
            receipt["host_id"],
            harness,
            require_exact_object_set=False,
        )
        manifest_harnesses[harness] = {
            "index_sha256": file_sha256(index_path),
            "object_sha256": sorted(
                row["object_sha256"] for row in index["conversations"]
            ),
        }
    relative_receipt = receipt_path.relative_to(source_root)
    manifest = {
        "schema_version": 1,
        "host_id": receipt["host_id"],
        "run_id": receipt["run_id"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "extractor_sha256": receipt["extractor_sha256"],
        "config_sha256": config_sha256,
        "receipt": {
            "path": relative_receipt.as_posix(),
            "sha256": file_sha256(receipt_path),
        },
        "harnesses": manifest_harnesses,
    }
    receipt_payload = read_bytes_snapshot_nofollow(
        receipt_path,
        max_bytes=MAX_STREAM_METADATA_BYTES,
        size_label="candidate receipt",
    )
    if hashlib.sha256(receipt_payload).hexdigest() != manifest["receipt"]["sha256"]:
        raise ValueError("candidate receipt changed before manifest commit")
    validate_receipt_value(
        exact_json_loads(receipt_payload, label="candidate receipt"),
        receipt["host_id"],
        manifest,
        expected_receipt_path=receipt_path,
    )
    atomic_write_json(source_root / "publish-manifest.json", manifest)
    return manifest


def validate_publish_manifest_metadata(source_root: Path, host_id: str) -> dict:
    """Validate the manifest fields needed to derive a transfer allowlist."""
    manifest_path = source_root / "publish-manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError(f"host shard has no publish manifest: {host_id}")
    payload = read_bytes_snapshot_nofollow(
        manifest_path,
        max_bytes=MAX_STREAM_METADATA_BYTES,
        size_label="publish manifest",
    )
    return validate_manifest_value(
        exact_json_loads(payload, label="publish manifest"), host_id
    )


def validate_publish_manifest(
    source_root: Path,
    host_id: str,
    present_harnesses: set[str],
    receipt_paths: list[Path],
    *,
    require_objects: bool = True,
) -> dict:
    manifest = validate_publish_manifest_metadata(source_root, host_id)
    harnesses = manifest["harnesses"]
    receipt_binding = manifest["receipt"]
    if set(harnesses) != present_harnesses:
        raise ValueError(f"invalid publish manifest identity: {host_id}")

    receipt_relative = Path(receipt_binding["path"])
    receipt_path = source_root / receipt_relative
    if receipt_path not in receipt_paths:
        raise ValueError(f"manifest receipt is missing: {host_id}")
    receipt_payload = read_bytes_snapshot_nofollow(
        receipt_path,
        max_bytes=MAX_STREAM_METADATA_BYTES,
        size_label="receipt",
    )
    if hashlib.sha256(receipt_payload).hexdigest() != receipt_binding["sha256"]:
        raise ValueError(f"manifest receipt hash mismatch: {host_id}")
    receipt = validate_receipt_value(
        exact_json_loads(receipt_payload, label="manifest-bound receipt"),
        host_id,
        manifest,
    )
    receipt_harnesses = receipt["harnesses"]
    for harness in present_harnesses:
        result = receipt_harnesses[harness]
        if result.get("status") == "not_present_on_host":
            raise ValueError(f"manifest receipt has incomplete {harness} extraction")
        binding = harnesses.get(harness)
        object_digests = (
            binding.get("object_sha256") if isinstance(binding, dict) else None
        )
        index_path = source_root / harness / "index.json"
        index_payload = read_bytes_snapshot_nofollow(
            index_path,
            max_bytes=MAX_STREAM_METADATA_BYTES,
            size_label="index",
        )
        index = validate_index_value(
            exact_json_loads(index_payload, label=f"{harness} archive index"),
            host_id,
            harness,
        )
        if require_objects:
            for row in index["conversations"]:
                object_path = (
                    source_root
                    / harness
                    / "objects"
                    / f"{row['object_sha256']}.json"
                )
                if not object_path.is_file() or object_path.is_symlink():
                    raise ValueError(
                        f"archive index references missing object: {row['object_sha256']}"
                    )
                payload = read_bytes_snapshot_nofollow(
                    object_path,
                    max_bytes=MAX_OBJECT_BYTES,
                    size_label="object",
                )
                validate_object_payload(
                    payload,
                    digest=row["object_sha256"],
                    harness=harness,
                    row=row,
                )
        actual_digests = sorted(row["object_sha256"] for row in index["conversations"])
        if (
            object_digests != actual_digests
            or binding["index_sha256"] != hashlib.sha256(index_payload).hexdigest()
        ):
            raise ValueError(f"publish manifest snapshot mismatch for {harness}")
    return manifest


def validated_shard_files(
    source_root: Path,
    host_id: str,
    *,
    require_healthy_receipt: bool = True,
) -> list[Path]:
    source_root = assert_no_symlink_components(source_root)
    if not source_root.is_dir():
        raise FileNotFoundError(f"host shard does not exist: {source_root}")
    files: list[Path] = []
    receipt_paths: list[Path] = []
    present_harnesses: set[str] = set()
    for current_root, directories, names in os.walk(source_root, followlinks=False):
        current = Path(current_root)
        for name in [*directories, *names]:
            candidate = current / name
            if candidate.is_symlink():
                raise ValueError(f"refusing symlink in host shard: {candidate}")
        for name in names:
            candidate = current / name
            if not candidate.is_file():
                raise ValueError(f"refusing non-regular shard entry: {candidate}")
            relative = candidate.relative_to(source_root)
            if name.startswith("."):
                raise ValueError(f"refusing temporary shard entry: {relative}")
            if not relative.parts:
                raise ValueError("invalid empty shard path")
            top_level = relative.parts[0]
            if top_level not in APPROVED_HARNESSES | {"receipts", "publish-manifest.json"}:
                raise ValueError(f"unauthorized harness in host shard: {top_level}")
            if top_level == "publish-manifest.json":
                if len(relative.parts) != 1:
                    raise ValueError(f"unexpected publish manifest path: {relative}")
            elif top_level in APPROVED_HARNESSES:
                present_harnesses.add(top_level)
                valid_shape = (
                    len(relative.parts) == 3
                    and relative.parts[1] == "objects"
                    and OBJECT_NAME_RE.fullmatch(relative.parts[2])
                ) or (len(relative.parts) == 2 and relative.parts[1] == "index.json")
                if not valid_shape:
                    raise ValueError(f"unexpected file in {top_level} shard: {relative}")
            elif len(relative.parts) != 2 or not relative.name.endswith(".json"):
                raise ValueError(f"unexpected receipt path: {relative}")

            if "objects" in relative.parts:
                pass
            elif name in {"index.json", "publish-manifest.json"} or top_level == "receipts":
                if not require_healthy_receipt:
                    read_json_nofollow(candidate)
                if top_level == "receipts":
                    receipt_paths.append(candidate)
            else:
                raise ValueError(f"unexpected file in host shard: {relative}")
            files.append(candidate)
    if not files:
        raise ValueError(f"host shard is empty: {source_root}")
    if require_healthy_receipt:
        manifest = validate_publish_manifest(
            source_root, host_id, present_harnesses, receipt_paths
        )
        authorized = {
            source_root / "publish-manifest.json",
            source_root / Path(manifest["receipt"]["path"]),
        }
        for harness, binding in manifest["harnesses"].items():
            authorized.add(source_root / harness / "index.json")
            authorized.update(
                source_root / harness / "objects" / f"{digest}.json"
                for digest in binding["object_sha256"]
            )
        if not authorized.issubset(set(files)):
            raise ValueError(f"publish manifest references missing shard files: {host_id}")
        return sorted(authorized)
    for harness in present_harnesses:
        index_path = source_root / harness / "index.json"
        if not index_path.is_file() or index_path.is_symlink():
            raise ValueError(f"host shard is missing {harness} index")
        validate_index_file(index_path, source_root, host_id, harness)
    return sorted(files)


def copy_verified_file(source: Path, destination: Path, *, immutable: bool) -> bool:
    assert_no_symlink_components(source)
    destination = assert_no_symlink_components(destination, include_leaf=False)
    source_digest = file_sha256(source)
    if destination.exists() or destination.is_symlink():
        assert_no_symlink_components(destination)
        destination_digest = file_sha256(destination)
        if immutable:
            if destination_digest != source_digest:
                raise ValueError(f"immutable archive collision: {destination.name}")
            return False
    secure_mkdir(destination.parent)
    _, directory_fd = open_directory_fd(destination.parent)
    temporary_name = f".{destination.name}.{uuid.uuid4().hex}.tmp"
    output_fd = os.open(
        temporary_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=directory_fd,
    )
    input_fd = open_regular_fd(source)
    try:
        copied_digest = hashlib.sha256()
        source_handle = os.fdopen(input_fd, "rb")
        input_fd = -1
        with source_handle, os.fdopen(
            output_fd, "wb", closefd=False
        ) as output_handle:
            for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                copied_digest.update(chunk)
                output_handle.write(chunk)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        os.fchmod(output_fd, 0o600)
        os.close(output_fd)
        output_fd = -1
        if copied_digest.hexdigest() != source_digest:
            raise ValueError(f"copy verification failed: {source.name}")
        if immutable:
            try:
                os.link(
                    temporary_name,
                    destination.name,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                assert_no_symlink_components(destination)
                if file_sha256(destination) != source_digest:
                    raise ValueError(f"immutable archive collision: {destination.name}")
                return False
        else:
            os.rename(
                temporary_name,
                destination.name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
        os.fsync(directory_fd)
        if file_sha256(destination) != source_digest:
            raise ValueError(f"destination verification failed: {destination.name}")
    finally:
        if input_fd >= 0:
            os.close(input_fd)
        if output_fd >= 0:
            os.close(output_fd)
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        os.close(directory_fd)
    return True


def link_verified_local_object(source: Path, destination: Path) -> bool:
    """Link a validated staging object without consuming a second copy's blocks."""
    assert_no_symlink_components(source)
    destination = assert_no_symlink_components(destination, include_leaf=False)
    secure_mkdir(destination.parent)
    _, source_directory_fd = open_directory_fd(source.parent)
    _, target_directory_fd = open_directory_fd(destination.parent)
    source_descriptor = -1
    target_descriptor = -1
    cross_device = False
    try:
        source_descriptor = os.open(
            source.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=source_directory_fd,
        )
        source_stat = os.fstat(source_descriptor)
        if not stat.S_ISREG(source_stat.st_mode):
            raise ValueError(f"refusing unsafe local object: {source.name}")
        source_digest = descriptor_sha256(source_descriptor)
        try:
            os.link(
                source.name,
                destination.name,
                src_dir_fd=source_directory_fd,
                dst_dir_fd=target_directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            target_descriptor = os.open(
                destination.name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=target_directory_fd,
            )
            if descriptor_sha256(target_descriptor) != source_digest:
                raise ValueError(f"immutable archive collision: {destination.name}")
            return False
        except OSError as error:
            if error.errno != errno.EXDEV:
                raise
            cross_device = True
        if not cross_device:
            target_descriptor = os.open(
                destination.name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=target_directory_fd,
            )
            target_stat = os.fstat(target_descriptor)
            if (
                target_stat.st_dev != source_stat.st_dev
                or target_stat.st_ino != source_stat.st_ino
                or descriptor_sha256(target_descriptor) != source_digest
            ):
                raise ValueError(f"local object link verification failed: {source.name}")
            os.fchmod(target_descriptor, 0o600)
            os.fsync(target_directory_fd)
    finally:
        if source_descriptor >= 0:
            os.close(source_descriptor)
        if target_descriptor >= 0:
            os.close(target_descriptor)
        os.close(source_directory_fd)
        os.close(target_directory_fd)
    if cross_device:
        return copy_verified_file(source, destination, immutable=True)
    return True


def merge_index_file(source: Path, destination: Path) -> bool:
    source_index = read_json_nofollow(source)
    if not destination.exists():
        atomic_write_json(destination, source_index)
        return True
    destination_index = read_json_nofollow(destination)
    if (
        source_index.get("host_id") != destination_index.get("host_id")
        or source_index.get("harness") != destination_index.get("harness")
    ):
        raise ValueError("refusing to merge indexes with different identities")
    destination_objects = destination.parent / "objects"
    for row in destination_index.get("conversations", []):
        digest = row.get("object_sha256") if isinstance(row, dict) else None
        try:
            destination_value = read_json_nofollow(
                destination_objects / f"{digest}.json"
            )
        except (FileNotFoundError, ValueError):
            atomic_write_json(destination, source_index)
            return True
        if (
            destination_value.get("archive_schema_version")
            != ARCHIVE_OBJECT_SCHEMA_VERSION
        ):
            atomic_write_json(destination, source_index)
            return True
    merged = {
        row["object_sha256"]: row
        for row in destination_index.get("conversations", [])
    }
    before = canonical_json(destination_index)
    for row in source_index.get("conversations", []):
        merged[row["object_sha256"]] = row
    destination_index["conversations"] = sorted(
        merged.values(),
        key=lambda row: (str(row.get("session_id")), row["object_sha256"]),
    )
    if canonical_json(destination_index) == before:
        return False
    atomic_write_json(destination, destination_index)
    return True


def quarantine_unindexed_objects(destination_parent: Path, host_id: str) -> int:
    """Move local objects not named by their current index out of the live shard."""
    destination_parent = assert_no_symlink_components(destination_parent)
    host_id = validate_host_id(host_id)
    host_root = destination_parent / "hosts" / host_id
    if not host_root.exists():
        return 0
    assert_no_symlink_components(host_root)
    manifest_references: dict[str, set[str]] = {}
    manifest_path = host_root / "publish-manifest.json"
    if manifest_path.is_file():
        manifest = read_json_nofollow(manifest_path)
        if (
            manifest.get("schema_version") != 1
            or manifest.get("host_id") != host_id
            or not isinstance(manifest.get("harnesses"), dict)
        ):
            raise ValueError("invalid publish manifest identity")
        for harness, binding in manifest["harnesses"].items():
            object_digests = binding.get("object_sha256") if isinstance(binding, dict) else None
            if (
                harness not in APPROVED_HARNESSES
                or not isinstance(object_digests, list)
                or any(
                    not OBJECT_NAME_RE.fullmatch(f"{digest}.json")
                    for digest in object_digests
                )
            ):
                raise ValueError("invalid publish manifest object binding")
            manifest_references[harness] = {
                f"{digest}.json" for digest in object_digests
            }
    orphans: list[tuple[str, Path]] = []
    for harness in sorted(APPROVED_HARNESSES):
        harness_root = host_root / harness
        if not harness_root.exists():
            continue
        assert_no_symlink_components(harness_root)
        referenced: set[str] = set(manifest_references.get(harness, set()))
        index_path = harness_root / "index.json"
        if index_path.is_file():
            index = read_json_nofollow(index_path)
            if (
                index.get("schema_version") != 1
                or index.get("host_id") != host_id
                or index.get("harness") != harness
                or not isinstance(index.get("conversations"), list)
            ):
                raise ValueError(f"invalid archive index identity: {index_path.name}")
            for row in index["conversations"]:
                digest = row.get("object_sha256") if isinstance(row, dict) else None
                if not OBJECT_NAME_RE.fullmatch(f"{digest}.json"):
                    raise ValueError(f"invalid archive index row: {index_path.name}")
                referenced.add(f"{digest}.json")
        object_root = harness_root / "objects"
        if not object_root.exists():
            continue
        assert_no_symlink_components(object_root)
        for candidate in object_root.iterdir():
            if candidate.is_symlink() or not candidate.is_file():
                raise ValueError(f"refusing unsafe local object: {candidate.name}")
            if not OBJECT_NAME_RE.fullmatch(candidate.name):
                raise ValueError(f"invalid archive object filename: {candidate.name}")
            if candidate.name not in referenced:
                orphans.append((harness, candidate))
    if not orphans:
        return 0
    quarantine_root = (
        destination_parent
        / "quarantine"
        / host_id
        / f"unindexed-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')}-{uuid.uuid4().hex[:8]}"
    )
    for harness, source in orphans:
        target_root = quarantine_root / harness / "objects"
        secure_mkdir(target_root)
        _, source_directory_fd = open_directory_fd(source.parent)
        _, target_directory_fd = open_directory_fd(target_root)
        target_descriptor = -1
        try:
            source_stat = os.stat(
                source.name, dir_fd=source_directory_fd, follow_symlinks=False
            )
            if not stat.S_ISREG(source_stat.st_mode):
                raise ValueError(f"refusing unsafe local object: {source.name}")
            try:
                os.link(
                    source.name,
                    source.name,
                    src_dir_fd=source_directory_fd,
                    dst_dir_fd=target_directory_fd,
                    follow_symlinks=False,
                )
            except FileExistsError as error:
                raise ValueError(f"quarantine object collision: {source.name}") from error
            target_stat = os.stat(
                source.name, dir_fd=target_directory_fd, follow_symlinks=False
            )
            if (
                not stat.S_ISREG(target_stat.st_mode)
                or target_stat.st_dev != source_stat.st_dev
                or target_stat.st_ino != source_stat.st_ino
            ):
                raise ValueError(f"quarantine link verification failed: {source.name}")
            target_descriptor = os.open(
                source.name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=target_directory_fd,
            )
            os.fchmod(target_descriptor, 0o600)
            os.unlink(source.name, dir_fd=source_directory_fd)
            os.fsync(source_directory_fd)
            os.fsync(target_directory_fd)
        finally:
            if target_descriptor >= 0:
                os.close(target_descriptor)
            os.close(source_directory_fd)
            os.close(target_directory_fd)
    return len(orphans)


def restore_indexes_to_manifest(source_root: Path, host_id: str) -> bool:
    """Recover an old manifested snapshot after an interrupted additive merge."""
    manifest_path = source_root / "publish-manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        return False
    manifest = read_json_nofollow(manifest_path)
    if manifest.get("host_id") != host_id or not isinstance(manifest.get("harnesses"), dict):
        return False
    repairs: list[tuple[Path, bytes]] = []
    for harness, binding in manifest["harnesses"].items():
        if harness not in APPROVED_HARNESSES or not isinstance(binding, dict):
            return False
        expected_objects = binding.get("object_sha256")
        expected_index_hash = binding.get("index_sha256")
        if not isinstance(expected_objects, list) or not isinstance(expected_index_hash, str):
            return False
        index_path = source_root / harness / "index.json"
        if not index_path.is_file() or index_path.is_symlink():
            return False
        current = read_json_nofollow(index_path)
        if not isinstance(current.get("conversations"), list):
            return False
        expected_set = set(expected_objects)
        candidate = dict(current)
        candidate["conversations"] = [
            row
            for row in current["conversations"]
            if isinstance(row, dict) and row.get("object_sha256") in expected_set
        ]
        payload = canonical_json(candidate) + b"\n"
        if hashlib.sha256(payload).hexdigest() != expected_index_hash:
            return False
        if file_sha256(index_path) != expected_index_hash:
            repairs.append((index_path, payload))
    for path, payload in repairs:
        atomic_write_bytes(path, payload)
    return bool(repairs)


def merge_host_shard(
    source_root: Path,
    destination_parent: Path,
    host_id: str,
    *,
    require_healthy_receipt: bool = True,
    _transaction_token: object | None = None,
) -> dict:
    if (
        _transaction_token is not None
        and _transaction_token is not _RUN_CONFIG_TRANSACTION
    ):
        raise ValueError("invalid merge transaction token")
    defer_destination_validation = _transaction_token is _RUN_CONFIG_TRANSACTION
    source_root = assert_no_symlink_components(source_root)
    destination_root = destination_parent / "hosts" / host_id
    quarantined_unindexed = (
        0
        if require_healthy_receipt
        else quarantine_unindexed_objects(destination_parent, host_id)
    )
    files = validated_shard_files(
        source_root, host_id, require_healthy_receipt=require_healthy_receipt
    )
    source_wins = True
    if (
        require_healthy_receipt
        and destination_root.exists()
        and (destination_root / "publish-manifest.json").is_file()
    ):
        try:
            validated_shard_files(destination_root, host_id, require_healthy_receipt=True)
        except ValueError:
            if not restore_indexes_to_manifest(destination_root, host_id):
                raise
            validated_shard_files(destination_root, host_id, require_healthy_receipt=True)
        source_manifest = read_json_nofollow(source_root / "publish-manifest.json")
        destination_manifest = read_json_nofollow(
            destination_root / "publish-manifest.json"
        )

        def snapshot_sets(manifest: dict) -> dict[str, set[str]]:
            return {
                harness: set(binding["object_sha256"])
                for harness, binding in manifest["harnesses"].items()
            }

        source_sets = snapshot_sets(source_manifest)
        destination_sets = snapshot_sets(destination_manifest)

        def snapshot_subset(left: dict[str, set[str]], right: dict[str, set[str]]) -> bool:
            return all(
                harness in right and digests.issubset(right[harness])
                for harness, digests in left.items()
            )

        destination_is_subset = snapshot_subset(destination_sets, source_sets)
        source_is_subset = snapshot_subset(source_sets, destination_sets)
        if not destination_is_subset and not source_is_subset:
            raise ValueError(f"divergent immutable snapshots for host: {host_id}")
        if source_is_subset and not destination_is_subset:
            source_wins = False
        elif source_is_subset and destination_is_subset:
            source_wins = str(source_manifest["generated_at"]) >= str(
                destination_manifest["generated_at"]
            )

    if require_healthy_receipt:
        if not source_wins:
            return {
                "status": "published",
                "files_copied": 0,
                "files_verified": len(files),
                "quarantined_unindexed_objects": quarantined_unindexed,
            }
        merge_parent = destination_parent / ".merge"
        secure_mkdir(merge_parent)
        backup_root = destination_parent / "hosts" / (
            f".{host_id}.last-good-{uuid.uuid4().hex}"
        )
        backup_created = False
        published_candidate = False
        with tempfile.TemporaryDirectory(
            dir=merge_parent, prefix=f"{host_id}-"
        ) as staged_directory:
            candidate_root = Path(staged_directory) / host_id
            copied = 0
            verified = 0
            for source in files:
                relative = source.relative_to(source_root)
                immutable = "objects" in relative.parts or "receipts" in relative.parts
                if copy_verified_file(
                    source,
                    candidate_root / relative,
                    immutable=immutable,
                ):
                    copied += 1
                verified += 1
            validated_shard_files(
                candidate_root, host_id, require_healthy_receipt=True
            )
            secure_mkdir(destination_root.parent)
            try:
                if destination_root.exists():
                    os.replace(destination_root, backup_root)
                    backup_created = True
                os.replace(candidate_root, destination_root)
                published_candidate = True
                validated_shard_files(
                    destination_root, host_id, require_healthy_receipt=True
                )
            except BaseException:
                if published_candidate and destination_root.exists():
                    shutil.rmtree(destination_root)
                if backup_created and backup_root.exists():
                    os.replace(backup_root, destination_root)
                raise
            if backup_created:
                shutil.rmtree(backup_root)
            return {
                "status": "published",
                "files_copied": copied,
                "files_verified": verified,
                "quarantined_unindexed_objects": quarantined_unindexed,
            }

    copied = 0
    verified = 0
    # Bodies first, indexes second, receipts third, manifest last. The manifest
    # is the only authorization pointer, so interrupted copies remain unusable.
    files.sort(
        key=lambda path: (
            3
            if path.name == "publish-manifest.json"
            else
            2
            if "receipts" in path.relative_to(source_root).parts
            else 1
            if path.name == "index.json"
            else 0,
            str(path),
        )
    )
    rollback_payloads: dict[Path, bytes] = {}
    if source_wins:
        for source in files:
            relative = source.relative_to(source_root)
            if source.name == "index.json" or source.name == "publish-manifest.json":
                destination = destination_root / relative
                if destination.is_file() and not destination.is_symlink():
                    rollback_payloads[destination] = read_bytes_nofollow(destination)
    try:
        for source in files:
            relative = source.relative_to(source_root)
            if not source_wins and "receipts" not in relative.parts:
                continue
            destination = destination_root / relative
            immutable = "objects" in relative.parts or "receipts" in relative.parts
            if source.name == "index.json":
                changed = (
                    copy_verified_file(source, destination, immutable=False)
                    if require_healthy_receipt
                    else merge_index_file(source, destination)
                )
            elif immutable and not require_healthy_receipt and "objects" in relative.parts:
                changed = link_verified_local_object(source, destination)
            else:
                changed = copy_verified_file(source, destination, immutable=immutable)
            if changed:
                copied += 1
            verified += 1
        if not defer_destination_validation:
            validated_shard_files(
                destination_root,
                host_id,
                require_healthy_receipt=require_healthy_receipt,
            )
    except BaseException:
        for destination, payload in rollback_payloads.items():
            atomic_write_bytes(destination, payload)
        raise
    return {
        "status": (
            "pending_validation"
            if defer_destination_validation
            else "published"
        ),
        "files_copied": copied,
        "files_verified": verified,
        "quarantined_unindexed_objects": quarantined_unindexed,
    }


def publish_host_shard(
    spool_root: Path,
    drive_root: Path,
    host_id: str,
    *,
    allow_non_google_drive: bool = False,
    require_healthy_receipt: bool = True,
) -> dict:
    spool_root = assert_no_symlink_components(spool_root)
    drive_root = assert_no_symlink_components(drive_root)
    if not drive_root.exists() or not drive_root.is_dir():
        return {"status": "blocked_drive_unavailable", "files_copied": 0}
    if not allow_non_google_drive and not is_google_drive_path(drive_root):
        return {"status": "blocked_not_google_drive", "files_copied": 0}
    try:
        validate_output_root(drive_root)
    except ValueError:
        return {
            "status": "blocked_integrity_failure",
            "files_copied": 0,
        }

    source_root = spool_root / "hosts" / host_id
    try:
        return merge_host_shard(
            source_root,
            drive_root,
            host_id,
            require_healthy_receipt=require_healthy_receipt,
        )
    except FileNotFoundError:
        return {"status": "blocked_source_missing", "files_copied": 0}
    except ValueError:
        return {
            "status": "blocked_integrity_failure",
            "files_copied": 0,
        }


def transport_identity_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def transport_projection_path(harness: str) -> str:
    return f"transport-index/{harness}.json"


def build_transport_projection(index: dict, harness: str) -> dict:
    rows = []
    for row in index["conversations"]:
        projected = {
            "transport_schema": "base-v1",
            "object_sha256": row["object_sha256"],
            "source": row["source"],
            "session_id_sha256": transport_identity_sha256(row["session_id"]),
        }
        if harness == "codex" and "source_sha256" in row:
            projected.update(
                {
                    "transport_schema": "codex-current-v1",
                    "source_sha256": row["source_sha256"],
                    "installation_sha256": transport_identity_sha256(
                        row["installation"]
                    ),
                }
            )
        rows.append(projected)
    rows.sort(key=lambda row: row["object_sha256"])
    return {
        "schema_version": 1,
        "host_id": index["host_id"],
        "harness": harness,
        "conversations": rows,
    }


def validate_transport_projection_value(
    projection: object, host_id: str, harness: str
) -> dict:
    projection = require_exact_keys(
        projection,
        required={"schema_version", "host_id", "harness", "conversations"},
        label=f"{harness} transport projection",
    )
    if (
        projection["schema_version"] != 1
        or projection["host_id"] != host_id
        or projection["harness"] != harness
        or not isinstance(projection["conversations"], list)
    ):
        raise ValueError(f"invalid {harness} transport projection identity")
    digest_pattern = re.compile(r"[0-9a-f]{64}\Z")
    seen = set()
    for raw_row in projection["conversations"]:
        if not isinstance(raw_row, dict):
            raise ValueError(f"invalid {harness} transport projection row")
        schema = raw_row.get("transport_schema")
        required = {
            "transport_schema",
            "object_sha256",
            "source",
            "session_id_sha256",
        }
        if schema == "codex-current-v1" and harness == "codex":
            required |= {"source_sha256", "installation_sha256"}
        elif schema != "base-v1":
            raise ValueError(f"invalid {harness} transport projection schema")
        row = require_exact_keys(
            raw_row,
            required=required,
            label=f"{harness} transport projection row",
        )
        digest = row["object_sha256"]
        for key in (
            "object_sha256",
            "session_id_sha256",
            "source_sha256",
            "installation_sha256",
        ):
            if key in row:
                require_bounded_string(
                    row[key],
                    label=f"projection {key}",
                    maximum=64,
                    pattern=digest_pattern,
                )
        if digest in seen or row["source"] not in HARNESS_SOURCES[harness]:
            raise ValueError(f"invalid {harness} transport projection provenance")
        seen.add(digest)
    if [row["object_sha256"] for row in projection["conversations"]] != sorted(seen):
        raise ValueError(f"invalid {harness} transport projection order")
    reject_metadata_secrets(projection, label=f"{harness} transport projection")
    return projection


def load_stream_metadata(
    spool_root: Path,
    host_id: str,
) -> tuple[Path, dict, list[tuple[str, bytes]], dict[str, dict[str, dict]]]:
    """Load and validate all body-free authorization metadata as byte snapshots."""
    source_root = spool_root / "hosts" / host_id
    manifest_path = source_root / "publish-manifest.json"
    if not manifest_path.exists() and not manifest_path.is_symlink():
        raise PendingManifestError("publish manifest is pending")
    manifest_payload = read_bytes_snapshot_nofollow(
        manifest_path,
        max_bytes=MAX_STREAM_METADATA_BYTES,
        size_label="publish manifest",
    )
    manifest = validate_manifest_value(
        exact_json_loads(manifest_payload, label="publish manifest"), host_id
    )
    frames: list[tuple[str, bytes]] = [("publish-manifest.json", manifest_payload)]

    receipt_relative = manifest["receipt"]["path"]
    receipt_path = source_root / receipt_relative
    receipt_payload = read_bytes_snapshot_nofollow(
        receipt_path,
        max_bytes=MAX_STREAM_METADATA_BYTES,
        size_label="manifest-bound receipt",
    )
    if hashlib.sha256(receipt_payload).hexdigest() != manifest["receipt"]["sha256"]:
        raise ValueError("manifest-bound receipt hash mismatch")
    receipt = validate_receipt_value(
        exact_json_loads(receipt_payload, label="manifest-bound receipt"),
        host_id,
        manifest,
        expected_receipt_path=receipt_path,
    )
    if "receipt_path" not in receipt:
        raise ValueError("stream receipt is missing its bound receipt_path")
    frames.append((receipt_relative, receipt_payload))

    rows_by_harness: dict[str, dict[str, dict]] = {}
    for harness in sorted(manifest["harnesses"]):
        if receipt["harnesses"][harness]["status"] == "not_present_on_host":
            raise ValueError(f"manifest receipt has incomplete {harness} extraction")
        index_relative = f"{harness}/index.json"
        index_payload = read_bytes_snapshot_nofollow(
            source_root / index_relative,
            max_bytes=MAX_STREAM_METADATA_BYTES,
            size_label=f"{harness} index",
        )
        binding = manifest["harnesses"][harness]
        if hashlib.sha256(index_payload).hexdigest() != binding["index_sha256"]:
            raise ValueError(f"manifest-bound index hash mismatch for {harness}")
        index = validate_index_value(
            exact_json_loads(index_payload, label=f"{harness} archive index"),
            host_id,
            harness,
        )
        if index_payload != canonical_json(index) + b"\n":
            raise ValueError(f"manifest-bound index is not canonical for {harness}")
        rows = {row["object_sha256"]: row for row in index["conversations"]}
        if sorted(rows) != binding["object_sha256"]:
            raise ValueError(f"manifest-bound object set mismatch for {harness}")
        rows_by_harness[harness] = rows
        projection = build_transport_projection(index, harness)
        validate_transport_projection_value(projection, host_id, harness)
        frames.append(
            (transport_projection_path(harness), canonical_json(projection) + b"\n")
        )
    return source_root, manifest, frames, rows_by_harness


def write_stream_frame(output, relative_path: str, payload: bytes) -> None:
    path_payload = relative_path.encode("utf-8")
    if not 0 < len(path_payload) <= MAX_STREAM_PATH_BYTES:
        raise ValueError("stream path length is out of bounds")
    output.write(STREAM_FRAME_HEADER.pack(len(path_payload), len(payload)))
    output.write(path_payload)
    view = memoryview(payload)
    for offset in range(0, len(view), 1024 * 1024):
        output.write(view[offset : offset + 1024 * 1024])


def parse_cache_hints(stream) -> set[str]:
    payload = stream.read(MAX_CACHE_HINT_BYTES + 1)
    if len(payload) > MAX_CACHE_HINT_BYTES:
        raise ValueError("cache hints exceed maximum size")
    if not payload:
        return set()
    if not payload.endswith(b"\n"):
        raise ValueError("cache hints must be newline terminated")
    try:
        lines = payload.decode("ascii").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError("cache hints must be ASCII") from error
    if (
        len(lines) > MAX_STREAM_FILES
        or lines != sorted(lines)
        or len(lines) != len(set(lines))
        or any(not re.fullmatch(r"[0-9a-f]{64}", line) for line in lines)
    ):
        raise ValueError("invalid cache hint set")
    return set(lines)


def encode_cache_hints(digests: set[str]) -> bytes:
    if len(digests) > MAX_STREAM_FILES or any(
        not re.fullmatch(r"[0-9a-f]{64}", digest) for digest in digests
    ):
        raise ValueError("invalid local cache hint set")
    return b"" if not digests else ("\n".join(sorted(digests)) + "\n").encode("ascii")


def stream_shard_to(
    output,
    spool_root: Path,
    host_id: str,
    *,
    after_validate=None,
    skip_object_digests: set[str] | None = None,
) -> None:
    """Emit only validated, manifest-authorized snapshots while holding the run lock."""
    if not spool_root.is_absolute():
        raise ValueError("stream spool_root must be absolute")
    spool_root = validate_output_root(spool_root)
    host_id = validate_host_id(host_id)
    skip_object_digests = set() if skip_object_digests is None else skip_object_digests
    encode_cache_hints(skip_object_digests)
    with archive_run_lock(spool_root):
        source_root, manifest, metadata_frames, rows_by_harness = load_stream_metadata(
            spool_root, host_id
        )
        file_count = len(metadata_frames)
        metadata_bytes = sum(len(payload) for _, payload in metadata_frames)
        total_bytes = metadata_bytes
        if (
            file_count > MAX_STREAM_FILES
            or metadata_bytes > MAX_STREAM_METADATA_TOTAL_BYTES
            or total_bytes > MAX_STREAM_TOTAL_BYTES
        ):
            raise ValueError("stream metadata exceeds transfer bounds")

        # Whole-snapshot preflight. This intentionally retains no parsed object
        # tree and emits no stdout byte, including magic, until every authorized
        # object (including cache-hinted objects) has passed the v2 contract.
        for harness in sorted(manifest["harnesses"]):
            for digest in manifest["harnesses"][harness]["object_sha256"]:
                relative = f"{harness}/objects/{digest}.json"
                payload = read_bytes_snapshot_nofollow(
                    source_root / relative,
                    max_bytes=MAX_OBJECT_BYTES,
                    size_label="manifest-bound object",
                )
                validate_object_payload(
                    payload,
                    digest=digest,
                    harness=harness,
                    row=rows_by_harness[harness][digest],
                )
                file_count += 1
                total_bytes += len(payload)
                if file_count > MAX_STREAM_FILES or total_bytes > MAX_STREAM_TOTAL_BYTES:
                    raise ValueError("stream shard exceeds transfer bounds")
                if after_validate is not None:
                    after_validate(source_root / relative, relative, payload)
                del payload

        output.write(STREAM_MAGIC)
        for relative, payload in metadata_frames:
            if after_validate is not None:
                after_validate(source_root / relative, relative, payload)
            write_stream_frame(output, relative, payload)

        for harness in sorted(manifest["harnesses"]):
            for digest in manifest["harnesses"][harness]["object_sha256"]:
                relative = f"{harness}/objects/{digest}.json"
                payload = read_bytes_snapshot_nofollow(
                    source_root / relative,
                    max_bytes=MAX_OBJECT_BYTES,
                    size_label="manifest-bound object",
                )
                validate_object_payload(
                    payload,
                    digest=digest,
                    harness=harness,
                    row=rows_by_harness[harness][digest],
                )
                if digest in skip_object_digests:
                    continue
                # Re-read and revalidate immediately before emitting this exact
                # snapshot. A post-preflight replacement cannot cross stdout.
                write_stream_frame(output, relative, payload)
        output.write(STREAM_FRAME_HEADER.pack(0, 0))
        output.flush()


def stream_shard_command(args: argparse.Namespace) -> int:
    try:
        cache_hints = parse_cache_hints(sys.stdin.buffer) if args.cache_hints_stdin else set()
        stream_shard_to(
            sys.stdout.buffer,
            Path(args.spool_root),
            args.host_id,
            skip_object_digests=cache_hints,
        )
        return 0
    except PendingManifestError:
        print("pending_manifest", file=sys.stderr)
        return STREAM_EXIT_PENDING_MANIFEST
    except LegacyArchiveSchemaError:
        print("legacy_schema", file=sys.stderr)
        return STREAM_EXIT_LEGACY_SCHEMA
    except (FileNotFoundError, OSError, ValueError):
        # Do not echo exception text: paths and parser details are not part of
        # the protocol and stdout must remain binary-only.
        print("remote_integrity_rejection", file=sys.stderr)
        return STREAM_EXIT_INTEGRITY_REJECTION


def validate_stream_relative_path(value: str) -> None:
    if (
        not value
        or len(value.encode("utf-8")) > MAX_STREAM_PATH_BYTES
        or "\x00" in value
    ):
        raise LocalStreamIntegrityError("invalid stream path")
    path = Path(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise LocalStreamIntegrityError("stream path traversal rejected")


def read_exact_before_deadline(stream, length: int, deadline: float) -> bytes:
    payload = bytearray()
    while len(payload) < length:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RemoteTimeoutError("remote stream timed out")
        try:
            descriptor = stream.fileno()
        except (AttributeError, OSError):
            chunk = stream.read(length - len(payload))
        else:
            readable, _, _ = select.select([descriptor], [], [], remaining)
            if not readable:
                raise RemoteTimeoutError("remote stream timed out")
            chunk = os.read(descriptor, min(1024 * 1024, length - len(payload)))
        if not chunk:
            raise EOFError("remote stream ended before its terminal frame")
        payload.extend(chunk)
    return bytes(payload)


def read_one_or_eof_before_deadline(stream, deadline: float) -> bytes:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RemoteTimeoutError("remote stream timed out")
    try:
        descriptor = stream.fileno()
    except (AttributeError, OSError):
        return stream.read(1)
    readable, _, _ = select.select([descriptor], [], [], remaining)
    if not readable:
        raise RemoteTimeoutError("remote stream timed out")
    return os.read(descriptor, 1)


def receive_payload_to_file(
    stream,
    destination: Path,
    length: int,
    deadline: float,
) -> None:
    secure_mkdir(destination.parent)
    _, directory_fd = open_directory_fd(destination.parent)
    descriptor = os.open(
        destination.name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=directory_fd,
    )
    try:
        remaining_length = length
        while remaining_length:
            chunk = read_exact_before_deadline(
                stream, min(1024 * 1024, remaining_length), deadline
            )
            view = memoryview(chunk)
            written = 0
            while written < len(view):
                count = os.write(descriptor, view[written:])
                if count <= 0:
                    raise OSError("short write while staging remote stream")
                written += count
            remaining_length -= len(chunk)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
        os.close(directory_fd)


def reconstruct_indexes_from_transport(
    incoming_root: Path,
    manifest: dict,
    host_id: str,
) -> None:
    for harness in sorted(manifest["harnesses"]):
        projection_relative = transport_projection_path(harness)
        projection_payload = read_bytes_snapshot_nofollow(
            incoming_root / projection_relative,
            max_bytes=MAX_STREAM_METADATA_BYTES,
            size_label=f"{harness} transport projection",
        )
        projection = validate_transport_projection_value(
            exact_json_loads(
                projection_payload, label=f"{harness} transport projection"
            ),
            host_id,
            harness,
        )
        expected_digests = manifest["harnesses"][harness]["object_sha256"]
        if [row["object_sha256"] for row in projection["conversations"]] != sorted(
            expected_digests
        ):
            raise LocalStreamIntegrityError("transport projection object set mismatch")
        reconstructed_rows = []
        for projected in projection["conversations"]:
            digest = projected["object_sha256"]
            object_payload = read_bytes_snapshot_nofollow(
                incoming_root / harness / "objects" / f"{digest}.json",
                max_bytes=MAX_OBJECT_BYTES,
                size_label="received archive object",
            )
            value = validate_object_payload(
                object_payload,
                digest=digest,
                harness=harness,
                row=None,
            )
            session_id = value.get("session_id")
            if transport_identity_sha256(session_id) != projected["session_id_sha256"]:
                raise LocalStreamIntegrityError("transport session identity mismatch")
            row = {
                "object_sha256": digest,
                "session_id": session_id,
                "source": projected["source"],
            }
            if value.get("source") != projected["source"]:
                raise LocalStreamIntegrityError("transport source identity mismatch")
            if projected["transport_schema"] == "codex-current-v1":
                installation = value.get("installation")
                if (
                    not isinstance(installation, str)
                    or transport_identity_sha256(installation)
                    != projected["installation_sha256"]
                ):
                    raise LocalStreamIntegrityError("Codex installation identity mismatch")
                row["source_sha256"] = projected["source_sha256"]
                row["installation"] = installation
            reconstructed_rows.append(row)
        reconstructed_rows.sort(
            key=lambda row: (str(row["session_id"]), row["object_sha256"])
        )
        index = {
            "schema_version": 1,
            "host_id": host_id,
            "harness": harness,
            "conversations": reconstructed_rows,
        }
        validate_index_value(index, host_id, harness)
        index_payload = canonical_json(index) + b"\n"
        if (
            hashlib.sha256(index_payload).hexdigest()
            != manifest["harnesses"][harness]["index_sha256"]
        ):
            raise LocalStreamIntegrityError("reconstructed index hash mismatch")
        atomic_write_bytes(incoming_root / harness / "index.json", index_payload)

    transport_root = incoming_root / "transport-index"
    for harness in sorted(manifest["harnesses"]):
        (transport_root / f"{harness}.json").unlink()
    transport_root.rmdir()


def receive_stream_to_directory(
    stream,
    incoming_root: Path,
    host_id: str,
    remote_spool_root: str,
    deadline: float,
    *,
    cached_shard: Path | None = None,
    cache_hints: set[str] | None = None,
) -> dict:
    if read_exact_before_deadline(stream, len(STREAM_MAGIC), deadline) != STREAM_MAGIC:
        raise LocalStreamIntegrityError("invalid remote stream magic")
    expected_paths: list[str] | None = None
    authorized_paths: set[str] | None = None
    cache_hints = set() if cache_hints is None else cache_hints
    received_paths: set[str] = set()
    total_bytes = 0
    metadata_bytes = 0
    file_count = 0
    object_count = 0
    while True:
        header = read_exact_before_deadline(stream, STREAM_FRAME_HEADER.size, deadline)
        path_length, payload_length = STREAM_FRAME_HEADER.unpack(header)
        if path_length == 0:
            if payload_length != 0:
                raise LocalStreamIntegrityError("invalid terminal stream frame")
            break
        if path_length > MAX_STREAM_PATH_BYTES:
            raise LocalStreamIntegrityError("stream path is oversized")
        raw_path = read_exact_before_deadline(stream, path_length, deadline)
        try:
            relative = raw_path.decode("utf-8")
        except UnicodeDecodeError as error:
            raise LocalStreamIntegrityError("stream path is not UTF-8") from error
        validate_stream_relative_path(relative)
        if relative in received_paths:
            raise LocalStreamIntegrityError("duplicate stream path")
        if expected_paths is None:
            if relative != "publish-manifest.json":
                raise LocalStreamIntegrityError("manifest is not the first stream frame")
            maximum = MAX_STREAM_METADATA_BYTES
        else:
            if file_count >= len(expected_paths) or relative != expected_paths[file_count]:
                raise LocalStreamIntegrityError("stream path is outside the exact manifest allowlist")
            maximum = (
                MAX_OBJECT_BYTES if "/objects/" in relative else MAX_STREAM_METADATA_BYTES
            )
        if payload_length > maximum:
            raise LocalStreamIntegrityError("stream payload is oversized")
        file_count += 1
        total_bytes += payload_length
        if "/objects/" in relative:
            object_count += 1
        else:
            metadata_bytes += payload_length
        if file_count > MAX_STREAM_FILES or total_bytes > MAX_STREAM_TOTAL_BYTES:
            raise LocalStreamIntegrityError("stream exceeds aggregate bounds")
        if metadata_bytes > MAX_STREAM_METADATA_TOTAL_BYTES:
            raise LocalStreamIntegrityError("stream metadata exceeds aggregate bounds")
        receive_payload_to_file(
            stream,
            incoming_root / relative,
            payload_length,
            deadline,
        )
        received_paths.add(relative)
        if expected_paths is None:
            manifest = validate_publish_manifest_metadata(incoming_root, host_id)
            all_paths = [
                "publish-manifest.json",
                manifest["receipt"]["path"],
                *(
                    transport_projection_path(harness)
                    for harness in sorted(manifest["harnesses"])
                ),
                *(
                    f"{harness}/objects/{digest}.json"
                    for harness in sorted(manifest["harnesses"])
                    for digest in manifest["harnesses"][harness]["object_sha256"]
                ),
            ]
            authorized_paths = set(all_paths)
            expected_paths = [
                relative_path
                for relative_path in all_paths
                if not (
                    "/objects/" in relative_path
                    and Path(relative_path).stem in cache_hints
                )
            ]
            if cached_shard is not None:
                for relative_path in all_paths:
                    if (
                        "/objects/" in relative_path
                        and Path(relative_path).stem in cache_hints
                    ):
                        cached_object = cached_shard / relative_path
                        if not cached_object.is_file() or cached_object.is_symlink():
                            raise LocalStreamIntegrityError(
                                "cache hint does not name a validated cached object"
                            )
                        link_verified_local_object(
                            cached_object, incoming_root / relative_path
                        )
    if expected_paths is None or file_count != len(expected_paths):
        raise LocalStreamIntegrityError("stream omitted a manifest-authorized path")
    if read_one_or_eof_before_deadline(stream, deadline):
        raise LocalStreamIntegrityError("bytes followed the terminal stream frame")

    if authorized_paths is None:
        raise LocalStreamIntegrityError("stream omitted its authorization manifest")
    validate_staged_allowlist(incoming_root, authorized_paths)
    manifest = validate_publish_manifest_metadata(incoming_root, host_id)
    remote_receipt_path = (
        Path(remote_spool_root)
        / "hosts"
        / host_id
        / manifest["receipt"]["path"]
    )
    receipt_payload = read_bytes_snapshot_nofollow(
        incoming_root / manifest["receipt"]["path"],
        max_bytes=MAX_STREAM_METADATA_BYTES,
        size_label="received receipt",
    )
    validate_receipt_value(
        exact_json_loads(receipt_payload, label="received receipt"),
        host_id,
        manifest,
        expected_receipt_path=remote_receipt_path,
    )
    reconstruct_indexes_from_transport(incoming_root, manifest, host_id)
    validated_shard_files(incoming_root, host_id, require_healthy_receipt=True)
    return {
        "stream_files_received": file_count,
        "stream_objects_received": object_count,
        "stream_bytes_received": total_bytes,
    }


def validate_staged_allowlist(source_root: Path, allowed_paths: set[str]) -> None:
    """Reject missing, extra, non-regular, or symlinked staged entries."""
    source_root = assert_no_symlink_components(source_root)
    allowed_directories = {
        Path(*Path(relative).parts[:depth]).as_posix()
        for relative in allowed_paths
        for depth in range(1, len(Path(relative).parts))
    }
    actual_paths: set[str] = set()
    actual_directories: set[str] = set()
    for current_root, directories, names in os.walk(source_root, followlinks=False):
        current = Path(current_root)
        for name in directories:
            candidate = current / name
            if candidate.is_symlink():
                raise ValueError(f"refusing symlink in staged remote shard: {candidate}")
            actual_directories.add(candidate.relative_to(source_root).as_posix())
        for name in names:
            candidate = current / name
            if candidate.is_symlink() or not candidate.is_file():
                raise ValueError(f"refusing unsafe staged remote entry: {candidate}")
            actual_paths.add(candidate.relative_to(source_root).as_posix())
    unauthorized = actual_paths - allowed_paths
    unauthorized_directories = actual_directories - allowed_directories
    missing = allowed_paths - actual_paths
    if unauthorized or unauthorized_directories:
        raise ValueError("remote transfer produced a path outside the manifest allowlist")
    if missing:
        raise ValueError("remote transfer omitted a manifest-authorized path")


def validate_remote_absolute_path_token(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or not REMOTE_SPOOL_ROOT_RE.fullmatch(value)
        or any(part in {"", ".", ".."} for part in value.split("/")[1:])
    ):
        raise ValueError(f"invalid {label}")
    return value


def write_before_deadline(stream, payload: bytes, deadline: float) -> None:
    view = memoryview(payload)
    offset = 0
    while offset < len(view):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RemoteTimeoutError("remote stream timed out")
        descriptor = stream.fileno()
        _, writable, _ = select.select([], [descriptor], [], remaining)
        if not writable:
            raise RemoteTimeoutError("remote stream timed out")
        written = os.write(descriptor, view[offset : offset + 1024 * 1024])
        if written <= 0:
            raise BrokenPipeError("remote helper closed cache-hint input")
        offset += written


def terminate_remote_process(process) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (AttributeError, ProcessLookupError, PermissionError):
        try:
            process.kill()
        except (AttributeError, ProcessLookupError):
            pass
    try:
        process.wait(timeout=5)
    except (subprocess.TimeoutExpired, AttributeError):
        pass


def wait_remote_process(process, deadline: float) -> int:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        terminate_remote_process(process)
        raise RemoteTimeoutError("remote stream timed out")
    try:
        return process.wait(timeout=remaining)
    except subprocess.TimeoutExpired as error:
        terminate_remote_process(process)
        raise RemoteTimeoutError("remote stream timed out") from error


def raise_for_remote_exit(returncode: int, *, stream_complete: bool) -> None:
    if returncode == 0:
        if not stream_complete:
            raise LocalStreamIntegrityError("remote stream ended before completion")
        return
    if returncode == STREAM_EXIT_PENDING_MANIFEST:
        raise PendingManifestError("remote manifest is pending")
    if returncode == STREAM_EXIT_LEGACY_SCHEMA:
        raise LegacyArchiveSchemaError("remote shard requires v2 regeneration")
    if returncode == 255:
        raise RemoteUnreachableError("SSH transport was unreachable")
    raise RemoteIntegrityError("trusted remote helper rejected the shard")


def pull_remote_stream(
    remote: dict,
    spool_root: Path,
    remote_host_id: str,
    cached_shard: Path | None,
    cache_hints: set[str],
) -> dict:
    ssh_host = remote["ssh_host"]
    remote_spool_root = remote["remote_spool_root"]
    remote_pipeline_path = remote["remote_pipeline_path"]
    timeout_seconds = remote.get("timeout_seconds", 300)
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 86400:
        raise ValueError("invalid remote timeout_seconds")
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        "-o",
        "ControlMaster=no",
        "-o",
        "ControlPath=none",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "UpdateHostKeys=no",
        ssh_host,
        "python3",
        remote_pipeline_path,
        "stream-shard",
        "--spool-root",
        remote_spool_root,
        "--host-id",
        remote_host_id,
        "--cache-hints-stdin",
    ]
    deadline = time.monotonic() + timeout_seconds
    incoming_parent = spool_root / ".incoming"
    secure_mkdir(incoming_parent)
    with tempfile.TemporaryDirectory(
        dir=incoming_parent, prefix=f"{remote_host_id}-"
    ) as incoming, tempfile.TemporaryFile(mode="w+b", dir=incoming_parent) as stderr_file:
        os.fchmod(stderr_file.fileno(), 0o600)
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr_file,
                start_new_session=True,
            )
        except OSError as error:
            raise RemoteUnreachableError("could not start SSH transport") from error
        stream_complete = False
        try:
            assert process.stdin is not None and process.stdout is not None
            try:
                write_before_deadline(
                    process.stdin, encode_cache_hints(cache_hints), deadline
                )
                process.stdin.close()
            except (BrokenPipeError, OSError):
                try:
                    process.stdin.close()
                except OSError:
                    pass
                returncode = wait_remote_process(process, deadline)
                raise_for_remote_exit(returncode, stream_complete=False)
            try:
                transfer = receive_stream_to_directory(
                    process.stdout,
                    Path(incoming),
                    remote_host_id,
                    remote_spool_root,
                    deadline,
                    cached_shard=cached_shard,
                    cache_hints=cache_hints,
                )
                stream_complete = True
            except EOFError:
                returncode = wait_remote_process(process, deadline)
                raise_for_remote_exit(returncode, stream_complete=False)
            returncode = wait_remote_process(process, deadline)
            raise_for_remote_exit(returncode, stream_complete=stream_complete)
            result = merge_host_shard(Path(incoming), spool_root, remote_host_id)
            result.update(transfer)
            return result
        except BaseException:
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except (OSError, ValueError):
                    pass
            if process.poll() is None:
                terminate_remote_process(process)
            else:
                try:
                    process.wait(timeout=0)
                except (subprocess.TimeoutExpired, AttributeError):
                    terminate_remote_process(process)
            raise
        finally:
            if process.stdout is not None:
                try:
                    process.stdout.close()
                except (OSError, ValueError):
                    pass


def pull_hub_remotes(hub: dict, spool_root: Path) -> dict:
    spool_root = validate_output_root(spool_root)
    if not isinstance(hub, dict) or set(hub) != {"remotes"} or not isinstance(
        hub["remotes"], list
    ):
        raise ValueError("invalid hub config schema")
    statuses = {}
    for remote in hub["remotes"]:
        if not isinstance(remote, dict) or "host_id" not in remote:
            raise ValueError("invalid remote config schema")
        remote_host_id = validate_host_id(remote["host_id"])
        if remote.get("source_spool_root"):
            if set(remote) != {"host_id", "source_spool_root"}:
                statuses[remote_host_id] = {
                    "status": "invalid_remote",
                    "files_copied": 0,
                }
                continue
            if (
                not isinstance(remote["source_spool_root"], str)
                or not Path(remote["source_spool_root"]).is_absolute()
            ):
                statuses[remote_host_id] = {
                    "status": "invalid_remote",
                    "files_copied": 0,
                }
                continue
            try:
                source_spool = assert_no_symlink_components(
                    Path(remote["source_spool_root"])
                )
                result = merge_host_shard(
                    source_spool / "hosts" / remote_host_id,
                    spool_root,
                    remote_host_id,
                )
            except LegacyArchiveSchemaError:
                statuses[remote_host_id] = {
                    "status": "legacy_schema",
                    "files_copied": 0,
                }
                continue
            except (FileNotFoundError, ValueError):
                statuses[remote_host_id] = {
                    "status": "local_integrity_rejection",
                    "files_copied": 0,
                }
                continue
            statuses[remote_host_id] = {
                "status": "pulled" if result["status"] == "published" else result["status"],
                "files_copied": result["files_copied"],
                "files_verified": result.get("files_verified", 0),
            }
            continue

        if set(remote) - {"timeout_seconds"} != {
            "host_id",
            "ssh_host",
            "remote_spool_root",
            "remote_pipeline_path",
        }:
            statuses[remote_host_id] = {"status": "invalid_remote", "files_copied": 0}
            continue
        ssh_host = remote.get("ssh_host")
        if (
            not isinstance(ssh_host, str)
            or not SSH_HOST_RE.fullmatch(ssh_host)
        ):
            statuses[remote_host_id] = {"status": "invalid_remote", "files_copied": 0}
            continue
        try:
            remote_spool_root = validate_remote_absolute_path_token(
                remote.get("remote_spool_root"), label="remote_spool_root"
            )
            remote_pipeline_path = validate_remote_absolute_path_token(
                remote.get("remote_pipeline_path"), label="remote_pipeline_path"
            )
        except ValueError:
            statuses[remote_host_id] = {"status": "invalid_remote", "files_copied": 0}
            continue
        timeout_seconds = remote.get("timeout_seconds", 300)
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 86400:
            statuses[remote_host_id] = {
                "status": "invalid_remote",
                "files_copied": 0,
            }
            continue
        cached_shard = spool_root / "hosts" / remote_host_id
        validated_cache: Path | None = None
        cache_hints: set[str] = set()
        if cached_shard.exists() and not (
            cached_shard / "publish-manifest.json"
        ).exists():
            # A cancelled first import has no authorization pointer. Treat it as
            # an untrusted partial cache; the transactional merge will replace it.
            validated_cache = None
        elif cached_shard.exists():
            try:
                cached_files = validated_shard_files(
                    cached_shard,
                    remote_host_id,
                    require_healthy_receipt=True,
                )
            except LegacyArchiveSchemaError:
                statuses[remote_host_id] = {
                    "status": "legacy_schema",
                    "files_copied": 0,
                }
                continue
            except (FileNotFoundError, ValueError):
                statuses[remote_host_id] = {
                    "status": "local_integrity_rejection",
                    "files_copied": 0,
                }
                continue
            validated_cache = assert_no_symlink_components(cached_shard)
            cache_hints = {
                path.stem
                for path in cached_files
                if path.parent.name == "objects"
            }
        try:
            result = pull_remote_stream(
                remote,
                spool_root,
                remote_host_id,
                validated_cache,
                cache_hints,
            )
            statuses[remote_host_id] = {
                "status": "pulled",
                "files_copied": result["files_copied"],
                "files_verified": result["files_verified"],
            }
        except PendingManifestError:
            statuses[remote_host_id] = {"status": "pending_manifest", "files_copied": 0}
        except LegacyArchiveSchemaError:
            statuses[remote_host_id] = {"status": "legacy_schema", "files_copied": 0}
        except RemoteTimeoutError:
            statuses[remote_host_id] = {"status": "timeout", "files_copied": 0}
        except RemoteUnreachableError:
            statuses[remote_host_id] = {"status": "unreachable", "files_copied": 0}
        except RemoteIntegrityError:
            statuses[remote_host_id] = {
                "status": "remote_integrity_rejection",
                "files_copied": 0,
            }
        except (LocalStreamIntegrityError, FileNotFoundError, ValueError):
            statuses[remote_host_id] = {
                "status": "local_integrity_rejection",
                "files_copied": 0,
            }
    return {"remotes": statuses}


@contextlib.contextmanager
def archive_run_lock(spool_root: Path):
    secure_mkdir(spool_root)
    lock_path = spool_root / ".run.lock"
    assert_no_symlink_components(lock_path, include_leaf=False)
    descriptor = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def collect(args: argparse.Namespace) -> int:
    archive_root = validate_output_root(Path(args.archive_root))
    host_id = validate_host_id(args.host_id)
    with archive_run_lock(archive_root):
        harnesses = collect_sources(
            archive_root,
            host_id,
            claude_roots=path_list([args.claude_root]),
            codex_roots=path_list([args.codex_root]),
            openclaw_roots=path_list([args.openclaw_root]),
            hermes_exports=path_list([args.hermes_export]),
        )

    receipt = {
        "schema_version": 1,
        "extractor_sha256": extractor_sha256(),
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "host_id": host_id,
        "harnesses": harnesses,
    }
    print(json.dumps(receipt, sort_keys=True))
    return 1 if any(not item.get("publishable", True) for item in harnesses.values()) else 0


def run_config(args: argparse.Namespace) -> int:
    config_path = assert_no_symlink_components(Path(args.config))
    config = read_json_nofollow(config_path)
    if config.get("schema_version") != 1:
        raise ValueError("unsupported config schema_version")
    config_digest = hashlib.sha256(canonical_json(config)).hexdigest()
    host_id = validate_host_id(config["host_id"])
    if "allow_non_google_drive" in config:
        raise ValueError("allow_non_google_drive is test-only and forbidden in configs")
    spool_root = validate_output_root(Path(config["spool_root"]))
    secure_mkdir(spool_root)
    sources = config.get("sources", {})
    inventory_harnesses = config.get(
        "inventory_harnesses", config.get("expected_harnesses", [])
    )
    if (
        not isinstance(inventory_harnesses, list)
        or len(inventory_harnesses) != len(set(inventory_harnesses))
        or any(
            not isinstance(harness, str) or harness not in APPROVED_HARNESSES
            for harness in inventory_harnesses
        )
    ):
        raise ValueError("inventory_harnesses must be unique approved harness names")
    unapproved_source_keys = sorted(set(sources) - APPROVED_SOURCE_KEYS)
    if unapproved_source_keys:
        raise ValueError(f"unapproved source keys: {', '.join(unapproved_source_keys)}")
    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    receipt_path = spool_root / "hosts" / host_id / "receipts" / f"{run_id}.json"
    harnesses: dict = {}
    hub_status = {"remotes": {}}
    publication = {"status": "not_attempted", "files_copied": 0}
    run_errors: list[dict] = []
    collection_errors: list[dict] = []
    collection_finished = False
    configured_collectors = (
        ("claude", "claude_roots"),
        ("codex", "codex_roots"),
        ("openclaw", "openclaw_roots"),
    )
    configured_harnesses = {
        harness
        for harness, source_key in configured_collectors
        if sources.get(source_key)
    }
    if sources.get("hermes_exports") or sources.get("hermes_instances"):
        configured_harnesses.add("hermes")
    source_root = spool_root / "hosts" / host_id
    prior_manifest_payload: bytes | None = None
    prior_index_payloads: dict[Path, bytes | None] = {}
    prior_state_payloads: dict[Path, bytes | None] = {}
    prior_object_names: dict[Path, set[str]] = {}
    prior_object_references: set[Path] = set()
    transaction_snapshots_ready = False
    manifest_committed = False

    def record_failure(harness: str, error: Exception) -> None:
        harnesses[harness] = {
            "status": "failed",
            "conversations": 0,
            "new_objects": 0,
            "publishable": False,
            "error_type": type(error).__name__,
            "error": str(error),
        }
        body_free_error = {"component": harness, "error_type": type(error).__name__}
        run_errors.append(body_free_error)
        collection_errors.append(body_free_error)

    def rollback_uncommitted_collection() -> None:
        if not transaction_snapshots_ready:
            return
        with defer_sigterm_during_rollback():
            for index_path, payload in prior_index_payloads.items():
                if payload is None:
                    remove_file_if_present(index_path)
                else:
                    atomic_write_bytes(index_path, payload)
            manifest_path = source_root / "publish-manifest.json"
            if prior_manifest_payload is None:
                remove_file_if_present(manifest_path)
            else:
                atomic_write_bytes(manifest_path, prior_manifest_payload)
            for state_path, payload in prior_state_payloads.items():
                if payload is None:
                    remove_file_if_present(state_path)
                else:
                    atomic_write_bytes(state_path, payload)
            for object_root, existing_names in prior_object_names.items():
                if not object_root.is_dir():
                    continue
                for candidate in object_root.iterdir():
                    if (
                        candidate.name not in existing_names
                        and candidate not in prior_object_references
                        and OBJECT_NAME_RE.fullmatch(candidate.name)
                        and candidate.is_file()
                        and not candidate.is_symlink()
                    ):
                        remove_file_if_present(candidate)

    with archive_run_lock(spool_root):
        try:
            for harness in configured_harnesses:
                state_path = spool_root / "state" / host_id / f"{harness}.json"
                prior_state_payloads[state_path] = (
                    read_bytes_nofollow(state_path) if state_path.is_file() else None
                )
                index_path = source_root / harness / "index.json"
                prior_index_payloads[index_path] = (
                    read_bytes_nofollow(index_path) if index_path.is_file() else None
                )
                object_root = source_root / harness / "objects"
                prior_object_names[object_root] = (
                    {candidate.name for candidate in object_root.iterdir()}
                    if object_root.is_dir()
                    else set()
                )
            prior_snapshot = load_healthy_manifest_snapshot(source_root, host_id)
            if prior_snapshot is not None:
                (
                    prior_manifest_payload,
                    manifest_index_payloads,
                    _prior_index_values,
                ) = prior_snapshot
                prior_index_payloads.update(manifest_index_payloads)
                for harness, index in _prior_index_values.items():
                    for row in index.get("conversations", []):
                        prior_object_references.add(
                            source_root
                            / harness
                            / "objects"
                            / f"{row['object_sha256']}.json"
                        )
            transaction_snapshots_ready = True
            for harness, source_key in configured_collectors:
                if not sources.get(source_key):
                    continue
                try:
                    options = {source_key: path_list(sources[source_key])}
                    harnesses.update(
                        collect_sources(
                            spool_root,
                            host_id,
                            _transaction_token=_RUN_CONFIG_TRANSACTION,
                            **options,
                        )
                    )
                except Exception as error:  # keep other harnesses collectable
                    record_failure(harness, error)

            if sources.get("hermes_exports") or sources.get("hermes_instances"):
                try:
                    work_parent = spool_root / ".work"
                    secure_mkdir(work_parent)
                    with tempfile.TemporaryDirectory(
                        dir=work_parent, prefix=f"{host_id}-"
                    ) as work:
                        hermes_exports = path_list(sources.get("hermes_exports", []))
                        hermes_exports.extend(
                            export_hermes_instances(
                                sources.get("hermes_instances", []), Path(work)
                            )
                        )
                        harnesses.update(
                            collect_sources(
                                spool_root,
                                host_id,
                                hermes_exports=hermes_exports,
                                _transaction_token=_RUN_CONFIG_TRANSACTION,
                            )
                        )
                except Exception as error:
                    record_failure("hermes", error)

            for harness in inventory_harnesses:
                harnesses.setdefault(
                    harness,
                    {
                        "status": "not_present_on_host",
                        "conversations": 0,
                        "new_objects": 0,
                        "publishable": False,
                        "inventory_only": True,
                    },
                )

            for harness, result in harnesses.items():
                if result.get("status") in {"partial", "source_missing"}:
                    body_free_error = {
                        "component": harness,
                        "error_type": "IncompleteExtraction",
                    }
                    run_errors.append(body_free_error)
                    collection_errors.append(body_free_error)

            try:
                hub_status = pull_hub_remotes(
                    config.get("hub", {"remotes": []}), spool_root
                )
            except Exception as error:
                hub_status = {
                    "remotes": {},
                    "status": "failed",
                    "error_type": type(error).__name__,
                }
                run_errors.append({"component": "hub", "error_type": type(error).__name__})

            drive_root, drive_root_status = configured_drive_root(
                config.get("drive_root")
            )
            local_publishable = all(
                result.get("publishable", True)
                for result in harnesses.values()
                if result.get("status") != "not_present_on_host"
            ) and not any(result.get("status") == "failed" for result in harnesses.values())
            if drive_root is not None:
                publication = (
                    {"status": "pending_manifest", "files_copied": 0}
                    if local_publishable
                    else {
                        "status": "blocked_incomplete_collection",
                        "files_copied": 0,
                    }
                )
                cached_host_ids = {
                    validate_host_id(remote["host_id"])
                    for remote in config.get("hub", {}).get("remotes", [])
                }
                hosts_root = spool_root / "hosts"
                if hosts_root.is_dir():
                    for candidate in hosts_root.iterdir():
                        if candidate.is_dir() and candidate.name != host_id:
                            cached_host_ids.add(validate_host_id(candidate.name))
                for remote_host_id in sorted(cached_host_ids):
                    remote_status = hub_status["remotes"].setdefault(
                        remote_host_id,
                        {"status": "cached", "files_copied": 0},
                    )
                    remote_status["publication"] = publish_host_shard(
                        spool_root, drive_root, remote_host_id
                    )
                failing_publications = [
                    *[
                        status.get("publication", {})
                        for status in hub_status["remotes"].values()
                    ],
                ]
                if any(
                    result.get("status") not in {"published", "blocked_source_missing"}
                    for result in failing_publications
                ):
                    run_errors.append(
                        {
                            "component": "publication",
                            "error_type": "PublicationBlocked",
                        }
                    )
            else:
                publication = {"status": drive_root_status, "files_copied": 0}
            collection_finished = True
        except Exception as error:
            body_free_error = {"component": "run", "error_type": type(error).__name__}
            run_errors.append(body_free_error)
            collection_errors.append(body_free_error)
            publication = {"status": "failed", "files_copied": 0}
        finally:
            if not collection_finished:
                interrupted_error = {
                    "component": "run",
                    "error_type": "InterruptedOrIncompleteRun",
                }
                if interrupted_error not in run_errors:
                    run_errors.append(interrupted_error)
                if interrupted_error not in collection_errors:
                    collection_errors.append(interrupted_error)
                rollback_uncommitted_collection()
            collection_status = (
                "failed"
                if collection_errors
                else "completed_with_absent_harnesses"
                if any(
                    item.get("status") == "not_present_on_host"
                    for item in harnesses.values()
                )
                else "completed"
            )
            receipt = {
                "schema_version": 1,
                "extractor_sha256": extractor_sha256(),
                "config_sha256": config_digest,
                "run_id": run_id,
                "collected_at": datetime.now(timezone.utc).isoformat(),
                "host_id": host_id,
                "collection_status": collection_status,
                "status": "failed" if run_errors else collection_status,
                "harnesses": harnesses,
                "hub": hub_status,
                "publication": publication,
                "errors": run_errors,
                "receipt_path": str(receipt_path),
            }
            if collection_status in {"completed", "completed_with_absent_harnesses"}:
                try:
                    atomic_write_json(receipt_path, receipt)
                    write_publish_manifest(
                        source_root, receipt_path, receipt, config_digest
                    )
                    manifest_committed = True
                    if drive_root is not None:
                        publication = publish_host_shard(
                            spool_root, drive_root, host_id
                        )
                        if publication.get("status") != "published":
                            run_errors.append(
                                {
                                    "component": "publication",
                                    "error_type": "PublicationBlocked",
                                }
                            )
                        receipt["publication"] = publication
                        receipt["status"] = "failed" if run_errors else collection_status
                        receipt["errors"] = run_errors

                        # A successful first copy already contains the immutable
                        # collection receipt. Bind the final publication result
                        # with a second immutable receipt and advance the manifest.
                        publication_suffix = (
                            "published"
                            if publication.get("status") == "published"
                            else "publication-blocked"
                        )
                        receipt_path = (
                            spool_root
                            / "hosts"
                            / host_id
                            / "receipts"
                            / f"{run_id}-{publication_suffix}.json"
                        )
                        receipt["receipt_path"] = str(receipt_path)
                        atomic_write_json(receipt_path, receipt)
                        write_publish_manifest(
                            source_root, receipt_path, receipt, config_digest
                        )
                        if publication.get("status") == "published":
                            receipt_publication = publish_host_shard(
                                spool_root, drive_root, host_id
                            )
                            receipt["receipt_publication"] = receipt_publication
                            if receipt_publication.get("status") != "published":
                                run_errors.append(
                                    {
                                        "component": "receipt_publication",
                                        "error_type": "PublicationBlocked",
                                    }
                                )
                                receipt["status"] = "failed"
                                receipt["errors"] = run_errors
                                receipt_path = (
                                    spool_root
                                    / "hosts"
                                    / host_id
                                    / "receipts"
                                    / f"{run_id}-publication-failed.json"
                                )
                                receipt["receipt_path"] = str(receipt_path)
                                atomic_write_json(receipt_path, receipt)
                                write_publish_manifest(
                                    source_root,
                                    receipt_path,
                                    receipt,
                                    config_digest,
                                )
                except BaseException as error:
                    if not manifest_committed:
                        rollback_uncommitted_collection()
                    if not isinstance(error, Exception):
                        raise
                    run_errors.append(
                        {
                            "component": "publish_manifest",
                            "error_type": type(error).__name__,
                        }
                    )
                    receipt["status"] = "failed"
                    receipt["errors"] = run_errors
                    if manifest_committed:
                        receipt_path = (
                            spool_root
                            / "hosts"
                            / host_id
                            / "receipts"
                            / f"{run_id}-publish-error.json"
                        )
                        receipt["receipt_path"] = str(receipt_path)
                    atomic_write_json(receipt_path, receipt)
            else:
                rollback_uncommitted_collection()
                atomic_write_json(receipt_path, receipt)

    print(json.dumps(receipt, sort_keys=True))
    return 1 if run_errors else 0


def install_launchd(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser().resolve()
    config = json.loads(config_path.read_text())
    host_id = validate_host_id(config["host_id"])
    label = f"com.mattrotundo.ai-chat-archive.{host_id}"
    agents_dir = (
        Path(args.launch_agents_dir).expanduser().resolve()
        if args.launch_agents_dir
        else Path.home() / "Library" / "LaunchAgents"
    )
    logs_dir = Path.home() / "Library" / "Logs" / "AIChatArchive"
    secure_mkdir(logs_dir)
    log_paths = [
        logs_dir / f"{host_id}.out.log",
        logs_dir / f"{host_id}.err.log",
    ]
    for log_path in log_paths:
        _, log_directory_fd = open_directory_fd(log_path.parent)
        try:
            log_fd = os.open(
                log_path.name,
                os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=log_directory_fd,
            )
            os.fchmod(log_fd, 0o600)
            os.close(log_fd)
        finally:
            os.close(log_directory_fd)
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
        "StartInterval": args.interval_seconds,
        "ProcessType": "Background",
        "StandardOutPath": str(log_paths[0]),
        "StandardErrorPath": str(log_paths[1]),
        "Umask": 0o077,
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
    }
    plist_path = agents_dir / f"{label}.plist"
    atomic_write_bytes(plist_path, plistlib.dumps(job, fmt=plistlib.FMT_XML))

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
    collect_command = commands.add_parser("collect")
    collect_command.add_argument("--host-id", required=True)
    collect_command.add_argument("--archive-root", required=True)
    collect_command.add_argument("--claude-root")
    collect_command.add_argument("--codex-root")
    collect_command.add_argument("--openclaw-root")
    collect_command.add_argument("--hermes-export")
    collect_command.set_defaults(func=collect)
    run_command = commands.add_parser("run")
    run_command.add_argument("--config", required=True)
    run_command.set_defaults(func=run_config)
    stream_command = commands.add_parser("stream-shard")
    stream_command.add_argument("--spool-root", required=True)
    stream_command.add_argument("--host-id", required=True)
    stream_command.add_argument("--cache-hints-stdin", action="store_true")
    stream_command.set_defaults(func=stream_shard_command)
    install_command = commands.add_parser("install-launchd")
    install_command.add_argument("--config", required=True)
    install_command.add_argument("--launch-agents-dir")
    install_command.add_argument("--interval-seconds", type=int, default=21600)
    install_command.add_argument("--no-load", action="store_true")
    install_command.set_defaults(func=install_launchd)
    return root


def main() -> int:
    def handle_termination(_signum: int, _frame: object) -> None:
        raise TerminationRequested

    signal.signal(signal.SIGTERM, handle_termination)
    args = parser().parse_args()
    try:
        return args.func(args)
    except TerminationRequested:
        return 128 + signal.SIGTERM


if __name__ == "__main__":
    raise SystemExit(main())
