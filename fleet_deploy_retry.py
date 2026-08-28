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
REMOTE_CONFIG_RELATIVE_PATH = "configs/old-macbook.json"
INVENTORY_HARNESSES = ("claude", "codex", "openclaw", "hermes")
RUNTIME_FILES = (
    "fleet_chat_archive.py",
    "extract_claude_code.py",
    "extract_codex.py",
    "extract_openclaw.py",
    "extract_hermes.py",
)
REVIEWED_RUNTIME_SHA256 = {
    "fleet_chat_archive.py": "279635cfdbf689e45698cb139d9a2b3f7308e024e618e45deda002f225711d43",
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
import hashlib
import json
import os
import platform
import pwd
import re
import stat
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path, PurePosixPath

RELATIVE_RE = re.compile(r"(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+\Z")
STAGE_RE = re.compile(r"\.fleet-deploy-[A-Za-z0-9._-]+\Z")
SHA_RE = re.compile(r"[0-9a-f]{64}\Z")


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


def make_directories(path, trusted_root):
    path = lexical_absolute(str(path))
    trusted_root = lexical_absolute(str(trusted_root))
    ensure_below(path, trusted_root)
    if not inspect_components(trusted_root, allow_missing=False):
        fail("unsafe_path")
    current = trusted_root
    relative = path.relative_to(trusted_root)
    for part in relative.parts:
        current = current / part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            os.mkdir(current, 0o700)
            metadata = os.lstat(current)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            fail("unsafe_path")
    return path


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
        make_directories(repo, home)
    else:
        inspect_components(repo, allow_missing=False)
        if not safe_directory(repo):
            fail("unsafe_path")
    return repo


def prepare(request):
    repo = checked_repo(request, create=True)
    relative_paths = request.get("relative_paths")
    if not isinstance(relative_paths, list) or len(relative_paths) != len(set(relative_paths)):
        fail("schema_drift")
    for value in relative_paths:
        safe_relative(value)
    stage = Path(tempfile.mkdtemp(prefix=".fleet-deploy-", dir=repo))
    os.chmod(stage, 0o700)
    for value in relative_paths:
        parent = stage / PurePosixPath(value).parent
        make_directories(parent, stage)
    return {"schema_version": 1, "ok": True, "stage_path": str(stage)}


def checked_stage(request, repo):
    stage = lexical_absolute(request.get("stage_path"))
    if stage.parent != repo or not STAGE_RE.fullmatch(stage.name):
        fail("unsafe_path")
    inspect_components(stage, allow_missing=False)
    if not safe_directory(stage):
        fail("unsafe_path")
    return stage


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


def hash_file(path):
    if not safe_file(path):
        fail("remote_file_missing")
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            fail("unsafe_path")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def verify_paths(base, expected):
    actual = {}
    for relative, wanted in sorted(expected.items()):
        candidate = base / PurePosixPath(relative)
        ensure_below(candidate, base)
        digest = hash_file(candidate)
        if digest != wanted:
            fail("hash_mismatch")
        actual[relative] = digest
    return actual


def verify_stage(request):
    repo = checked_repo(request)
    stage = checked_stage(request, repo)
    actual = verify_paths(stage, expected_hashes(request))
    return {"schema_version": 1, "ok": True, "sha256": actual}


def install_one(source, target, mode):
    if target.exists() or target.is_symlink():
        metadata = os.lstat(target)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            fail("unsafe_path")
    temporary = target.parent / (
        "." + target.name + ".fleet-tmp-" + uuid.uuid4().hex
    )
    source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    target_fd = -1
    try:
        if not stat.S_ISREG(os.fstat(source_fd).st_mode):
            fail("unsafe_path")
        target_fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            mode,
        )
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            offset = 0
            while offset < len(chunk):
                offset += os.write(target_fd, chunk[offset:])
        os.fsync(target_fd)
        os.fchmod(target_fd, mode)
        os.close(target_fd)
        target_fd = -1
        os.replace(temporary, target)
    finally:
        os.close(source_fd)
        if target_fd >= 0:
            os.close(target_fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def install_files(request):
    repo = checked_repo(request)
    stage = checked_stage(request, repo)
    expected = expected_hashes(request)
    verify_paths(stage, expected)
    install_order = sorted(
        expected,
        key=lambda relative: (not relative.endswith(".py"), relative),
    )
    for relative in install_order:
        source = stage / PurePosixPath(relative)
        target = repo / PurePosixPath(relative)
        make_directories(target.parent, repo)
        install_one(source, target, 0o700 if relative.endswith(".py") else 0o600)
    actual = verify_paths(repo, expected)
    for relative in sorted(expected, reverse=True):
        os.unlink(stage / PurePosixPath(relative))
    stage_directories = {
        (stage / PurePosixPath(relative)).parent
        for relative in expected
    }
    for directory in sorted(stage_directories, key=lambda item: len(item.parts), reverse=True):
        if directory != stage:
            os.rmdir(directory)
    os.rmdir(stage)
    return {"schema_version": 1, "ok": True, "sha256": actual}


def verify_final(request):
    repo = checked_repo(request)
    actual = verify_paths(repo, expected_hashes(request))
    return {"schema_version": 1, "ok": True, "sha256": actual}


def install_archive_launchd(request):
    repo = checked_repo(request)
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
    if not isinstance(launchd, dict):
        fail("archive_launchd_failed")
    return {"schema_version": 1, "ok": True, "launchd": launchd}


def main():
    try:
        request = json.loads(base64.b64decode(REQUEST_B64, validate=True))
        if not isinstance(request, dict) or request.get("schema_version") != 1:
            fail("schema_drift")
        action = request.get("action")
        handlers = {
            "inventory": inventory,
            "prepare": prepare,
            "verify_stage": verify_stage,
            "install_files": install_files,
            "verify_final": verify_final,
            "install_archive_launchd": install_archive_launchd,
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


class SshTransport:
    """SSH/SCP transport whose output is never forwarded to the status receipt."""

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
                "remote_helper_failure",
            }
            raise DeployError(code if code in allowed else "remote_helper_failure")
        return response

    def copy_file(self, local_path: Path, remote_path: str) -> None:
        remote_path = validate_remote_path(remote_path, below=self.config.remote_repo_root)
        if local_path.is_symlink() or not local_path.is_file():
            raise DeployError("local_runtime_missing")
        command = [
            "scp",
            "-q",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=8",
            "--",
            str(local_path),
            f"{self.config.ssh_host}:{remote_path}",
        ]
        try:
            completed = self.runner(
                command,
                text=True,
                capture_output=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise OfflineError() from error
        except OSError as error:
            raise DeployError("local_transport_error") from error
        self._raise_for_transport(completed, python_required=False)

    def install_archive_launchd(self, remote_script: str, remote_config: str) -> dict:
        remote_script = validate_remote_path(remote_script, below=self.config.remote_repo_root)
        remote_config = validate_remote_path(remote_config, below=self.config.remote_repo_root)
        response = self.helper(
            "install_archive_launchd",
            {
                "script_relative_path": str(
                    PurePosixPath(remote_script).relative_to(self.config.remote_repo_root)
                ),
                "config_relative_path": str(
                    PurePosixPath(remote_config).relative_to(self.config.remote_repo_root)
                ),
                "interval_seconds": self.config.archive_interval_seconds,
            },
        )
        if set(response) != {"schema_version", "ok", "launchd"}:
            raise DeployError("archive_launchd_failed")
        launchd = response.get("launchd")
        expected_label = f"com.mattrotundo.ai-chat-archive.{self.config.host_id}"
        if (
            not isinstance(launchd, dict)
            or set(launchd) != {"label", "loaded", "plist_path"}
            or launchd.get("label") != expected_label
            or launchd.get("loaded") is not True
        ):
            raise DeployError("archive_launchd_failed")
        validate_remote_path(
            launchd.get("plist_path"), below=self.config.expected_remote_home
        )
        return launchd


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
            prepared = transport.helper("prepare", {"relative_paths": list(expected_hashes)})
            if (
                not isinstance(prepared, dict)
                or set(prepared) != {"schema_version", "ok", "stage_path"}
            ):
                raise DeployError("remote_schema_drift")
            if prepared.get("schema_version") != SCHEMA_VERSION or prepared.get("ok") is not True:
                raise DeployError("remote_schema_drift")
            stage_path = validate_remote_path(
                prepared.get("stage_path"), below=config.remote_repo_root
            )

            stage = "copy"
            for item in files:
                destination = f"{stage_path}/{item.relative_path}"
                transport.copy_file(item.local_path, destination)

            stage = "verify_staged_hashes"
            staged = transport.helper(
                "verify_stage",
                {"stage_path": stage_path, "expected_sha256": expected_hashes},
            )
            validate_hash_response(staged, expected_hashes)

            stage = "install_files"
            installed = transport.helper(
                "install_files",
                {"stage_path": stage_path, "expected_sha256": expected_hashes},
            )
            validate_hash_response(installed, expected_hashes)

            stage = "verify_remote_hashes"
            final_hashes = transport.helper(
                "verify_final", {"expected_sha256": expected_hashes}
            )
            validate_hash_response(final_hashes, expected_hashes)

            stage = "install_archive_launchd"
            remote_script = f"{config.remote_repo_root}/fleet_chat_archive.py"
            remote_config = f"{config.remote_repo_root}/{config.remote_config_relative_path}"
            transport.install_archive_launchd(remote_script, remote_config)

        return {
            "schema_version": SCHEMA_VERSION,
            "host_id": config.host_id,
            "status": "deployed",
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
                "interval_seconds": config.archive_interval_seconds,
            },
            "errors": [],
        }
    except OfflineError as error:
        return failure_status(config, error.code, stage, inventory)
    except DeployError as error:
        return failure_status(config, error.code, stage, inventory)


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
    label = "com.mattrotundo.ai-chat-archive.old-macbook-deploy-retry"
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
        completed = runner(
            ["launchctl", "bootstrap", domain, str(plist_path)],
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
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
