#!/usr/bin/env python3
"""
Extract ALL Codex chat data from all projects
Includes: messages, code context, diffs, file references
Auto-discovers Codex installations on the device
"""

import json
from pathlib import Path
from datetime import datetime
import platform
import os
import errno
import stat


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


def _open_regular_jsonl(path):
    """Open one regular JSONL file without following a leaf symlink."""
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
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ValueError(f"JSONL input must be a regular file: {path}")
    return os.fdopen(descriptor, "r", encoding="utf-8")


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


def _append_message(messages, origins, message, origin):
    """Append in source order, coalescing adjacent cross-schema copies."""
    if messages and origins[-1] != origin:
        previous = messages[-1]
        if (
            previous.get("role") == message.get("role")
            and previous.get("content", "").strip() == message.get("content", "").strip()
        ):
            for key, value in message.items():
                if value is not None and key not in previous:
                    previous[key] = value
            return
    messages.append(message)
    origins.append(origin)


def _walk_jsonl_files(root, include):
    """Discover JSONL files deterministically and reject symlinks in the tree."""
    root = _reject_symlink_components(root)
    if not root.exists():
        return []
    if not root.is_dir():
        raise ValueError(f"input root must be a directory: {root}")

    discovered = []
    for current, directory_names, file_names in os.walk(root, followlinks=False):
        directory_names.sort()
        file_names.sort()
        current_path = Path(current)
        directory_names[:] = [
            name for name in directory_names if not (current_path / name).is_symlink()
        ]
        for name in file_names:
            candidate = current_path / name
            if candidate.is_symlink() and include(candidate):
                raise ValueError(f"refusing symlink in input root: {candidate}")
            if include(candidate):
                discovered.append(candidate)
    return discovered

def find_codex_installations():
    """Find all Codex installation directories"""
    system = platform.system()
    home = Path.home()

    locations = []

    # Search patterns for Codex directories
    codex_patterns = [
        'codex', 'codex-local', '.codex', '.codex-local'
    ]

    if system == "Darwin":  # macOS
        base_dirs = [
            home / "Library/Application Support",
            home / ".config",
            home
        ]
    elif system == "Linux":
        base_dirs = [
            home / ".config",
            home / ".local/share",
            home
        ]
    elif system == "Windows":
        base_dirs = [
            Path(os.environ.get('APPDATA', home / 'AppData/Roaming')),
            Path(os.environ.get('LOCALAPPDATA', home / 'AppData/Local')),
            home
        ]
    else:
        base_dirs = [home / ".config", home]

    for base_dir in base_dirs:
        if not base_dir.exists():
            continue

        for pattern in codex_patterns:
            codex_dir = base_dir / pattern
            if codex_dir.exists():
                locations.append(codex_dir)

    return list(set(locations))

def extract_codex_session(session_file, *, quality_out=None):
    """Extract conversation from a Codex rollout file with full context"""
    messages = []
    message_origins = []
    session_meta = {}
    tool_results = []
    discovered_lines = 0
    parsed_lines = 0
    failed_lines = 0
    recognized_lines = 0
    known_event_payloads = {
        "agent_reasoning",
        "context_compacted",
        "item_completed",
        "mcp_tool_call_end",
        "patch_apply_end",
        "sub_agent_activity",
        "task_complete",
        "task_started",
        "thread_goal_updated",
        "thread_settings_applied",
        "token_count",
        "turn_aborted",
        "web_search_end",
    }

    with _open_regular_jsonl(session_file) as f:
        for line in f:
            if not line.strip():
                continue
            discovered_lines += 1
            try:
                obj = json.loads(line)
                if not isinstance(obj, dict):
                    failed_lines += 1
                    continue
                parsed_lines += 1
                event_type = obj.get('type')

                if event_type == 'session_meta':
                    recognized_lines += 1
                    session_meta = obj.get('payload', {})

                elif event_type == 'event_msg':
                    payload = obj.get('payload', {})
                    payload_type = payload.get('type')

                    if payload_type == 'user_message':
                        recognized_lines += 1
                        message_text = payload.get('message', '').strip()
                        if message_text:
                            msg = {
                                'role': 'user',
                                'content': message_text,
                                'timestamp': obj.get('timestamp')
                            }

                            # Add context if available
                            if 'context' in payload:
                                msg['context'] = payload['context']

                            _append_message(messages, message_origins, msg, "event_msg")

                    elif payload_type == 'agent_message':
                        recognized_lines += 1
                        message_text = payload.get('message', '').strip()
                        if message_text:
                            msg = {
                                'role': 'assistant',
                                'content': message_text,
                                'timestamp': obj.get('timestamp')
                            }

                            # Add model info if available
                            if 'model' in payload:
                                msg['model'] = payload['model']

                            _append_message(messages, message_origins, msg, "event_msg")

                    elif payload_type == 'tool_use':
                        recognized_lines += 1
                        # Code execution, file edits, etc.
                        tool_use = {
                            'type': 'tool_use',
                            'tool': payload.get('tool'),
                            'input': payload.get('input'),
                            'timestamp': obj.get('timestamp')
                        }
                        tool_results.append(tool_use)

                    elif payload_type == 'tool_result':
                        recognized_lines += 1
                        # Results from tool execution (diffs, outputs, etc.)
                        tool_result = {
                            'type': 'tool_result',
                            'tool': payload.get('tool'),
                            'output': payload.get('output'),
                            'timestamp': obj.get('timestamp')
                        }
                        tool_results.append(tool_result)

                    elif payload_type == 'diff':
                        recognized_lines += 1
                        # Code diffs
                        diff = {
                            'type': 'diff',
                            'file': payload.get('file'),
                            'diff': payload.get('diff'),
                            'timestamp': obj.get('timestamp')
                        }
                        tool_results.append(diff)
                    elif payload_type in known_event_payloads:
                        recognized_lines += 1
                        tool_results.append(
                            {
                                "type": f"event_msg:{payload_type}",
                                "payload": payload,
                                "timestamp": obj.get("timestamp"),
                            }
                        )
                    else:
                        failed_lines += 1

                elif event_type == 'response_item':
                    payload = obj.get('payload', {})
                    payload_type = payload.get('type')

                    if payload_type == 'message':
                        role = payload.get('role')
                        content = payload.get('content', [])
                        if role not in {'user', 'assistant', 'system', 'developer'} or not isinstance(content, list):
                            failed_lines += 1
                            continue
                        text_parts = []
                        invalid_content = False
                        for item in content:
                            if not isinstance(item, dict):
                                invalid_content = True
                                break
                            if item.get('type') in {'input_text', 'output_text', 'text'}:
                                text = item.get('text')
                                if isinstance(text, str) and text:
                                    text_parts.append(text)
                                elif not isinstance(text, str):
                                    invalid_content = True
                                    break
                            else:
                                invalid_content = True
                                break
                        if invalid_content or not text_parts:
                            failed_lines += 1
                            continue
                        recognized_lines += 1
                        if text_parts:
                            _append_message(messages, message_origins, {
                                'role': role,
                                'content': '\n'.join(text_parts),
                                'message_id': payload.get('id'),
                                'timestamp': obj.get('timestamp')
                            }, "response_item")

                    elif payload_type in {
                        'custom_tool_call', 'custom_tool_call_output',
                        'function_call', 'function_call_output'
                    }:
                        recognized_lines += 1
                        tool_event = {
                            'type': payload_type,
                            'id': payload.get('id'),
                            'call_id': payload.get('call_id'),
                            'timestamp': obj.get('timestamp')
                        }
                        for key in ('name', 'input', 'arguments', 'output', 'status'):
                            if key in payload:
                                tool_event[key] = payload[key]
                        tool_results.append(tool_event)
                    elif payload_type in {'reasoning', 'agent_message'}:
                        recognized_lines += 1
                        tool_results.append(
                            {
                                "type": f"response_item:{payload_type}",
                                "payload": payload,
                                "timestamp": obj.get("timestamp"),
                            }
                        )
                    else:
                        failed_lines += 1
                elif event_type in {
                    'turn_context',
                    'compacted',
                    'inter_agent_communication_metadata',
                    'world_state',
                }:
                    recognized_lines += 1
                    tool_results.append(
                        {
                            "type": event_type,
                            "payload": obj.get("payload"),
                            "timestamp": obj.get("timestamp"),
                        }
                    )
                else:
                    failed_lines += 1

            except json.JSONDecodeError:
                failed_lines += 1

    quality = _quality(discovered_lines, parsed_lines, failed_lines)
    if discovered_lines and not recognized_lines:
        quality["status"] = "partial"
    quality["recognized_lines"] = recognized_lines
    quality["discovered_files"] = 1
    _publish_quality(quality_out, quality)
    if messages:
        conv = {
            'messages': messages,
            'session_id': session_meta.get('id'),
            'cwd': session_meta.get('cwd'),
            'source': 'codex',
            'session_file': str(session_file),
            'timestamp': session_meta.get('timestamp'),
        }

        if tool_results:
            conv['tool_results'] = tool_results

        return conv

    return None

def find_all_codex_sessions(installation):
    """Find all Codex session files in an installation"""
    installation = _reject_symlink_components(installation)
    session_files = []

    # Check for sessions directory
    sessions_dir = installation / 'sessions'
    if sessions_dir.exists():
        # Sessions are organized by date: YYYY/MM/DD/rollout-*.jsonl
        session_files.extend(
            _walk_jsonl_files(
                sessions_dir,
                lambda path: path.suffix == ".jsonl" and path.name.startswith("rollout-"),
            )
        )

    # Also check for project-based structure
    projects_dir = installation / 'projects'
    if projects_dir.exists():
        session_files.extend(
            _walk_jsonl_files(projects_dir, lambda path: path.suffix == ".jsonl")
        )

    archived_dir = installation / 'archived_sessions'
    if archived_dir.exists():
        session_files.extend(
            _walk_jsonl_files(
                archived_dir,
                lambda path: path.suffix == ".jsonl" and path.name.startswith("rollout-"),
            )
        )

    return sorted(set(session_files))

def main():
    print("="*80)
    print("CODEX COMPLETE DATA EXTRACTION")
    print("="*80)
    print()

    # Find all Codex installations
    print("🔍 Searching for Codex installations...")
    installations = find_codex_installations()

    if not installations:
        print("❌ No Codex installations found!")
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

        session_files = find_all_codex_sessions(installation)
        print(f"   Found {len(session_files)} session files")

        conversations = []
        for session_file in session_files:
            conv = extract_codex_session(session_file)
            if conv:
                conv['installation'] = str(installation)
                conversations.append(conv)

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
    with_tools = sum(1 for c in all_conversations if 'tool_results' in c)
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
    output_file = output_dir / f'codex_conversations_{timestamp}.jsonl'

    with open(output_file, 'w') as f:
        for conv in all_conversations:
            f.write(json.dumps(conv, ensure_ascii=False) + '\n')

    file_size = output_file.stat().st_size / 1024 / 1024
    print(f"✅ Saved to: {output_file}")
    print(f"   Size: {file_size:.2f} MB")
    print(f"   Format: JSONL (one conversation per line)")

if __name__ == '__main__':
    main()
