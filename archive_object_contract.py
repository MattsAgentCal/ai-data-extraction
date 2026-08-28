#!/usr/bin/env python3
"""Closed, versioned archive-object contract shared by collectors and transfer."""

from __future__ import annotations

from collections.abc import Iterable
import math


ARCHIVE_OBJECT_SCHEMA_VERSION = 2
MAX_ARCHIVE_JSON_DEPTH = 64
MAX_ARCHIVE_JSON_NODES = 8_000_000

HARNESS_SOURCES = {
    "claude": "claude-code",
    "codex": "codex",
    "openclaw": "openclaw",
    "hermes": "hermes",
}

CLAUDE_EVENT_TYPES = {
    "agent-name", "atis-latch", "artifact-comment-monitor", "attachment",
    "ai-title", "bridge-session", "custom-title", "file-history-delta",
    "file-history-snapshot", "frame-link", "last-prompt", "mode", "pr-link",
    "permission-mode", "progress", "queue-operation", "result", "started",
    "summary", "system", "relocated", "worktree-state",
}
CODEX_EVENT_TYPES = {
    "tool_use", "tool_result", "diff", "custom_tool_call",
    "custom_tool_call_output", "function_call", "function_call_output",
    "turn_context", "compacted", "inter_agent_communication_metadata",
    "world_state", "response_item:message_empty", "response_item:reasoning",
    "response_item:agent_message", "response_item:image_generation_call",
    "response_item:tool_search_call", "response_item:tool_search_output",
    "response_item:web_search_call",
} | {
    "event_msg:" + name
    for name in {
        "agent_reasoning", "collab_agent_interaction_end", "collab_agent_spawn_end",
        "collab_close_end", "collab_waiting_end", "context_compacted",
        "dynamic_tool_call_request", "dynamic_tool_call_response",
        "entered_review_mode", "error", "exec_command_end", "exited_review_mode",
        "image_generation_end", "item_completed", "mcp_tool_call_end",
        "patch_apply_end", "sub_agent_activity", "task_complete", "task_started",
        "thread_goal_updated", "thread_name_updated", "thread_rolled_back",
        "thread_settings_applied", "token_count", "turn_aborted",
        "view_image_tool_call", "web_search_end",
    }
}


def _exact(
    value: object,
    *,
    required: Iterable[str],
    optional: Iterable[str] = (),
    label: str,
) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    required_keys = set(required)
    allowed = required_keys | set(optional)
    if set(value) - allowed or required_keys - set(value):
        raise ValueError(f"invalid {label} schema")
    return value


def validate_json_bounds(
    value: object,
    *,
    max_depth: int = MAX_ARCHIVE_JSON_DEPTH,
    max_nodes: int = MAX_ARCHIVE_JSON_NODES,
) -> None:
    """Bound opaque content without retaining a second parsed object tree."""
    stack = [(value, 1)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > max_nodes:
            raise ValueError("archive object exceeds JSON node bound")
        if depth > max_depth:
            raise ValueError("archive object exceeds JSON depth bound")
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("archive object has a non-string JSON key")
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
        elif type(item) is float and not math.isfinite(item):
            raise ValueError("archive object has a non-finite number")
        elif item is not None and type(item) not in {str, int, float, bool}:
            raise ValueError("archive object has an unsupported JSON value")


def _validate_message(record: object, *, harness: str) -> None:
    optional = {
        "timestamp",
        "message_id",
        "model",
        "context",
        "content_items",
        "tool_use",
        "tool_uses",
        "tool_results",
        "tool_name",
    }
    record = _exact(
        record,
        required={"role", "content"},
        optional=optional,
        label=f"{harness} message",
    )
    allowed_roles = {
        "claude": {"user", "assistant"},
        "codex": {"user", "assistant", "system", "developer"},
        "openclaw": {"user", "assistant", "system", "developer"},
        "hermes": {"user", "assistant", "system", "developer", "tool", "toolResult"},
    }[harness]
    if record["role"] not in allowed_roles or not isinstance(
        record["content"], (str, list)
    ):
        raise ValueError(f"invalid {harness} message")
    if harness in {"codex", "openclaw"} and not isinstance(record["content"], str):
        raise ValueError(f"invalid {harness} message content")
    if "timestamp" in record and record["timestamp"] is not None and type(
        record["timestamp"]
    ) not in {str, int, float}:
        raise ValueError(f"invalid {harness} message timestamp")
    if "message_id" in record and record["message_id"] is not None and type(
        record["message_id"]
    ) not in {str, int}:
        raise ValueError(f"invalid {harness} message_id")
    for key in ("model", "tool_name"):
        if key in record and record[key] is not None and not isinstance(record[key], str):
            raise ValueError(f"invalid {harness} message {key}")
    if "content_items" in record:
        if not isinstance(record["content_items"], list):
            raise ValueError("invalid Codex message content_items")
        for item in record["content_items"]:
            item = _exact(
                item,
                required={"type", "image_url"},
                optional={"detail"},
                label="Codex image content",
            )
            if item["type"] != "input_image" or not isinstance(item["image_url"], str):
                raise ValueError("invalid Codex image content")
            if "detail" in item and item["detail"] is not None and not isinstance(
                item["detail"], str
            ):
                raise ValueError("invalid Codex image detail")
    for key in ("tool_uses", "tool_results"):
        if key in record and not isinstance(record[key], list):
            raise ValueError(f"invalid {harness} message {key}")


def _validate_event(record: object, *, harness: str) -> None:
    record = _exact(
        record,
        required={"type", "payload"},
        optional={"timestamp"},
        label=f"{harness} event",
    )
    if (
        not isinstance(record["type"], str)
        or not record["type"]
        or len(record["type"].encode("utf-8")) > 256
        or "\x00" in record["type"]
    ):
        raise ValueError(f"invalid {harness} event type")
    if harness == "claude" and record["type"] not in CLAUDE_EVENT_TYPES:
        raise ValueError("unknown Claude event type")
    if harness == "codex" and record["type"] not in CODEX_EVENT_TYPES:
        raise ValueError("unknown Codex event type")
    if "timestamp" in record and record["timestamp"] is not None and type(
        record["timestamp"]
    ) not in {str, int, float}:
        raise ValueError(f"invalid {harness} event timestamp")


def validate_archive_object(value: object, *, harness: str | None = None) -> dict:
    validate_json_bounds(value)
    if not isinstance(value, dict):
        raise ValueError("archive object must be an object")
    source = value.get("source")
    if harness is None:
        matches = [name for name, expected in HARNESS_SOURCES.items() if expected == source]
        if len(matches) != 1:
            raise ValueError("unknown archive object source")
        harness = matches[0]
    if harness not in HARNESS_SOURCES or source != HARNESS_SOURCES[harness]:
        raise ValueError("archive object source/harness mismatch")

    common_required = {"archive_schema_version", "source", "session_id", "messages"}
    common_optional = {"archive_redaction_count", "installation"}
    harness_required = {
        "claude": {"project_path", "project_name", "source_file"},
        "codex": {"cwd", "session_file", "timestamp"},
        "openclaw": {"cwd", "session_file", "timestamp", "source_schema"},
        "hermes": {"native_source", "source_schema"},
    }[harness]
    harness_optional = {
        "claude": {"events"},
        "codex": {"tool_results"},
        "openclaw": {"tool_results"},
        "hermes": {
            "cwd",
            "started_at",
            "ended_at",
            "title",
            "model",
            "provider",
            "agent_id",
            "session_type",
            "events",
        },
    }[harness]
    value = _exact(
        value,
        required=common_required | harness_required,
        optional=common_optional | harness_optional,
        label=f"{harness} archive object",
    )
    if value["archive_schema_version"] != ARCHIVE_OBJECT_SCHEMA_VERSION:
        raise ValueError("legacy or unsupported archive object schema")
    if value["session_id"] is not None and not isinstance(value["session_id"], str):
        raise ValueError("archive object session_id must be a string or null")
    if not isinstance(value["messages"], list):
        raise ValueError("archive object messages must be a list")
    for message in value["messages"]:
        _validate_message(message, harness=harness)
    event_key = "events" if harness in {"claude", "hermes"} else "tool_results"
    if event_key in value:
        if not isinstance(value[event_key], list):
            raise ValueError(f"{harness} {event_key} must be a list")
        for event in value[event_key]:
            _validate_event(event, harness=harness)
    if "archive_redaction_count" in value and (
        type(value["archive_redaction_count"]) is not int
        or value["archive_redaction_count"] < 0
    ):
        raise ValueError("invalid archive redaction count")
    if "installation" in value and (
        not isinstance(value["installation"], str)
        or not value["installation"].startswith("/")
    ):
        raise ValueError("invalid archive installation")
    for key in ("project_path", "project_name", "cwd", "native_source"):
        if key in value and value[key] is not None and not isinstance(value[key], str):
            raise ValueError(f"invalid archive object {key}")
    if "timestamp" in value and value["timestamp"] is not None and type(
        value["timestamp"]
    ) not in {str, int, float}:
        raise ValueError("invalid archive object timestamp")
    for key in ("source_file", "session_file", "source_schema"):
        if key in value and not isinstance(value[key], str):
            raise ValueError(f"invalid archive object {key}")
    if harness == "openclaw" and value["source_schema"] != "openclaw-jsonl-v3":
        raise ValueError("invalid OpenClaw source schema")
    if (
        harness == "hermes"
        and value["source_schema"] != "hermes-sessions-export-jsonl-v1"
    ):
        raise ValueError("invalid Hermes source schema")
    if harness == "hermes":
        for key in ("started_at", "ended_at"):
            if key in value and value[key] is not None and type(value[key]) not in {
                str,
                int,
                float,
            }:
                raise ValueError(f"invalid Hermes {key}")
        for key in ("title", "model", "provider", "agent_id", "session_type"):
            if key in value and value[key] is not None and not isinstance(
                value[key], str
            ):
                raise ValueError(f"invalid Hermes {key}")
    return value


def event_envelope(event_type: str, payload: object, timestamp: object = None) -> dict:
    return {"type": event_type, "payload": payload, "timestamp": timestamp}
