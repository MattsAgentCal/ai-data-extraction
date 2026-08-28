#!/usr/bin/env python3
"""Read Hermes' supported `sessions export --format jsonl --redact` output."""

import json
import errno
import os
import stat
from pathlib import Path


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


def iter_hermes_export(
    export_file,
    *,
    quality_out=None,
    max_source_bytes=None,
    expected_fingerprint=None,
):
    discovered_lines = 0
    parsed_lines = 0
    failed_lines = 0
    try:
        source_handle, source_metadata = _open_regular_jsonl(
            export_file,
            max_bytes=max_source_bytes,
            expected_fingerprint=expected_fingerprint,
        )
        with source_handle as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                discovered_lines += 1
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    failed_lines += 1
                    continue
                if not isinstance(row, dict) or not row.get("id"):
                    failed_lines += 1
                    continue
                if not isinstance(row.get("messages"), list):
                    failed_lines += 1
                    continue
                valid_roles = {
                    "user",
                    "assistant",
                    "system",
                    "developer",
                    "tool",
                    "toolResult",
                }
                chat_messages = []
                auxiliary_events = []
                valid_messages = True
                for message in row["messages"]:
                    if not isinstance(message, dict):
                        valid_messages = False
                        break
                    role = message.get("role")
                    content = message.get("content")
                    if role == "session_meta" and content is None:
                        auxiliary_events.append(message)
                    elif role in valid_roles and isinstance(content, (str, list)):
                        chat_messages.append(message)
                    else:
                        valid_messages = False
                        break
                if not valid_messages:
                    failed_lines += 1
                    continue
                parsed_lines += 1
                conversation = dict(row)
                conversation["messages"] = chat_messages
                if auxiliary_events:
                    existing_events = conversation.get("events")
                    if existing_events is None:
                        conversation["events"] = auxiliary_events
                    elif isinstance(existing_events, list):
                        conversation["events"] = [*existing_events, *auxiliary_events]
                    else:
                        failed_lines += 1
                        parsed_lines -= 1
                        continue
                conversation["native_source"] = row.get("source")
                conversation["source"] = "hermes"
                conversation["session_id"] = row["id"]
                conversation["source_schema"] = "hermes-sessions-export-jsonl-v1"
                yield conversation
            _verify_parsed_source(
                handle,
                export_file,
                source_metadata,
                max_source_bytes,
            )
    finally:
        quality = _quality(discovered_lines, parsed_lines, failed_lines)
        quality["recognized_lines"] = parsed_lines
        quality["discovered_files"] = 1
        if quality_out is not None:
            quality_out.clear()
            quality_out.update(quality)


def extract_hermes_export(
    export_file,
    *,
    quality_out=None,
    max_source_bytes=None,
    expected_fingerprint=None,
):
    return list(
        iter_hermes_export(
            export_file,
            quality_out=quality_out,
            max_source_bytes=max_source_bytes,
            expected_fingerprint=expected_fingerprint,
        )
    )
