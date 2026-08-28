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
import shlex
import select
import shutil
import signal
import struct
import subprocess
import stat
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
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
PROVIDER_TOKEN_PATTERN = (
    r"\b(?:sk-[A-Za-z0-9_-]{20,}|gh[opusr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})\b"
)
JWT_PATTERN = (
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
)
AUTH_HEADER_PATTERN = r"\b(?:authorization|proxy-authorization)\s*:\s*[^\r\n]+"
COOKIE_HEADER_PATTERN = r"\b(?:cookie|set-cookie)\s*:\s*[^\r\n]+"
GENERIC_BEARER_PATTERN = r"\b(?:bearer|basic)\s+[A-Za-z0-9+/=_-]{8,}"
PROVIDER_TOKEN_RE = re.compile(
    PROVIDER_TOKEN_PATTERN
)
JWT_RE = re.compile(JWT_PATTERN)
AUTH_HEADER_RE = re.compile(AUTH_HEADER_PATTERN, re.IGNORECASE)
COOKIE_HEADER_RE = re.compile(COOKIE_HEADER_PATTERN, re.IGNORECASE)
GENERIC_BEARER_RE = re.compile(GENERIC_BEARER_PATTERN, re.IGNORECASE)
REDACTABLE_SECRET_PATTERN = "|".join(
    (
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
PIPELINE_RECEIPT_SUFFIXES = {
    "",
    "-published",
    "-publication-blocked",
    "-publication-failed",
}
RUN_ID_RE = re.compile(r"[0-9]{8}T[0-9]{6}\.[0-9]{6}Z-[0-9a-f]{8}\Z")
GENERATED_AT_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"\.[0-9]{6}\+00:00\Z"
)
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
MAX_PRIVATE_KEY_LABEL_CHARS = 64
MAX_REMOTE_TIMEOUT_SECONDS = 24 * 60 * 60
MAX_PUBLISH_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_RECEIPT_METADATA_BYTES = MAX_PUBLISH_MANIFEST_BYTES
MAX_INDEX_METADATA_BYTES = MAX_PUBLISH_MANIFEST_BYTES
MAX_METADATA_STRING_CHARS = 4096
MAX_RECEIPT_ERRORS = 1024
MAX_RECEIPT_REMOTES = 1024
INTEGRITY_ERROR_CODE = "IntegrityFailure"
HUB_ERROR_CODE = "HubFailure"
PUBLICATION_ERROR_CODE = "PublicationBlocked"
RECEIPT_ERROR_CODE_BY_COMPONENT = {
    "claude": "CollectionFailure",
    "codex": "CollectionFailure",
    "openclaw": "CollectionFailure",
    "hermes": "CollectionFailure",
    "hub": HUB_ERROR_CODE,
    "publication": PUBLICATION_ERROR_CODE,
    "receipt_publication": PUBLICATION_ERROR_CODE,
    "run": "RunFailure",
    "publish_manifest": "ManifestFailure",
}
MANIFEST_RECEIPT_ERROR_CODE_BY_COMPONENT = {
    "hub": HUB_ERROR_CODE,
    "publication": PUBLICATION_ERROR_CODE,
    "receipt_publication": PUBLICATION_ERROR_CODE,
}
# The inspected 2026-08-28 fleet high-water marks were 1,129 objects in one
# harness and 1,158 total. These leave substantial growth room without making
# a manifest-controlled transfer or allocation unbounded.
MAX_MANIFEST_OBJECTS_PER_HARNESS = 25_000
MAX_MANIFEST_OBJECTS_TOTAL = 50_000
MAX_TRANSFER_ERROR_BYTES = 4096
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
MAX_REMOTE_TIMEOUT_SECONDS = 24 * 60 * 60
REMOTE_PROCESS_REAP_SECONDS = 2
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


@dataclass(frozen=True)
class ObjectValidationProof:
    """Body-free proof that one exact object inode passed full validation."""

    digest: str
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    source: str
    session_id: str


ObjectValidationProofCache = dict[str, ObjectValidationProof]


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def is_exact_schema_version_one(value: object) -> bool:
    return type(value) is int and value == 1


def is_nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def is_bounded_metadata_string(value: object, *, allow_empty: bool = False) -> bool:
    return (
        isinstance(value, str)
        and (allow_empty or bool(value))
        and len(value) <= MAX_METADATA_STRING_CHARS
        and not any(character in value for character in ("\0", "\r", "\n"))
    )


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


def file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def read_bytes_nofollow(
    path: Path,
    *,
    max_bytes: int | None = None,
    size_label: str = "file",
) -> bytes:
    descriptor = open_regular_fd(path)
    try:
        initial_metadata = os.fstat(descriptor)
        if max_bytes is not None and initial_metadata.st_size > max_bytes:
            raise ValueError(f"{size_label} exceeds maximum of {max_bytes} bytes: {path}")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            payload = handle.read() if max_bytes is None else handle.read(max_bytes + 1)
            final_metadata = os.fstat(handle.fileno())
            if max_bytes is not None and (
                len(payload) > max_bytes or final_metadata.st_size > max_bytes
            ):
                raise ValueError(
                    f"{size_label} exceeds maximum of {max_bytes} bytes: {path}"
                )
            if (
                file_identity(initial_metadata) != file_identity(final_metadata)
                or len(payload) != final_metadata.st_size
            ):
                raise ValueError(f"{size_label} changed while reading: {path}")
            return payload
    finally:
        if descriptor >= 0:
            os.close(descriptor)


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
            if max_bytes is None:
                json_source = handle
            else:
                class BoundedJSONSource:
                    def read(self, size: int = -1) -> str:
                        ceiling = max_bytes + 1
                        return handle.read(
                            ceiling if size < 0 or size > ceiling else size
                        )

                json_source = BoundedJSONSource()
            value = json.load(json_source)
            final_metadata = os.fstat(handle.fileno())
            if (
                file_identity(initial_metadata) != file_identity(final_metadata)
                or os.lseek(handle.fileno(), 0, os.SEEK_CUR) != final_metadata.st_size
            ):
                raise ValueError(f"{size_label} changed while parsing: {path}")
            if max_bytes is not None and final_metadata.st_size > max_bytes:
                raise ValueError(
                    f"{size_label} exceeds maximum of {max_bytes} bytes: {path}"
                )
            return value
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, member in pairs:
        if key in value:
            raise ValueError("duplicate JSON object key")
        value[key] = member
    return value


def reject_json_constant(_constant: str):
    raise ValueError("non-finite JSON number")


def bounded_json_loads(payload: bytes):
    return json.loads(
        payload,
        object_pairs_hook=unique_json_object,
        parse_constant=reject_json_constant,
    )


def parse_canonical_json(payload: bytes, *, size_label: str):
    try:
        value = bounded_json_loads(payload)
        canonical_payload = canonical_json(value) + b"\n"
    except (OverflowError, TypeError, UnicodeError, ValueError) as error:
        raise ValueError(f"{size_label} is not canonical JSON") from error
    if payload != canonical_payload:
        raise ValueError(f"{size_label} is not canonical JSON")
    return value


def read_canonical_json_nofollow(
    path: Path,
    *,
    max_bytes: int,
    size_label: str,
):
    payload = read_bytes_nofollow(
        path,
        max_bytes=max_bytes,
        size_label=size_label,
    )
    return parse_canonical_json(payload, size_label=size_label)


def read_bytes_snapshot_nofollow(
    path: Path,
    *,
    max_bytes: int,
    size_label: str,
    _return_metadata: bool = False,
) -> bytes | tuple[bytes, os.stat_result]:
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
        snapshot = bytes(payload)
        return (snapshot, final) if _return_metadata else snapshot
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


def has_private_key_begin(value: str, lowered: str | None = None) -> bool:
    """Recognize a PEM private-key BEGIN header in one forward scan."""
    lowered = regex_compatible_lower(value) if lowered is None else lowered
    prefix = "-----begin "
    suffix = "-----"
    search_from = 0
    length = len(lowered)
    while True:
        begin = lowered.find(prefix, search_from)
        if begin < 0:
            return False
        label_start = begin + len(prefix)
        cursor = label_start
        while cursor < length:
            if lowered.startswith(suffix, cursor):
                # This slice and split are safe because the forward scan below
                # fails closed before a label can exceed the strict PEM bound.
                words = lowered[label_start:cursor].split(" ")
                if (
                    len(words) >= 2
                    and words[-2:] == ["private", "key"]
                    and all(
                        word and all("a" <= character <= "z" for character in word)
                        for word in words
                    )
                ):
                    return True
                search_from = (
                    cursor
                    if lowered.startswith(prefix, cursor)
                    else cursor + len(suffix)
                )
                break
            character = lowered[cursor]
            if character != " " and not "a" <= character <= "z":
                search_from = cursor
                break
            if cursor - label_start >= MAX_PRIVATE_KEY_LABEL_CHARS:
                # An overlong letters/spaces label is begin-like but cannot be
                # safely classified as a bounded PEM label. Treat the entire
                # containing string as sensitive without scanning or slicing it.
                return True
            cursor += 1
        else:
            return False
        # The label scan stopped at the first byte that cannot belong to this
        # header. Resume there so adversarial repeated BEGIN fragments remain
        # linear instead of rescanning an ever-shrinking suffix.


def redact_text(value: str) -> tuple[str, int]:
    lowered = regex_compatible_lower(value)
    # Assignment values can be multiline YAML blocks, shell $'...' strings, or
    # escaped serialized JSON. Redact the containing string as one unit rather
    # than risk preserving a suffix the parser cannot safely delimit.
    if has_residual_assignment(value, lowered):
        return "[REDACTED]", 1
    # A valid private-key BEGIN header makes the entire containing string
    # sensitive. This intentionally fails closed for missing, mismatched, or
    # nested END blocks without searching arbitrarily far for a terminator.
    if has_private_key_begin(value, lowered):
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
                key_has_secret = (
                    has_residual_assignment(key, lowered_key)
                    or has_private_key_begin(key, lowered_key)
                    or (
                        any(
                            marker in lowered_key
                            for marker in REDACTABLE_TEXT_MARKERS
                        )
                        and bool(RESIDUAL_SECRET_RE.search(key))
                    )
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
        if (
            has_residual_assignment(value, lowered)
            or has_private_key_begin(value, lowered)
            or (
                any(marker in lowered for marker in REDACTABLE_TEXT_MARKERS)
                and RESIDUAL_SECRET_RE.search(value)
            )
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
def defer_termination_signals_during_rollback():
    previous_mask = None
    if hasattr(signal, "pthread_sigmask"):
        previous_mask = signal.pthread_sigmask(
            signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM}
        )
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
        type(manifest.get("schema_version")) is not int
        or manifest.get("schema_version") != 1
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
        type(receipt.get("schema_version")) is not int
        or receipt.get("schema_version") != 1
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
            type(index.get("schema_version")) is not int
            or index.get("schema_version") != 1
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


def archive_row_identity(row: dict) -> tuple[object, ...]:
    """Identify one current native session, scoped by Codex installation."""
    if row.get("source") == "codex":
        return (row.get("source"), row.get("installation"), row.get("session_id"))
    return (row.get("source"), row.get("session_id"))


def is_current_codex_index_row(row: object) -> bool:
    return isinstance(row, dict) and set(row) == {
        "object_sha256",
        "session_id",
        "source",
        "source_sha256",
        "installation",
    }


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
        if harness == "codex" and any(
            not is_current_codex_index_row(row) for row in prior_rows
        ):
            # A v2 Codex collection cannot inherit a base-row projection. The
            # next complete extraction regenerates the current index while the
            # old manifest remains available to the run transaction rollback.
            prior_rows = []
        if any(
            not isinstance(row, dict)
            or not isinstance(row.get("session_id"), str)
            or not row["session_id"]
            for row in prior_rows
        ):
            prior_rows = []
        for row in prior_rows:
            digest = row.get("object_sha256") if isinstance(row, dict) else None
            candidate = objects_root / f"{digest}.json"
            try:
                prior_value = read_json_nofollow(candidate)
            except (OSError, ValueError):
                # Regeneration is replacement, not a migration of arbitrary v1
                # bodies. A run transaction preserves the old manifested index
                # until the new v2 manifest commits.
                prior_rows = []
                break
            if (
                type(prior_value.get("archive_schema_version")) is not int
                or prior_value.get("archive_schema_version")
                != ARCHIVE_OBJECT_SCHEMA_VERSION
            ):
                prior_rows = []
                break
    index_by_identity = {
        archive_row_identity(row): row
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
        index_by_identity[archive_row_identity(index_row)] = index_row

    index_rows = list(index_by_identity.values())
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
    validation_proofs: ObjectValidationProofCache | None = None,
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
                    validation_proofs=validation_proofs,
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
                        installation_identity=codex_root,
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
    if not is_pipeline_run_id(manifest["run_id"]):
        raise ValueError(f"invalid publish manifest identity: {host_id}")
    if not is_canonical_utc_timestamp(manifest["generated_at"]):
        raise ValueError(f"invalid publish manifest identity: {host_id}")
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
        or not is_pipeline_receipt_path(receipt_path, manifest["run_id"])
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
        if len(digests) > MAX_MANIFEST_OBJECTS_PER_HARNESS:
            raise ValueError(f"invalid publish manifest binding for {harness}")
        total_objects += len(digests)
        if (
            total_objects > MAX_MANIFEST_OBJECTS_TOTAL
            or total_objects + len(harnesses) + 2 > MAX_STREAM_FILES
        ):
            raise ValueError("publish manifest object count exceeds fleet limit")
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
    seen_identities: set[tuple[object, ...]] = set()
    expected_row_keys = {
        "object_sha256",
        "session_id",
        "source",
        *({"source_sha256", "installation"} if harness == "codex" else set()),
    }
    for row in index["conversations"]:
        if harness == "codex" and isinstance(row, dict) and set(row) == {
            "object_sha256",
            "session_id",
            "source",
        }:
            raise LegacyArchiveSchemaError(
                "v2 Codex index requires source_sha256 and installation regeneration"
            )
        if not isinstance(row, dict) or set(row) != expected_row_keys:
            raise ValueError(f"invalid archive index row schema for {harness}")
        digest = row["object_sha256"]
        if not isinstance(digest, str) or not OBJECT_NAME_RE.fullmatch(f"{digest}.json"):
            raise ValueError("invalid archive index row: index.json")
        if digest in seen:
            raise ValueError(f"duplicate archive index row: {digest}")
        seen.add(digest)
        if (
            not isinstance(row["session_id"], str)
            or not row["session_id"]
            or "\x00" in row["session_id"]
            or len(row["session_id"].encode("utf-8")) > 4096
        ):
            raise LegacyArchiveSchemaError(
                "archive index requires non-null session_id regeneration"
            )
        if row["source"] not in HARNESS_SOURCES[harness]:
            raise ValueError(f"unauthorized source in {harness} index")
        if harness == "codex":
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
        identity = archive_row_identity(row)
        if identity in seen_identities:
            raise ValueError(f"duplicate logical archive index row for {harness}")
        seen_identities.add(identity)
    if index["conversations"] != sorted(
        index["conversations"],
        key=lambda row: (str(row["session_id"]), row["object_sha256"]),
    ):
        raise ValueError(f"non-canonical archive index row order for {harness}")
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
            "error_code",
        },
        label=label,
    )
    if record["status"] not in allowed_statuses:
        raise ValueError(f"invalid {label} status")
    require_nonnegative_int(record["files_copied"], label=f"{label} files_copied")
    for key in ("files_verified", "quarantined_unindexed_objects"):
        if key in record:
            require_nonnegative_int(record[key], label=f"{label} {key}")
    if "error_code" in record:
        if (
            record["status"] != "blocked_integrity_failure"
            or record["error_code"] != INTEGRITY_ERROR_CODE
        ):
            raise ValueError(f"invalid {label} error_code")
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
        if expected_receipt_path is not None:
            expected_suffix = (
                "hosts",
                host_id,
                "receipts",
                expected_receipt_path.name,
            )
            if tuple(Path(receipt_path).parts[-4:]) != expected_suffix:
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
            {"remotes", "status", "error_code"},
        ):
            raise ValueError("invalid receipt hub schema")
        if not isinstance(hub["remotes"], dict):
            raise ValueError("invalid receipt hub remotes")
        if set(hub) == {"remotes", "status", "error_code"}:
            if (
                hub["remotes"]
                or hub["status"] != "failed"
                or hub["error_code"] != HUB_ERROR_CODE
            ):
                raise ValueError("invalid receipt hub failure schema")
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
                required={"component", "error_code"},
                label="body-free receipt error",
            )
            if error["component"] not in allowed_components:
                raise ValueError("invalid body-free receipt error component")
            if (
                error["error_code"]
                != RECEIPT_ERROR_CODE_BY_COMPONENT[error["component"]]
            ):
                raise ValueError("invalid body-free receipt error code")
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
        or type(value.get("archive_schema_version")) is not int
        or value.get("archive_schema_version") != ARCHIVE_OBJECT_SCHEMA_VERSION
    ):
        raise LegacyArchiveSchemaError("archive object requires v2 regeneration")
    validate_archive_object(value, harness=harness)
    if harness == "codex" and not isinstance(value.get("installation"), str):
        raise ValueError("v2 Codex object is missing installation identity")
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


def object_file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


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
        raise ValueError(f"archive object hash mismatch or noncanonical JSON: {path.name}")
    residuals = residual_secret_paths(value)
    if residuals:
        raise ValueError(
            "recognized credential in archive object at " + ", ".join(residuals[:5])
        )
    require_redaction_idempotence(value)
    return value


def validated_object_provenance(
    path: Path,
    harness: str,
    row: dict,
    validation_proofs: ObjectValidationProofCache | None,
) -> tuple[str, str]:
    """Validate one indexed object, reusing only an exact run-local proof."""
    assert_no_symlink_components(path)
    if not OBJECT_NAME_RE.fullmatch(path.name):
        raise ValueError(f"invalid archive object filename: {path.name}")
    digest = path.stem
    proof = validation_proofs.get(digest) if validation_proofs is not None else None
    metadata = None
    if proof is not None and harness != "codex":
        descriptor = open_regular_fd(path)
        try:
            metadata = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        if (
            proof.digest == digest
            and object_file_identity(metadata)
            == (
                proof.device,
                proof.inode,
                proof.size,
                proof.mtime_ns,
                proof.ctime_ns,
            )
        ):
            archived_source = proof.source
            archived_session_id = proof.session_id
            fully_validated = False
        else:
            validation_proofs.pop(digest, None)
            proof = None

    if proof is None or harness == "codex":
        archived = validate_object_file(path)
        proof_payload, metadata = read_bytes_snapshot_nofollow(
            path,
            max_bytes=MAX_OBJECT_BYTES,
            size_label="object proof",
            _return_metadata=True,
        )
        if proof_payload != canonical_json(archived) + b"\n":
            raise ValueError(f"archive object changed after validation: {digest}")
        archived_source = archived.get("source")
        archived_session_id = archived.get("session_id")
        fully_validated = True
        if harness == "codex" and archived.get("installation") != row.get("installation"):
            raise ValueError(f"archive object provenance mismatch for {harness}: {digest}")

    if (
        not isinstance(archived_source, str)
        or not isinstance(archived_session_id, str)
        or archived_source not in HARNESS_SOURCES[harness]
        or archived_source != row.get("source")
        or archived_session_id != row.get("session_id")
    ):
        raise ValueError(f"archive object provenance mismatch for {harness}: {digest}")

    if fully_validated and validation_proofs is not None:
        assert metadata is not None
        validation_proofs[digest] = ObjectValidationProof(
            digest=digest,
            device=metadata.st_dev,
            inode=metadata.st_ino,
            size=metadata.st_size,
            mtime_ns=metadata.st_mtime_ns,
            ctime_ns=metadata.st_ctime_ns,
            source=archived_source,
            session_id=archived_session_id,
        )
    return archived_source, archived_session_id


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
    validation_proofs: ObjectValidationProofCache | None = None,
) -> dict:
    index = validate_index_metadata(index_path, host_id, harness)
    referenced: set[str] = set()
    for row in index["conversations"]:
        digest = row["object_sha256"]
        object_path = source_root / harness / "objects" / f"{digest}.json"
        if not object_path.is_file() or object_path.is_symlink():
            raise ValueError(f"archive index references missing object: {digest}")
        validated_object_provenance(
            object_path,
            harness,
            row,
            validation_proofs,
        )
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


def is_pipeline_run_id(value: object) -> bool:
    if not isinstance(value, str) or not RUN_ID_RE.fullmatch(value):
        return False
    try:
        datetime.strptime(value[:22], "%Y%m%dT%H%M%S.%f")
    except ValueError:
        return False
    return True


def is_canonical_utc_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not GENERATED_AT_RE.fullmatch(value):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    offset = parsed.utcoffset()
    return offset is not None and offset.total_seconds() == 0


def is_archive_receipt_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
        r"(?:\.[0-9]{6})?\+00:00",
        value,
    ):
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    offset = parsed.utcoffset()
    return offset is not None and offset.total_seconds() == 0


def is_pipeline_receipt_path(value: object, run_id: str) -> bool:
    if not isinstance(value, str):
        return False
    return value in {
        f"receipts/{run_id}{suffix}.json"
        for suffix in PIPELINE_RECEIPT_SUFFIXES
    }


def write_publish_manifest(
    source_root: Path,
    receipt_path: Path,
    receipt: dict,
    config_sha256: str,
    *,
    validation_proofs: ObjectValidationProofCache | None = None,
) -> dict:
    """Atomically bind a healthy receipt to one exact transferable snapshot."""
    run_id = receipt.get("run_id")
    if not is_pipeline_run_id(run_id):
        raise ValueError("refusing to manifest an invalid pipeline run_id")
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
            # A replacement transaction keeps the prior manifest's immutable
            # objects live until this new authorization pointer commits. Only
            # current index rows are bound into the new manifest.
            require_exact_object_set=False,
            validation_proofs=validation_proofs,
        )
        manifest_harnesses[harness] = {
            "index_sha256": file_sha256(index_path),
            "object_sha256": sorted(
                row["object_sha256"] for row in index["conversations"]
            ),
        }
    relative_receipt = receipt_path.relative_to(source_root)
    if not is_pipeline_receipt_path(relative_receipt.as_posix(), run_id):
        raise ValueError("refusing to manifest a receipt outside the pipeline path")
    manifest = {
        "schema_version": 1,
        "host_id": receipt["host_id"],
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "extractor_sha256": receipt["extractor_sha256"],
        "config_sha256": config_sha256,
        "receipt": {
            "path": relative_receipt.as_posix(),
            "sha256": file_sha256(receipt_path),
        },
        "harnesses": manifest_harnesses,
    }
    validate_manifest_value(manifest, receipt["host_id"])
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
    """Validate one canonical, bounded manifest before deriving any path."""
    manifest_path = source_root / "publish-manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError(f"host shard has no publish manifest: {host_id}")
    payload = read_bytes_snapshot_nofollow(
        manifest_path,
        max_bytes=min(MAX_PUBLISH_MANIFEST_BYTES, MAX_STREAM_METADATA_BYTES),
        size_label="publish manifest",
    )
    manifest = parse_canonical_json(payload, size_label="publish manifest")
    return validate_manifest_value(manifest, host_id)


def project_publication_receipt_metadata(value: object) -> dict:
    """Drop operational detail from a publication result before persistence."""
    result = value if isinstance(value, dict) else {}
    status = result.get("status")
    allowed_statuses = {
        "not_attempted",
        "pending_manifest",
        "blocked_no_drive_root",
        "blocked_drive_unavailable",
        "blocked_ambiguous_drive_root",
        "blocked_not_google_drive",
        "blocked_source_missing",
        "blocked_incomplete_collection",
        "blocked_integrity_failure",
        "published",
        "failed",
    }
    if status not in allowed_statuses:
        status = "failed"
    projected = {
        "status": status,
        "files_copied": (
            result.get("files_copied")
            if is_nonnegative_int(result.get("files_copied"))
            else 0
        ),
    }
    if status == "published":
        projected["files_verified"] = (
            result.get("files_verified")
            if is_nonnegative_int(result.get("files_verified"))
            else 0
        )
        projected["quarantined_unindexed_objects"] = (
            result.get("quarantined_unindexed_objects")
            if is_nonnegative_int(result.get("quarantined_unindexed_objects"))
            else 0
        )
    elif status == "blocked_integrity_failure":
        projected["error_code"] = INTEGRITY_ERROR_CODE
    return projected


def project_remote_receipt_metadata(value: object) -> dict:
    """Project one raw hub result to its fixed-code persisted form."""
    result = value if isinstance(value, dict) else {}
    status = result.get("status")
    allowed_statuses = {
        "pulled",
        "cached",
        "invalid_remote",
        "unreachable",
        "pending_manifest",
        "blocked_integrity_failure",
    }
    if status not in allowed_statuses:
        status = "invalid_remote"
    projected = {
        "status": status,
        "files_copied": (
            result.get("files_copied")
            if is_nonnegative_int(result.get("files_copied"))
            else 0
        ),
    }
    if status == "pulled":
        projected["files_verified"] = (
            result.get("files_verified")
            if is_nonnegative_int(result.get("files_verified"))
            else 0
        )
    elif status == "blocked_integrity_failure":
        projected["error_code"] = INTEGRITY_ERROR_CODE
    if "publication" in result:
        projected["publication"] = project_publication_receipt_metadata(
            result["publication"]
        )
    return projected


def project_hub_receipt_metadata(value: object) -> dict:
    """Project raw hub diagnostics without retaining free-form strings."""
    hub = value if isinstance(value, dict) else {}
    raw_remotes = hub.get("remotes")
    remotes = raw_remotes if isinstance(raw_remotes, dict) else {}
    projected_remotes = {
        remote_host_id: project_remote_receipt_metadata(remote)
        for remote_host_id, remote in remotes.items()
        if isinstance(remote_host_id, str) and HOST_ID_RE.fullmatch(remote_host_id)
    }
    projected = {"remotes": projected_remotes}
    if hub.get("status") == "failed":
        projected.update({"status": "failed", "error_code": HUB_ERROR_CODE})
    return projected


def project_receipt_errors(value: object) -> list[dict]:
    """Replace arbitrary exception class names with fixed receipt codes."""
    errors = value if isinstance(value, list) else []
    projected: list[dict] = []
    for error in errors[:MAX_RECEIPT_ERRORS]:
        component = error.get("component") if isinstance(error, dict) else None
        if component not in RECEIPT_ERROR_CODE_BY_COMPONENT:
            component = "run"
        entry = {
            "component": component,
            "error_code": RECEIPT_ERROR_CODE_BY_COMPONENT[component],
        }
        if entry not in projected:
            projected.append(entry)
    return projected


def project_harness_receipt_metadata(value: object) -> dict:
    """Project one harness result without exception strings or arbitrary fields."""
    result = value if isinstance(value, dict) else {}
    status = result.get("status")
    if status == "not_present_on_host":
        return {
            "status": "not_present_on_host",
            "conversations": 0,
            "new_objects": 0,
            "publishable": False,
            "inventory_only": True,
        }
    if status == "failed":
        return {
            "status": "failed",
            "conversations": 0,
            "new_objects": 0,
            "publishable": False,
            "error_code": "CollectionFailure",
        }
    if status not in {"collected", "no_conversations", "partial", "source_missing"}:
        return {
            "status": "failed",
            "conversations": 0,
            "new_objects": 0,
            "publishable": False,
            "error_code": "CollectionFailure",
        }
    quality = result.get("quality") if isinstance(result.get("quality"), dict) else {}
    quality_status = quality.get("status")
    if quality_status not in {"complete", "partial", "source_missing"}:
        quality_status = "partial"
    projected_quality = {
        field: quality.get(field) if is_nonnegative_int(quality.get(field)) else 0
        for field in (
            "discovered_lines",
            "parsed_lines",
            "failed_lines",
            "recognized_lines",
            "discovered_files",
            "processed_files",
            "skipped_unchanged_files",
        )
    }
    projected_quality["status"] = quality_status
    projected = {
        "status": status,
        "conversations": (
            result.get("conversations")
            if is_nonnegative_int(result.get("conversations"))
            else 0
        ),
        "new_objects": (
            result.get("new_objects")
            if is_nonnegative_int(result.get("new_objects"))
            else 0
        ),
        "redactions": (
            result.get("redactions")
            if is_nonnegative_int(result.get("redactions"))
            else 0
        ),
        "index_conversations": (
            result.get("index_conversations")
            if is_nonnegative_int(result.get("index_conversations"))
            else 0
        ),
        "publishable": result.get("publishable") is True,
        "quality": projected_quality,
    }
    if "staged_objects_discarded" in result:
        projected["staged_objects_discarded"] = (
            result.get("staged_objects_discarded")
            if is_nonnegative_int(result.get("staged_objects_discarded"))
            else 0
        )
    return projected


def project_receipt_for_persistence(receipt: dict) -> dict:
    """Return the body-free receipt projection written to durable storage."""
    projected = {
        field: receipt.get(field)
        for field in (
            "schema_version",
            "extractor_sha256",
            "config_sha256",
            "run_id",
            "collected_at",
            "host_id",
            "collection_status",
            "status",
            "receipt_path",
        )
    }
    raw_harnesses = receipt.get("harnesses")
    harnesses = raw_harnesses if isinstance(raw_harnesses, dict) else {}
    projected["harnesses"] = {
        harness: project_harness_receipt_metadata(result)
        for harness, result in harnesses.items()
        if harness in APPROVED_HARNESSES
    }
    projected["hub"] = project_hub_receipt_metadata(receipt.get("hub"))
    projected["publication"] = project_publication_receipt_metadata(
        receipt.get("publication")
    )
    projected["errors"] = project_receipt_errors(receipt.get("errors"))
    if "receipt_publication" in receipt:
        projected["receipt_publication"] = project_publication_receipt_metadata(
            receipt["receipt_publication"]
        )
    return projected


def write_persisted_receipt(path: Path, receipt: dict) -> dict:
    projected = project_receipt_for_persistence(receipt)
    atomic_write_json(path, projected)
    return projected


def validate_publication_receipt_metadata(value: object) -> None:
    if not isinstance(value, dict):
        raise ValueError("invalid manifest-bound publication metadata")
    status = value.get("status")
    base_fields = {"status", "files_copied"}
    if status == "published":
        expected_fields = base_fields | {
            "files_verified",
            "quarantined_unindexed_objects",
        }
    elif status == "blocked_integrity_failure":
        expected_fields = base_fields | {"error_code"}
    else:
        expected_fields = base_fields
    if (
        set(value) != expected_fields
        or status
        not in {
            "not_attempted",
            "pending_manifest",
            "blocked_no_drive_root",
            "blocked_drive_unavailable",
            "blocked_ambiguous_drive_root",
            "blocked_not_google_drive",
            "blocked_source_missing",
            "blocked_incomplete_collection",
            "blocked_integrity_failure",
            "published",
            "failed",
        }
        or not is_nonnegative_int(value.get("files_copied"))
        or (
            "files_verified" in value
            and not is_nonnegative_int(value["files_verified"])
        )
        or (
            "quarantined_unindexed_objects" in value
            and not is_nonnegative_int(value["quarantined_unindexed_objects"])
        )
        or (
            "error_code" in value
            and value["error_code"] != INTEGRITY_ERROR_CODE
        )
    ):
        raise ValueError("invalid manifest-bound publication metadata")


def validate_harness_receipt_metadata(harness: str, value: object) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"invalid manifest-bound {harness} receipt metadata")
    if value.get("status") == "not_present_on_host":
        if (
            set(value)
            != {
                "status",
                "conversations",
                "new_objects",
                "publishable",
                "inventory_only",
            }
            or value.get("conversations") != 0
            or value.get("new_objects") != 0
            or value.get("publishable") is not False
            or value.get("inventory_only") is not True
        ):
            raise ValueError(f"invalid manifest-bound {harness} receipt metadata")
        return

    expected_fields = {
        "status",
        "conversations",
        "new_objects",
        "redactions",
        "index_conversations",
        "publishable",
        "quality",
    }
    quality = value.get("quality")
    quality_fields = {
        "discovered_lines",
        "parsed_lines",
        "failed_lines",
        "recognized_lines",
        "discovered_files",
        "processed_files",
        "skipped_unchanged_files",
        "status",
    }
    if (
        set(value) != expected_fields
        or value.get("status") not in {"collected", "no_conversations"}
        or any(
            not is_nonnegative_int(value.get(field))
            for field in (
                "conversations",
                "new_objects",
                "redactions",
                "index_conversations",
            )
        )
        or value.get("publishable") is not True
        or not isinstance(quality, dict)
        or set(quality) != quality_fields
        or quality.get("status") != "complete"
        or any(
            not is_nonnegative_int(quality.get(field))
            for field in quality_fields - {"status"}
        )
    ):
        raise ValueError(f"invalid manifest-bound {harness} receipt metadata")


def validate_remote_receipt_metadata(value: object) -> None:
    if not isinstance(value, dict):
        raise ValueError("invalid manifest-bound remote receipt metadata")
    status = value.get("status")
    expected_fields = {"status", "files_copied"}
    if status == "pulled":
        expected_fields.add("files_verified")
    elif status == "blocked_integrity_failure":
        expected_fields.add("error_code")
    if "publication" in value:
        expected_fields.add("publication")
    if (
        set(value) != expected_fields
        or status
        not in {
            "pulled",
            "cached",
            "invalid_remote",
            "unreachable",
            "pending_manifest",
            "blocked_integrity_failure",
        }
        or not is_nonnegative_int(value.get("files_copied"))
        or (
            "files_verified" in value
            and not is_nonnegative_int(value["files_verified"])
        )
        or (
            "error_code" in value
            and value["error_code"] != INTEGRITY_ERROR_CODE
        )
    ):
        raise ValueError("invalid manifest-bound remote receipt metadata")
    if "publication" in value:
        validate_publication_receipt_metadata(value["publication"])


def validate_hub_receipt_metadata(value: object) -> None:
    if not isinstance(value, dict) or not isinstance(value.get("remotes"), dict):
        raise ValueError("invalid manifest-bound hub receipt metadata")
    if set(value) == {"remotes"}:
        pass
    elif (
        set(value) == {"remotes", "status", "error_code"}
        and value.get("status") == "failed"
        and value.get("error_code") == HUB_ERROR_CODE
    ):
        pass
    else:
        raise ValueError("invalid manifest-bound hub receipt metadata")
    remotes = value["remotes"]
    if len(remotes) > MAX_RECEIPT_REMOTES:
        raise ValueError("invalid manifest-bound hub receipt metadata")
    for remote_host_id, remote in remotes.items():
        if not isinstance(remote_host_id, str) or not HOST_ID_RE.fullmatch(remote_host_id):
            raise ValueError("invalid manifest-bound hub receipt metadata")
        validate_remote_receipt_metadata(remote)


def validate_manifest_bound_receipt_metadata(
    receipt: object,
    manifest: dict,
    host_id: str,
    receipt_relative: Path,
) -> dict:
    if not isinstance(receipt, dict):
        raise ValueError(f"manifest-bound receipt is not healthy: {host_id}")
    required_fields = {
        "schema_version",
        "extractor_sha256",
        "config_sha256",
        "run_id",
        "collected_at",
        "host_id",
        "collection_status",
        "status",
        "harnesses",
        "hub",
        "publication",
        "errors",
        "receipt_path",
    }
    allowed_fields = required_fields | {"receipt_publication"}
    receipt_harnesses = receipt.get("harnesses")
    collection_status = receipt.get("collection_status")
    errors = receipt.get("errors")
    receipt_path_value = receipt.get("receipt_path")
    if (
        not required_fields.issubset(receipt)
        or not set(receipt).issubset(allowed_fields)
        or not is_exact_schema_version_one(receipt.get("schema_version"))
        or receipt.get("host_id") != host_id
        or receipt.get("run_id") != manifest["run_id"]
        or receipt.get("extractor_sha256") != manifest["extractor_sha256"]
        or receipt.get("config_sha256") != manifest["config_sha256"]
        or not is_archive_receipt_timestamp(receipt.get("collected_at"))
        or collection_status
        not in {"completed", "completed_with_absent_harnesses"}
        or receipt.get("status") not in {collection_status, "failed"}
        or not isinstance(receipt_harnesses, dict)
        or not set(receipt_harnesses).issubset(APPROVED_HARNESSES)
        or not isinstance(errors, list)
        or len(errors) > MAX_RECEIPT_ERRORS
        or not is_bounded_metadata_string(receipt_path_value)
    ):
        raise ValueError(f"manifest-bound receipt is not healthy: {host_id}")
    receipt_path = Path(receipt_path_value)
    if (
        not receipt_path.is_absolute()
        or receipt_path.as_posix() != receipt_path_value
        or tuple(receipt_path.parts[-4:])
        != ("hosts", host_id, "receipts", receipt_relative.name)
    ):
        raise ValueError(f"manifest-bound receipt is not healthy: {host_id}")
    for error in errors:
        if (
            not isinstance(error, dict)
            or set(error) != {"component", "error_code"}
            or error.get("component") not in MANIFEST_RECEIPT_ERROR_CODE_BY_COMPONENT
            or error.get("error_code")
            != MANIFEST_RECEIPT_ERROR_CODE_BY_COMPONENT[error["component"]]
        ):
            raise ValueError(f"manifest-bound receipt is not healthy: {host_id}")
    if bool(errors) != (receipt.get("status") == "failed"):
        raise ValueError(f"manifest-bound receipt is not healthy: {host_id}")
    for harness, result in receipt_harnesses.items():
        validate_harness_receipt_metadata(harness, result)
    validate_hub_receipt_metadata(receipt.get("hub"))
    validate_publication_receipt_metadata(receipt.get("publication"))
    if "receipt_publication" in receipt:
        validate_publication_receipt_metadata(receipt["receipt_publication"])
    return receipt


def validate_publish_manifest(
    source_root: Path,
    host_id: str,
    present_harnesses: set[str],
    receipt_paths: list[Path],
    *,
    require_objects: bool = True,
    validation_proofs: ObjectValidationProofCache | None = None,
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
                validated_object_provenance(
                    object_path,
                    harness,
                    row,
                    validation_proofs,
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
    allow_unindexed_objects: bool = False,
    validation_proofs: ObjectValidationProofCache | None = None,
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
                valid_shape = (
                    len(relative.parts) == 3
                    and relative.parts[1] == "objects"
                    and OBJECT_NAME_RE.fullmatch(relative.parts[2])
                ) or (len(relative.parts) == 2 and relative.parts[1] == "index.json")
                if not valid_shape:
                    raise ValueError(f"unexpected file in {top_level} shard: {relative}")
                # A manifest authorizes mutable indexes, not every immutable
                # body left by an interrupted later activation. Object-only
                # harnesses remain unbound until a matching index is committed.
                if not require_healthy_receipt or relative.parts[1] == "index.json":
                    present_harnesses.add(top_level)
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
            source_root,
            host_id,
            present_harnesses,
            receipt_paths,
            validation_proofs=validation_proofs,
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
        # Only the live destination of an in-progress local merge may retain a
        # previous body for transaction rollback. Staged inputs remain exact;
        # a healthy manifest commit is followed by quarantine and exact proof.
        validate_index_file(
            index_path,
            source_root,
            host_id,
            harness,
            require_exact_object_set=not allow_unindexed_objects,
            validation_proofs=validation_proofs,
        )
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


def merge_index_values(source_index: dict, destination_index: dict | None) -> dict:
    """Return the installation-scoped logical replacement of two v2 indexes."""
    if destination_index is None:
        return source_index
    if (
        source_index.get("host_id") != destination_index.get("host_id")
        or source_index.get("harness") != destination_index.get("harness")
    ):
        raise ValueError("refusing to merge indexes with different identities")
    harness = source_index["harness"]
    if harness == "codex" and any(
        not is_current_codex_index_row(row)
        for row in destination_index.get("conversations", [])
    ):
        return source_index
    if any(
        not isinstance(row, dict)
        or not isinstance(row.get("session_id"), str)
        or not row["session_id"]
        for row in destination_index.get("conversations", [])
    ):
        return source_index
    validate_index_value(source_index, source_index["host_id"], harness)
    validate_index_value(destination_index, source_index["host_id"], harness)
    merged = {
        archive_row_identity(row): row
        for row in destination_index.get("conversations", [])
    }
    for row in source_index.get("conversations", []):
        merged[archive_row_identity(row)] = row
    return {
        **destination_index,
        "conversations": sorted(
            merged.values(),
            key=lambda row: (str(row.get("session_id")), row["object_sha256"]),
        ),
    }


def merge_index_file(source: Path, destination: Path) -> bool:
    source_index = read_json_nofollow(source)
    source_host_id = source_index.get("host_id")
    source_harness = source_index.get("harness")
    if not isinstance(source_host_id, str) or source_harness not in APPROVED_HARNESSES:
        raise ValueError("invalid source archive index identity")
    source_index = validate_index_value(source_index, source_host_id, source_harness)
    if not destination.exists():
        atomic_write_json(destination, source_index)
        return True
    destination_index = read_json_nofollow(destination)
    if (
        source_index.get("host_id") != destination_index.get("host_id")
        or source_index.get("harness") != destination_index.get("harness")
    ):
        raise ValueError("refusing to merge indexes with different identities")
    harness = source_index["harness"]
    legacy_destination = (
        harness == "codex"
        and any(
            not is_current_codex_index_row(row)
            for row in destination_index.get("conversations", [])
        )
    ) or any(
        not isinstance(row, dict)
        or not isinstance(row.get("session_id"), str)
        or not row["session_id"]
        for row in destination_index.get("conversations", [])
    )
    if not legacy_destination:
        validate_index_value(destination_index, source_index["host_id"], harness)
        destination_objects = destination.parent / "objects"
        for row in destination_index.get("conversations", []):
            digest = row.get("object_sha256")
            try:
                destination_value = read_json_nofollow(
                    destination_objects / f"{digest}.json"
                )
            except (FileNotFoundError, ValueError):
                legacy_destination = True
                break
            if (
                type(destination_value.get("archive_schema_version")) is not int
                or destination_value.get("archive_schema_version")
                != ARCHIVE_OBJECT_SCHEMA_VERSION
            ):
                legacy_destination = True
                break
    merged_index = (
        source_index
        if legacy_destination
        else merge_index_values(source_index, destination_index)
    )
    if canonical_json(destination_index) == canonical_json(merged_index):
        return False
    atomic_write_json(destination, merged_index)
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
            type(manifest.get("schema_version")) is not int
            or manifest.get("schema_version") != 1
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
                type(index.get("schema_version")) is not int
                or index.get("schema_version") != 1
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


def finalize_manifested_object_set(destination_parent: Path, host_id: str) -> int:
    """Quarantine stale bodies only after a healthy manifest is authoritative."""
    host_root = destination_parent / "hosts" / validate_host_id(host_id)
    moved = quarantine_unindexed_objects(destination_parent, host_id)
    manifest = validate_publish_manifest_metadata(host_root, host_id)
    for harness, binding in manifest["harnesses"].items():
        index = validate_index_metadata(
            host_root / harness / "index.json", host_id, harness
        )
        indexed = sorted(row["object_sha256"] for row in index["conversations"])
        object_root = host_root / harness / "objects"
        live = sorted(
            path.stem
            for path in object_root.iterdir()
            if path.is_file() and not path.is_symlink()
        )
        if indexed != binding["object_sha256"] or live != indexed:
            raise ValueError(f"manifest exact-object proof failed for {harness}")
    return moved


def restore_indexes_to_manifest(source_root: Path, host_id: str) -> bool:
    """Recover an old manifested snapshot after an interrupted additive merge."""
    manifest_path = source_root / "publish-manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        return False
    manifest = read_json_nofollow(manifest_path)
    if (
        type(manifest.get("schema_version")) is not int
        or manifest.get("schema_version") != 1
        or manifest.get("host_id") != host_id
        or not isinstance(manifest.get("harnesses"), dict)
    ):
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
        if (
            type(current.get("schema_version")) is not int
            or current.get("schema_version") != 1
            or not isinstance(current.get("conversations"), list)
        ):
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


def quarantine_replaced_snapshot(
    snapshot_root: Path,
    destination_parent: Path,
    host_id: str,
) -> Path:
    """Move a superseded last-good tree out of the live host namespace."""
    snapshot_root = assert_no_symlink_components(snapshot_root)
    quarantine_parent = destination_parent / "quarantine" / validate_host_id(host_id)
    secure_mkdir(quarantine_parent)
    target = quarantine_parent / (
        "replaced-snapshot-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        + f"-{uuid.uuid4().hex[:8]}"
    )
    os.replace(snapshot_root, target)
    _, directory_fd = open_directory_fd(quarantine_parent)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return target


def merge_host_shard(
    source_root: Path,
    destination_parent: Path,
    host_id: str,
    *,
    require_healthy_receipt: bool = True,
    validation_proofs: ObjectValidationProofCache | None = None,
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
    # Local collection is a manifest transaction. Unindexed immutable objects
    # remain rollback-safe until the replacement manifest commits.
    quarantined_unindexed = 0
    files = validated_shard_files(
        source_root,
        host_id,
        require_healthy_receipt=require_healthy_receipt,
        validation_proofs=validation_proofs,
    )
    source_wins = True
    stale_destination_objects = 0
    if (
        require_healthy_receipt
        and destination_root.exists()
        and (destination_root / "publish-manifest.json").is_file()
    ):
        try:
            validated_shard_files(
                destination_root,
                host_id,
                require_healthy_receipt=True,
                validation_proofs=validation_proofs,
            )
        except ValueError:
            if not restore_indexes_to_manifest(destination_root, host_id):
                raise
            validated_shard_files(
                destination_root,
                host_id,
                require_healthy_receipt=True,
                validation_proofs=validation_proofs,
            )
        source_manifest = read_json_nofollow(source_root / "publish-manifest.json")
        destination_manifest = read_json_nofollow(
            destination_root / "publish-manifest.json"
        )

        def snapshot_sets(
            root: Path, manifest: dict
        ) -> dict[str, set[tuple[object, object]]]:
            return {
                harness: {
                    archive_row_identity(row)
                    for row in read_json_nofollow(root / harness / "index.json")[
                        "conversations"
                    ]
                }
                for harness in manifest["harnesses"]
            }

        source_sets = snapshot_sets(source_root, source_manifest)
        destination_sets = snapshot_sets(destination_root, destination_manifest)
        stale_destination_objects = sum(
            len(
                set(
                    destination_manifest["harnesses"]
                    .get(harness, {})
                    .get("object_sha256", [])
                )
                - set(
                    source_manifest["harnesses"]
                    .get(harness, {})
                    .get("object_sha256", [])
                )
            )
            for harness in destination_manifest["harnesses"]
        )

        def snapshot_subset(
            left: dict[str, set[tuple[object, object]]],
            right: dict[str, set[tuple[object, object]]],
        ) -> bool:
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
                try:
                    with defer_termination_signals_during_rollback():
                        if published_candidate and destination_root.exists():
                            try:
                                shutil.rmtree(destination_root)
                            except BaseException:
                                pass
                        if (
                            backup_created
                            and backup_root.exists()
                            and not destination_root.exists()
                        ):
                            try:
                                os.replace(backup_root, destination_root)
                                validated_shard_files(
                                    destination_root,
                                    host_id,
                                    require_healthy_receipt=True,
                                )
                            except BaseException:
                                pass
                except BaseException:
                    pass
                raise
            if backup_created:
                if stale_destination_objects:
                    quarantine_replaced_snapshot(
                        backup_root, destination_parent, host_id
                    )
                    quarantined_unindexed += stale_destination_objects
                else:
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
    rollback_payloads: dict[Path, bytes | None] = {}
    attempted_payloads: dict[Path, bytes] = {}
    touched_mutable_destinations: list[Path] = []
    newly_created_paths: dict[Path, str] = {}
    newly_created_directories: set[Path] = set()

    def mutable_destination_payload(destination: Path) -> bytes | None:
        if not destination.exists() and not destination.is_symlink():
            return None
        return read_bytes_nofollow(destination)

    if source_wins:
        for source in files:
            relative = source.relative_to(source_root)
            if source.name == "index.json" or source.name == "publish-manifest.json":
                destination = destination_root / relative
                previous_payload = mutable_destination_payload(destination)
                rollback_payloads[destination] = previous_payload
                if source.name == "index.json" and not require_healthy_receipt:
                    source_index = read_json_nofollow(source)
                    destination_index = (
                        json.loads(previous_payload)
                        if previous_payload is not None
                        else None
                    )
                    attempted_payloads[destination] = (
                        canonical_json(
                            merge_index_values(source_index, destination_index)
                        )
                        + b"\n"
                    )
                else:
                    attempted_payloads[destination] = read_bytes_nofollow(source)
    try:
        for source in files:
            relative = source.relative_to(source_root)
            if not source_wins and "receipts" not in relative.parts:
                continue
            destination = destination_root / relative
            if not destination.exists() and not destination.is_symlink():
                newly_created_paths[destination] = file_sha256(source)
                directory = destination.parent
                while (
                    directory == destination_root
                    or destination_root in directory.parents
                ):
                    if directory.exists() or directory.is_symlink():
                        break
                    newly_created_directories.add(directory)
                    directory = directory.parent
            immutable = "objects" in relative.parts or "receipts" in relative.parts
            if destination in rollback_payloads:
                if (
                    mutable_destination_payload(destination)
                    != rollback_payloads[destination]
                ):
                    raise ValueError(
                        f"mutable destination changed during merge: {relative}"
                    )
                touched_mutable_destinations.append(destination)
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
                allow_unindexed_objects=not require_healthy_receipt,
                validation_proofs=validation_proofs,
            )
    except BaseException:
        try:
            with defer_termination_signals_during_rollback():
                for destination in touched_mutable_destinations:
                    try:
                        current_payload = mutable_destination_payload(destination)
                    except (OSError, ValueError):
                        # A path we wrote was replaced with unrelated or unsafe data.
                        # Do not let rollback turn that race into a destructive unlink.
                        continue
                    previous_payload = rollback_payloads[destination]
                    if current_payload == previous_payload:
                        continue
                    if current_payload != attempted_payloads[destination]:
                        continue
                    try:
                        if previous_payload is None:
                            remove_file_if_present(destination)
                        else:
                            atomic_write_bytes(destination, previous_payload)
                    except BaseException:
                        pass

                mutable_metadata_restored = True
                for destination, previous_payload in rollback_payloads.items():
                    try:
                        current_payload = mutable_destination_payload(destination)
                    except BaseException:
                        mutable_metadata_restored = False
                        continue
                    mutable_metadata_restored = (
                        mutable_metadata_restored
                        and current_payload == previous_payload
                    )

                if mutable_metadata_restored:
                    for destination, expected_digest in sorted(
                        newly_created_paths.items(),
                        key=lambda item: len(item[0].parts),
                        reverse=True,
                    ):
                        try:
                            if (
                                destination.is_file()
                                and not destination.is_symlink()
                                and file_sha256(destination) == expected_digest
                            ):
                                remove_file_if_present(destination)
                        except BaseException:
                            pass
                    for directory in sorted(
                        newly_created_directories,
                        key=lambda path: len(path.parts),
                        reverse=True,
                    ):
                        try:
                            directory.rmdir()
                        except OSError:
                            pass
        except BaseException:
            pass
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
        # Publication is a trust boundary: never reuse local-run proofs here.
        return merge_host_shard(
            source_root,
            drive_root,
            host_id,
            require_healthy_receipt=require_healthy_receipt,
            validation_proofs=None,
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
        if not isinstance(row.get("session_id"), str) or not row["session_id"]:
            raise LegacyArchiveSchemaError(
                "transport projection requires non-null session_id regeneration"
            )
        if harness == "codex":
            if not is_current_codex_index_row(row):
                raise LegacyArchiveSchemaError(
                    "v2 Codex index requires current source and installation identity"
                )
            projected = {
                "transport_schema": "codex-current-v1",
                "object_sha256": row["object_sha256"],
                "source": row["source"],
                "session_id_sha256": transport_identity_sha256(row["session_id"]),
                "source_sha256": row["source_sha256"],
                "installation_sha256": transport_identity_sha256(
                    row["installation"]
                ),
            }
        else:
            projected = {
                "transport_schema": "base-v1",
                "object_sha256": row["object_sha256"],
                "source": row["source"],
                "session_id_sha256": transport_identity_sha256(row["session_id"]),
            }
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
        type(projection["schema_version"]) is not int
        or projection["schema_version"] != 1
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
        elif schema != "base-v1" or harness == "codex":
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
) -> tuple[
    Path,
    dict,
    list[tuple[str, bytes]],
    dict[str, dict[str, dict]],
    dict[str, dict[str, dict]],
]:
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
    projections_by_harness: dict[str, dict[str, dict]] = {}
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
        projections_by_harness[harness] = {
            row["object_sha256"]: row for row in projection["conversations"]
        }
        frames.append(
            (transport_projection_path(harness), canonical_json(projection) + b"\n")
        )
    return source_root, manifest, frames, rows_by_harness, projections_by_harness


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
        (
            source_root,
            manifest,
            metadata_frames,
            rows_by_harness,
            projections_by_harness,
        ) = load_stream_metadata(spool_root, host_id)
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
            reconstructed_rows = []
            for digest in manifest["harnesses"][harness]["object_sha256"]:
                relative = f"{harness}/objects/{digest}.json"
                payload = read_bytes_snapshot_nofollow(
                    source_root / relative,
                    max_bytes=MAX_OBJECT_BYTES,
                    size_label="manifest-bound object",
                )
                value = validate_object_payload(
                    payload,
                    digest=digest,
                    harness=harness,
                    row=rows_by_harness[harness][digest],
                )
                reconstructed_rows.append(
                    reconstruct_transport_row(
                        projections_by_harness[harness][digest],
                        value,
                        harness,
                    )
                )
                file_count += 1
                total_bytes += len(payload)
                if file_count > MAX_STREAM_FILES or total_bytes > MAX_STREAM_TOTAL_BYTES:
                    raise ValueError("stream shard exceeds transfer bounds")
                if after_validate is not None:
                    after_validate(source_root / relative, relative, payload)
                del value
                del payload
            reconstructed_index_payload(
                reconstructed_rows,
                host_id,
                harness,
                manifest["harnesses"][harness]["index_sha256"],
            )

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


def reconstruct_transport_row(projected: dict, value: dict, harness: str) -> dict:
    digest = projected["object_sha256"]
    session_id = value.get("session_id")
    if transport_identity_sha256(session_id) != projected["session_id_sha256"]:
        raise ValueError("transport session identity mismatch")
    if value.get("source") != projected["source"]:
        raise ValueError("transport source identity mismatch")
    row = {
        "object_sha256": digest,
        "session_id": session_id,
        "source": projected["source"],
    }
    if harness == "codex":
        if projected["transport_schema"] != "codex-current-v1":
            raise LegacyArchiveSchemaError(
                "v2 Codex transport requires current identity projection"
            )
        installation = value.get("installation")
        if (
            not isinstance(installation, str)
            or transport_identity_sha256(installation)
            != projected["installation_sha256"]
        ):
            raise ValueError("Codex installation identity mismatch")
        row["source_sha256"] = projected["source_sha256"]
        row["installation"] = installation
    elif projected["transport_schema"] != "base-v1":
        raise ValueError(f"invalid {harness} transport projection schema")
    return row


def reconstructed_index_payload(
    rows: list[dict],
    host_id: str,
    harness: str,
    expected_sha256: str,
) -> bytes:
    rows.sort(key=lambda row: (str(row["session_id"]), row["object_sha256"]))
    index = {
        "schema_version": 1,
        "host_id": host_id,
        "harness": harness,
        "conversations": rows,
    }
    validate_index_value(index, host_id, harness)
    payload = canonical_json(index) + b"\n"
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError("reconstructed index hash mismatch")
    return payload


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
        try:
            projection = validate_transport_projection_value(
                exact_json_loads(
                    projection_payload, label=f"{harness} transport projection"
                ),
                host_id,
                harness,
            )
        except (LegacyArchiveSchemaError, ValueError) as error:
            raise LocalStreamIntegrityError(str(error)) from error
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
            try:
                reconstructed_rows.append(
                    reconstruct_transport_row(projected, value, harness)
                )
            except (LegacyArchiveSchemaError, ValueError) as error:
                raise LocalStreamIntegrityError(str(error)) from error
        try:
            index_payload = reconstructed_index_payload(
                reconstructed_rows,
                host_id,
                harness,
                manifest["harnesses"][harness]["index_sha256"],
            )
        except (LegacyArchiveSchemaError, ValueError) as error:
            raise LocalStreamIntegrityError(str(error)) from error
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


def quarantine_invalid_cached_shard(
    spool_root: Path, host_id: str, *, incomplete: bool
) -> Path:
    """Atomically remove an unusable cache from the live host namespace."""
    spool_root = assert_no_symlink_components(spool_root)
    host_id = validate_host_id(host_id)
    source = assert_no_symlink_components(spool_root / "hosts" / host_id)
    if not source.is_dir():
        raise ValueError("invalid cached shard")
    quarantine_parent = spool_root / "quarantine" / host_id
    secure_mkdir(quarantine_parent)
    label = "incomplete-cache" if incomplete else "invalid-cache"
    target = quarantine_parent / (
        f"{label}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')}"
        f"-{uuid.uuid4().hex[:8]}"
    )
    _, source_parent_fd = open_directory_fd(source.parent)
    _, target_parent_fd = open_directory_fd(target.parent)
    try:
        os.rename(
            source.name,
            target.name,
            src_dir_fd=source_parent_fd,
            dst_dir_fd=target_parent_fd,
        )
        os.fsync(source_parent_fd)
        os.fsync(target_parent_fd)
    finally:
        os.close(source_parent_fd)
        os.close(target_parent_fd)
    return target


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


def bounded_remote_process_wait(process, deadline: float) -> int:
    remaining = max(0.0, deadline - time.monotonic())
    return process.wait(timeout=remaining)


def terminate_remote_process(process, deadline: float) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (AttributeError, OSError):
        try:
            process.kill()
        except (AttributeError, OSError):
            pass
    try:
        now = time.monotonic()
        reap_deadline = min(
            deadline + REMOTE_PROCESS_REAP_SECONDS,
            now + REMOTE_PROCESS_REAP_SECONDS,
        )
        bounded_remote_process_wait(process, reap_deadline)
    except BaseException:
        pass


def cleanup_failed_remote_process(process, deadline: float) -> None:
    """Close pipes and reap a failed SSH helper without masking its exception."""
    try:
        if process.stdin is not None:
            process.stdin.close()
    except BaseException:
        pass
    try:
        try:
            live = process.poll() is None
        except BaseException:
            live = True
        if live:
            terminate_remote_process(process, deadline)
        else:
            bounded_remote_process_wait(process, deadline)
    except BaseException:
        pass
    try:
        if process.stdout is not None:
            process.stdout.close()
    except BaseException:
        pass


def wait_remote_process(process, deadline: float) -> int:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        terminate_remote_process(process, deadline)
        raise RemoteTimeoutError("remote stream timed out")
    try:
        return process.wait(timeout=remaining)
    except subprocess.TimeoutExpired as error:
        terminate_remote_process(process, deadline)
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
    if (
        type(timeout_seconds) is not int
        or not 1 <= timeout_seconds <= MAX_REMOTE_TIMEOUT_SECONDS
    ):
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
        failure_cleaned_up = False
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
            failure_cleaned_up = True
            cleanup_failed_remote_process(process, deadline)
            raise
        finally:
            if not failure_cleaned_up and process.stdout is not None:
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
                    validation_proofs=None,
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
        timeout_seconds = remote.get("timeout_seconds", 300)
        if (
            type(timeout_seconds) is not int
            or not 1 <= timeout_seconds <= MAX_REMOTE_TIMEOUT_SECONDS
        ):
            statuses[remote_host_id] = {
                "status": "invalid_remote",
                "files_copied": 0,
            }
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
        cached_shard = spool_root / "hosts" / remote_host_id
        validated_cache: Path | None = None
        cache_hints: set[str] = set()
        if cached_shard.exists() or cached_shard.is_symlink():
            try:
                cached_shard = assert_no_symlink_components(cached_shard)
                manifest_path = cached_shard / "publish-manifest.json"
                manifest_absent = (
                    not manifest_path.is_file() or manifest_path.is_symlink()
                )
                if manifest_absent:
                    quarantine_invalid_cached_shard(
                        spool_root, remote_host_id, incomplete=True
                    )
                    cached_shard = spool_root / "hosts" / remote_host_id
                    cached_files = []
                else:
                    try:
                        cached_files = validated_shard_files(
                            cached_shard,
                            remote_host_id,
                            require_healthy_receipt=True,
                            validation_proofs=None,
                        )
                    except (OSError, ValueError):
                        try:
                            restored = restore_indexes_to_manifest(
                                cached_shard, remote_host_id
                            )
                        except (OSError, ValueError):
                            restored = False
                        if not restored:
                            raise ValueError("invalid cached shard")
                        cached_files = validated_shard_files(
                            cached_shard,
                            remote_host_id,
                            require_healthy_receipt=True,
                            validation_proofs=None,
                        )
            except (OSError, ValueError):
                try:
                    quarantine_invalid_cached_shard(
                        spool_root, remote_host_id, incomplete=False
                    )
                except (OSError, ValueError):
                    statuses[remote_host_id] = {
                        "status": "local_integrity_rejection",
                        "files_copied": 0,
                    }
                    continue
                cached_shard = spool_root / "hosts" / remote_host_id
                cached_files = []
            if cached_files:
                validated_cache = cached_shard
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
    # Deliberately starts empty for each invocation and is never serialized.
    validation_proofs: ObjectValidationProofCache = {}
    with archive_run_lock(archive_root):
        harnesses = collect_sources(
            archive_root,
            host_id,
            claude_roots=path_list([args.claude_root]),
            codex_roots=path_list([args.codex_root]),
            openclaw_roots=path_list([args.openclaw_root]),
            hermes_exports=path_list([args.hermes_export]),
            validation_proofs=validation_proofs,
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
    if (
        type(config.get("schema_version")) is not int
        or config.get("schema_version") != 1
    ):
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
    # Manifest/index snapshots are inputs to validation, never cache seeds.
    validation_proofs: ObjectValidationProofCache = {}
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
        with defer_termination_signals_during_rollback():
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
                            validation_proofs=validation_proofs,
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
                                validation_proofs=validation_proofs,
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
                    persisted_receipt = write_persisted_receipt(receipt_path, receipt)
                    write_publish_manifest(
                        source_root,
                        receipt_path,
                        persisted_receipt,
                        config_digest,
                        validation_proofs=validation_proofs,
                    )
                    manifest_committed = True
                    finalize_manifested_object_set(spool_root, host_id)
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
                        persisted_receipt = write_persisted_receipt(
                            receipt_path, receipt
                        )
                        write_publish_manifest(
                            source_root,
                            receipt_path,
                            persisted_receipt,
                            config_digest,
                            validation_proofs=validation_proofs,
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
                                persisted_receipt = write_persisted_receipt(
                                    receipt_path, receipt
                                )
                                write_publish_manifest(
                                    source_root,
                                    receipt_path,
                                    persisted_receipt,
                                    config_digest,
                                    validation_proofs=validation_proofs,
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
                    write_persisted_receipt(receipt_path, receipt)
            else:
                rollback_uncommitted_collection()
                write_persisted_receipt(receipt_path, receipt)

    print(json.dumps(project_receipt_for_persistence(receipt), sort_keys=True))
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
