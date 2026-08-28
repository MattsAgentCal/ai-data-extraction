#!/usr/bin/env python3
"""Read Hermes' supported `sessions export --format jsonl --redact` output."""

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

_RETAINED_ROW_KEYS = {
    "id", "source", "cwd", "started_at", "ended_at", "title",
    "model", "provider", "agent_id", "session_type", "messages", "events",
}
_IGNORED_ROW_STRING_KEYS = {
    "billing_base_url", "billing_mode", "billing_provider", "chat_id", "chat_type",
    "compression_failure_error", "cost_source", "cost_status", "display_name",
    "end_reason", "git_branch", "git_repo_root", "handoff_error", "handoff_platform",
    "handoff_state", "last_activity_description", "last_activity_provenance",
    "model_config", "origin_json", "parent_session_id", "pricing_version",
    "profile_name", "session_key", "system_prompt", "system_prompt_hash", "thread_id",
    "title_source", "user_id",
}
_IGNORED_ROW_NUMBER_KEYS = {
    "actual_cost_usd", "compression_failure_cooldown_until", "estimated_cost_usd",
    "last_active", "last_activity_at", "last_read_at",
}
_IGNORED_ROW_INTEGER_KEYS = {
    "api_call_count", "archived", "cache_read_tokens", "cache_write_tokens",
    "compression_fallback_streak", "compression_ineffective_count", "expiry_finalized",
    "git_metadata_generation", "hidden", "input_tokens", "message_count",
    "output_tokens", "pinned", "reasoning_tokens", "rewind_count", "tool_call_count",
}
_IGNORED_ROW_KEYS = (
    _IGNORED_ROW_STRING_KEYS | _IGNORED_ROW_NUMBER_KEYS | _IGNORED_ROW_INTEGER_KEYS
)

_RETAINED_MESSAGE_KEYS = {
    "id", "role", "content", "created_at", "timestamp", "is_meta", "tool_name", "name",
}
_IGNORED_MESSAGE_STRING_KEYS = {
    "api_content", "codex_message_items", "codex_reasoning_items", "display_kind",
    "effect_disposition", "finish_reason", "platform_message_id", "reasoning",
    "reasoning_content", "reasoning_details", "session_id", "tool_call_id",
}
_IGNORED_MESSAGE_INTEGER_KEYS = {"active", "compacted", "observed", "token_count"}
_IGNORED_MESSAGE_KEYS = (
    _IGNORED_MESSAGE_STRING_KEYS
    | _IGNORED_MESSAGE_INTEGER_KEYS
    | {"display_metadata", "tool_calls"}
)


def _known_extension_types_are_valid(
    value, *, string_keys=frozenset(), number_keys=frozenset(), integer_keys=frozenset()
):
    return (
        all(
            key not in value or type(value[key]) in {str, type(None)}
            for key in string_keys
        )
        and all(
            key not in value or type(value[key]) in {int, float, type(None)}
            for key in number_keys
        )
        and all(
            key not in value or type(value[key]) in {int, type(None)}
            for key in integer_keys
        )
    )


def _retained_message_types_are_valid(message):
    return (
        (
            "id" not in message
            or message["id"] is None
            or type(message["id"]) in {str, int}
        )
        and all(
            key not in message
            or message[key] is None
            or type(message[key]) in {str, int, float}
            for key in ("created_at", "timestamp")
        )
        and (
            "is_meta" not in message
            or message["is_meta"] is None
            or type(message["is_meta"]) in {bool, int}
        )
        and all(
            key not in message
            or message[key] is None
            or type(message[key]) is str
            for key in ("name", "tool_name")
        )
    )


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
                if (
                    not isinstance(row, dict)
                    or type(row.get("id")) not in {str, int}
                    or not str(row["id"])
                    or set(row) - (_RETAINED_ROW_KEYS | _IGNORED_ROW_KEYS)
                    or not _known_extension_types_are_valid(
                        row,
                        string_keys=_IGNORED_ROW_STRING_KEYS,
                        number_keys=_IGNORED_ROW_NUMBER_KEYS,
                        integer_keys=_IGNORED_ROW_INTEGER_KEYS,
                    )
                ):
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
                    if (
                        not isinstance(message, dict)
                        or set(message) - (_RETAINED_MESSAGE_KEYS | _IGNORED_MESSAGE_KEYS)
                        or not _known_extension_types_are_valid(
                            message,
                            string_keys=_IGNORED_MESSAGE_STRING_KEYS,
                            integer_keys=_IGNORED_MESSAGE_INTEGER_KEYS,
                        )
                        or not _retained_message_types_are_valid(message)
                        or (
                            "tool_calls" in message
                            and type(message["tool_calls"]) not in {list, type(None)}
                        )
                        or (
                            "display_metadata" in message
                            and type(message["display_metadata"])
                            not in {dict, type(None)}
                        )
                        or (
                            "session_id" in message
                            and message["session_id"] != str(row["id"])
                        )
                    ):
                        valid_messages = False
                        break
                    retained_message = {
                        key: message[key]
                        for key in _RETAINED_MESSAGE_KEYS
                        if key in message
                    }
                    role = retained_message.get("role")
                    content = retained_message.get("content")
                    if role == "session_meta" and content is None:
                        auxiliary_events.append(
                            event_envelope(
                                "session_meta",
                                {
                                    key: value
                                    for key, value in retained_message.items()
                                    if key not in {"role", "timestamp"}
                                },
                                retained_message.get(
                                    "timestamp", retained_message.get("created_at")
                                ),
                            )
                        )
                    elif role in valid_roles and isinstance(content, (str, list)):
                        normalized = {"role": role, "content": content}
                        if "id" in retained_message:
                            normalized["message_id"] = retained_message["id"]
                        timestamp = retained_message.get(
                            "timestamp", retained_message.get("created_at")
                        )
                        if (
                            "timestamp" in retained_message
                            or "created_at" in retained_message
                        ):
                            normalized["timestamp"] = timestamp
                        if "tool_name" in retained_message:
                            normalized["tool_name"] = retained_message["tool_name"]
                        chat_messages.append(normalized)
                    else:
                        valid_messages = False
                        break
                if not valid_messages:
                    failed_lines += 1
                    continue
                parsed_lines += 1
                existing_events = row.get("events", [])
                if not isinstance(existing_events, list) or any(
                    not isinstance(event, dict)
                    or not isinstance(event.get("type"), str)
                    or not event.get("type")
                    for event in existing_events
                ):
                    failed_lines += 1
                    parsed_lines -= 1
                    continue
                normalized_events = [
                    event_envelope(
                        event["type"],
                        {key: value for key, value in event.items() if key not in {"type", "timestamp"}},
                        event.get("timestamp"),
                    )
                    for event in existing_events
                ]
                conversation = {
                    "archive_schema_version": ARCHIVE_OBJECT_SCHEMA_VERSION,
                    "messages": chat_messages,
                    "native_source": row.get("source"),
                    "source": "hermes",
                    "session_id": str(row["id"]),
                    "source_schema": "hermes-sessions-export-jsonl-v1",
                }
                for key in (
                    "cwd", "started_at", "ended_at", "title", "model", "provider",
                    "agent_id", "session_type",
                ):
                    if key in row:
                        conversation[key] = row[key]
                if auxiliary_events:
                    normalized_events.extend(auxiliary_events)
                if normalized_events:
                    conversation["events"] = normalized_events
                yield validate_archive_object(conversation, harness="hermes")
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
