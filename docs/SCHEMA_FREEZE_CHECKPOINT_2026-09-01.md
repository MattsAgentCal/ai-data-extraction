# Schema-freeze checkpoint — 2026-09-01

This checkpoint authorizes the first release that adds the launchd-owned Drive
plugin publisher. It is a release gate: one live-schema census, one canonical
validator, and one review wave. A later broad release needs a new checkpoint.

## Current reconciliation — 2026-09-01T08:45Z

The repaired code tree is `300ce290c9d75e4187a240770db7f9793c57d577` (local
commit `4b665987ad9c06c618a73fc67e7b6004d1bd1881`; GitHub-plugin commit
`ec96761786196f58b8157de8dc917c85947a09b8`). The one live census found 71
`realtime_item` rows in the current Codex rollout that the previous contract
rejected. One narrow repair added that event to the extractor and canonical
contract, followed by one batched review/re-review; the full suite is 270
tests, OK. The New collector is still a single launchd-supervised canary under
PID 84541, so this checkpoint does not claim a terminal runtime receipt or a
broad host release. The Mini source and `.gz` copy are absent; no disk mutation
occurred and freed bytes are 0. Any later broad release needs a new checkpoint.

## Checkpoint record

| Field | Value |
|---|---|
| Checkpoint ID | `SF-2026-09-01-drive-publisher` |
| Release under freeze | `drive-plugin-publisher` (local pre-commit) |
| Status | `satisfied-for-repaired-release; broad host release still pending terminal canary` |
| Live census time | `2026-09-01` (MacBook checkout, immediately before this release review) |
| Evidence | One live-schema census, one canonical validator, one batched repair/re-review, and the hashes in this file |

## 1. One live-schema census

The checked-out contract was inspected once before the repair review. The
observed schema is `archive_schema_version=2`, with four source mappings:
`claude -> claude-code`, `codex -> codex`, `openclaw -> openclaw`, and
`hermes -> hermes`. The census found 22 Claude event types and 46 Codex event
types after including the newly observed `realtime_item`; the prior live row
shape produced 71 unknown-event findings in one rollout. The full event-name
output is the sorted output of the single census command.

`archive_object_contract.py` SHA-256:
`632f94bbb177766286aeb7d772ae4c0c6fd0b0197884317cbfe3687ade0d2007`

## 2. One canonical validator

The canonical object validator remains
`archive_object_contract.validate_archive_object`. The publisher uses the
existing collector boundary validators for the committed manifest and indexes,
then calls `fleet_chat_archive.validated_object_provenance` for every object in
the bounded publication batch. No second archive-object schema was introduced;
the repaired contract is the sole validator input.

The reviewed collector hash is
`625e98c4f33aba2e57864f49c70992924491c78e3561dd2bf1360687da039a0b`.
The reviewed publisher hash after the one repair/re-review is
`0fd9391948ac7aaacddc5a6dfdd93b09b50a71ae95f3d94ce145d1ac3d241c58`.

## 3. One review wave

One review wave inspected the bounded `realtime_item` repair, focused tests,
and the complete unittest suite. Findings were batched into one repair; the
same focused tests and full suite were re-run once after that repair:
`270 tests, OK`. No parallel review wave was used or authorized for this
checkpoint.

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
