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
    validate_json_bounds,
)


_MACOS_COMPATIBILITY_SYMLINKS = {
    Path("/etc"): Path("/private/etc"),
    Path("/tmp"): Path("/private/tmp"),
    Path("/var"): Path("/private/var"),
}

# Hermes 0.20 has two observed stored-column dialects. The newer dialect adds
# both metadata columns together; accepting either one alone would turn a
# partially migrated or future schema into trusted chat data. Unfiltered
# exports add the computed `last_active` search column, while filtered and
# single-session exports return the same exact stored columns without it.
HERMES_LEGACY_SESSION_KEYS = {
    "agent_id", "cwd", "ended_at", "events", "id", "messages", "model",
    "provider", "session_type", "source", "started_at", "title",
}
HERMES_V020_SESSION_KEYS = {
    "actual_cost_usd", "api_call_count", "archived",
    "billing_base_url", "billing_mode", "billing_provider",
    "cache_read_tokens", "cache_write_tokens", "chat_id", "chat_type",
    "compression_failure_cooldown_until", "compression_failure_error",
    "compression_fallback_streak", "compression_ineffective_count",
    "cost_source", "cost_status", "cwd", "display_name", "end_reason",
    "ended_at", "estimated_cost_usd", "expiry_finalized",
    "git_branch", "git_metadata_generation", "git_repo_root", "handoff_error",
    "handoff_platform", "handoff_state", "id", "input_tokens", "last_active",
    "last_activity_at", "last_activity_description",
    "last_activity_provenance", "last_read_at", "message_count", "messages",
    "model", "model_config", "origin_json", "output_tokens",
    "parent_session_id", "pinned", "pricing_version", "profile_name",
    "reasoning_tokens", "rewind_count", "session_key", "source", "started_at",
    "system_prompt", "system_prompt_hash", "thread_id", "title",
    "title_source", "tool_call_count", "user_id", "hidden",
}
HERMES_V020_BASE_SESSION_KEYS = HERMES_V020_SESSION_KEYS - {
    "git_metadata_generation", "hidden",
}
HERMES_V020_FILTERED_SESSION_KEYS = HERMES_V020_SESSION_KEYS - {"last_active"}
HERMES_V020_BASE_FILTERED_SESSION_KEYS = HERMES_V020_BASE_SESSION_KEYS - {
    "last_active",
}
HERMES_V020_SESSION_KEYSETS = {
    frozenset(HERMES_V020_BASE_SESSION_KEYS),
    frozenset(HERMES_V020_BASE_FILTERED_SESSION_KEYS),
    frozenset(HERMES_V020_FILTERED_SESSION_KEYS),
    frozenset(HERMES_V020_SESSION_KEYS),
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
HERMES_LEGACY_MESSAGE_KEYS = {
    "content", "created_at", "id", "is_meta", "name", "role", "timestamp",
    "tool_name",
}
HERMES_V020_MESSAGE_KEYS = {
    "active", "api_content", "codex_message_items", "codex_reasoning_items",
    "compacted", "content", "display_kind", "display_metadata",
    "effect_disposition", "finish_reason", "id", "observed",
    "platform_message_id", "reasoning", "reasoning_content",
    "reasoning_details", "role", "session_id", "timestamp", "token_count",
    "tool_call_id", "tool_calls", "tool_name",
}
_IGNORED_MESSAGE_STRING_KEYS = {
    "api_content", "codex_message_items", "codex_reasoning_items", "display_kind",
    "effect_disposition", "finish_reason", "platform_message_id", "reasoning",
    "reasoning_content", "reasoning_details", "session_id", "tool_call_id",
}
_IGNORED_MESSAGE_INTEGER_KEYS = {"active", "compacted", "observed", "token_count"}
HERMES_SESSION_META_KEYS = {
    "active", "compacted", "content", "created_at", "id", "is_meta",
    "name", "observed", "session_id", "tool_name",
}
HERMES_TOOL_CALL_KEYS = {
    "call_id", "function", "id", "response_item_id", "type",
}
HERMES_TOOL_CALL_KEYSETS = {
    frozenset(HERMES_TOOL_CALL_KEYS),
    frozenset(HERMES_TOOL_CALL_KEYS | {"extra_content"}),
}
HERMES_TOOL_FUNCTION_KEYS = {"arguments", "name"}


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
                row_keys = set(row) if isinstance(row, dict) else set()
                if frozenset(row_keys) in HERMES_V020_SESSION_KEYSETS:
                    input_schema = "v020"
                elif (
                    {"id", "messages"}.issubset(row_keys)
                    and row_keys.issubset(HERMES_LEGACY_SESSION_KEYS)
                ):
                    input_schema = "legacy"
                else:
                    input_schema = None
                if (
                    not isinstance(row, dict)
                    or type(row.get("id")) not in {str, int}
                    or not str(row["id"])
                    or input_schema is None
                    or (
                        input_schema == "v020"
                        and not _known_extension_types_are_valid(
                            row,
                            string_keys=_IGNORED_ROW_STRING_KEYS,
                            number_keys=_IGNORED_ROW_NUMBER_KEYS,
                            integer_keys=_IGNORED_ROW_INTEGER_KEYS,
                        )
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
                    message_keys = set(message) if isinstance(message, dict) else set()
                    message_schema_valid = (
                        message_keys == HERMES_V020_MESSAGE_KEYS
                        if input_schema == "v020"
                        else {"role", "content"}.issubset(message_keys)
                        and message_keys.issubset(HERMES_LEGACY_MESSAGE_KEYS)
                    )
                    if (
                        not isinstance(message, dict)
                        or not message_schema_valid
                    ):
                        valid_messages = False
                        break
                    role = message.get("role")
                    content = message.get("content")
                    if input_schema == "v020":
                        selected_metadata_valid = (
                            type(message.get("id")) is int
                            and type(message.get("timestamp")) is float
                            and all(
                                type(message.get(key)) is int
                                and message[key] in {0, 1}
                                for key in ("active", "compacted", "observed")
                            )
                            and isinstance(message.get("session_id"), str)
                            and message["session_id"] == str(row["id"])
                            and "\x00" not in message["session_id"]
                            and len(message["session_id"].encode("utf-8")) <= 4096
                            and (
                                message.get("tool_name") is None
                                or isinstance(message["tool_name"], str)
                            )
                            and (
                                message.get("tool_call_id") is None
                                or isinstance(message["tool_call_id"], str)
                            )
                            and type(message.get("display_metadata"))
                            in {dict, type(None)}
                            and _known_extension_types_are_valid(
                                message,
                                string_keys=_IGNORED_MESSAGE_STRING_KEYS,
                                integer_keys=_IGNORED_MESSAGE_INTEGER_KEYS,
                            )
                        )
                    else:
                        selected_metadata_valid = _retained_message_types_are_valid(
                            message
                        )
                    if not selected_metadata_valid:
                        valid_messages = False
                        break
                    if role == "session_meta" and content is None:
                        auxiliary_events.append(
                            event_envelope(
                                "session_meta",
                                {
                                    key: value
                                    for key, value in message.items()
                                    if key in HERMES_SESSION_META_KEYS
                                },
                                message.get("timestamp", message.get("created_at")),
                            )
                        )
                    elif role in valid_roles and isinstance(content, (str, list)):
                        normalized = {"role": role, "content": content}
                        if "id" in message:
                            normalized["message_id"] = message["id"]
                        timestamp = message.get("timestamp", message.get("created_at"))
                        if "timestamp" in message or "created_at" in message:
                            normalized["timestamp"] = timestamp
                        if message.get("tool_name") is not None:
                            normalized["tool_name"] = message["tool_name"]
                        tool_calls = message.get("tool_calls")
                        if tool_calls is not None:
                            if role != "assistant" or not isinstance(tool_calls, list):
                                valid_messages = False
                                break
                            normalized_tool_uses = []
                            for tool_call in tool_calls:
                                function = (
                                    tool_call.get("function")
                                    if isinstance(tool_call, dict)
                                    else None
                                )
                                if (
                                    not isinstance(tool_call, dict)
                                    or frozenset(tool_call)
                                    not in HERMES_TOOL_CALL_KEYSETS
                                    or tool_call.get("type") != "function"
                                    or any(
                                        not isinstance(tool_call.get(key), str)
                                        or not tool_call[key]
                                        for key in (
                                            "call_id", "id", "response_item_id"
                                        )
                                    )
                                    or not isinstance(function, dict)
                                    or set(function) != HERMES_TOOL_FUNCTION_KEYS
                                    or any(
                                        not isinstance(function.get(key), str)
                                        for key in HERMES_TOOL_FUNCTION_KEYS
                                    )
                                    or not function["name"]
                                    or (
                                        "extra_content" in tool_call
                                        and not isinstance(
                                            tool_call["extra_content"], dict
                                        )
                                    )
                                ):
                                    valid_messages = False
                                    break
                                if "extra_content" in tool_call:
                                    try:
                                        validate_json_bounds(
                                            tool_call["extra_content"],
                                            max_depth=16,
                                            max_nodes=4096,
                                        )
                                    except ValueError:
                                        valid_messages = False
                                        break
                                # Gemini thought signatures are provider replay
                                # metadata, not chat/training content. Validate
                                # their shape above, then deliberately omit them.
                                normalized_tool_call = {
                                    key: tool_call[key]
                                    for key in sorted(HERMES_TOOL_CALL_KEYS)
                                }
                                normalized_tool_uses.append(
                                    event_envelope(
                                        "tool_use", normalized_tool_call, timestamp
                                    )
                                )
                            if not valid_messages:
                                break
                            if normalized_tool_uses:
                                normalized["tool_uses"] = normalized_tool_uses
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
                if "provider" not in row and row.get("billing_provider") is not None:
                    conversation["provider"] = row["billing_provider"]
                if "session_type" not in row and row.get("chat_type") is not None:
                    conversation["session_type"] = row["chat_type"]
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
