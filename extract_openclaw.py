#!/usr/bin/env python3
"""Extract OpenClaw v3 JSONL sessions without reading credential databases."""

import json
import errno
import os
import stat
from pathlib import Path

from archive_object_contract import (
    ARCHIVE_OBJECT_SCHEMA_VERSION,
    event_envelope,
    validate_archive_object,
)


_MACOS_COMPATIBILITY_SYMLINKS = {
    Path("/etc"): Path("/private/etc"),
    Path("/tmp"): Path("/private/tmp"),
    Path("/var"): Path("/private/var"),
}


def _is_macos_compatibility_symlink(path):
    expected = _MACOS_COMPATIBILITY_SYMLINKS.get(path)
    return expected is not None and path.resolve(strict=True) == expected


def _reject_symlink_components(path):
    expanded = Path(path).expanduser()
    absolute = Path(os.path.abspath(os.fspath(expanded)))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink() and not _is_macos_compatibility_symlink(current):
            raise ValueError(f"refusing symlink path component: {current}")
    return expanded


def _metadata_identity(metadata):
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _matches_expected_fingerprint(metadata, expected_fingerprint):
    attributes = {
        "size": "st_size",
        "mtime_ns": "st_mtime_ns",
        "ctime_ns": "st_ctime_ns",
        "inode": "st_ino",
    }
    return expected_fingerprint is None or all(
        getattr(metadata, attribute) == expected_fingerprint[key]
        for key, attribute in attributes.items()
    )


def _open_regular_jsonl(path, *, max_bytes=None, expected_fingerprint=None):
    path = _reject_symlink_components(path)
    absolute = Path(os.path.abspath(os.fspath(path)))
    for alias, target in _MACOS_COMPATIBILITY_SYMLINKS.items():
        try:
            absolute = target / absolute.relative_to(alias)
            break
        except ValueError:
            continue
    directory_fd = os.open(
        absolute.anchor,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        for part in absolute.parts[1:-1]:
            next_fd = os.open(
                part,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd
        descriptor = os.open(
            absolute.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ValueError(f"refusing symlinked JSONL file: {path}") from exc
        raise
    finally:
        os.close(directory_fd)
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise ValueError(f"JSONL input must be a regular file: {path}")
    if max_bytes is not None and metadata.st_size > max_bytes:
        os.close(descriptor)
        raise ValueError(f"source exceeds maximum of {max_bytes} bytes: {path}")
    if not _matches_expected_fingerprint(metadata, expected_fingerprint):
        os.close(descriptor)
        raise ValueError(f"source changed before extraction: {path}")
    return os.fdopen(descriptor, "r", encoding="utf-8"), metadata


def _verify_parsed_source(handle, path, initial_metadata, max_bytes):
    final_metadata = os.fstat(handle.fileno())
    if (
        _metadata_identity(initial_metadata) != _metadata_identity(final_metadata)
        or (max_bytes is not None and final_metadata.st_size > max_bytes)
        or os.lseek(handle.fileno(), 0, os.SEEK_CUR) != final_metadata.st_size
    ):
        raise ValueError(f"source changed during extraction: {path}")


def _quality(discovered_lines, parsed_lines, failed_lines):
    return {
        "status": "partial" if failed_lines else "complete",
        "discovered_lines": discovered_lines,
        "parsed_lines": parsed_lines,
        "failed_lines": failed_lines,
    }


def _publish_quality(quality_out, value):
    if quality_out is not None:
        quality_out.clear()
        quality_out.update(value)


def extract_openclaw_session(
    session_file,
    *,
    quality_out=None,
    max_source_bytes=None,
    expected_fingerprint=None,
):
    messages = []
    tool_results = []
    session_meta = {}
    discovered_lines = 0
    parsed_lines = 0
    failed_lines = 0
    recognized_lines = 0
    session_version_valid = False

    source_handle, source_metadata = _open_regular_jsonl(
        session_file,
        max_bytes=max_source_bytes,
        expected_fingerprint=expected_fingerprint,
    )
    with source_handle as handle:
        for line in handle:
            if not line.strip():
                continue
            discovered_lines += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                failed_lines += 1
                continue
            if not isinstance(row, dict):
                failed_lines += 1
                continue
            parsed_lines += 1

            if row.get("type") == "session":
                recognized_lines += 1
                session_meta = row
                session_version_valid = row.get("version") == 3
                if not session_version_valid:
                    failed_lines += 1
                continue
            if row.get("type") != "message":
                failed_lines += 1
                continue
            if not isinstance(row.get("message"), dict):
                failed_lines += 1
                continue
            recognized_lines += 1

            native = row["message"]
            role = native.get("role")
            content = native.get("content")
            if role not in {"user", "assistant", "system", "developer", "toolResult"}:
                failed_lines += 1
                recognized_lines -= 1
                continue
            if not isinstance(content, (str, list)):
                failed_lines += 1
                recognized_lines -= 1
                continue
            timestamp = row.get("timestamp") or native.get("timestamp")
            text_parts = []

            if isinstance(content, str):
                if role in {"user", "assistant", "system", "developer"}:
                    text_parts.append(content)
                elif role == "toolResult":
                    tool_results.append(
                        event_envelope(
                            "toolResult",
                            {
                                "tool": native.get("toolName"),
                                "content": content,
                                "message_id": row.get("id"),
                            },
                            timestamp,
                        )
                    )
            elif isinstance(content, list):
                if any(not isinstance(item, dict) for item in content):
                    failed_lines += 1
                    recognized_lines -= 1
                    continue
                for item in content:
                    if item.get("type") == "text" and isinstance(item.get("text"), str):
                        if role == "toolResult":
                            tool_results.append(
                                event_envelope(
                                    "toolResult",
                                    {
                                        "tool": native.get("toolName"),
                                        "content": item["text"],
                                        "message_id": row.get("id"),
                                    },
                                    timestamp,
                                )
                            )
                        else:
                            text_parts.append(item["text"])
                    else:
                        event_type = item.get("type")
                        if not isinstance(event_type, str) or not event_type:
                            failed_lines += 1
                            recognized_lines -= 1
                            text_parts = []
                            tool_results = tool_results[:]
                            break
                        tool_results.append(
                            event_envelope(
                                event_type,
                                {
                                    "message_id": row.get("id"),
                                    "content": {
                                        key: value for key, value in item.items() if key != "type"
                                    },
                                },
                                timestamp,
                            )
                        )

            if role in {"user", "assistant", "system", "developer"} and text_parts:
                messages.append({
                    "role": role,
                    "content": "\n".join(text_parts),
                    "message_id": row.get("id"),
                    "timestamp": timestamp,
                })
        _verify_parsed_source(
            handle,
            session_file,
            source_metadata,
            max_source_bytes,
        )

    quality = _quality(discovered_lines, parsed_lines, failed_lines)
    if discovered_lines and (not recognized_lines or not session_version_valid):
        quality["status"] = "partial"
    quality["recognized_lines"] = recognized_lines
    quality["discovered_files"] = 1
    _publish_quality(quality_out, quality)
    if not session_version_valid or (not messages and not tool_results):
        return None
    conversation = {
        "archive_schema_version": ARCHIVE_OBJECT_SCHEMA_VERSION,
        "messages": messages,
        "session_id": session_meta.get("id") or session_file.stem,
        "cwd": session_meta.get("cwd"),
        "source": "openclaw",
        "session_file": str(session_file),
        "timestamp": session_meta.get("timestamp"),
        "source_schema": f"openclaw-jsonl-v{session_meta.get('version', 'unknown')}",
    }
    if tool_results:
        conversation["tool_results"] = tool_results
    return validate_archive_object(conversation, harness="openclaw")


def find_all_openclaw_sessions(root):
    root = _reject_symlink_components(root)
    if not root.exists():
        return []
    if not root.is_dir():
        raise ValueError(f"input root must be a directory: {root}")

    session_files = []
    for current, directory_names, file_names in os.walk(root, followlinks=False):
        directory_names.sort()
        file_names.sort()
        current_path = Path(current)
        directory_names[:] = [
            name for name in directory_names if not (current_path / name).is_symlink()
        ]
        for name in file_names:
            candidate = current_path / name
            if candidate.is_symlink() and candidate.suffix == ".jsonl":
                raise ValueError(f"refusing symlink in input root: {candidate}")
            if candidate.suffix == ".jsonl" and not candidate.name.endswith(
                ".trajectory.jsonl"
            ):
                session_files.append(candidate)
    return session_files
