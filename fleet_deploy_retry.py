#!/usr/bin/env python3
"""Offline-safe, allowlisted deployment retry for the Old MacBook archive job."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import plistlib
import re
import secrets
import signal
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable


SCHEMA_VERSION = 1
RETRY_INTERVAL_SECONDS = 21_600
ARCHIVE_INTERVAL_SECONDS = 21_600
EXPECTED_HOST_ID = "old-macbook"
EXPECTED_SSH_HOST = "oldmac"
EXPECTED_REMOTE_USER = "mattrotundo"
EXPECTED_REMOTE_HOME = "/Users/mattrotundo"
EXPECTED_REMOTE_REPO = "/Users/mattrotundo/Projects/ai-data-extraction"
REMOTE_DEPLOY_ROOT_RELATIVE = ".local/share/ai-chat-archive-deploy"
REMOTE_CONFIG_RELATIVE_PATH = "configs/old-macbook.json"
RETRY_LAUNCHD_LABEL = "com.mattrotundo.ai-chat-archive.old-macbook-deploy-retry"
INVENTORY_HARNESSES = ("claude", "codex", "openclaw", "hermes")
RUNTIME_FILES = (
    "fleet_chat_archive.py",
    "archive_object_contract.py",
    "extract_claude_code.py",
    "extract_codex.py",
    "extract_openclaw.py",
    "extract_hermes.py",
)
REVIEWED_RUNTIME_SHA256 = {
    "fleet_chat_archive.py": "7ebf2fa68a1d11dacc52e3638f87fc133129ce6b12f36e124ca76850c6aa6913",
    "archive_object_contract.py": "f31e840f49fcc9f35dc8223d1d0da3a479ae6da2de7fc62fac73ef6e8521825a",
    "extract_claude_code.py": "cc09aa37295d98572fdffcf6d8ef465d340e9154c1722f85871991aa9af8512e",
    "extract_codex.py": "0e1254d39dd9978ad6e2da72f3198f6edae27dcb101b7342c6feee4f71b825c0",
    "extract_openclaw.py": "6049a3832abcddb380b9a9845e4cd1ef264467358be8ad8ce000a11da3e1b84b",
    "extract_hermes.py": "d72ede52c85696db8fd0dc16b4086c2ed3bfb8457507e364d4515463c3174c56",
}
DEPLOYED_FILES = (*RUNTIME_FILES, REMOTE_CONFIG_RELATIVE_PATH)
DEPLOY_CONFIG_KEYS = {
    "schema_version",
    "host_id",
    "ssh_host",
    "expected_remote_user",
    "expected_remote_home",
    "remote_repo_root",
    "retry_interval_seconds",
    "archive_interval_seconds",
    "runtime_files",
    "runtime_sha256",
    "remote_config_relative_path",
}
HOST_ID_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62})\Z")
SSH_HOST_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,254})\Z")
REMOTE_PATH_RE = re.compile(r"/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class DeployError(Exception):
    """A body-free deployment failure safe to report in a status receipt."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class OfflineError(DeployError):
    """The target was not reachable; the recurring job should retry normally."""

    def __init__(self):
        super().__init__("ssh_unreachable")


@dataclass(frozen=True)
class DeployConfig:
    host_id: str
    ssh_host: str
    expected_remote_user: str
    expected_remote_home: str
    remote_repo_root: str
    retry_interval_seconds: int
    archive_interval_seconds: int
    runtime_files: tuple[str, ...]
    runtime_sha256: dict[str, str]
    remote_config_relative_path: str


@dataclass(frozen=True)
class DeployFile:
    relative_path: str
    local_path: Path
    sha256: str


REMOTE_HELPER = r'''
import base64
import errno
import fcntl
import hashlib
import json
import os
import platform
import pwd
import re
import signal
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path, PurePosixPath

RELATIVE_RE = re.compile(r"(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+\Z")
DEPLOYMENT_RE = re.compile(r"[0-9a-f]{32}\Z")
STAGE_RE = re.compile(r"\.fleet-deploy-[0-9a-f]{32}\Z")
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
DEPLOY_ROOT_RELATIVE = PurePosixPath(".local/share/ai-chat-archive-deploy")
STAGES_DIRECTORY = "stages"
LOCK_FILE = "active.json"
MUTEX_FILE = "mutex.lock"
TRANSACTION_DIRECTORY = ".transaction"
BACKUPS_DIRECTORY = "backups"
JOURNAL_FILE = "journal.json"
MAX_STALE_STAGES = 32
MAX_STAGE_ENTRIES = 128
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
STALE_LOCK_SECONDS = 24 * 60 * 60
DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


class ControlledFailure(Exception):
    pass


def fail(code):
    raise ControlledFailure(code)


def emit(value):
    sys.stdout.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")


def safe_relative(value):
    if not isinstance(value, str) or not RELATIVE_RE.fullmatch(value):
        fail("unsafe_path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        fail("unsafe_path")
    return value


def lexical_absolute(value):
    if not isinstance(value, str) or not value.startswith("/"):
        fail("unsafe_path")
    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in path.parts[1:]):
        fail("unsafe_path")
    return Path(value)


def ensure_below(path, parent):
    try:
        path.relative_to(parent)
    except ValueError:
        fail("unsafe_path")


def inspect_components(path, allow_missing=True):
    path = lexical_absolute(str(path))
    current = Path("/")
    for part in path.parts[1:]:
        current = current / part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            if allow_missing:
                return False
            fail("unsafe_path")
        if stat.S_ISLNK(metadata.st_mode):
            fail("unsafe_path")
        if current != path and not stat.S_ISDIR(metadata.st_mode):
            fail("unsafe_path")
    return True


def safe_directory(path):
    if not inspect_components(path):
        return False
    metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode):
        fail("unsafe_path")
    return stat.S_ISDIR(metadata.st_mode)


def safe_file(path):
    if not inspect_components(path):
        return False
    metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode):
        fail("unsafe_path")
    return stat.S_ISREG(metadata.st_mode)


def identity(request):
    expected_user = request.get("expected_remote_user")
    expected_home = lexical_absolute(request.get("expected_remote_home"))
    account = pwd.getpwuid(os.getuid())
    if account.pw_name != expected_user:
        fail("identity_mismatch")
    if account.pw_dir != str(expected_home) or str(Path.home()) != str(expected_home):
        fail("home_mismatch")
    if platform.system() != "Darwin":
        fail("non_darwin")
    inspect_components(expected_home, allow_missing=False)
    return {
        "user": account.pw_name,
        "home": account.pw_dir,
        "platform": "Darwin",
        "python_major": sys.version_info.major,
    }


def inventory(request):
    observed_identity = identity(request)
    home = lexical_absolute(request["expected_remote_home"])

    claude_root = home / ".claude"
    codex_root = home / ".codex"
    claude_roots = [str(claude_root)] if safe_directory(claude_root) else []
    codex_roots = [str(codex_root)] if safe_directory(codex_root) else []

    openclaw_roots = []
    agents_root = home / ".openclaw" / "agents"
    if safe_directory(agents_root):
        for entry in sorted(os.scandir(agents_root), key=lambda item: item.name):
            if not RELATIVE_RE.fullmatch(entry.name) or "/" in entry.name:
                fail("unsafe_path")
            if entry.is_symlink():
                fail("unsafe_path")
            if not entry.is_dir(follow_symlinks=False):
                continue
            sessions = Path(entry.path) / "sessions"
            if safe_directory(sessions):
                openclaw_roots.append(str(sessions))

    hermes_root = home / ".hermes"
    hermes_homes = []
    if safe_directory(hermes_root):
        hermes_homes.append(str(hermes_root))
        profiles_root = hermes_root / "profiles"
        if safe_directory(profiles_root):
            for entry in sorted(os.scandir(profiles_root), key=lambda item: item.name):
                if not RELATIVE_RE.fullmatch(entry.name) or "/" in entry.name:
                    fail("unsafe_path")
                if entry.is_symlink():
                    fail("unsafe_path")
                if entry.is_dir(follow_symlinks=False):
                    hermes_homes.append(entry.path)
    hermes_binaries = [
        candidate
        for candidate in (home / ".local" / "bin" / "hermes", home / ".hermes" / "bin" / "hermes")
        if safe_file(candidate) and os.access(candidate, os.X_OK)
    ]
    if len(hermes_binaries) > 1:
        fail("ambiguous_hermes")
    hermes_instances = []
    if len(hermes_binaries) == 1 and hermes_homes:
        hermes_instances = [
            {"home": value, "binary": str(hermes_binaries[0])}
            for value in hermes_homes
        ]

    observed = {
        "claude": {"present": bool(claude_roots), "roots": claude_roots},
        "codex": {"present": bool(codex_roots), "roots": codex_roots},
        "openclaw": {"present": bool(openclaw_roots), "roots": openclaw_roots},
        "hermes": {"present": bool(hermes_instances), "instances": hermes_instances},
    }
    return {"schema_version": 1, "ok": True, "identity": observed_identity, "inventory": observed}


def checked_repo(request, create=False):
    identity(request)
    home = lexical_absolute(request["expected_remote_home"])
    repo = lexical_absolute(request["remote_repo_root"])
    ensure_below(repo, home)
    if create:
        home_descriptor = open_absolute_directory(home)
        try:
            repo_descriptor = open_directory_below(
                home_descriptor,
                repo.relative_to(home).parts,
                create=True,
            )
            os.close(repo_descriptor)
        finally:
            os.close(home_descriptor)
    else:
        inspect_components(repo, allow_missing=False)
        if not safe_directory(repo):
            fail("unsafe_path")
    return repo


def deployment_id(request):
    value = request.get("deployment_id")
    if not isinstance(value, str) or not DEPLOYMENT_RE.fullmatch(value):
        fail("schema_drift")
    return value


def open_absolute_directory(path):
    path = lexical_absolute(str(path))
    descriptor = os.open("/", DIRECTORY_FLAGS)
    try:
        for part in path.parts[1:]:
            next_descriptor = os.open(part, DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            fail("unsafe_path")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def open_directory_below(root_descriptor, parts, *, create=False, owner_only=False):
    descriptor = os.dup(root_descriptor)
    try:
        for part in parts:
            safe_relative(part)
            if "/" in part:
                fail("unsafe_path")
            try:
                next_descriptor = os.open(part, DIRECTORY_FLAGS, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, 0o700, dir_fd=descriptor)
                os.fsync(descriptor)
                next_descriptor = os.open(part, DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
            fail("unsafe_path")
        if owner_only:
            os.fchmod(descriptor, 0o700)
            if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o700:
                fail("unsafe_path")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def open_deploy_directories(request, *, create):
    identity(request)
    home = lexical_absolute(request["expected_remote_home"])
    home_descriptor = open_absolute_directory(home)
    deploy_descriptor = -1
    stages_descriptor = -1
    try:
        deploy_descriptor = open_directory_below(
            home_descriptor,
            DEPLOY_ROOT_RELATIVE.parts,
            create=create,
            owner_only=True,
        )
        stages_descriptor = open_directory_below(
            deploy_descriptor,
            (STAGES_DIRECTORY,),
            create=create,
            owner_only=True,
        )
        return (
            home / DEPLOY_ROOT_RELATIVE,
            deploy_descriptor,
            stages_descriptor,
        )
    except BaseException:
        if stages_descriptor >= 0:
            os.close(stages_descriptor)
        if deploy_descriptor >= 0:
            os.close(deploy_descriptor)
        raise
    finally:
        os.close(home_descriptor)


def close_descriptors(*descriptors):
    for descriptor in descriptors:
        if descriptor is not None and descriptor >= 0:
            os.close(descriptor)


def read_regular_at(parent_descriptor, name, *, limit):
    descriptor = os.open(
        name,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent_descriptor,
    )
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_size > limit
        ):
            fail("unsafe_path")
        chunks = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > limit:
            fail("unsafe_path")
        return payload, metadata
    finally:
        os.close(descriptor)


def write_regular_at(parent_descriptor, name, payload, mode):
    temporary = "." + name + ".fleet-tmp-" + uuid.uuid4().hex
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            mode,
            dir_fd=parent_descriptor,
        )
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
        os.fchmod(descriptor, mode)
        os.close(descriptor)
        descriptor = -1
        os.replace(
            temporary,
            name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        os.fsync(parent_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=parent_descriptor)
        except FileNotFoundError:
            pass


def read_lock(deploy_descriptor):
    try:
        payload, metadata = read_regular_at(deploy_descriptor, LOCK_FILE, limit=4096)
    except FileNotFoundError:
        return None, None
    try:
        value = json.loads(payload)
    except Exception:
        fail("unsafe_path")
    if (
        not isinstance(value, dict)
        or set(value) != {"deployment_id", "relative_paths", "state"}
        or not isinstance(value.get("relative_paths"), list)
        or value.get("state") not in {"prepared", "rollback_failed"}
    ):
        fail("unsafe_path")
    checked_id = value.get("deployment_id")
    if not isinstance(checked_id, str) or not DEPLOYMENT_RE.fullmatch(checked_id):
        fail("unsafe_path")
    checked_paths = [safe_relative(item) for item in value["relative_paths"]]
    if len(checked_paths) != len(set(checked_paths)):
        fail("unsafe_path")
    value["relative_paths"] = checked_paths
    return value, metadata


def acquire_mutex(deploy_descriptor):
    descriptor = os.open(
        MUTEX_FILE,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=deploy_descriptor,
    )
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
        os.close(descriptor)
        fail("unsafe_path")
    os.fchmod(descriptor, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    return descriptor


def release_mutex(descriptor):
    fcntl.flock(descriptor, fcntl.LOCK_UN)
    os.close(descriptor)


def write_lock_unlocked(deploy_descriptor, checked_id, relative_paths, state):
    write_regular_at(
        deploy_descriptor,
        LOCK_FILE,
        json.dumps(
            {
                "deployment_id": checked_id,
                "relative_paths": list(relative_paths),
                "state": state,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8"),
        0o600,
    )


def set_lock(deploy_descriptor, checked_id, relative_paths, state):
    mutex = acquire_mutex(deploy_descriptor)
    try:
        current, _ = read_lock(deploy_descriptor)
        if current is None or current["deployment_id"] != checked_id:
            fail("deployment_lock_mismatch")
        write_lock_unlocked(
            deploy_descriptor, checked_id, relative_paths, state
        )
    finally:
        release_mutex(mutex)


def acquire_lock(
    request,
    deploy_descriptor,
    stages_descriptor,
    checked_id,
    relative_paths,
):
    payload = json.dumps(
        {
            "deployment_id": checked_id,
            "relative_paths": list(relative_paths),
            "state": "prepared",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    mutex = acquire_mutex(deploy_descriptor)
    try:
        existing, metadata = read_lock(deploy_descriptor)
        if existing is not None:
            if metadata is None or time.time() - metadata.st_mtime <= STALE_LOCK_SECONDS:
                fail("deployment_locked")
            if existing["state"] == "rollback_failed":
                fail("rollback_failed")
            recover_stale_transaction(
                request,
                deploy_descriptor,
                stages_descriptor,
                existing,
            )
        descriptor = os.open(
            LOCK_FILE,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=deploy_descriptor,
        )
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        os.fsync(deploy_descriptor)
    finally:
        release_mutex(mutex)


def require_lock(deploy_descriptor, request):
    checked_id = deployment_id(request)
    lock, _ = read_lock(deploy_descriptor)
    if lock is None or lock["deployment_id"] != checked_id:
        fail("deployment_lock_mismatch")
    if lock["state"] == "rollback_failed":
        fail("rollback_failed")
    return lock


def release_lock(deploy_descriptor, checked_id):
    mutex = acquire_mutex(deploy_descriptor)
    try:
        lock, _ = read_lock(deploy_descriptor)
        if lock is None or lock["deployment_id"] != checked_id:
            fail("deployment_lock_mismatch")
        os.unlink(LOCK_FILE, dir_fd=deploy_descriptor)
        os.fsync(deploy_descriptor)
    finally:
        release_mutex(mutex)


def remove_tree_at(parent_descriptor, name, budget, *, depth=0):
    if depth > 4 or not RELATIVE_RE.fullmatch(name) or "/" in name:
        fail("unsafe_path")
    try:
        metadata = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    if metadata.st_uid != os.getuid() or stat.S_ISLNK(metadata.st_mode):
        fail("unsafe_path")
    budget[0] += 1
    if budget[0] > MAX_STAGE_ENTRIES:
        fail("stale_stage_limit")
    if stat.S_ISREG(metadata.st_mode):
        os.unlink(name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
        return
    if not stat.S_ISDIR(metadata.st_mode):
        fail("unsafe_path")
    descriptor = os.open(name, DIRECTORY_FLAGS, dir_fd=parent_descriptor)
    try:
        entries = os.listdir(descriptor)
        for entry in entries:
            remove_tree_at(descriptor, entry, budget, depth=depth + 1)
    finally:
        os.close(descriptor)
    os.rmdir(name, dir_fd=parent_descriptor)
    os.fsync(parent_descriptor)


def cleanup_stages(stages_descriptor):
    entries = os.listdir(stages_descriptor)
    if len(entries) > MAX_STALE_STAGES:
        fail("stale_stage_limit")
    for entry in entries:
        if not STAGE_RE.fullmatch(entry):
            fail("unsafe_path")
        stage_descriptor = os.open(entry, DIRECTORY_FLAGS, dir_fd=stages_descriptor)
        try:
            if TRANSACTION_DIRECTORY in os.listdir(stage_descriptor):
                fail("recovery_required")
        finally:
            os.close(stage_descriptor)
        remove_tree_at(stages_descriptor, entry, [0])


def stage_details(request):
    checked_id = deployment_id(request)
    deploy_root, deploy_descriptor, stages_descriptor = open_deploy_directories(
        request, create=False
    )
    stage_descriptor = -1
    try:
        lock = require_lock(deploy_descriptor, request)
        stage_name = ".fleet-deploy-" + checked_id
        expected_path = deploy_root / STAGES_DIRECTORY / stage_name
        if request.get("stage_path") != str(expected_path):
            fail("unsafe_path")
        stage_descriptor = os.open(stage_name, DIRECTORY_FLAGS, dir_fd=stages_descriptor)
        metadata = os.fstat(stage_descriptor)
        if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
            fail("unsafe_path")
        return (
            expected_path,
            deploy_descriptor,
            stages_descriptor,
            stage_descriptor,
            lock,
        )
    except BaseException:
        close_descriptors(stage_descriptor, stages_descriptor, deploy_descriptor)
        raise


def prepare(request):
    checked_repo(request, create=True)
    checked_id = deployment_id(request)
    relative_paths = request.get("relative_paths")
    if not isinstance(relative_paths, list) or len(relative_paths) != len(set(relative_paths)):
        fail("schema_drift")
    checked_paths = [safe_relative(value) for value in relative_paths]
    deploy_root, deploy_descriptor, stages_descriptor = open_deploy_directories(
        request, create=True
    )
    stage_name = ".fleet-deploy-" + checked_id
    stage_created = False
    lock_acquired = False
    try:
        acquire_lock(
            request,
            deploy_descriptor,
            stages_descriptor,
            checked_id,
            checked_paths,
        )
        lock_acquired = True
        cleanup_stages(stages_descriptor)
        os.mkdir(stage_name, 0o700, dir_fd=stages_descriptor)
        os.fsync(stages_descriptor)
        stage_created = True
        stage_descriptor = os.open(stage_name, DIRECTORY_FLAGS, dir_fd=stages_descriptor)
        try:
            os.fchmod(stage_descriptor, 0o700)
            for value in checked_paths:
                parent_parts = PurePosixPath(value).parent.parts
                parent_descriptor = open_directory_below(
                    stage_descriptor,
                    parent_parts,
                    create=True,
                    owner_only=True,
                )
                os.close(parent_descriptor)
        finally:
            os.close(stage_descriptor)
        return {
            "schema_version": 1,
            "ok": True,
            "stage_path": str(deploy_root / STAGES_DIRECTORY / stage_name),
        }
    except BaseException:
        if stage_created:
            try:
                remove_tree_at(stages_descriptor, stage_name, [0])
            except BaseException:
                pass
        if lock_acquired:
            try:
                release_lock(deploy_descriptor, checked_id)
            except BaseException:
                pass
        raise
    finally:
        close_descriptors(stages_descriptor, deploy_descriptor)


def expected_hashes(request):
    values = request.get("expected_sha256")
    if not isinstance(values, dict) or not values:
        fail("schema_drift")
    checked = {}
    for relative, digest in values.items():
        checked[safe_relative(relative)] = digest
        if not isinstance(digest, str) or not SHA_RE.fullmatch(digest):
            fail("schema_drift")
    return checked


def hash_descriptor(descriptor):
    digest = hashlib.sha256()
    os.lseek(descriptor, 0, os.SEEK_SET)
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return digest.hexdigest()


def open_relative_file(base_descriptor, relative):
    path = PurePosixPath(safe_relative(relative))
    parent_descriptor = open_directory_below(
        base_descriptor, path.parent.parts, create=False
    )
    try:
        descriptor = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
    finally:
        os.close(parent_descriptor)
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
        os.close(descriptor)
        fail("unsafe_path")
    return descriptor


def verify_paths_descriptor(base_descriptor, expected):
    actual = {}
    for relative, wanted in sorted(expected.items()):
        try:
            descriptor = open_relative_file(base_descriptor, relative)
        except FileNotFoundError:
            fail("remote_file_missing")
        try:
            digest = hash_descriptor(descriptor)
        finally:
            os.close(descriptor)
        if digest != wanted:
            fail("hash_mismatch")
        actual[relative] = digest
    return actual


def open_repo_descriptor(request, *, create):
    home = lexical_absolute(request["expected_remote_home"])
    repo = lexical_absolute(request["remote_repo_root"])
    ensure_below(repo, home)
    home_descriptor = open_absolute_directory(home)
    try:
        return open_directory_below(
            home_descriptor,
            repo.relative_to(home).parts,
            create=create,
        )
    finally:
        os.close(home_descriptor)


def open_target_parent(repo_descriptor, relative, *, create):
    path = PurePosixPath(safe_relative(relative))
    descriptor = open_directory_below(
        repo_descriptor,
        path.parent.parts,
        create=create,
        owner_only=create,
    )
    return descriptor, path.name


def valid_archive_state(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"enabled", "loaded"}
        or not isinstance(value.get("enabled"), bool)
        or not isinstance(value.get("loaded"), bool)
    ):
        fail("recovery_required")
    return dict(value)


def validate_journal(value, lock):
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schema_version",
            "deployment_id",
            "phase",
            "expected_sha256",
            "backups",
            "archive_state",
        }
        or value.get("schema_version") != 1
        or value.get("deployment_id") != lock["deployment_id"]
        or value.get("phase")
        not in {
            "prepared",
            "activating",
            "activated",
            "launched",
            "verified",
            "rolled_back",
            "rollback_failed",
        }
    ):
        fail("recovery_required")
    expected = value.get("expected_sha256")
    backups = value.get("backups")
    if (
        not isinstance(expected, dict)
        or not isinstance(backups, dict)
        or set(expected) != set(lock["relative_paths"])
        or set(backups) != set(expected)
    ):
        fail("recovery_required")
    for relative, digest in expected.items():
        safe_relative(relative)
        if not isinstance(digest, str) or not SHA_RE.fullmatch(digest):
            fail("recovery_required")
        backup = backups[relative]
        if (
            not isinstance(backup, dict)
            or set(backup) != {"present", "sha256", "mode", "parent_present"}
            or not isinstance(backup.get("present"), bool)
            or not isinstance(backup.get("parent_present"), bool)
        ):
            fail("recovery_required")
        if backup["present"]:
            if (
                not backup["parent_present"]
                or not isinstance(backup.get("sha256"), str)
                or not SHA_RE.fullmatch(backup["sha256"])
                or not isinstance(backup.get("mode"), int)
                or backup["mode"] < 0
                or backup["mode"] > 0o777
            ):
                fail("recovery_required")
        elif backup.get("sha256") is not None or backup.get("mode") is not None:
            fail("recovery_required")
    value = dict(value)
    value["expected_sha256"] = dict(expected)
    value["backups"] = {key: dict(item) for key, item in backups.items()}
    value["archive_state"] = valid_archive_state(value.get("archive_state"))
    return value


def open_transaction(stage_descriptor, *, create):
    transaction_descriptor = -1
    try:
        transaction_descriptor = open_directory_below(
            stage_descriptor,
            (TRANSACTION_DIRECTORY,),
            create=create,
            owner_only=True,
        )
        backups_descriptor = open_directory_below(
            transaction_descriptor,
            (BACKUPS_DIRECTORY,),
            create=create,
            owner_only=True,
        )
        return transaction_descriptor, backups_descriptor
    except BaseException:
        close_descriptors(transaction_descriptor)
        raise


def write_journal(transaction_descriptor, journal):
    write_regular_at(
        transaction_descriptor,
        JOURNAL_FILE,
        json.dumps(journal, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        0o600,
    )
    os.fsync(transaction_descriptor)


def read_journal(stage_descriptor, lock):
    try:
        metadata = os.stat(
            TRANSACTION_DIRECTORY,
            dir_fd=stage_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
    ):
        fail("recovery_required")
    transaction_descriptor = -1
    backups_descriptor = -1
    try:
        try:
            transaction_descriptor, backups_descriptor = open_transaction(
                stage_descriptor, create=False
            )
            payload, _ = read_regular_at(
                transaction_descriptor, JOURNAL_FILE, limit=128 * 1024
            )
        except FileNotFoundError:
            fail("recovery_required")
        try:
            value = json.loads(payload)
        except Exception:
            fail("recovery_required")
        return validate_journal(value, lock)
    finally:
        close_descriptors(backups_descriptor, transaction_descriptor)


def set_journal_phase(stage_descriptor, lock, journal, phase):
    transitions = {
        "prepared": {"activating", "rolled_back", "rollback_failed"},
        "activating": {"activated", "rolled_back", "rollback_failed"},
        "activated": {"launched", "rolled_back", "rollback_failed"},
        "launched": {"verified", "rolled_back", "rollback_failed"},
        "verified": {"rolled_back", "rollback_failed"},
        "rolled_back": {"rolled_back", "rollback_failed"},
        "rollback_failed": {"rollback_failed"},
    }
    if phase not in transitions.get(journal.get("phase"), set()):
        fail("recovery_required")
    journal = dict(journal)
    journal["phase"] = phase
    journal = validate_journal(journal, lock)
    transaction_descriptor, backups_descriptor = open_transaction(
        stage_descriptor, create=False
    )
    try:
        write_journal(transaction_descriptor, journal)
    finally:
        close_descriptors(backups_descriptor, transaction_descriptor)
    return journal


def create_transaction(
    stage_descriptor,
    repo_descriptor,
    lock,
    expected,
    archive_state,
):
    if (
        not isinstance(archive_state, tuple)
        or len(archive_state) != 2
        or not all(isinstance(value, bool) for value in archive_state)
    ):
        fail("recovery_required")
    if read_journal(stage_descriptor, lock) is not None:
        fail("recovery_required")
    transaction_descriptor, backups_descriptor = open_transaction(
        stage_descriptor, create=True
    )
    backups = {}
    try:
        for relative in sorted(expected):
            parent_descriptor = -1
            parent_present = True
            try:
                try:
                    parent_descriptor, name = open_target_parent(
                        repo_descriptor, relative, create=False
                    )
                except FileNotFoundError:
                    parent_present = False
                    name = PurePosixPath(relative).name
                backup = None
                if parent_present:
                    backup = read_target(parent_descriptor, name)
                if backup is None:
                    backups[relative] = {
                        "present": False,
                        "sha256": None,
                        "mode": None,
                        "parent_present": parent_present,
                    }
                else:
                    payload, mode = backup
                    backup_path = PurePosixPath(relative)
                    backup_parent = open_directory_below(
                        backups_descriptor,
                        backup_path.parent.parts,
                        create=True,
                        owner_only=True,
                    )
                    try:
                        write_regular_at(
                            backup_parent, backup_path.name, payload, 0o600
                        )
                    finally:
                        os.close(backup_parent)
                    backups[relative] = {
                        "present": True,
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "mode": mode,
                        "parent_present": True,
                    }
            finally:
                close_descriptors(parent_descriptor)
        journal = {
            "schema_version": 1,
            "deployment_id": lock["deployment_id"],
            "phase": "prepared",
            "expected_sha256": dict(expected),
            "backups": backups,
            "archive_state": {
                "enabled": archive_state[0],
                "loaded": archive_state[1],
            },
        }
        journal = validate_journal(journal, lock)
        write_journal(transaction_descriptor, journal)
        os.fsync(backups_descriptor)
        os.fsync(stage_descriptor)
        return journal
    finally:
        close_descriptors(backups_descriptor, transaction_descriptor)


def read_backup(backups_descriptor, relative, metadata):
    descriptor = open_relative_file(backups_descriptor, relative)
    try:
        payload = b""
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        payload = b"".join(chunks)
    finally:
        os.close(descriptor)
    if hashlib.sha256(payload).hexdigest() != metadata["sha256"]:
        fail("rollback_failed")
    return payload


def transaction_temporary_name(relative, lock):
    return (
        "."
        + PurePosixPath(relative).name
        + ".fleet-tmp-"
        + lock["deployment_id"]
    )


def remove_transaction_temporaries(repo_descriptor, lock, journal):
    for relative in sorted(journal["expected_sha256"]):
        try:
            parent_descriptor, _ = open_target_parent(
                repo_descriptor, relative, create=False
            )
        except FileNotFoundError:
            continue
        try:
            temporary = transaction_temporary_name(relative, lock)
            try:
                metadata = os.stat(
                    temporary,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
                fail("rollback_failed")
            os.unlink(temporary, dir_fd=parent_descriptor)
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)


def remove_created_parent_directories(repo_descriptor, relative):
    parts = list(PurePosixPath(relative).parent.parts)
    while parts:
        ancestor_descriptor = -1
        try:
            try:
                ancestor_descriptor = open_directory_below(
                    repo_descriptor, tuple(parts[:-1]), create=False
                )
            except FileNotFoundError:
                parts.pop()
                continue
            name = parts[-1]
            try:
                metadata = os.stat(
                    name,
                    dir_fd=ancestor_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                parts.pop()
                continue
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_uid != os.getuid()
            ):
                fail("rollback_failed")
            try:
                os.rmdir(name, dir_fd=ancestor_descriptor)
            except OSError as error:
                if error.errno in {errno.ENOTEMPTY, errno.EEXIST}:
                    return
                fail("rollback_failed")
            os.fsync(ancestor_descriptor)
        finally:
            close_descriptors(ancestor_descriptor)
        parts.pop()


def restore_runtime(
    stage_descriptor,
    repo_descriptor,
    request,
    lock,
    journal,
):
    transaction_descriptor, backups_descriptor = open_transaction(
        stage_descriptor, create=False
    )
    try:
        for relative in sorted(journal["backups"]):
            metadata = journal["backups"][relative]
            parent_descriptor = -1
            try:
                try:
                    parent_descriptor, name = open_target_parent(
                        repo_descriptor,
                        relative,
                        create=metadata["present"],
                    )
                except FileNotFoundError:
                    if metadata["present"]:
                        fail("rollback_failed")
                    continue
                if metadata["present"]:
                    payload = read_backup(backups_descriptor, relative, metadata)
                    write_regular_at(
                        parent_descriptor, name, payload, metadata["mode"]
                    )
                else:
                    try:
                        target = os.stat(
                            name,
                            dir_fd=parent_descriptor,
                            follow_symlinks=False,
                        )
                    except FileNotFoundError:
                        continue
                    if stat.S_ISLNK(target.st_mode) or not stat.S_ISREG(target.st_mode):
                        fail("rollback_failed")
                    os.unlink(name, dir_fd=parent_descriptor)
                    os.fsync(parent_descriptor)
            finally:
                close_descriptors(parent_descriptor)

        remove_transaction_temporaries(repo_descriptor, lock, journal)
        for relative, metadata in sorted(
            journal["backups"].items(),
            key=lambda item: len(PurePosixPath(item[0]).parts),
            reverse=True,
        ):
            if not metadata["parent_present"]:
                remove_created_parent_directories(repo_descriptor, relative)

        for relative, metadata in journal["backups"].items():
            try:
                parent_descriptor, name = open_target_parent(
                    repo_descriptor, relative, create=False
                )
            except FileNotFoundError:
                if metadata["present"]:
                    fail("rollback_failed")
                continue
            try:
                if metadata["present"]:
                    payload, _ = read_regular_at(
                        parent_descriptor, name, limit=MAX_UPLOAD_BYTES
                    )
                    if hashlib.sha256(payload).hexdigest() != metadata["sha256"]:
                        fail("rollback_failed")
                else:
                    try:
                        os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    fail("rollback_failed")
            finally:
                os.close(parent_descriptor)
        restore_archive_state(request, journal["archive_state"])
    finally:
        close_descriptors(backups_descriptor, transaction_descriptor)


def upload_file(request):
    (
        stage_path,
        deploy_descriptor,
        stages_descriptor,
        stage_descriptor,
        lock,
    ) = stage_details(request)
    try:
        relative = safe_relative(request.get("relative_path"))
        if relative not in lock["relative_paths"]:
            fail("schema_drift")
        wanted = request.get("expected_sha256")
        if not isinstance(wanted, str) or not SHA_RE.fullmatch(wanted):
            fail("schema_drift")
        encoded = request.get("content_b64")
        if not isinstance(encoded, str) or len(encoded) > (MAX_UPLOAD_BYTES * 4 // 3) + 8:
            fail("schema_drift")
        try:
            payload = base64.b64decode(encoded, validate=True)
        except Exception:
            fail("schema_drift")
        if len(payload) > MAX_UPLOAD_BYTES or hashlib.sha256(payload).hexdigest() != wanted:
            fail("hash_mismatch")
        path = PurePosixPath(relative)
        parent_descriptor = open_directory_below(
            stage_descriptor, path.parent.parts, create=False
        )
        try:
            write_regular_at(parent_descriptor, path.name, payload, 0o600)
        finally:
            os.close(parent_descriptor)
        actual = verify_paths_descriptor(stage_descriptor, {relative: wanted})
        return {"schema_version": 1, "ok": True, "sha256": actual}
    finally:
        close_descriptors(stage_descriptor, stages_descriptor, deploy_descriptor)


def verify_stage(request):
    (
        stage_path,
        deploy_descriptor,
        stages_descriptor,
        stage_descriptor,
        lock,
    ) = stage_details(request)
    try:
        expected = expected_hashes(request)
        if set(expected) != set(lock["relative_paths"]):
            fail("schema_drift")
        actual = verify_paths_descriptor(stage_descriptor, expected)
        return {"schema_version": 1, "ok": True, "sha256": actual}
    finally:
        close_descriptors(stage_descriptor, stages_descriptor, deploy_descriptor)


def launchctl(command):
    return subprocess.run(
        ["launchctl", *command],
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )


def archive_label(request):
    host_id = request.get("host_id")
    if host_id != "old-macbook":
        fail("schema_drift")
    return "com.mattrotundo.ai-chat-archive." + host_id


def launchd_disabled(response, label, error_code):
    if response.returncode != 0:
        fail(error_code)
    match = re.search(
        r'"?' + re.escape(label) + r'"?\s*=>\s*(true|false)(?:\s|$)',
        response.stdout or "",
    )
    if match:
        return match.group(1) == "true"
    if re.search(
        r'"?' + re.escape(label) + r'"?\s*=>', response.stdout or ""
    ):
        fail(error_code)
    return False


def launchctl_service_loaded(target, error_code):
    try:
        response = launchctl(["print", target])
    except (OSError, subprocess.TimeoutExpired):
        fail(error_code)
    label = target.rsplit("/", 1)[-1]
    uid = os.getuid()
    loaded_header = target + " = {\n"
    if response.returncode == 0:
        if (
            response.stderr != ""
            or not isinstance(response.stdout, str)
            or not response.stdout.startswith(loaded_header)
            or not response.stdout.endswith("}\n")
        ):
            fail(error_code)
        return True
    missing_stderr = (
        "Bad request.\nCould not find service \"{}\" in domain for user gui: {}\n".format(
            label, uid
        )
    )
    if (
        response.returncode == 113
        and response.stdout == ""
        and response.stderr == missing_stderr
    ):
        return False
    fail(error_code)


def snapshot_archive_state(request):
    label = archive_label(request)
    domain = "gui/{}".format(os.getuid())
    target = domain + "/" + label
    loaded = launchctl_service_loaded(target, "archive_launchd_failed")
    disabled = launchd_disabled(
        launchctl(["print-disabled", domain]),
        label,
        "archive_launchd_failed",
    )
    return {"enabled": not disabled, "loaded": loaded}


def quiesce_archive(request):
    label = archive_label(request)
    target = "gui/{}/{}".format(os.getuid(), label)
    if launchctl(["disable", target]).returncode != 0:
        fail("archive_quiesce_failed")
    launchctl(["bootout", target])
    if launchctl_service_loaded(target, "archive_quiesce_failed"):
        fail("archive_still_loaded")


def restore_archive_state(request, state):
    state = valid_archive_state(state)
    label = archive_label(request)
    target = "gui/{}/{}".format(os.getuid(), label)
    if launchctl(["disable", target]).returncode != 0:
        fail("rollback_failed")
    launchctl(["bootout", target])
    if launchctl_service_loaded(target, "rollback_failed"):
        fail("rollback_failed")
    if state["loaded"]:
        install_archive_job(request, lexical_absolute(request["remote_repo_root"]))
    command = "enable" if state["enabled"] else "disable"
    if launchctl([command, target]).returncode != 0:
        fail("rollback_failed")
    observed = snapshot_archive_state(request)
    if observed != state:
        fail("rollback_failed")


def install_archive_job(request, repo):
    script_relative = safe_relative(request.get("script_relative_path"))
    config_relative = safe_relative(request.get("config_relative_path"))
    if script_relative != "fleet_chat_archive.py" or config_relative != "configs/old-macbook.json":
        fail("schema_drift")
    if request.get("interval_seconds") != 21600:
        fail("schema_drift")
    script = repo / script_relative
    config = repo / config_relative
    if not safe_file(script) or not safe_file(config):
        fail("remote_file_missing")
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "install-launchd",
            "--config",
            str(config),
            "--interval-seconds",
            "21600",
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        fail("archive_launchd_failed")
    try:
        launchd = json.loads(completed.stdout)
    except Exception:
        fail("archive_launchd_failed")
    label = archive_label(request)
    expected_plist = (
        lexical_absolute(request["expected_remote_home"])
        / "Library"
        / "LaunchAgents"
        / (label + ".plist")
    )
    if (
        not isinstance(launchd, dict)
        or set(launchd) != {"label", "loaded", "plist_path"}
        or launchd.get("label") != label
        or launchd.get("loaded") is not True
        or launchd.get("plist_path") != str(expected_plist)
    ):
        fail("archive_launchd_failed")
    target = "gui/{}/{}".format(os.getuid(), label)
    if not launchctl_service_loaded(target, "archive_launchd_failed"):
        fail("archive_launchd_failed")
    return launchd


def read_target(parent_descriptor, name):
    try:
        payload, metadata = read_regular_at(parent_descriptor, name, limit=MAX_UPLOAD_BYTES)
    except FileNotFoundError:
        return None
    return payload, stat.S_IMODE(metadata.st_mode)


def block_termination_signals():
    if hasattr(signal, "pthread_sigmask"):
        signals = {signal.SIGTERM, signal.SIGINT, signal.SIGHUP}
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, signals)
        return lambda: signal.pthread_sigmask(signal.SIG_SETMASK, previous)
    return lambda: None


def rollback_transaction(
    request,
    stage_descriptor,
    repo_descriptor,
    lock,
    journal,
):
    if journal["phase"] == "rollback_failed":
        fail("rollback_failed")
    quiesce_archive(request)
    restore_runtime(
        stage_descriptor,
        repo_descriptor,
        request,
        lock,
        journal,
    )
    journal = set_journal_phase(
        stage_descriptor, lock, journal, "rolled_back"
    )
    return journal


def mark_rollback_failed(
    deploy_descriptor,
    stage_descriptor,
    lock,
    journal,
    *,
    mutex_held=False,
):
    try:
        set_journal_phase(stage_descriptor, lock, journal, "rollback_failed")
    except BaseException:
        pass
    try:
        if mutex_held:
            write_lock_unlocked(
                deploy_descriptor,
                lock["deployment_id"],
                lock["relative_paths"],
                "rollback_failed",
            )
        else:
            set_lock(
                deploy_descriptor,
                lock["deployment_id"],
                lock["relative_paths"],
                "rollback_failed",
            )
    except BaseException:
        pass


def recover_stale_transaction(
    request,
    deploy_descriptor,
    stages_descriptor,
    lock,
):
    stage_name = ".fleet-deploy-" + lock["deployment_id"]
    try:
        stage_descriptor = os.open(
            stage_name, DIRECTORY_FLAGS, dir_fd=stages_descriptor
        )
    except FileNotFoundError:
        fail("recovery_required")
    repo_descriptor = -1
    signals_restore = lambda: None
    try:
        journal = read_journal(stage_descriptor, lock)
        if journal is None:
            remove_tree_at(stages_descriptor, stage_name, [0])
            os.unlink(LOCK_FILE, dir_fd=deploy_descriptor)
            os.fsync(deploy_descriptor)
            return
        if journal["phase"] == "rollback_failed":
            fail("rollback_failed")
        repo_descriptor = open_repo_descriptor(request, create=False)
        signals_restore = block_termination_signals()
        try:
            journal = rollback_transaction(
                request,
                stage_descriptor,
                repo_descriptor,
                lock,
                journal,
            )
        except BaseException:
            mark_rollback_failed(
                deploy_descriptor,
                stage_descriptor,
                lock,
                journal,
                mutex_held=True,
            )
            fail("rollback_failed")
        finally:
            signals_restore()
        os.close(stage_descriptor)
        stage_descriptor = -1
        remove_tree_at(stages_descriptor, stage_name, [0])
        os.unlink(LOCK_FILE, dir_fd=deploy_descriptor)
        os.fsync(deploy_descriptor)
    finally:
        close_descriptors(repo_descriptor, stage_descriptor)


def install_files(request):
    repo = checked_repo(request)
    (
        stage_path,
        deploy_descriptor,
        stages_descriptor,
        stage_descriptor,
        lock,
    ) = stage_details(request)
    repo_descriptor = -1
    targets = {}
    temporary_names = {}
    signals_restore = lambda: None
    journal = None
    try:
        expected = expected_hashes(request)
        if set(expected) != set(lock["relative_paths"]):
            fail("schema_drift")
        verify_paths_descriptor(stage_descriptor, expected)
        repo_descriptor = open_repo_descriptor(request, create=False)
        observed_archive_state = valid_archive_state(
            snapshot_archive_state(request)
        )
        immutable_archive_state = (
            observed_archive_state["enabled"],
            observed_archive_state["loaded"],
        )
        journal = create_transaction(
            stage_descriptor,
            repo_descriptor,
            lock,
            expected,
            immutable_archive_state,
        )

        for relative in sorted(expected):
            parent_descriptor, target_name = open_target_parent(
                repo_descriptor, relative, create=True
            )
            targets[relative] = (
                parent_descriptor,
                target_name,
                0o700 if relative.endswith(".py") else 0o600,
            )
            source_descriptor = open_relative_file(stage_descriptor, relative)
            temporary = transaction_temporary_name(relative, lock)
            temporary_names[relative] = temporary
            try:
                payload = b""
                chunks = []
                while True:
                    chunk = os.read(source_descriptor, 1024 * 1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
                payload = b"".join(chunks)
                write_regular_at(parent_descriptor, temporary, payload, targets[relative][2])
            finally:
                os.close(source_descriptor)

        signals_restore = block_termination_signals()
        try:
            quiesce_archive(request)
            journal = set_journal_phase(
                stage_descriptor, lock, journal, "activating"
            )
            install_order = sorted(
                expected,
                key=lambda relative: (not relative.endswith(".py"), relative),
            )
            for relative in install_order:
                parent_descriptor, target_name, _ = targets[relative]
                temporary = temporary_names[relative]
                os.replace(
                    temporary,
                    target_name,
                    src_dir_fd=parent_descriptor,
                    dst_dir_fd=parent_descriptor,
                )
                os.fsync(parent_descriptor)
            actual = verify_paths_descriptor(repo_descriptor, expected)
            journal = set_journal_phase(
                stage_descriptor, lock, journal, "activated"
            )
            launchd = install_archive_job(request, repo)
            journal = set_journal_phase(
                stage_descriptor, lock, journal, "launched"
            )
        except BaseException as original:
            try:
                journal = rollback_transaction(
                    request,
                    stage_descriptor,
                    repo_descriptor,
                    lock,
                    journal,
                )
            except BaseException:
                mark_rollback_failed(
                    deploy_descriptor,
                    stage_descriptor,
                    lock,
                    journal,
                )
                fail("rollback_failed")
            if isinstance(original, ControlledFailure):
                raise original
            if not isinstance(original, Exception):
                raise original
            fail("install_failed")
        finally:
            signals_restore()
        return {
            "schema_version": 1,
            "ok": True,
            "sha256": actual,
            "launchd": launchd,
        }
    finally:
        for relative, (parent_descriptor, _, _) in targets.items():
            temporary = temporary_names.get(relative)
            if temporary:
                try:
                    os.unlink(temporary, dir_fd=parent_descriptor)
                except FileNotFoundError:
                    pass
            os.close(parent_descriptor)
        close_descriptors(repo_descriptor, stage_descriptor, stages_descriptor, deploy_descriptor)


def verify_final(request):
    checked_repo(request)
    (
        stage_path,
        deploy_descriptor,
        stages_descriptor,
        stage_descriptor,
        lock,
    ) = stage_details(request)
    repo_descriptor = -1
    signals_restore = lambda: None
    journal = None
    try:
        repo_descriptor = open_repo_descriptor(request, create=False)
        journal = read_journal(stage_descriptor, lock)
        if journal is None or journal["phase"] != "launched":
            fail("recovery_required")
        expected = expected_hashes(request)
        if expected != journal["expected_sha256"]:
            fail("schema_drift")
        signals_restore = block_termination_signals()
        try:
            actual = verify_paths_descriptor(repo_descriptor, expected)
            observed = snapshot_archive_state(request)
            if observed != {"enabled": True, "loaded": True}:
                fail("archive_launchd_failed")
            journal = set_journal_phase(
                stage_descriptor, lock, journal, "verified"
            )
        except BaseException as original:
            try:
                journal = rollback_transaction(
                    request,
                    stage_descriptor,
                    repo_descriptor,
                    lock,
                    journal,
                )
            except BaseException:
                mark_rollback_failed(
                    deploy_descriptor,
                    stage_descriptor,
                    lock,
                    journal,
                )
                fail("rollback_failed")
            raise original
        finally:
            signals_restore()
        return {"schema_version": 1, "ok": True, "sha256": actual}
    finally:
        close_descriptors(
            repo_descriptor,
            stage_descriptor,
            stages_descriptor,
            deploy_descriptor,
        )


def finish(request):
    (
        stage_path,
        deploy_descriptor,
        stages_descriptor,
        stage_descriptor,
        lock,
    ) = stage_details(request)
    repo_descriptor = -1
    signals_restore = lambda: None
    try:
        commit = request.get("commit") is True and request.get("abort") is not True
        abort = request.get("abort") is True and request.get("commit") is not True
        if not (commit or abort):
            fail("schema_drift")
        mutex = acquire_mutex(deploy_descriptor)
        try:
            require_lock(deploy_descriptor, request)
            journal = read_journal(stage_descriptor, lock)
            if commit:
                if journal is None or journal["phase"] != "verified":
                    fail("recovery_required")
            elif journal is not None:
                repo_descriptor = open_repo_descriptor(request, create=False)
                signals_restore = block_termination_signals()
                try:
                    journal = rollback_transaction(
                        request,
                        stage_descriptor,
                        repo_descriptor,
                        lock,
                        journal,
                    )
                except BaseException:
                    mark_rollback_failed(
                        deploy_descriptor,
                        stage_descriptor,
                        lock,
                        journal,
                        mutex_held=True,
                    )
                    fail("rollback_failed")
                finally:
                    signals_restore()
            os.close(stage_descriptor)
            stage_descriptor = -1
            remove_tree_at(stages_descriptor, stage_path.name, [0])
            os.unlink(LOCK_FILE, dir_fd=deploy_descriptor)
            os.fsync(deploy_descriptor)
        finally:
            release_mutex(mutex)
        return {"schema_version": 1, "ok": True, "finished": True}
    finally:
        close_descriptors(
            repo_descriptor,
            stage_descriptor,
            stages_descriptor,
            deploy_descriptor,
        )


def main():
    try:
        request = json.loads(base64.b64decode(REQUEST_B64, validate=True))
        if not isinstance(request, dict) or request.get("schema_version") != 1:
            fail("schema_drift")
        action = request.get("action")
        handlers = {
            "inventory": inventory,
            "prepare": prepare,
            "upload_file": upload_file,
            "verify_stage": verify_stage,
            "install_files": install_files,
            "verify_final": verify_final,
            "finish": finish,
        }
        if action not in handlers:
            fail("schema_drift")
        emit(handlers[action](request))
    except ControlledFailure as error:
        emit({"schema_version": 1, "ok": False, "error": str(error)})
    except Exception:
        emit({"schema_version": 1, "ok": False, "error": "remote_helper_failure"})
'''


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def validate_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise DeployError("unsafe_path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise DeployError("unsafe_path")
    if not all(re.fullmatch(r"[A-Za-z0-9._-]+", part) for part in path.parts):
        raise DeployError("unsafe_path")
    return value


def validate_remote_path(value: object, *, below: str | None = None) -> str:
    if not isinstance(value, str) or not REMOTE_PATH_RE.fullmatch(value):
        raise DeployError("unsafe_path")
    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in path.parts[1:]):
        raise DeployError("unsafe_path")
    if below is not None:
        try:
            path.relative_to(PurePosixPath(below))
        except ValueError as error:
            raise DeployError("unsafe_path") from error
    return value


def remote_stage_path(config: DeployConfig, deployment_id: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", deployment_id):
        raise DeployError("config_schema_drift")
    return (
        f"{config.expected_remote_home}/{REMOTE_DEPLOY_ROOT_RELATIVE}/stages/"
        f".fleet-deploy-{deployment_id}"
    )


def read_json_file(path: Path) -> dict:
    path = Path(os.path.abspath(os.path.expanduser(str(path))))
    if path.is_symlink() or not path.is_file():
        raise DeployError("unsafe_path")
    if path.stat().st_size > 128 * 1024:
        raise DeployError("config_schema_drift")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DeployError("config_schema_drift") from error
    if not isinstance(value, dict):
        raise DeployError("config_schema_drift")
    return value


def load_deploy_config(path: Path) -> DeployConfig:
    value = read_json_file(path)
    if set(value) != DEPLOY_CONFIG_KEYS or value.get("schema_version") != SCHEMA_VERSION:
        raise DeployError("config_schema_drift")
    if value.get("host_id") != EXPECTED_HOST_ID or not HOST_ID_RE.fullmatch(value["host_id"]):
        raise DeployError("config_schema_drift")
    if value.get("ssh_host") != EXPECTED_SSH_HOST or not SSH_HOST_RE.fullmatch(value["ssh_host"]):
        raise DeployError("config_schema_drift")
    exact_values = {
        "expected_remote_user": EXPECTED_REMOTE_USER,
        "expected_remote_home": EXPECTED_REMOTE_HOME,
        "remote_repo_root": EXPECTED_REMOTE_REPO,
        "retry_interval_seconds": RETRY_INTERVAL_SECONDS,
        "archive_interval_seconds": ARCHIVE_INTERVAL_SECONDS,
        "remote_config_relative_path": REMOTE_CONFIG_RELATIVE_PATH,
    }
    if any(value.get(key) != expected for key, expected in exact_values.items()):
        raise DeployError("config_schema_drift")
    if value.get("runtime_files") != list(RUNTIME_FILES):
        raise DeployError("config_schema_drift")
    if value.get("runtime_sha256") != REVIEWED_RUNTIME_SHA256:
        raise DeployError("config_schema_drift")
    validate_remote_path(value["expected_remote_home"])
    validate_remote_path(value["remote_repo_root"], below=value["expected_remote_home"])
    validate_relative_path(value["remote_config_relative_path"])
    for relative in value["runtime_files"]:
        validate_relative_path(relative)
    return DeployConfig(
        host_id=value["host_id"],
        ssh_host=value["ssh_host"],
        expected_remote_user=value["expected_remote_user"],
        expected_remote_home=value["expected_remote_home"],
        remote_repo_root=value["remote_repo_root"],
        retry_interval_seconds=value["retry_interval_seconds"],
        archive_interval_seconds=value["archive_interval_seconds"],
        runtime_files=tuple(value["runtime_files"]),
        runtime_sha256=dict(value["runtime_sha256"]),
        remote_config_relative_path=value["remote_config_relative_path"],
    )


def identity_request(config: DeployConfig) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "expected_remote_user": config.expected_remote_user,
        "expected_remote_home": config.expected_remote_home,
        "remote_repo_root": config.remote_repo_root,
    }


def validate_inventory_response(config: DeployConfig, response: dict) -> dict:
    required_outer = {"schema_version", "ok", "identity", "inventory"}
    if not isinstance(response, dict) or set(response) != required_outer:
        raise DeployError("remote_schema_drift")
    if response.get("schema_version") != SCHEMA_VERSION or response.get("ok") is not True:
        raise DeployError("remote_schema_drift")
    identity = response["identity"]
    if (
        not isinstance(identity, dict)
        or set(identity) != {"user", "home", "platform", "python_major"}
    ):
        raise DeployError("remote_schema_drift")
    if identity.get("user") != config.expected_remote_user:
        raise DeployError("identity_mismatch")
    if identity.get("home") != config.expected_remote_home:
        raise DeployError("home_mismatch")
    if identity.get("platform") != "Darwin":
        raise DeployError("non_darwin")
    if not isinstance(identity.get("python_major"), int) or identity["python_major"] < 3:
        raise DeployError("missing_python")

    inventory = response["inventory"]
    if not isinstance(inventory, dict) or set(inventory) != set(INVENTORY_HARNESSES):
        raise DeployError("remote_schema_drift")
    for harness in ("claude", "codex", "openclaw"):
        entry = inventory[harness]
        if not isinstance(entry, dict) or set(entry) != {"present", "roots"}:
            raise DeployError("remote_schema_drift")
        roots = entry.get("roots")
        if not isinstance(entry.get("present"), bool) or not isinstance(roots, list):
            raise DeployError("remote_schema_drift")
        if entry["present"] != bool(roots) or len(roots) != len(set(roots)):
            raise DeployError("remote_schema_drift")
        for root in roots:
            validate_remote_path(root, below=config.expected_remote_home)
    hermes = inventory["hermes"]
    if not isinstance(hermes, dict) or set(hermes) != {"present", "instances"}:
        raise DeployError("remote_schema_drift")
    instances = hermes.get("instances")
    if not isinstance(hermes.get("present"), bool) or not isinstance(instances, list):
        raise DeployError("remote_schema_drift")
    if hermes["present"] != bool(instances):
        raise DeployError("remote_schema_drift")
    seen_homes = set()
    seen_binaries = set()
    for instance in instances:
        if not isinstance(instance, dict) or set(instance) != {"home", "binary"}:
            raise DeployError("remote_schema_drift")
        home = validate_remote_path(instance["home"], below=config.expected_remote_home)
        binary = validate_remote_path(instance["binary"], below=config.expected_remote_home)
        if home in seen_homes:
            raise DeployError("ambiguous_hermes")
        seen_homes.add(home)
        seen_binaries.add(binary)
    if len(seen_binaries) > 1:
        raise DeployError("ambiguous_hermes")
    return inventory


def derive_archive_config(config: DeployConfig, inventory: dict) -> dict:
    sources: dict[str, list] = {}
    if inventory["claude"]["present"]:
        sources["claude_roots"] = list(inventory["claude"]["roots"])
    if inventory["codex"]["present"]:
        sources["codex_roots"] = list(inventory["codex"]["roots"])
    if inventory["openclaw"]["present"]:
        sources["openclaw_roots"] = list(inventory["openclaw"]["roots"])
    if inventory["hermes"]["present"]:
        sources["hermes_instances"] = [dict(item) for item in inventory["hermes"]["instances"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "host_id": config.host_id,
        "spool_root": f"{config.expected_remote_home}/.local/share/ai-chat-archive/spool",
        "drive_root": None,
        "inventory_harnesses": list(INVENTORY_HARNESSES),
        "sources": sources,
    }


def runtime_manifest(
    repo_root: Path,
    config: DeployConfig,
    snapshot_root: Path,
) -> list[DeployFile]:
    repo_root = Path(os.path.abspath(str(repo_root)))
    snapshot_root.mkdir(mode=0o700)
    files = []
    for relative in config.runtime_files:
        validate_relative_path(relative)
        candidate = repo_root / PurePosixPath(relative)
        if candidate.is_symlink() or not candidate.is_file():
            raise DeployError("local_runtime_missing")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(candidate, flags)
        except OSError as error:
            raise DeployError("local_runtime_missing") from error
        try:
            initial = os.fstat(descriptor)
            if not stat.S_ISREG(initial.st_mode) or initial.st_size > 4 * 1024 * 1024:
                raise DeployError("local_runtime_missing")
            chunks = []
            remaining = initial.st_size
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    raise DeployError("local_runtime_changed")
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                raise DeployError("local_runtime_changed")
            final = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        initial_identity = (
            initial.st_dev,
            initial.st_ino,
            initial.st_size,
            initial.st_mtime_ns,
            initial.st_ctime_ns,
        )
        final_identity = (
            final.st_dev,
            final.st_ino,
            final.st_size,
            final.st_mtime_ns,
            final.st_ctime_ns,
        )
        if initial_identity != final_identity:
            raise DeployError("local_runtime_changed")
        payload = b"".join(chunks)
        digest = hashlib.sha256(payload).hexdigest()
        if digest != config.runtime_sha256.get(relative):
            raise DeployError("local_runtime_hash_mismatch")
        snapshot = snapshot_root / relative
        snapshot.write_bytes(payload)
        os.chmod(snapshot, 0o600)
        files.append(DeployFile(relative, snapshot, digest))
    if tuple(item.relative_path for item in files) != RUNTIME_FILES:
        raise DeployError("config_schema_drift")
    return files


def validate_hash_response(response: dict, expected: dict[str, str]) -> None:
    if not isinstance(response, dict) or set(response) != {"schema_version", "ok", "sha256"}:
        raise DeployError("remote_schema_drift")
    if response.get("schema_version") != SCHEMA_VERSION or response.get("ok") is not True:
        raise DeployError("remote_schema_drift")
    hashes = response.get("sha256")
    if not isinstance(hashes, dict) or hashes != expected:
        raise DeployError("hash_mismatch")
    if any(
        not isinstance(value, str) or not SHA256_RE.fullmatch(value)
        for value in hashes.values()
    ):
        raise DeployError("hash_mismatch")


def validate_install_response(
    response: dict,
    expected_hashes: dict[str, str],
    config: DeployConfig,
) -> dict:
    if not isinstance(response, dict) or set(response) != {
        "schema_version",
        "ok",
        "sha256",
        "launchd",
    }:
        raise DeployError("remote_schema_drift")
    validate_hash_response(
        {key: response[key] for key in ("schema_version", "ok", "sha256")},
        expected_hashes,
    )
    launchd = response.get("launchd")
    expected_label = f"com.mattrotundo.ai-chat-archive.{config.host_id}"
    expected_plist = (
        f"{config.expected_remote_home}/Library/LaunchAgents/{expected_label}.plist"
    )
    if (
        not isinstance(launchd, dict)
        or set(launchd) != {"label", "loaded", "plist_path"}
        or launchd.get("label") != expected_label
        or launchd.get("loaded") is not True
        or launchd.get("plist_path") != expected_plist
    ):
        raise DeployError("archive_launchd_failed")
    return launchd


class SshTransport:
    """SSH transport whose output is never forwarded to the status receipt."""

    def __init__(
        self,
        config: DeployConfig,
        *,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ):
        self.config = config
        self.runner = runner
        self.ssh_base = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=8",
            "-o",
            "ControlMaster=no",
            "-o",
            "ControlPath=none",
        ]

    @staticmethod
    def _raise_for_transport(
        completed: subprocess.CompletedProcess,
        *,
        python_required: bool,
    ) -> None:
        if completed.returncode == 255:
            diagnostic = (completed.stderr or "").lower()
            fail_closed_markers = (
                "permission denied",
                "host key verification failed",
                "remote host identification has changed",
                "too many authentication failures",
            )
            if any(marker in diagnostic for marker in fail_closed_markers):
                raise DeployError("remote_command_failed")
            raise OfflineError()
        if completed.returncode == 127 and python_required:
            raise DeployError("missing_python")
        if completed.returncode != 0:
            raise DeployError("remote_command_failed")

    def helper(self, action: str, values: dict) -> dict:
        request = dict(identity_request(self.config))
        request.update(values)
        request["schema_version"] = SCHEMA_VERSION
        request["action"] = action
        encoded = base64.b64encode(canonical_json(request)).decode("ascii")
        program = REMOTE_HELPER + "\nREQUEST_B64 = " + repr(encoded) + "\nmain()\n"
        try:
            completed = self.runner(
                [*self.ssh_base, self.config.ssh_host, "python3", "-"],
                input=program,
                text=True,
                capture_output=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise OfflineError() from error
        except OSError as error:
            raise DeployError("local_transport_error") from error
        self._raise_for_transport(completed, python_required=True)
        if len(completed.stdout.encode("utf-8", errors="ignore")) > 128 * 1024:
            raise DeployError("remote_schema_drift")
        try:
            response = json.loads(completed.stdout)
        except (TypeError, json.JSONDecodeError) as error:
            raise DeployError("remote_schema_drift") from error
        if not isinstance(response, dict) or response.get("schema_version") != SCHEMA_VERSION:
            raise DeployError("remote_schema_drift")
        if response.get("ok") is False:
            code = response.get("error")
            allowed = {
                "identity_mismatch",
                "home_mismatch",
                "non_darwin",
                "unsafe_path",
                "ambiguous_hermes",
                "schema_drift",
                "remote_file_missing",
                "hash_mismatch",
                "archive_launchd_failed",
                "archive_quiesce_failed",
                "archive_still_loaded",
                "deployment_locked",
                "deployment_lock_mismatch",
                "install_failed",
                "rollback_failed",
                "recovery_required",
                "stale_stage_limit",
                "remote_helper_failure",
            }
            raise DeployError(code if code in allowed else "remote_helper_failure")
        return response

    def copy_file(
        self,
        local_path: Path,
        *,
        deployment_id: str,
        stage_path: str,
        relative_path: str,
        expected_sha256: str,
    ) -> None:
        relative_path = validate_relative_path(relative_path)
        if stage_path != remote_stage_path(self.config, deployment_id):
            raise DeployError("unsafe_path")
        if local_path.is_symlink() or not local_path.is_file():
            raise DeployError("local_runtime_missing")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(local_path, flags)
        except OSError as error:
            raise DeployError("local_runtime_missing") from error
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 4 * 1024 * 1024:
                raise DeployError("local_runtime_missing")
            chunks = []
            remaining = metadata.st_size
            while remaining:
                chunk = os.read(descriptor, min(remaining, 1024 * 1024))
                if not chunk:
                    raise DeployError("local_runtime_changed")
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                raise DeployError("local_runtime_changed")
            final = os.fstat(descriptor)
            if (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_size,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
            ) != (
                final.st_dev,
                final.st_ino,
                final.st_size,
                final.st_mtime_ns,
                final.st_ctime_ns,
            ):
                raise DeployError("local_runtime_changed")
        finally:
            os.close(descriptor)
        payload = b"".join(chunks)
        if hashlib.sha256(payload).hexdigest() != expected_sha256:
            raise DeployError("local_runtime_hash_mismatch")
        response = self.helper(
            "upload_file",
            {
                "deployment_id": deployment_id,
                "stage_path": stage_path,
                "relative_path": relative_path,
                "expected_sha256": expected_sha256,
                "content_b64": base64.b64encode(payload).decode("ascii"),
            },
        )
        validate_hash_response(response, {relative_path: expected_sha256})


def body_free_inventory(inventory: dict | None) -> dict:
    return {
        harness: bool(inventory and inventory[harness]["present"])
        for harness in INVENTORY_HARNESSES
    }


def failure_status(config: DeployConfig, code: str, stage: str, inventory: dict | None) -> dict:
    retryable = code == "ssh_unreachable"
    return {
        "schema_version": SCHEMA_VERSION,
        "host_id": config.host_id,
        "status": "offline_retryable" if retryable else "failed",
        "retryable": retryable,
        "stage": stage,
        "inventory": body_free_inventory(inventory),
        "files": {"count": 0, "verified": False},
        "archive_launchd": {
            "installed": False,
            "interval_seconds": config.archive_interval_seconds,
        },
        "errors": [{"code": code}],
    }


def deploy_once(config: DeployConfig, transport, *, repo_root: Path | None = None) -> dict:
    inventory = None
    stage = "inventory"
    deployment_id = None
    stage_path = None
    remote_finished = False
    try:
        inventory_response = transport.helper("inventory", {})
        inventory = validate_inventory_response(config, inventory_response)
        archive_config = derive_archive_config(config, inventory)
        archive_payload = json.dumps(
            archive_config,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ).encode("utf-8") + b"\n"

        with tempfile.TemporaryDirectory(prefix="old-macbook-deploy-") as temporary:
            temporary_root = Path(temporary).resolve()
            source_root = repo_root or Path(__file__).resolve().parent
            stage = "snapshot_runtime"
            files = runtime_manifest(
                source_root,
                config,
                temporary_root / "runtime",
            )
            generated_config = temporary_root / "old-macbook.json"
            generated_config.write_bytes(archive_payload)
            os.chmod(generated_config, 0o600)
            files.append(
                DeployFile(
                    config.remote_config_relative_path,
                    generated_config,
                    hashlib.sha256(archive_payload).hexdigest(),
                )
            )
            if tuple(item.relative_path for item in files) != DEPLOYED_FILES:
                raise DeployError("config_schema_drift")
            expected_hashes = {item.relative_path: item.sha256 for item in files}

            stage = "prepare"
            deployment_id = secrets.token_hex(16)
            stage_path = remote_stage_path(config, deployment_id)
            prepared = transport.helper(
                "prepare",
                {
                    "deployment_id": deployment_id,
                    "relative_paths": list(expected_hashes),
                    "host_id": config.host_id,
                    "script_relative_path": "fleet_chat_archive.py",
                    "config_relative_path": config.remote_config_relative_path,
                    "interval_seconds": config.archive_interval_seconds,
                },
            )
            if (
                not isinstance(prepared, dict)
                or set(prepared) != {"schema_version", "ok", "stage_path"}
            ):
                raise DeployError("remote_schema_drift")
            if prepared.get("schema_version") != SCHEMA_VERSION or prepared.get("ok") is not True:
                raise DeployError("remote_schema_drift")
            returned_stage = validate_remote_path(
                prepared.get("stage_path"), below=config.expected_remote_home
            )
            if returned_stage != stage_path:
                raise DeployError("unsafe_path")

            stage = "copy"
            for item in files:
                transport.copy_file(
                    item.local_path,
                    deployment_id=deployment_id,
                    stage_path=stage_path,
                    relative_path=item.relative_path,
                    expected_sha256=item.sha256,
                )

            stage = "verify_staged_hashes"
            staged = transport.helper(
                "verify_stage",
                {
                    "deployment_id": deployment_id,
                    "stage_path": stage_path,
                    "expected_sha256": expected_hashes,
                },
            )
            validate_hash_response(staged, expected_hashes)

            stage = "activate_runtime"
            installed = transport.helper(
                "install_files",
                {
                    "deployment_id": deployment_id,
                    "stage_path": stage_path,
                    "expected_sha256": expected_hashes,
                    "host_id": config.host_id,
                    "script_relative_path": "fleet_chat_archive.py",
                    "config_relative_path": config.remote_config_relative_path,
                    "interval_seconds": config.archive_interval_seconds,
                },
            )
            launchd = validate_install_response(installed, expected_hashes, config)

            stage = "verify_remote_hashes"
            final_hashes = transport.helper(
                "verify_final",
                {
                    "deployment_id": deployment_id,
                    "stage_path": stage_path,
                    "expected_sha256": expected_hashes,
                },
            )
            validate_hash_response(final_hashes, expected_hashes)

            stage = "finish_remote_deploy"
            finished = transport.helper(
                "finish",
                {
                    "deployment_id": deployment_id,
                    "stage_path": stage_path,
                    "commit": True,
                },
            )
            if finished != {
                "schema_version": SCHEMA_VERSION,
                "ok": True,
                "finished": True,
            }:
                raise DeployError("remote_schema_drift")
            remote_finished = True

        return {
            "schema_version": SCHEMA_VERSION,
            "host_id": config.host_id,
            "status": "remote_deployed",
            "retryable": False,
            "stage": "completed",
            "inventory": body_free_inventory(inventory),
            "files": {
                "count": len(expected_hashes),
                "verified": True,
                "sha256": expected_hashes,
            },
            "archive_launchd": {
                "installed": True,
                "loaded": launchd["loaded"],
                "interval_seconds": config.archive_interval_seconds,
            },
            "errors": [],
        }
    except OfflineError as error:
        return failure_status(config, error.code, stage, inventory)
    except DeployError as error:
        return failure_status(config, error.code, stage, inventory)
    finally:
        if deployment_id is not None and stage_path is not None and not remote_finished:
            try:
                transport.helper(
                    "finish",
                    {
                        "deployment_id": deployment_id,
                        "stage_path": stage_path,
                        "abort": True,
                    },
                )
            except Exception:
                pass


def secure_directory(path: Path) -> Path:
    absolute = Path(os.path.abspath(os.path.expanduser(str(path))))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            metadata = current.lstat()
        if current.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
            raise DeployError("unsafe_path")
    os.chmod(absolute, 0o700)
    return absolute


def inspect_directory_path(path: Path) -> Path:
    absolute = Path(os.path.abspath(os.path.expanduser(str(path))))
    current = Path(absolute.anchor)
    missing = False
    for part in absolute.parts[1:]:
        current = current / part
        if missing:
            continue
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            missing = True
            continue
        if current.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
            raise DeployError("unsafe_path")
    return absolute


def atomic_owner_write(path: Path, payload: bytes) -> None:
    parent = secure_directory(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.close(descriptor)
        descriptor = -1
        try:
            existing = path.lstat()
        except FileNotFoundError:
            existing = None
        if existing is not None and (
            path.is_symlink()
            or not stat.S_ISREG(existing.st_mode)
            or existing.st_uid != os.getuid()
        ):
            raise DeployError("unsafe_path")
        os.replace(temporary, path)
        parent_descriptor = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def ensure_owner_log(path: Path) -> None:
    parent = secure_directory(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(parent / path.name, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise DeployError("unsafe_path")
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def launchctl_result(
    runner: Callable[..., subprocess.CompletedProcess],
    command: list[str],
) -> subprocess.CompletedProcess:
    try:
        return runner(
            ["launchctl", *command],
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DeployError("retry_launchd_failed") from error


def local_launchd_disabled(
    response: subprocess.CompletedProcess,
    label: str,
) -> bool:
    if response.returncode != 0:
        raise DeployError("retry_launchd_failed")
    match = re.search(
        rf'"?{re.escape(label)}"?\s*=>\s*(true|false)(?:\s|$)',
        response.stdout or "",
    )
    if match:
        return match.group(1) == "true"
    if re.search(
        rf'"?{re.escape(label)}"?\s*=>', response.stdout or ""
    ):
        raise DeployError("retry_launchd_failed")
    return False


def retry_launchd_state(
    runner: Callable[..., subprocess.CompletedProcess],
    domain: str,
    label: str,
) -> dict:
    target = f"{domain}/{label}"
    printed = launchctl_result(runner, ["print", target])
    loaded = local_launchctl_service_loaded(printed, target)
    disabled = local_launchd_disabled(
        launchctl_result(runner, ["print-disabled", domain]),
        label,
    )
    return {"enabled": not disabled, "loaded": loaded}


def local_launchctl_service_loaded(
    response: subprocess.CompletedProcess,
    target: str,
) -> bool:
    label = target.rsplit("/", 1)[-1]
    uid = os.getuid()
    loaded_header = f"{target} = {{\n"
    if response.returncode == 0:
        if (
            response.stderr != ""
            or not isinstance(response.stdout, str)
            or not response.stdout.startswith(loaded_header)
            or not response.stdout.endswith("}\n")
        ):
            raise DeployError("retry_launchd_failed")
        return True
    missing_stderr = (
        f'Bad request.\nCould not find service "{label}" '
        f"in domain for user gui: {uid}\n"
    )
    if (
        response.returncode == 113
        and response.stdout == ""
        and response.stderr == missing_stderr
    ):
        return False
    raise DeployError("retry_launchd_failed")


def snapshot_owner_file(path: Path) -> dict:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return {"present": False, "payload": None, "mode": None}
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
    ):
        raise DeployError("unsafe_path")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        checked = os.fstat(descriptor)
        if not stat.S_ISREG(checked.st_mode) or checked.st_size > 128 * 1024:
            raise DeployError("unsafe_path")
        chunks = []
        remaining = checked.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise DeployError("unsafe_path")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise DeployError("unsafe_path")
    finally:
        os.close(descriptor)
    return {
        "present": True,
        "payload": b"".join(chunks),
        "mode": stat.S_IMODE(checked.st_mode),
    }


def restore_owner_file(path: Path, snapshot: dict) -> None:
    if snapshot["present"]:
        atomic_owner_write(path, snapshot["payload"])
        os.chmod(path, snapshot["mode"])
        parent_descriptor = os.open(
            path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        )
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
        return
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
    ):
        raise DeployError("unsafe_path")
    path.unlink()
    parent_descriptor = os.open(
        path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    )
    try:
        os.fsync(parent_descriptor)
    finally:
        os.close(parent_descriptor)


def block_local_termination_signals() -> Callable[[], None]:
    if hasattr(signal, "pthread_sigmask"):
        signals = {signal.SIGTERM, signal.SIGINT, signal.SIGHUP}
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, signals)
        return lambda: signal.pthread_sigmask(signal.SIG_SETMASK, previous)
    return lambda: None


def restore_retry_launchd(
    runner: Callable[..., subprocess.CompletedProcess],
    domain: str,
    label: str,
    plist_path: Path,
    file_snapshot: dict,
    launchd_snapshot: dict,
) -> None:
    target = f"{domain}/{label}"
    launchctl_result(runner, ["bootout", target])
    if local_launchctl_service_loaded(
        launchctl_result(runner, ["print", target]), target
    ):
        raise DeployError("retry_launchd_restore_failed")
    restore_owner_file(plist_path, file_snapshot)
    if launchd_snapshot["loaded"]:
        if not file_snapshot["present"]:
            raise DeployError("retry_launchd_restore_failed")
        if launchctl_result(runner, ["enable", target]).returncode != 0:
            raise DeployError("retry_launchd_restore_failed")
        if (
            launchctl_result(
                runner, ["bootstrap", domain, str(plist_path)]
            ).returncode
            != 0
        ):
            raise DeployError("retry_launchd_restore_failed")
    command = "enable" if launchd_snapshot["enabled"] else "disable"
    if launchctl_result(runner, [command, target]).returncode != 0:
        raise DeployError("retry_launchd_restore_failed")
    try:
        observed = retry_launchd_state(runner, domain, label)
    except DeployError as error:
        raise DeployError("retry_launchd_restore_failed") from error
    if observed != launchd_snapshot:
        raise DeployError("retry_launchd_restore_failed")


def install_retry_launchd(
    config_path: Path,
    config: DeployConfig,
    *,
    launch_agents_dir: Path | None = None,
    logs_dir: Path | None = None,
    load: bool = True,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict:
    if platform.system() != "Darwin":
        raise DeployError("non_darwin")
    config_path = Path(os.path.abspath(os.path.expanduser(str(config_path))))
    if config_path.is_symlink() or not config_path.is_file():
        raise DeployError("unsafe_path")
    agents_candidate = inspect_directory_path(
        launch_agents_dir or Path.home() / "Library" / "LaunchAgents"
    )
    logs_candidate = inspect_directory_path(
        logs_dir or Path.home() / "Library" / "Logs" / "AIChatArchiveDeploy"
    )
    label = RETRY_LAUNCHD_LABEL
    plist_path = agents_candidate / f"{label}.plist"
    if load:
        domain = f"gui/{os.getuid()}"
        target = f"{domain}/{label}"
        launchd_snapshot = retry_launchd_state(runner, domain, label)
        file_snapshot = snapshot_owner_file(plist_path)
    agents_dir = secure_directory(agents_candidate)
    logs_root = secure_directory(logs_candidate)
    stdout_path = logs_root / "old-macbook.out.log"
    stderr_path = logs_root / "old-macbook.err.log"
    ensure_owner_log(stdout_path)
    ensure_owner_log(stderr_path)
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
        "StartInterval": config.retry_interval_seconds,
        "ProcessType": "Background",
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
        "Umask": 0o077,
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
    }
    plist_path = agents_dir / f"{label}.plist"
    loaded = False
    payload = plistlib.dumps(job, fmt=plistlib.FMT_XML)
    if not load:
        atomic_owner_write(plist_path, payload)
    else:
        signals_restore = block_local_termination_signals()
        try:
            atomic_owner_write(plist_path, payload)
            launchctl_result(runner, ["bootout", target])
            if launchctl_result(runner, ["enable", target]).returncode != 0:
                raise DeployError("retry_launchd_failed")
            if (
                launchctl_result(
                    runner, ["bootstrap", domain, str(plist_path)]
                ).returncode
                != 0
            ):
                raise DeployError("retry_launchd_failed")
            observed = retry_launchd_state(runner, domain, label)
            if observed != {"enabled": True, "loaded": True}:
                raise DeployError("retry_launchd_failed")
            loaded = True
        except BaseException as original:
            try:
                restore_retry_launchd(
                    runner,
                    domain,
                    label,
                    plist_path,
                    file_snapshot,
                    launchd_snapshot,
                )
            except BaseException as restore_error:
                raise DeployError("retry_launchd_restore_failed") from restore_error
            if isinstance(original, DeployError):
                raise original
            if not isinstance(original, Exception):
                raise original
            raise DeployError("retry_launchd_failed") from original
        finally:
            signals_restore()
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "installed",
        "label": label,
        "loaded": loaded,
        "interval_seconds": config.retry_interval_seconds,
        "plist_path": str(plist_path),
    }


def disable_retry_launchd(
    *,
    launch_agents_dir: Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict:
    if platform.system() != "Darwin":
        raise DeployError("non_darwin")
    domain = f"gui/{os.getuid()}"
    label = RETRY_LAUNCHD_LABEL
    target = f"{domain}/{label}"
    agents_dir = inspect_directory_path(
        launch_agents_dir or Path.home() / "Library" / "LaunchAgents"
    )
    plist_path = agents_dir / f"{label}.plist"
    launchd_snapshot = retry_launchd_state(runner, domain, label)
    file_snapshot = snapshot_owner_file(plist_path)
    signals_restore = block_local_termination_signals()
    try:
        if launchctl_result(runner, ["disable", target]).returncode != 0:
            raise DeployError("retry_launchd_failed")
        if launchd_snapshot["loaded"]:
            if launchctl_result(runner, ["bootout", target]).returncode != 0:
                raise DeployError("retry_launchd_failed")
        observed = retry_launchd_state(runner, domain, label)
        if observed != {"enabled": False, "loaded": False}:
            raise DeployError("retry_launchd_failed")
    except BaseException as original:
        try:
            restore_retry_launchd(
                runner,
                domain,
                label,
                plist_path,
                file_snapshot,
                launchd_snapshot,
            )
        except BaseException as restore_error:
            raise DeployError("retry_launchd_restore_failed") from restore_error
        if isinstance(original, DeployError):
            raise original
        if not isinstance(original, Exception):
            raise original
        raise DeployError("retry_launchd_failed") from original
    finally:
        signals_restore()
    return {
        "label": RETRY_LAUNCHD_LABEL,
        "disabled": True,
        "loaded": False,
        "recurring": False,
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    run_command = commands.add_parser("run")
    run_command.add_argument("--config", required=True)
    install_command = commands.add_parser("install-launchd")
    install_command.add_argument("--config", required=True)
    install_command.add_argument("--launch-agents-dir")
    install_command.add_argument("--logs-dir")
    install_command.add_argument("--no-load", action="store_true")
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        config_path = Path(args.config)
        config = load_deploy_config(config_path)
        if args.command == "run":
            result = deploy_once(config, SshTransport(config))
            if result["status"] == "remote_deployed":
                try:
                    retry_launchd = disable_retry_launchd()
                except DeployError as error:
                    result["status"] = "failed"
                    result["stage"] = "disable_retry_launchd"
                    result["errors"] = [{"code": error.code}]
                    result["retry_launchd"] = {
                        "disabled": False,
                        "loaded": None,
                        "recurring": None,
                    }
                else:
                    result["status"] = "deployed"
                    result["retry_launchd"] = retry_launchd
            print(json.dumps(result, sort_keys=True, separators=(",", ":")))
            return 0 if result["status"] in {"deployed", "offline_retryable"} else 1
        result = install_retry_launchd(
            config_path,
            config,
            launch_agents_dir=Path(args.launch_agents_dir) if args.launch_agents_dir else None,
            logs_dir=Path(args.logs_dir) if args.logs_dir else None,
            load=not args.no_load,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except DeployError as error:
        print(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "status": "failed",
                    "retryable": False,
                    "errors": [{"code": error.code}],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1
    except Exception:
        print(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "status": "failed",
                    "retryable": False,
                    "errors": [{"code": "unexpected_failure"}],
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
