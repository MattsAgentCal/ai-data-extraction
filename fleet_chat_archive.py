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
import shutil
import signal
import subprocess
import stat
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from extract_claude_code import extract_claude_session, find_all_claude_sessions
from extract_codex import extract_codex_session, find_all_codex_sessions
from extract_hermes import extract_hermes_export, iter_hermes_export
from extract_openclaw import extract_openclaw_session, find_all_openclaw_sessions


class TerminationRequested(BaseException):
    """Cancellation signal that operational exception handlers must not swallow."""


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
APPROVED_HARNESSES = {"claude", "codex", "openclaw", "hermes"}
HARNESS_SOURCES = {
    "claude": {"claude-code"},
    "codex": {"codex"},
    "openclaw": {"openclaw"},
    "hermes": {"hermes"},
}
EXTRACTOR_FILES = (
    "fleet_chat_archive.py",
    "extract_claude_code.py",
    "extract_codex.py",
    "extract_openclaw.py",
    "extract_hermes.py",
)
MAX_SOURCE_BYTES = 1280 * 1024 * 1024
MAX_OBJECT_BYTES = 1280 * 1024 * 1024
CODEX_PATH_PROVENANCE_FIELDS = frozenset({"session_file"})


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
                        archived = validate_object_file(candidate_path)
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
) -> dict:
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


def validate_object_file(path: Path) -> dict:
    assert_no_symlink_components(path)
    if not OBJECT_NAME_RE.fullmatch(path.name):
        raise ValueError(f"invalid archive object filename: {path.name}")
    value = read_json_nofollow(
        path,
        max_bytes=MAX_OBJECT_BYTES,
        size_label="object",
    )
    expected = path.stem
    actual = hashlib.sha256(canonical_json(value)).hexdigest()
    if actual != expected:
        raise ValueError(f"archive object hash mismatch: {path.name}")
    if not isinstance(value, dict):
        raise ValueError(f"archive object must be a JSON object: {path.name}")
    residuals = residual_secret_paths(value)
    if residuals:
        raise ValueError(
            "recognized credential in archive object at " + ", ".join(residuals[:5])
        )
    return value


def validate_index_file(
    index_path: Path,
    source_root: Path,
    host_id: str,
    harness: str,
    *,
    require_exact_object_set: bool = True,
) -> dict:
    index = read_json_nofollow(index_path)
    if (
        index.get("schema_version") != 1
        or index.get("host_id") != host_id
        or index.get("harness") != harness
        or not isinstance(index.get("conversations"), list)
    ):
        raise ValueError(f"invalid archive index identity: {index_path.name}")
    referenced: set[str] = set()
    for row in index["conversations"]:
        if not isinstance(row, dict) or not OBJECT_NAME_RE.fullmatch(
            f"{row.get('object_sha256', '')}.json"
        ):
            raise ValueError(f"invalid archive index row: {index_path.name}")
        if row.get("source_sha256") is not None and not re.fullmatch(
            r"[0-9a-f]{64}", str(row["source_sha256"])
        ):
            raise ValueError(f"invalid archive source identity: {index_path.name}")
        if (
            harness == "codex"
            and row.get("source_sha256") is not None
            and not isinstance(row.get("installation"), str)
        ):
            raise ValueError(f"invalid Codex installation identity: {index_path.name}")
        digest = row["object_sha256"]
        if digest in referenced:
            raise ValueError(f"duplicate archive index row: {digest}")
        if row.get("source") not in HARNESS_SOURCES[harness]:
            raise ValueError(f"unauthorized source in {harness} index")
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
        if path.is_dir() and path.name in APPROVED_HARNESSES
    )
    manifest_harnesses = {}
    for harness in present_harnesses:
        index_path = source_root / harness / "index.json"
        index = validate_index_file(index_path, source_root, receipt["host_id"], harness)
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
    atomic_write_json(source_root / "publish-manifest.json", manifest)
    return manifest


def validate_publish_manifest(
    source_root: Path,
    host_id: str,
    present_harnesses: set[str],
    receipt_paths: list[Path],
) -> dict:
    manifest_path = source_root / "publish-manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError(f"host shard has no publish manifest: {host_id}")
    manifest = read_json_nofollow(manifest_path)
    harnesses = manifest.get("harnesses")
    receipt_binding = manifest.get("receipt")
    digest_fields = (
        manifest.get("extractor_sha256"),
        manifest.get("config_sha256"),
        receipt_binding.get("sha256") if isinstance(receipt_binding, dict) else None,
    )
    if (
        manifest.get("schema_version") != 1
        or manifest.get("host_id") != host_id
        or not isinstance(manifest.get("run_id"), str)
        or not isinstance(manifest.get("generated_at"), str)
        or not all(re.fullmatch(r"[0-9a-f]{64}", str(value)) for value in digest_fields)
        or not isinstance(harnesses, dict)
        or not set(harnesses).issubset(present_harnesses)
        or not present_harnesses.issubset(APPROVED_HARNESSES)
        or not isinstance(receipt_binding, dict)
    ):
        raise ValueError(f"invalid publish manifest identity: {host_id}")

    receipt_relative = Path(str(receipt_binding.get("path", "")))
    if (
        len(receipt_relative.parts) != 2
        or receipt_relative.parts[0] != "receipts"
        or not receipt_relative.name.endswith(".json")
    ):
        raise ValueError(f"invalid manifest receipt path: {host_id}")
    receipt_path = source_root / receipt_relative
    if receipt_path not in receipt_paths:
        raise ValueError(f"manifest receipt is missing: {host_id}")
    receipt_payload = read_bytes_nofollow(receipt_path)
    if hashlib.sha256(receipt_payload).hexdigest() != receipt_binding["sha256"]:
        raise ValueError(f"manifest receipt hash mismatch: {host_id}")
    receipt = json.loads(receipt_payload)
    receipt_harnesses = receipt.get("harnesses")
    collection_status = receipt.get("collection_status", receipt.get("status"))
    if (
        receipt.get("schema_version") != 1
        or receipt.get("host_id") != host_id
        or receipt.get("run_id") != manifest["run_id"]
        or receipt.get("extractor_sha256") != manifest["extractor_sha256"]
        or receipt.get("config_sha256") != manifest["config_sha256"]
        or collection_status not in {"completed", "completed_with_absent_harnesses"}
        or not isinstance(receipt_harnesses, dict)
        or not set(receipt_harnesses).issubset(APPROVED_HARNESSES)
        or not present_harnesses.issubset(receipt_harnesses)
    ):
        raise ValueError(f"manifest-bound receipt is not healthy: {host_id}")
    for harness in present_harnesses:
        result = receipt_harnesses[harness]
        if not isinstance(result, dict) or result.get("status") in {
            "failed",
            "partial",
            "source_missing",
            "not_present_on_host",
        }:
            raise ValueError(f"manifest receipt has incomplete {harness} extraction")
        binding = harnesses.get(harness)
        object_digests = binding.get("object_sha256") if isinstance(binding, dict) else None
        index_path = source_root / harness / "index.json"
        index = validate_index_file(
            index_path,
            source_root,
            host_id,
            harness,
            require_exact_object_set=False,
        )
        actual_digests = sorted(row["object_sha256"] for row in index["conversations"])
        if (
            not isinstance(binding, dict)
            or not re.fullmatch(r"[0-9a-f]{64}", str(binding.get("index_sha256", "")))
            or not isinstance(object_digests, list)
            or any(not re.fullmatch(r"[0-9a-f]{64}", str(item)) for item in object_digests)
            or len(object_digests) != len(set(object_digests))
            or object_digests != actual_digests
            or binding["index_sha256"] != file_sha256(index_path)
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
) -> dict:
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
        "status": "published",
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
    except ValueError as error:
        return {
            "status": "blocked_integrity_failure",
            "files_copied": 0,
            "error": str(error),
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
    except ValueError as error:
        return {
            "status": "blocked_integrity_failure",
            "files_copied": 0,
            "error": str(error),
        }


def pull_hub_remotes(hub: dict, spool_root: Path) -> dict:
    spool_root = validate_output_root(spool_root)
    statuses = {}
    for remote in hub.get("remotes", []):
        remote_host_id = validate_host_id(remote["host_id"])
        if remote.get("source_spool_root"):
            source_spool = assert_no_symlink_components(Path(remote["source_spool_root"]))
            try:
                result = merge_host_shard(
                    source_spool / "hosts" / remote_host_id,
                    spool_root,
                    remote_host_id,
                )
            except (FileNotFoundError, ValueError) as error:
                statuses[remote_host_id] = {
                    "status": "blocked_integrity_failure",
                    "files_copied": 0,
                    "error": str(error),
                }
                continue
            statuses[remote_host_id] = {
                "status": "pulled" if result["status"] == "published" else result["status"],
                "files_copied": result["files_copied"],
                "files_verified": result.get("files_verified", 0),
            }
            continue

        ssh_host = remote.get("ssh_host")
        remote_spool_root = remote.get("remote_spool_root")
        if (
            not isinstance(ssh_host, str)
            or not SSH_HOST_RE.fullmatch(ssh_host)
            or not isinstance(remote_spool_root, str)
            or not REMOTE_SPOOL_ROOT_RE.fullmatch(remote_spool_root)
            or any(part in {".", ".."} for part in remote_spool_root.split("/"))
        ):
            statuses[remote_host_id] = {"status": "invalid_remote", "files_copied": 0}
            continue
        ssh_options = [
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=8",
            "-o",
            "ControlMaster=no",
            "-o",
            "ControlPath=none",
        ]
        manifest_path = (
            f"{remote_spool_root.rstrip('/')}/hosts/{remote_host_id}/"
            "publish-manifest.json"
        )
        try:
            manifest_probe = subprocess.run(
                ["ssh", *ssh_options, ssh_host, "test", "-f", manifest_path],
                check=False,
                capture_output=True,
                text=True,
                timeout=min(int(remote.get("timeout_seconds", 300)), 30),
            )
        except (OSError, subprocess.TimeoutExpired):
            statuses[remote_host_id] = {"status": "unreachable", "files_copied": 0}
            continue
        if manifest_probe.returncode != 0:
            statuses[remote_host_id] = {
                "status": (
                    "pending_manifest"
                    if manifest_probe.returncode == 1
                    else "unreachable"
                ),
                "files_copied": 0,
            }
            continue
        source = (
            f"{ssh_host}:{remote_spool_root.rstrip('/')}/hosts/{remote_host_id}/"
        )
        incoming_root = spool_root / ".incoming"
        secure_mkdir(incoming_root)
        try:
            with tempfile.TemporaryDirectory(
                dir=incoming_root, prefix=f"{remote_host_id}-"
            ) as incoming:
                subprocess.run(
                    [
                        "rsync",
                        "-rt",
                        "--no-links",
                        "--safe-links",
                        "--exclude=.*",
                        "--exclude=*.tmp",
                        "--exclude=*.partial",
                        "-e",
                        "ssh -o BatchMode=yes -o ConnectTimeout=8 -o ControlMaster=no -o ControlPath=none",
                        source,
                        str(Path(incoming)) + "/",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=int(remote.get("timeout_seconds", 300)),
                )
                result = merge_host_shard(
                    Path(incoming), spool_root, remote_host_id
                )
            statuses[remote_host_id] = {
                "status": "pulled",
                "files_copied": result["files_copied"],
                "files_verified": result["files_verified"],
            }
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            statuses[remote_host_id] = {"status": "unreachable", "files_copied": 0}
        except (FileNotFoundError, ValueError) as error:
            statuses[remote_host_id] = {
                "status": "blocked_integrity_failure",
                "files_copied": 0,
                "error": str(error),
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
                    harnesses.update(collect_sources(spool_root, host_id, **options))
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
                            )
                        )
                except Exception as error:
                    record_failure("hermes", error)

            inventory_harnesses = config.get(
                "inventory_harnesses", config.get("expected_harnesses", [])
            )
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
                hub_status = pull_hub_remotes(config.get("hub", {}), spool_root)
            except Exception as error:
                hub_status = {
                    "remotes": {},
                    "status": "failed",
                    "error_type": type(error).__name__,
                }
                run_errors.append({"component": "hub", "error_type": type(error).__name__})

            drive_root_value = config.get("drive_root")
            local_publishable = all(
                result.get("publishable", True)
                for result in harnesses.values()
                if result.get("status") != "not_present_on_host"
            ) and not any(result.get("status") == "failed" for result in harnesses.values())
            if drive_root_value:
                drive_root = assert_no_symlink_components(Path(drive_root_value))
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
                publication = {"status": "blocked_no_drive_root", "files_copied": 0}
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
                    if config.get("drive_root"):
                        drive_root = assert_no_symlink_components(
                            Path(config["drive_root"])
                        )
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
