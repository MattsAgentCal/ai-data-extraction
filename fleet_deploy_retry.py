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
    "extract_claude_code.py",
    "extract_codex.py",
    "extract_openclaw.py",
    "extract_hermes.py",
)
REVIEWED_RUNTIME_SHA256 = {
    "fleet_chat_archive.py": "dfadf89647326fda657412f38e84055ece5cd5dd55e6ab7fbfd9010c2e253459",
    "extract_claude_code.py": "3b6d311a208574f51813699b0885586d97cfc61c7e01a9dda6e97d09be6e9328",
    "extract_codex.py": "50a34f07cc5abc4d19565bd855754c59fffe1165631f6c6c6cd2e6b3869d62e8",
    "extract_openclaw.py": "a13ef9e016fa8b49a6839098377d95f16f1cd6b0f82896c9585de5e9316c2c1d",
    "extract_hermes.py": "5d3211855af039e5caf660c2929831a9d460394866fe8b2eefcabcc8e65e372a",
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


def set_lock(deploy_descriptor, checked_id, relative_paths, state):
    mutex = acquire_mutex(deploy_descriptor)
    try:
        current, _ = read_lock(deploy_descriptor)
        if current is None or current["deployment_id"] != checked_id:
            fail("deployment_lock_mismatch")
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
    finally:
        release_mutex(mutex)


def acquire_lock(deploy_descriptor, checked_id, relative_paths):
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
            os.unlink(LOCK_FILE, dir_fd=deploy_descriptor)
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


def cleanup_stages(stages_descriptor):
    entries = os.listdir(stages_descriptor)
    if len(entries) > MAX_STALE_STAGES:
        fail("stale_stage_limit")
    for entry in entries:
        if not STAGE_RE.fullmatch(entry):
            fail("unsafe_path")
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
        acquire_lock(deploy_descriptor, checked_id, checked_paths)
        lock_acquired = True
        cleanup_stages(stages_descriptor)
        os.mkdir(stage_name, 0o700, dir_fd=stages_descriptor)
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


def quiesce_archive(request):
    label = archive_label(request)
    target = "gui/{}/{}".format(os.getuid(), label)
    was_loaded = launchctl(["print", target]).returncode == 0
    disabled = False
    try:
        if launchctl(["disable", target]).returncode != 0:
            fail("archive_quiesce_failed")
        disabled = True
        bootout = launchctl(["bootout", target])
        if was_loaded and bootout.returncode != 0:
            fail("archive_quiesce_failed")
        if launchctl(["print", target]).returncode == 0:
            fail("archive_still_loaded")
        return was_loaded
    except BaseException:
        if disabled:
            launchctl(["enable", target])
        raise


def enable_archive(request):
    label = archive_label(request)
    target = "gui/{}/{}".format(os.getuid(), label)
    if launchctl(["enable", target]).returncode != 0:
        fail("archive_launchd_failed")


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
    if launchctl(["print", target]).returncode != 0:
        fail("archive_launchd_failed")
    return launchd


def read_target(parent_descriptor, name):
    try:
        payload, metadata = read_regular_at(parent_descriptor, name, limit=MAX_UPLOAD_BYTES)
    except FileNotFoundError:
        return None
    return payload, stat.S_IMODE(metadata.st_mode)


def restore_targets(targets, backups, replaced):
    for relative in reversed(replaced):
        parent_descriptor, name, _ = targets[relative]
        backup = backups[relative]
        if backup is None:
            try:
                metadata = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                fail("rollback_failed")
            os.unlink(name, dir_fd=parent_descriptor)
        else:
            payload, mode = backup
            write_regular_at(parent_descriptor, name, payload, mode)


def block_termination_signals():
    if hasattr(signal, "pthread_sigmask"):
        signals = {signal.SIGTERM, signal.SIGINT, signal.SIGHUP}
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, signals)
        return lambda: signal.pthread_sigmask(signal.SIG_SETMASK, previous)
    return lambda: None


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
    backups = {}
    replaced = []
    was_loaded = False
    signals_restore = lambda: None
    try:
        expected = expected_hashes(request)
        if set(expected) != set(lock["relative_paths"]):
            fail("schema_drift")
        verify_paths_descriptor(stage_descriptor, expected)
        home_descriptor = open_absolute_directory(lexical_absolute(request["expected_remote_home"]))
        try:
            repo_relative = repo.relative_to(lexical_absolute(request["expected_remote_home"]))
            repo_descriptor = open_directory_below(
                home_descriptor, repo_relative.parts, create=False
            )
        finally:
            os.close(home_descriptor)

        for relative in sorted(expected):
            path = PurePosixPath(relative)
            parent_descriptor = open_directory_below(
                repo_descriptor, path.parent.parts, create=True, owner_only=True
            )
            targets[relative] = (
                parent_descriptor,
                path.name,
                0o700 if relative.endswith(".py") else 0o600,
            )
            backups[relative] = read_target(parent_descriptor, path.name)
            source_descriptor = open_relative_file(stage_descriptor, relative)
            temporary = "." + path.name + ".fleet-tmp-" + uuid.uuid4().hex
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

        was_loaded = quiesce_archive(request)
        signals_restore = block_termination_signals()
        try:
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
                replaced.append(relative)
            actual = verify_paths_descriptor(repo_descriptor, expected)
            launchd = install_archive_job(request, repo)
        except BaseException as original:
            recovery_failed = False
            try:
                try:
                    quiesce_archive(request)
                except BaseException:
                    pass
                restore_targets(targets, backups, replaced)
                for relative, backup in backups.items():
                    parent_descriptor, name, _ = targets[relative]
                    if backup is None:
                        try:
                            os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
                        except FileNotFoundError:
                            continue
                        fail("rollback_failed")
                    payload, _ = backup
                    current, _ = read_regular_at(parent_descriptor, name, limit=MAX_UPLOAD_BYTES)
                    if current != payload:
                        fail("rollback_failed")
                if was_loaded:
                    install_archive_job(request, repo)
                else:
                    enable_archive(request)
            except BaseException:
                recovery_failed = True
            if recovery_failed:
                set_lock(
                    deploy_descriptor,
                    deployment_id(request),
                    lock["relative_paths"],
                    "rollback_failed",
                )
                fail("rollback_failed")
            if isinstance(original, ControlledFailure):
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
    repo = checked_repo(request)
    deploy_root, deploy_descriptor, stages_descriptor = open_deploy_directories(
        request, create=False
    )
    repo_descriptor = -1
    try:
        require_lock(deploy_descriptor, request)
        home = lexical_absolute(request["expected_remote_home"])
        home_descriptor = open_absolute_directory(home)
        try:
            repo_descriptor = open_directory_below(
                home_descriptor, repo.relative_to(home).parts, create=False
            )
        finally:
            os.close(home_descriptor)
        actual = verify_paths_descriptor(repo_descriptor, expected_hashes(request))
        return {"schema_version": 1, "ok": True, "sha256": actual}
    finally:
        close_descriptors(repo_descriptor, stages_descriptor, deploy_descriptor)


def finish(request):
    (
        stage_path,
        deploy_descriptor,
        stages_descriptor,
        stage_descriptor,
        lock,
    ) = stage_details(request)
    try:
        mutex = acquire_mutex(deploy_descriptor)
        try:
            require_lock(deploy_descriptor, request)
            os.close(stage_descriptor)
            stage_descriptor = -1
            remove_tree_at(stages_descriptor, stage_path.name, [0])
            os.unlink(LOCK_FILE, dir_fd=deploy_descriptor)
        finally:
            release_mutex(mutex)
        return {"schema_version": 1, "ok": True, "finished": True}
    finally:
        close_descriptors(stage_descriptor, stages_descriptor, deploy_descriptor)


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
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise DeployError("unsafe_path")
        os.replace(temporary, path)
        os.chmod(path, 0o600)
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
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise DeployError("unsafe_path")
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


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
    agents_dir = secure_directory(
        launch_agents_dir or Path.home() / "Library" / "LaunchAgents"
    )
    logs_root = secure_directory(
        logs_dir or Path.home() / "Library" / "Logs" / "AIChatArchiveDeploy"
    )
    label = RETRY_LAUNCHD_LABEL
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
    atomic_owner_write(plist_path, plistlib.dumps(job, fmt=plistlib.FMT_XML))
    loaded = False
    if load:
        domain = f"gui/{os.getuid()}"
        runner(
            ["launchctl", "bootout", f"{domain}/{label}"],
            text=True,
            capture_output=True,
            check=False,
        )
        enabled = runner(
            ["launchctl", "enable", f"{domain}/{label}"],
            text=True,
            capture_output=True,
            check=False,
        )
        if enabled.returncode != 0:
            raise DeployError("retry_launchd_failed")
        completed = runner(
            ["launchctl", "bootstrap", domain, str(plist_path)],
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise DeployError("retry_launchd_failed")
        printed = runner(
            ["launchctl", "print", f"{domain}/{label}"],
            text=True,
            capture_output=True,
            check=False,
        )
        if printed.returncode != 0:
            raise DeployError("retry_launchd_failed")
        loaded = True
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
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict:
    if platform.system() != "Darwin":
        raise DeployError("non_darwin")
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{RETRY_LAUNCHD_LABEL}"
    initially_loaded = runner(
        ["launchctl", "print", target],
        text=True,
        capture_output=True,
        check=False,
    ).returncode == 0
    disabled = runner(
        ["launchctl", "disable", target],
        text=True,
        capture_output=True,
        check=False,
    )
    if disabled.returncode != 0:
        raise DeployError("retry_launchd_failed")
    disabled_state = runner(
        ["launchctl", "print-disabled", domain],
        text=True,
        capture_output=True,
        check=False,
    )
    disabled_pattern = re.compile(
        rf'"?{re.escape(RETRY_LAUNCHD_LABEL)}"?\s*=>\s*true(?:\s|$)'
    )
    if disabled_state.returncode != 0 or not disabled_pattern.search(
        disabled_state.stdout or ""
    ):
        raise DeployError("retry_launchd_failed")

    previous_sigterm = signal.getsignal(signal.SIGTERM)
    try:
        signal.signal(signal.SIGTERM, lambda _signum, _frame: None)
        if initially_loaded:
            bootout = runner(
                ["launchctl", "bootout", target],
                text=True,
                capture_output=True,
                check=False,
            )
            if bootout.returncode != 0:
                raise DeployError("retry_launchd_failed")
        printed = runner(
            ["launchctl", "print", target],
            text=True,
            capture_output=True,
            check=False,
        )
        if printed.returncode == 0:
            raise DeployError("retry_launchd_failed")
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
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
