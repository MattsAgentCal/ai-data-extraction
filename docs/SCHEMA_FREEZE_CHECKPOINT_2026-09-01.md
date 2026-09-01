# Schema-freeze checkpoint — 2026-09-01

This checkpoint authorizes the first release that adds the launchd-owned Drive
plugin publisher. It is a release gate: one live-schema census, one canonical
validator, and one review wave. A later broad release needs a new checkpoint.

## Checkpoint record

| Field | Value |
|---|---|
| Checkpoint ID | `SF-2026-09-01-drive-publisher` |
| Release under freeze | `drive-plugin-publisher` (local pre-commit) |
| Status | `satisfied-for-reviewed-release` |
| Live census time | `2026-09-01` (MacBook checkout, immediately before this release review) |
| Evidence | The command output below and the hashes in this file |

## 1. One live-schema census

The checked-out contract was inspected once before the publisher review. The
observed schema is `archive_schema_version=2`, with four source mappings:
`claude -> claude-code`, `codex -> codex`, `openclaw -> openclaw`, and
`hermes -> hermes`. The contract exposes 22 Claude event types and 45 Codex
event types. The full event-name output is the sorted output of the census
command; it is intentionally not duplicated here so this gate has one source
of truth.

`archive_object_contract.py` SHA-256:
`f31e840f49fcc9f35dc8223d1d0da3a479ae6da2de7fc62fac73ef6e8521825a`

## 2. One canonical validator

The canonical object validator remains
`archive_object_contract.validate_archive_object`. The publisher uses the
existing collector boundary validators for the committed manifest and indexes,
then calls `fleet_chat_archive.validated_object_provenance` for every object in
the bounded publication batch. No second archive-object schema was introduced.

The reviewed collector hash is
`625e98c4f33aba2e57864f49c70992924491c78e3561dd2bf1360687da039a0b`.
The reviewed publisher hash after the one repair/re-review is
`0fd9391948ac7aaacddc5a6dfdd93b09b50a71ae95f3d94ce145d1ac3d241c58`.

## 3. One review wave

One review wave inspected the bounded publisher diff, focused tests, and the
complete unittest suite. Four findings were batched into one repair: absolute
spool-path enforcement, timezone-aware lease parsing, parent-ID normalization,
and fixed connector error codes. The same focused tests and full suite were
re-run once after that repair: `270 tests, OK`. No parallel review wave was
used or authorized for this checkpoint.

## Enforcement shape

- The repo-root [`.deployment-lease.json`](../.deployment-lease.json) names the
  sole release owner. A different session reads it and stands down.
- The collector and publisher are launchd jobs with `RunAtLoad`,
  `StartInterval=21600`, background process type, owner-only logs, and a
  persistent receipt. A foreground process cannot satisfy the canary gate.
- Drive writes are made only by the authenticated Codex Google Drive plugin;
  the publisher prompt carries object paths and byte metadata, never bodies.
- Mini remains read-only and Old remains a retry queue; neither is a reason to
  weaken this checkpoint.
