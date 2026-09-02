# Schema-freeze checkpoint — 2026-09-01

This checkpoint authorizes the first release that adds the launchd-owned Drive
plugin publisher. It is a release gate: one live-schema census, one canonical
validator, and one review wave. A later broad release needs a new checkpoint.

## Current implementation reconciliation — 2026-09-02T05:33Z

The frozen archive schema and one canonical validator remain unchanged. Local
`HEAD` is `858d3aa` / tree `7b3e13c4296b61b7d008a4dd0935d5fc0160aeb9`;
GitHub-plugin commit `bdd361116860832e076c0f9576b7acde5ca02fc3` publishes that
tree non-force from remote parent `374b53eec092ee00181a542181efdd1f0baec50c`.
Studio fast-forwarded to the published tree and passed `282/282` tests. The
Drive model-turn transport is still non-schema and uses the already-approved
validator/readback path; no second census, validator, or review wave was
opened. The natural New collector/publisher proof is still pending. Mini's
requested closed log and `.gz` are absent; available space is
`27,012,556 KiB` (`27,660,857,344` bytes), with source/archive/deletion/freed
bytes all `0` and the active log untouched.

## Current reconciliation — 2026-09-01T11:14Z

The shipped code tree is `5e33dd15aeb26ecbe059ac6cf83338442a21c493` (local
commit `88d65a7677dbdb483b36a85ff59b907231727502`; GitHub-plugin commit
`68c02d1953b428b2ecf6443e26a56560bb81f436`). The one live census still governs
this checkpoint: it found 71 `realtime_item` rows, one narrow contract repair,
one canonical-validator review wave, and the post-repair suite now passes 272
tests. The publisher lock-timeout change is operational configuration only and
does not introduce a second archive schema or census. New has a terminal
zero-error launchd receipt; Studio was fast-forwarded to this tree but its
single canary was safely stopped at disk pressure (`SIGTERM`, receipt
`20260901T104451.299032Z-40cc7d40`). The Mini source and `.gz` copy remain
absent; no disk mutation occurred and freed bytes are 0. This record does not
authorize a retry into Studio's nearly full disk or a broad release beyond the
reachable evidence.

## Current implementation reconciliation — 2026-09-02T04:50Z

The Drive transport repair is non-schema and follows this checkpoint's single
census/validator/review-wave decision. Local runtime commit
`aff135274f26469012e117c1977a641fd8569999` routes publication through an
ephemeral authenticated app-server model turn, then performs an independent
exact-folder metadata readback. Lease binding is commit
`aff135274f26469012e117c1977a641fd8569999` (the lease-file update itself is
`963342806620c126ba4a4afdd2a94fa37507033f`). Focused tests pass `19/19` and the
full suite passes `282/282`. The GitHub-plugin publication is
`1303414310a1193e812dfcd3a7f7e53703f35b30` with tree
`e992d33f0a00d2fc1fa781b7219017eb386df9b5`, matching local `HEAD^{tree}`;
`force=false` was used. The current launchd publisher receipt
`20260902T042550.910403Z-3ad4684c` was produced by the pre-repair process and
is partial (23 verified skips, one historical metadata failure); it is not
treated as a new-chat success. The next natural collector/publisher cycle must
be read back before claiming automatic Drive appearance. Mini remains paused
and untouched; its requested closed log is absent and no bytes were changed.

## Checkpoint record

| Field | Value |
|---|---|
| Checkpoint ID | `SF-2026-09-01-drive-publisher` |
| Release under freeze | `drive-plugin-publisher` plus non-schema lock-wait configuration repair |
| Status | `satisfied-for-shipped-schema; Studio canary disk-gated; broad fleet completion pending` |
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
`270 tests, OK`. The later lock-timeout repair is non-schema configuration and
test coverage only; its separate bounded verification brought the repository
suite to `272 tests, OK`. No parallel schema review wave was used or authorized
for this checkpoint.

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
