#!/usr/bin/env python3
"""
Extract ALL Claude Code chat data from all projects
Includes: messages, code context, diffs, file references
Auto-discovers Claude Code installations on the device
"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime
import hashlib
import platform
import os
import errno
import stat


_MACOS_COMPATIBILITY_SYMLINKS = {
    Path("/etc"): Path("/private/etc"),
    Path("/tmp"): Path("/private/tmp"),
    Path("/var"): Path("/private/var"),
}


def _reject_symlink_components(path):
    expanded = Path(path).expanduser()
    absolute = Path(os.path.abspath(os.fspath(expanded)))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            expected = _MACOS_COMPATIBILITY_SYMLINKS.get(current)
            if expected is None or current.resolve(strict=True) != expected:
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
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ValueError(f"refusing symlinked JSONL file: {path}") from error
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


def _discover_session_files(project_dir):
    project_dir = _reject_symlink_components(project_dir)
    if not project_dir.exists():
        return []
    if not project_dir.is_dir():
        raise ValueError(f"input root must be a directory: {project_dir}")
    search_root = project_dir / "projects" if (project_dir / "projects").exists() else project_dir
    files = []
    for current, directory_names, file_names in os.walk(search_root, followlinks=False):
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
            if candidate.suffix == ".jsonl" and not candidate.name.startswith("agent-"):
                files.append(candidate)
    return files

def find_claude_installations():
    """Find all Claude Code installation directories"""
    system = platform.system()
    home = Path.home()

    # Common installation locations by OS
    locations = []

    if system == "Darwin":  # macOS
        base_dirs = [
            home / "Library/Application Support",
            home / ".config"
        ]
    elif system == "Linux":
        base_dirs = [
            home / ".config",
            home / ".local/share"
        ]
    elif system == "Windows":
        base_dirs = [
            Path(os.environ.get('APPDATA', home / 'AppData/Roaming')),
            Path(os.environ.get('LOCALAPPDATA', home / 'AppData/Local'))
        ]
    else:
        base_dirs = [home / ".config"]

    # Search for Claude-related directories
    claude_patterns = [
        'claude', 'claude-code', 'claude-local', 'claude-m2', 'claude-zai',
        '.claude', '.claude-code', '.claude-local', '.claude-m2', '.claude-zai'
    ]

    for base_dir in base_dirs:
        if not base_dir.exists():
            continue

        # Check direct children
        for pattern in claude_patterns:
            claude_dir = base_dir / pattern
            if claude_dir.exists():
                locations.append(claude_dir)

        # Also check home directory directly
        for pattern in claude_patterns:
            home_dir = home / pattern
            if home_dir.exists():
                locations.append(home_dir)

    return list(set(locations))  # Remove duplicates

CLAUDE_IGNORED_RECORD_TYPES = {
    "agent-name",
    "atis-latch",
    "artifact-comment-monitor",
    "attachment",
    "ai-title",
    "bridge-session",
    "custom-title",
    "file-history-delta",
    "file-history-snapshot",
    "frame-link",
    "last-prompt",
    "mode",
    "pr-link",
    "permission-mode",
    "progress",
    "queue-operation",
    "result",
    "started",
    "summary",
    "system",
}


def _is_relocated_record(value):
    return (
        set(value) == {"type", "sessionId", "relocatedCwd"}
        and value.get("type") == "relocated"
        and isinstance(value.get("sessionId"), str)
        and isinstance(value.get("relocatedCwd"), str)
    )


def _is_worktree_state_record(value):
    if (
        set(value) != {"type", "sessionId", "worktreeSession"}
        or value.get("type") != "worktree-state"
        or not isinstance(value.get("sessionId"), str)
        or not isinstance(value.get("worktreeSession"), dict)
    ):
        return False
    worktree_session = value["worktreeSession"]
    expected_keys = {
        "originalBranch",
        "originalCwd",
        "originalHeadCommit",
        "preEnterOriginalCwd",
        "sessionId",
        "worktreeBranch",
        "worktreeName",
        "worktreePath",
    }
    return (
        set(worktree_session) == expected_keys
        and all(isinstance(worktree_session[key], str) for key in expected_keys)
        and worktree_session["sessionId"] == value["sessionId"]
    )


def _is_strict_auxiliary_record(value):
    return _is_relocated_record(value) or _is_worktree_state_record(value)


def find_all_claude_sessions(project_dir):
    return _discover_session_files(project_dir)


def extract_claude_session(
    jsonl_file,
    *,
    installation=None,
    quality_out=None,
    max_source_bytes=None,
    expected_fingerprint=None,
):
    messages = []
    auxiliary_events = []
    session_id = jsonl_file.stem
    project_path = None
    project_name = jsonl_file.parent.name if jsonl_file.parent.name != "projects" else None
    discovered_lines = 0
    parsed_lines = 0
    failed_lines = 0
    recognized_lines = 0
    source_handle, source_metadata = _open_regular_jsonl(
        jsonl_file,
        max_bytes=max_source_bytes,
        expected_fingerprint=expected_fingerprint,
    )
    with source_handle as handle:
        for line in handle:
            if not line.strip():
                continue
            discovered_lines += 1
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                failed_lines += 1
                continue
            if not isinstance(obj, dict):
                failed_lines += 1
                continue
            parsed_lines += 1
            msg_type = obj.get("type")
            if msg_type == "user":
                message = obj.get("message", {})
                if not isinstance(message, dict):
                    failed_lines += 1
                    continue
                recognized_lines += 1
                content = message.get("content", "")
                if not isinstance(content, (str, list)):
                    failed_lines += 1
                    recognized_lines -= 1
                    continue
                if content:
                    record = {
                        "role": "user",
                        "content": content,
                        "timestamp": obj.get("timestamp"),
                    }
                    if "toolUse" in obj:
                        record["tool_use"] = obj["toolUse"]
                    messages.append(record)
                project_path = obj.get("cwd", project_path)
            elif msg_type == "assistant":
                message = obj.get("message", {})
                if not isinstance(message, dict):
                    failed_lines += 1
                    continue
                recognized_lines += 1
                content = message.get("content", [])
                text_parts = []
                tool_uses = []
                if isinstance(content, list):
                    invalid_content = False
                    for item in content:
                        if not isinstance(item, dict):
                            invalid_content = True
                            break
                        if item.get("type") == "text" and isinstance(item.get("text"), str):
                            text_parts.append(item.get("text", ""))
                        elif isinstance(item, dict) and item.get("type") == "tool_use":
                            tool_uses.append(item)
                        else:
                            tool_uses.append(item)
                    if invalid_content:
                        failed_lines += 1
                        recognized_lines -= 1
                        continue
                elif isinstance(content, str):
                    text_parts.append(content)
                else:
                    failed_lines += 1
                    recognized_lines -= 1
                    continue
                full_text = "\n".join(text_parts)
                if full_text or tool_uses:
                    record = {
                        "role": "assistant",
                        "content": full_text,
                        "model": message.get("model"),
                        "timestamp": obj.get("timestamp"),
                    }
                    if tool_uses:
                        record["tool_uses"] = tool_uses
                    messages.append(record)
            elif msg_type == "tool_result":
                recognized_lines += 1
                tool_result = obj.get("toolResult", {})
                if tool_result and messages:
                    messages[-1].setdefault("tool_results", []).append(tool_result)
            elif _is_strict_auxiliary_record(obj):
                recognized_lines += 1
                auxiliary_events.append(obj)
            elif msg_type in CLAUDE_IGNORED_RECORD_TYPES:
                recognized_lines += 1
                auxiliary_events.append(obj)
            else:
                failed_lines += 1
        _verify_parsed_source(
            handle,
            jsonl_file,
            source_metadata,
            max_source_bytes,
        )

    quality = {
        "status": (
            "partial"
            if failed_lines or (discovered_lines and not recognized_lines)
            else "complete"
        ),
        "discovered_lines": discovered_lines,
        "parsed_lines": parsed_lines,
        "failed_lines": failed_lines,
        "discovered_files": 1,
        "recognized_lines": recognized_lines,
    }
    if quality_out is not None:
        quality_out.clear()
        quality_out.update(quality)
    if not messages and not auxiliary_events:
        return None
    conversation = {
        "messages": messages,
        "source": "claude-code",
        "session_id": session_id,
        "project_path": project_path,
        "project_name": project_name,
        "source_file": str(jsonl_file),
    }
    if auxiliary_events:
        conversation["events"] = auxiliary_events
    if installation is not None:
        conversation["installation"] = str(installation)
    return conversation


def extract_claude_project_conversations(project_dir, *, quality_out=None):
    conversations = []
    qualities = []
    for jsonl_file in find_all_claude_sessions(project_dir):
        quality = {}
        conversation = extract_claude_session(
            jsonl_file, installation=project_dir, quality_out=quality
        )
        qualities.append(quality)
        if conversation:
            conversations.append(conversation)
    aggregate = {
        "status": "partial" if any(q["status"] == "partial" for q in qualities) else "complete",
        "discovered_lines": sum(q["discovered_lines"] for q in qualities),
        "parsed_lines": sum(q["parsed_lines"] for q in qualities),
        "failed_lines": sum(q["failed_lines"] for q in qualities),
        "discovered_files": len(qualities),
        "recognized_lines": sum(q["recognized_lines"] for q in qualities),
    }
    if quality_out is not None:
        quality_out.clear()
        quality_out.update(aggregate)
    return conversations

def main():
    print("="*80)
    print("CLAUDE CODE COMPLETE DATA EXTRACTION")
    print("="*80)
    print()

    # Find all Claude installations
    print("🔍 Searching for Claude Code installations...")
    installations = find_claude_installations()

    if not installations:
        print("❌ No Claude Code installations found!")
        return

    print(f"✅ Found {len(installations)} installation(s):")
    for inst in installations:
        print(f"   - {inst}")
    print()

    # Extract from all installations
    all_conversations = []
    installation_stats = {}

    for installation in installations:
        print(f"📂 Processing: {installation}")

        conversations = extract_claude_project_conversations(installation)

        if conversations:
            all_conversations.extend(conversations)
            installation_stats[str(installation)] = len(conversations)
            print(f"   ✅ {len(conversations)} conversations")
        else:
            print(f"   ⚠️  No conversations found")

    print()
    print("="*80)
    print("EXTRACTION COMPLETE")
    print("="*80)
    print(f"Total conversations: {len(all_conversations):,}")

    if not all_conversations:
        print("No conversations found!")
        return

    # Statistics
    total_messages = sum(len(c['messages']) for c in all_conversations)
    with_tools = sum(1 for c in all_conversations
                     if any('tool_use' in m or 'tool_uses' in m or 'tool_results' in m
                           for m in c['messages']))
    complete = sum(1 for c in all_conversations
                   if any(m['role'] == 'assistant' for m in c['messages']))

    print(f"Complete conversations: {complete:,}")
    print(f"Total messages: {total_messages:,}")
    print(f"With tool use/diffs: {with_tools:,}")
    print()

    print("Breakdown by installation:")
    for inst, count in sorted(installation_stats.items(), key=lambda x: -x[1]):
        print(f"  {Path(inst).name:20} {count:5,} conversations")
    print()

    # Save to organized JSONL
    output_dir = Path('extracted_data')
    output_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = output_dir / f'claude_code_conversations_{timestamp}.jsonl'

    with open(output_file, 'w') as f:
        for conv in all_conversations:
            f.write(json.dumps(conv, ensure_ascii=False) + '\n')

    file_size = output_file.stat().st_size / 1024 / 1024
    print(f"✅ Saved to: {output_file}")
    print(f"   Size: {file_size:.2f} MB")
    print(f"   Format: JSONL (one conversation per line)")

if __name__ == '__main__':
    main()
