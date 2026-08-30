# Fleet chat archive

This local extension turns the upstream one-shot extractors into a recurring private archive for four explicitly approved harnesses: Claude Code, Codex, OpenClaw, and Hermes. No other source kind is accepted; in particular, Messages/iMessage is not supported. The implementation is locally tested; [`FLEET_CHAT_ARCHIVE_LIVE_STATE.md`](FLEET_CHAT_ARCHIVE_LIVE_STATE.md) is the source of truth for live readiness, and [`RECEIPT_FLEET_CHAT_ARCHIVE_2026-08-29.md`](RECEIPT_FLEET_CHAT_ARCHIVE_2026-08-29.md) holds the dated body-free evidence.

## Data path

1. When its reviewed scheduler is enabled, each Mac collects local harness data every six hours into an owner-only spool at `~/.local/share/ai-chat-archive/spool`. Unchanged transcript files are fingerprinted and skipped; changed sessions are streamed one at a time.
2. Conversations are normalized into the closed archive-object v2 contract, credential-redacted locally, staged until parsing is complete, stored as immutable content-addressed objects, and indexed separately by host and harness. Message content and normalized context/tool/event `payload` values are the only intentionally opaque body-bearing fields; their wrappers remain exact and bounded.
3. The Mac Studio invokes the same trusted Python pipeline on each source host over authenticated SSH. The source validates one manifest-bound shard under its archive lock and emits a bounded binary stream; the Studio stages and validates that stream before a transactional logical-session merge.
4. The six-hour launchd collectors stage each host's validated tree locally. The
   authenticated Google Drive plugin can then import the exact staged,
   redacted object files into the private `AI Chat Archive` folder; the local
   Studio File Provider path remains a separate runtime publication route. A
   plugin import is verified by target-folder parent, MIME, and byte size and
   must never be described as an automatic launchd-to-plugin call.

The Google Drive account is needed on only the Studio. Host folders never share mutable object files, preventing cross-machine filename collisions.

## Safety contract

- Raw conversation bodies never enter Git or AgentBrain.
- Every output directory is created owner-only (`0700`) and every artifact owner-only (`0600`) where the filesystem supports POSIX modes.
- Credential-like structured fields and headers, assignments, provider tokens, JWTs, cookies, and private-key blocks are replaced with `[REDACTED]` before hashing or publication. A second validation pass blocks recognized residuals. This is a deterministic first gate, not a guarantee that every possible secret pattern is covered.
- Hermes uses its supported `sessions export --format jsonl --redact` command. The live Hermes SQLite database is never copied.
- OpenClaw reads v3 transcript JSONL only. Its credential-bearing databases are never copied.
- Every valid-spool run writes a body-free receipt, including failed runs, with host/harness counts, file-processing and parse-quality counts, redaction counts, publication status, and an extractor hash.
- A `publish-manifest.json` atomically binds the exact healthy receipt, config/extractor hashes, harness indexes, and object set that may cross a machine boundary. Unbound files from an interrupted transfer are excluded, and a retry repairs an interrupted additive index update from the last manifest.
- Malformed or missing configured sources are marked incomplete and are not published. Prior index rows and immutable objects remain discoverable.
- Remote shards are emitted from no-follow byte snapshots while the source archive lock is held, staged owner-only on the receiver, symlink-rejected, content-hash verified, and transactionally published without overwriting a differing immutable object. A process lock serializes scheduled and manual runs.
- Publication positively requires a mounted path below the current user's `~/Library/CloudStorage/GoogleDrive-*`. Production configs cannot bypass this gate.
- Every long canary is a launchd-owned node, never a foreground terminal or
  agent process. Its plist must expose absolute repo/config paths, owner-only
  stdout/stderr, `Umask=077`, and read-backable run/exit state; a loaded
  `RunAtLoad`/`StartInterval` job proves supervision and cadence, not Drive
  publication.
- The repo-root [`.deployment-lease.json`](../.deployment-lease.json) is the
  machine-readable release-owner gate. A session must read and match its owner,
  host, session, branch, scope, and expiry before any deploy, restart, merge, or
  connector write; every other session stands down. Transfer requires an owner
  commit.
- Before any broad release, the tracked
  [`SCHEMA_FREEZE_CHECKPOINT_2026-08-29.md`](SCHEMA_FREEZE_CHECKPOINT_2026-08-29.md)
  must record exactly one live-schema census, one canonical
  `validate_archive_object` validator/hash, and one batched review wave. A new
  runtime requires a new checkpoint; this record is not edited in place.
- Existing and newly copied objects are verified against their content-addressed filenames and approved harness provenance before publication; receipts, indexes, and the final manifest are verified after copying.
- No pipeline command deletes a source chat or destroys a superseded archive body. A changed `(harness, source, session_id)` replaces its live index row; the prior object remains available for rollback until the healthy manifest commits, then moves to owner-only quarantine. Healthy remote snapshot replacement likewise quarantines a superseded last-good tree instead of discarding its stale body.

## Trusted remote stream

Each SSH remote must configure all of `host_id`, `ssh_host`,
`remote_spool_root`, `remote_pipeline_path`, and optionally
`timeout_seconds`. `remote_pipeline_path` is an absolute path to the reviewed,
deployed `fleet_chat_archive.py` on that source host. The Studio configuration
currently expects:

- New and Old MacBooks: `/Users/mattrotundo/Projects/ai-data-extraction/fleet_chat_archive.py`
- Mac mini: `/Users/calrotundo/Projects/ai-data-extraction/fleet_chat_archive.py`

The trust boundary is the owned host authenticated by its pinned SSH host key
plus that exact deployed helper. There is no additional shard-signing key. A
release is not ready merely because the Studio contains newer code: the
reviewed helper must be deployed at every configured source path, and each SSH
alias must already resolve to the intended pinned host key.

The helper validates the exact manifest, receipt, local index, v2 per-harness
object schema, provenance, canonical JSON, redactor idempotence, hash, and
residual-secret rules for the whole authorized snapshot before emitting even
the protocol magic. Its preflight also performs the receiver-equivalent
projection binding, canonical row reconstruction, ordering, and manifest index
hash check. It then takes and revalidates a fresh no-follow object
snapshot immediately before emitting that body. The receiver uses one
end-to-end monotonic deadline, writes into an owner-only incoming directory,
rejects malformed, duplicate, traversing, extra, or oversized frames, then runs
the full shard validator before transactional merge.

All v2 archive objects and indexes require a stable, non-empty session ID.
Claude and OpenClaw can fall back to their transcript filename identity;
Hermes requires its exported row ID; and a Codex rollout without
`session_meta.id` receives a deterministic opaque ID derived from hashed
installation identity plus its validated rollout filename. That filename is
stable when Codex moves a transcript from `sessions/YYYY/MM/DD` to
`archived_sessions`; nonstandard locations retain a hashed relative-path
identity. These fallback inputs never appear in transport metadata. Historical
nullable rows are replaced by a complete local regeneration and cannot enter
the remote stream.

Ordinary indexes never cross SSH. The source emits a fixed-format transport
projection containing the object digest, approved source enum, and a SHA-256
commitment to the native session ID. Every v2 Codex row must use the current
schema carrying the source digest, a commitment to `installation`, and an explicit schema
discriminator. The receiver validates each v2 object, checks those commitments,
reconstructs the ordinary canonical index (including the unrestricted native
ID and Codex installation from the sanitized body), and requires its hash to
equal the original manifest binding. Thus source-controlled session IDs and
installation paths do not appear in pre-body transport metadata.
Archive-object, manifest, index, transport, and receipt schema discriminators
must be exact JSON integers; booleans and floating-point lookalikes fail closed.

Each metadata frame is capped at 16 MiB and all metadata together at 32 MiB;
the observed indexes are below 174 KiB and the observed manifest is 78 KiB.
Objects retain the compatible 1,280 MiB cap (observed maxima: Claude 27,948,676
bytes, Codex 552,957,718 bytes, Hermes 109,244 bytes). Validation retains one
parsed object tree at a time. Peak validation memory still includes the input
byte snapshot, one canonical byte representation, and the parser's object tree,
so the largest Codex object needs substantial free RAM even though multiple
parsed conversation trees are never accumulated.

For a fully validated cached shard, the Studio sends a bounded set of cached
object-digest hints. Hints only suppress network transmission: the source still
opens and validates every manifest object, and the receiver reconstructs hinted
objects only from its already validated cache. A stale, forged, or mismatched
hint cannot authorize a body and causes final validation to fail closed.

Remote receipt statuses distinguish `pending_manifest`, `legacy_schema`, `unreachable`,
`timeout`, `remote_integrity_rejection`, and `local_integrity_rejection`.
Successful imports report `pulled`; a valid cached shard can still be published
when the new remote attempt reports `unreachable`.

## Current host state as of 2026-08-30T09:40:49Z

| Host | Verified rollout truth | Scheduler / blocker |
|---|---|---|
| New MacBook | Runtime `3c732d7`; launchd label `com.mattrotundo.ai-chat-archive.new-macbook` is idle after `runs=4`, last exit 0, with `StartInterval=21600`. The three successive completed receipts are `20260829T195959.324876Z-be2608f7` (`20:19:56.940667Z`), `20260830T022016.788683Z-c11f0266` (`02:39:32.956025Z`), and `20260830T083957.226400Z-280640fe` (`09:08:58.239553Z`); the gaps are 6:19:36.015358 and 6:29:25.283528. The latest is zero-error `completed_with_absent_harnesses`, `blocked_no_drive_root`, Claude 1 conversation/0 new objects, Codex 4 conversations/4 new objects, Hermes 0/OpenClaw absent. Four latest redacted objects were imported once through the Drive plugin and verified. | Repeated six-hour elapsed collection proof and eleven-object plugin publication are **DONE**; automatic runtime Drive publication is **IN-FLIGHT** because the plugin has no launchd/shell bridge. |
| Mac Studio | Runtime `3c732d7`; `com.mattrotundo.ai-chat-archive.mac-studio` is idle after `runs=2`, last exit 0, `StartInterval=21600`. The long process was launchd-owned (`PID 76865`, `PPID 1`) from `00:02:18` until completion. Receipt `20260830T040218.778731Z-c65bac22` collected at `07:38:20.693304+00:00` reports `completed_with_absent_harnesses`, errors `[]`, and runtime `publication=blocked_drive_unavailable`: Claude 18/16 new objects/319 redactions, Codex 71/62/881, Hermes 0/0, OpenClaw absent. Exactly 78 finalized objects were imported through the Drive plugin; one bounded repair/re-review recovered the three initially absent Codex objects. | Checkout, preflight, schedule, supervised scan, receipt, and 78-object plugin publication are **DONE**; runtime automatic Drive publication and the end-to-end new-chat proof remain **IN-FLIGHT**. |
| Mac Mini | Runtime `3c732d7`; no production archive label or process is loaded. The latest read-only census found approximately 7.29 GB free, active `CoreSimulator.log` about 5.51 GB, and closed `CoreSimulator.prev.log` 15,403,577,516 bytes. | Storage approval is required before any export, cleanup, canary, or schedule; no log was touched. |
| Old MacBook | `ssh oldmac` remains unreachable. New's retry label is loaded at `StartInterval=21600`, `runs=3`, exit 0, with `offline_retryable`/`ssh_unreachable` records. | Non-blocking retry queue is **IN-FLIGHT**; online deployment/canary is not started. |

The Drive plugin test write was body-free canary ID
`1kFQrT2pFI2qSZl2A1Fovv-qyagvod47l` (125 bytes, `text/plain`, exact target
parent). It was followed by seven New objects, all 78 Studio objects, and the
four objects from New receipt `20260830T083957.226400Z-280640fe`, verified by
exact parent, MIME, and size. The `AI Chat Archive` folder now has 94 items
(92 `text/plain` files plus two receipt Docs; 634,718,155 listed bytes), and
the Studio set totals 418,815,569 bytes. This is staged plugin publication, not
evidence that launchd can call the plugin: `codex mcp list` has no Google Drive
server and no shell `mcp`, `gdrive`, or `rclone` bridge. Studio has no File
Provider, so automatic launchd-to-Drive and the success criterion “Matt watches
a new chat appear in Drive” remain unproven.

## Historical host state as of 2026-08-30T06:17:38Z

| Host | Verified rollout truth | Scheduler / blocker |
|---|---|---|
| New MacBook | Runtime `3c732d7`; launchd label `com.mattrotundo.ai-chat-archive.new-macbook` is loaded with `RunAtLoad=true`, `StartInterval=21600`, `runs=3`, state `not running`, exit 0. Receipt `20260830T022016.788683Z-c11f0266` is `completed_with_absent_harnesses`, zero errors, `blocked_no_drive_root`; Claude 1, Codex 6, Hermes none, OpenClaw absent. Its prior receipt is 6:19:36 earlier, and seven new redacted objects from the follow-up were imported and verified through the Drive plugin. | Six-hour elapsed collection proof and seven-object plugin publication are **DONE**; automatic runtime Drive publication remains **IN-FLIGHT** because no local provider is mounted. |
| Mac Studio | Runtime `3c732d7`; launchd label `com.mattrotundo.ai-chat-archive.mac-studio` is loaded with `RunAtLoad=true`, `StartInterval=21600`, `runs=2`, `state=running`, PID `76865` (PPID 1), and `last exit code=0`. The process started at `00:02:18` local time and was still active at the 06:17:38Z poll. Receipt `20260829T184201.313238Z-c87faa38` was collected at `2026-08-29T22:01:36.872837Z`, reports `completed_with_absent_harnesses`, zero errors, and `publication=blocked_drive_unavailable`; the second scan is still in-flight. CloudStorage has zero `GoogleDrive-*` providers. | Checkout, preflight, schedule, and first supervised scan are **DONE**; second scan, Studio plugin publication, and runtime File Provider publication are **IN-FLIGHT**. |
| Mac mini | Clean at runtime `3c732d7`; no production archive label is enabled. The latest read-only census found approximately 7.29 GB free; active `CoreSimulator.log` is about 5.51 GB and closed `CoreSimulator.prev.log` is 15,403,577,516 bytes. | Storage approval is required before any canary or schedule; no cleanup or log mutation was performed. |
| Old MacBook | Offline; `oldmac` remains unreachable. New's retry label `com.mattrotundo.ai-chat-archive.old-macbook-deploy-retry` is enabled/loaded under launchd with `StartInterval=21600`, `runs=3`, exit 0, and `offline_retryable`/`ssh_unreachable` records. | Retry queue is **ACTIVE**; no live deployment or canary proof. |

`configs/old-macbook.pending.json` is a placeholder, not a deployment receipt. It must be checked against the live host before installation.

The Google Drive plugin is authenticated. It created and verified the private
folder `AI Chat Archive` (ID `1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV`) and its two
body-free receipt Docs, then imported the two prior redacted text artifacts and
exactly seven newly staged New-host objects (folder count 5→12), with each
file's parent, MIME, and size read back. The runtime File Provider is still not
installed or mounted on Studio, so the local launchd process cannot invoke the
plugin automatically; plugin publication and automatic runtime Drive
publication are separate statuses.

Existing v1 objects, nullable-session rows, and legacy base-format Codex rows
are not trusted or silently migrated by the new stream.
The extractor hash forces a fresh v2 collection while the previous manifested
snapshot remains the rollback point. A source reports `legacy_schema` until a
complete v2 manifest commits. Reviewed deployment therefore requires new v2
canaries on every reachable host before any scheduler is enabled.

## Commands

Run one configured collection:

```bash
python3 fleet_chat_archive.py run --config configs/new-macbook.json
```

Install or refresh the per-user six-hour launchd job:

```bash
python3 fleet_chat_archive.py install-launchd --config configs/new-macbook.json --interval-seconds 21600
```

The trusted-stream commit is reviewed and deployed to the reachable configured
helper paths. New and Studio have the production six-hour labels loaded under
launchd. New's receipts are 6:19:36 apart with launchd `runs=2→3` and exit 0,
which proves one elapsed collection cycle after the controlling session was
gone. A loaded plist plus receipt does not by itself prove an automatic
launchd-to-plugin call; Studio's second launchd attempt completed with receipt
`20260830T040218.778731Z-c65bac22`, and its runtime publication remains
`blocked_drive_unavailable`.

The Studio config polls `newmac`, `cals-mac-mini`, and `oldmac` only when its
scheduler or a reviewed manual run is active. An offline remote is recorded as
`unreachable`; it does not invalidate a previously verified cached shard.

## Historical execution readback — 2026-08-30T06:17:38Z

New's launchd-owned receipts are 6:19:36 apart (`20260829T195959.324876Z-be2608f7`
at `20:19:56.940667Z` and `20260830T022016.788683Z-c11f0266` at
`02:39:32.956025Z`), with launchd `runs=2→3`, exit 0, zero errors, and seven
new redacted objects. The Drive plugin imported exactly those seven objects and
the folder count moved from 5 to 12 with metadata/listing verification. Studio's
second launchd-owned transaction remains active at `runs=2`, PID `76865`, PPID 1,
and `last exit code=0` at the 06:17:38Z poll; its last persisted receipt is
`20260829T184201.313238Z-c87faa38`, zero errors, `blocked_drive_unavailable`.
Mini remains paused and Old remains on its
non-blocking retry queue. No release, lease, schema checkpoint, or review wave
changed.

## Google Drive publication route

The Desktop DMG path is superseded. Use the authenticated Google Drive plugin
for staged, redacted object publication. The target folder is `AI Chat Archive`
(ID `1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV`). Each import must be restricted to
explicit object paths from a completed, zero-error receipt; move the imported
file into that folder and read back its exact parent, MIME, and byte size.
Do not upload raw indexes, databases, receipts, or unreviewed files.

The Studio config still uses `drive_root: "auto"` for the optional local runtime
route. Once exactly one live Google Drive File Provider appears, that route uses
this private folder (the plugin has reserved the matching Drive folder ID, but
that does not create a local mount):

```text
/Users/calstudio/Library/CloudStorage/GoogleDrive-<account>/My Drive/AI Chat Archive
```

Zero providers remain blocked as unavailable; multiple signed-in Google accounts
remain blocked as ambiguous instead of guessing. A plugin import is a manual
publication receipt, not proof that launchd can call a plugin. End-to-end
automatic new-chat-in-Drive remains unproven until a completed scheduled receipt
and a corresponding Drive object are both observed; the current New six-hour
proof establishes collection and plugin publication separately, while Studio's
78-object plugin publication is complete but its runtime File Provider route is
still unavailable.
