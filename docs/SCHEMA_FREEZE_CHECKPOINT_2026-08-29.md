# Schema-freeze checkpoint — 2026-08-29

This is a release gate, not a narrative recommendation. A broad release may
proceed only after the ordered records below are complete: one live-schema
census, one canonical validator, and one review wave. A second review wave or
an unrecorded validator is a failed checkpoint.

## Checkpoint record

| Field | Value |
|---|---|
| Checkpoint ID | `SF-2026-08-29-3c732d7` |
| Release under freeze | `3c732d7b1031949bd18db90ae4ac40f667f6cfa7` |
| Status | `satisfied-for-reviewed-release` |
| Broad-release rule | A new release must create a new checkpoint; this record does not authorize a later release. |
| Evidence receipt | [`RECEIPT_FLEET_CHAT_ARCHIVE_2026-08-29.md`](RECEIPT_FLEET_CHAT_ARCHIVE_2026-08-29.md) |

## 1. One live-schema census

The census was run against the checked-out runtime, not recalled from a prior
thread. Observed values: `archive_schema_version=2`; source mapping is
`claude -> claude-code`, `codex -> codex`, `openclaw -> openclaw`, and
`hermes -> hermes`; the contract contains 22 Claude event types and 45 Codex
event types; OpenClaw requires `openclaw-jsonl-v3`; Hermes requires
`hermes-sessions-export-jsonl-v1`.

The census source is `archive_object_contract.py` at SHA-256
`f31e840f49fcc9f35dc8223d1d0da3a479ae6da2de7fc62fac73ef6e8521825a`.

## 2. One canonical validator

Every adapter and transfer boundary uses
`archive_object_contract.validate_archive_object(value, harness=...)`. The
validator is the only canonical contract authority for archive objects at this
checkpoint. Its exact source hash is the census hash above; a different hash
requires a new checkpoint.

## 3. One review wave

The reviewed release's hardening checkpoint used one batched review wave,
one bounded repair, and one re-review, recorded by the release receipt and
the release commit. No parallel review wave is authorized for this checkpoint.

## Enforcement shape

- The active release owner is recorded in the repo-root
  [`.deployment-lease.json`](../.deployment-lease.json). Any session whose
  identity does not match that file must stand down; transfer is an owner
  commit, not an informal chat handoff.
- Every long canary is a launchd-owned job with durable stdout, stderr, exit
  status, and KeepAlive/retry state. A foreground shell or controlling chat
  session is not a canary node and cannot satisfy this gate.
- Before enabling a broad release, update this checkpoint with the new census,
  validator hash, and exactly one review-wave result. Do not reuse this record
  by changing only the release commit.
