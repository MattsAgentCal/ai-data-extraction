# Fleet chat archive

This local extension turns the upstream one-shot extractors into a recurring private archive for four explicitly approved harnesses: Claude Code, Codex, OpenClaw, and Hermes. No other source kind is accepted; in particular, Messages/iMessage is not supported. The implementation is locally tested; [`FLEET_CHAT_ARCHIVE_LIVE_STATE.md`](FLEET_CHAT_ARCHIVE_LIVE_STATE.md) is the source of truth for live readiness, and [`RECEIPT_FLEET_CHAT_ARCHIVE_2026-08-29.md`](RECEIPT_FLEET_CHAT_ARCHIVE_2026-08-29.md) holds the dated body-free evidence.

## Current live reconciliation — 2026-09-02T05:33Z

This body-free checkpoint supersedes the 04:50Z block below. Local `HEAD` is
`858d3aa` with tree `7b3e13c4296b61b7d008a4dd0935d5fc0160aeb9`; GitHub-plugin
commit `bdd361116860832e076c0f9576b7acde5ca02fc3` publishes that tree
non-force from remote parent `374b53eec092ee00181a542181efdd1f0baec50c`. The
Studio clone fast-forwarded cleanly to that commit and its shipped tree passes
`282/282` tests. New and Studio collectors remain launchd-owned at
`StartInterval=21600`; New is idle at `runs=13`, exit `0`, and Studio is idle at
`runs=3`, exit `0`.

| Host / route | Current evidence | Honest state |
|---|---|---|
| New MacBook | Persisted Codex canary `01a0607a-97d4-7c70-8386-2c9ca81f2bb1` has source mtime `2026-09-02T00:57:29-0400`, `133,692` bytes, SHA-256 `0c90fa63eaa4926de0b9d766e77c7df73d1e943fc8c11147570ab9e2192a8e1f`; no post-canary collector receipt exists yet. The last publisher receipt `20260902T042550.910403Z-3ad4684c` was partial (23 exact skips, one historical metadata failure, zero uploads). | **IN-FLIGHT:** preserve natural collection/publishing cadence and bind this canary to Drive. |
| Mac Studio | Clone is clean at `374b53eec092ee00181a542181efdd1f0baec50c`; launchd collector is idle at `runs=3`, exit `0`, and Studio tests are `282/282` OK. Latest receipt remains zero-error `20260902T013528.694544Z-bb6f6ea0`. | **DONE for reachable shipped collection; fresh Drive correlation remains in-flight through New.** |
| Mac Mini | Exact `CoreSimulator.prev.log` and `.gz` are absent. Available space readback is `27,012,556 KiB` = `27,660,857,344` bytes (`25.7611808777 GiB`); source/archive/deletion/freed bytes are all `0`, and active `CoreSimulator.log` was untouched. | **PAUSED / not started:** no gzip, `gzip -t`, spot-decompress, deletion, export, or canary. |
| Old MacBook | SSH timed out; the New-host retry label remains loaded at six hours with `offline_retryable` / `ssh_unreachable` receipts. | **IN-FLIGHT:** nonblocking retry; deployment unverified. |
| Drive plugin | Target folder remains `AI Chat Archive` (`1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV`); the previously verified model-turn object/readback remains valid. | **Route DONE; automatic fresh-chat appearance pending.** |

No raw conversation bodies or plugin transcripts are present in this checkpoint.

## Current live reconciliation — 2026-09-02T04:50Z

This body-free checkpoint supersedes the older dated snapshots below. The
MacBook checkout is local `963342806620c126ba4a4afdd2a94fa37507033f` (tree
`e992d33f0a00d2fc1fa781b7219017eb386df9b5`) and the same tree is published on
the owned fork by GitHub-plugin commit
`1303414310a1193e812dfcd3a7f7e53703f35b30`, using `force=false`. The runtime
change is `aff135274f26469012e117c1977a641fd8569999`; it routes Drive uploads
through an ephemeral authenticated Codex app-server model turn and independently
re-verifies Drive metadata. Focused tests are `19/19`; the full suite is
`282/282`.

| Host / route | Current evidence | Honest state |
|---|---|---|
| New MacBook | Collector label is loaded under launchd (`runs=13`, exit `0`, `StartInterval=21600`). Receipt `20260901T211000.917786Z-97b49fd7` finished `2026-09-01T23:21:40Z` with Claude 3/2 new, Codex 11/11, Hermes 4/3, and OpenClaw inventory-only. Publisher receipt `20260902T042550.910403Z-3ad4684c` ran under launchd against New + Studio shards and ended partial: 23 exact metadata-verified skips, one failed historical object; its process exited before the receipt-field repair. | **IN-FLIGHT:** preserve natural six-hour collection and publisher cadence; correlate a fresh chat through object, receipt, and Drive. |
| Mac Studio | Collector is loaded and idle (`runs=3`, exit `0`, `StartInterval=21600`). Receipt `20260902T013528.694544Z-bb6f6ea0` is zero-error: Claude 14/8 new, Codex 463/463, Hermes 186/1, OpenClaw absent. Free space was `417,574,804 KiB`. New's publisher validates this shard; Studio has no separate Drive publisher. | **DONE for reachable collection; Drive correlation remains in-flight through New.** |
| Mac Mini | SSH is reachable, but export/canary/schedule remains paused by instruction. Exact `CoreSimulator.prev.log` and `.gz` are absent; active `CoreSimulator.log` was not touched. Current free space was `27,667,742,720` bytes (`25.767593 GiB`); archive and freed bytes are `0`. | **PAUSED / not started:** no gzip, verification, deletion, or four-harness canary. |
| Old MacBook | SSH timed out; the New-host retry label remains loaded at six hours and latest evidence is `offline_retryable` / `ssh_unreachable`. | **IN-FLIGHT:** nonblocking retry; deployment remains unverified. |
| Drive plugin | Folder `AI Chat Archive` is `1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV`. A live model-turn write/readback verified `8ef66ffab4606d80134252cc4040042d4ab753da58167ec66956c4dbee39f6b1.json` as file `1-g2pg5Ce498zwWDG2kd0MyUL57FKEoJv`, `text/plain`, 67,687 bytes, exact parent. | **DONE:** plugin route and metadata gate; **IN-FLIGHT:** natural new-chat proof. |

The only remaining Matt-controlled data gate is Mini deletion (and any future
decision to start its paused export). The former Drive Desktop install/login
gate is superseded by the activated Codex plugin. Raw bodies remain host-local
and are not present in this checkpoint.

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

## Latest live reconciliation — 2026-09-01T11:53Z

This body-free checkpoint supersedes the 11:31Z block below. At
`2026-09-01T11:49:53.147944Z`, a deliberately tiny **persistent** Codex canary
completed successfully as thread
`01a05ccd-e6b1-7510-82fa-e2296481b2f1`. Its session file is under the configured
`/Users/mattrotundo/.codex/sessions/2026/09/01` source tree, is `134,708` bytes,
and has SHA-256
`8a76ae73ad5de14d330e99374ee3553c2206a55bfe98741b37322b161a2ae5e3`. The
collector and publisher were not manually kicked; the next natural windows are
approximately `2026-09-01T16:22:36Z` and `2026-09-01T16:37:07Z` based on their
last completed receipts and six-hour intervals.

| Host / route | Observed state | Honest classification |
|---|---|---|
| New MacBook | At `2026-09-01T11:53:49Z`, collector `runs=11` and publisher `runs=3` were idle, both exit `0`, and both `StartInterval=21600`. The persistent canary is present in the configured Codex root; no post-canary receipt exists yet. | **IN-FLIGHT:** leave the natural cycle untouched and correlate the new object through Drive. |
| Mac Studio | At `2026-09-01T11:52:53Z`, free space was `127,312 KiB`; the shipped label is idle at `runs=1`, exit `143`, `StartInterval=21600`. | **DISK-GATED:** no retry or quarantine mutation. |
| Mac Mini | At `2026-09-01T11:48:38Z`, the exact requested source and `.gz` were absent; free space was `29,876,336 KiB` = `30,593,368,064` bytes. Direct source/archive sizes were `0`; no bytes changed. | **DONE:** guarded no-op; export and schedule remain paused. |
| Old MacBook | At `2026-09-01T11:52:53Z`, SSH remains unreachable; the retry label is loaded at `runs=9`, exit `0`, `StartInterval=21600`. | **IN-FLIGHT:** nonblocking offline retry. |
| Drive plugin | The prior supervised batch remains `24/24/0/0`; no new post-canary publication has occurred yet. | **DONE:** staged route; **IN-FLIGHT:** automatic new-chat proof. |

The 11:31Z reconciliation remains below as historical evidence.

## Historical live reconciliation — 2026-09-01T11:31Z

This body-free checkpoint supersedes the 11:14Z block below. Its parent
GitHub-plugin documentation ref is `f23912ffaed783ee5be84b441980f9dc68d818cf` (tree
`2f68e6c0dcc2193bec6d0ab3128ab0d9c1b24c35`), parent `68c02d1…`, and the local
checkout has the same tree. The authenticated Drive plugin canary wrote and
read back `ai-chat-archive-plugin-canary-20260901.txt` in the target folder
(`text/plain`, `18,167` bytes, exact parent), then deleted only that canary;
the archive batch remains unchanged. New's next natural run is intentionally
left loaded for the automatic-chat proof; no manual kick was used.

| Host / route | Observed state | Honest classification |
|---|---|---|
| New MacBook | Collector label is idle at `runs=11`, exit `0`, `StartInterval=21600`; publisher is idle at `runs=3`, exit `0`, same interval. Baseline readback at `2026-09-01T11:31:12Z` precedes the next natural cycle. The ephemeral Drive-canary Codex thread (`01a05cb9-bfca-7252-a5c1-cc0e514b5d90`) is a body-free new-chat candidate for that cycle. | **IN-FLIGHT:** natural six-hour collection, publisher correlation, and Drive appearance. |
| Mac Studio | Shipped label remains idle after supervised PID `54195` termination (`runs=1`, exit `143`). At `2026-09-01T11:26:37Z`, free space was `973,732 KiB` and `.work` was empty; quarantine remains `73,136,828 KiB` across `22,501` files. | **DISK-GATED:** no retry or unrelated cleanup. |
| Mac Mini | The requested `CoreSimulator.prev.log` and `.gz` remain absent; latest free-space sample at `2026-09-01T11:24:16Z` was `29,893,336 KiB` = `30,610,776,064` bytes. No bytes were written or deleted. | **DONE:** guarded no-op; export/schedule remain paused. |
| Old MacBook | SSH remains unreachable; the loaded retry queue remains the only action. | **IN-FLIGHT:** non-blocking offline retry. |
| Drive plugin | The prior launchd publisher receipt remains `24` candidates / `24` uploaded / `0` failed; the separate canary write/readback/cleanup succeeded. | **DONE:** route access and staged batch; **IN-FLIGHT:** automatic new-chat proof. |

The 11:14Z reconciliation remains below as a historical receipt.

## Historical live reconciliation — 2026-09-01T11:14Z

This body-free checkpoint is the current rollout truth. The shipped tree is
local commit `88d65a7677dbdb483b36a85ff59b907231727502` / tree
`5e33dd15aeb26ecbe059ac6cf83338442a21c493`, and the GitHub-plugin ref is
`68c02d1953b428b2ecf6443e26a56560bb81f436` with the same tree. The one
lock-wait repair was tested with `9/9` focused tests and `272` full-suite tests,
all OK. The active lease remains owned by `codex:macbook`.

| Host / route | Observed state | Honest classification |
|---|---|---|
| New MacBook | Launchd label is idle after `runs=11`, exit `0`, `StartInterval=21600`. Receipt `20260901T064318.917191Z-a3e5881a` collected at `2026-09-01T10:22:36.435118Z`; errors `[]`; Claude 31/1 new, Codex 1,224/17 new, Hermes 4/2 new, OpenClaw absent/inventory-only, present-harness quality complete. | **DONE:** shipped-tree terminal collector canary. **IN-FLIGHT:** wait for a second natural interval to strengthen the six-hour proof. |
| Mac Studio | Fast-forwarded cleanly to `68c02d1…` / tree `5e33dd15…`. The launchd-owned canary ran as PID `54195`/PPID `1`; launchd terminated it with `SIGTERM` (`exit=143`) after free space fell below 2 GiB. Receipt `20260901T104451.299032Z-40cc7d40` is `failed`/`RunFailure`; staging and lock are clean. Existing quarantine is `74,892,111,872` bytes (`73,136,828 KiB`, `22,501` files). | **IN-FLIGHT / DISK-GATED:** do not retry until quarantine cleanup is explicitly authorized; no unrelated data was deleted. |
| Mac Mini | The requested closed log and `.gz` sibling are absent. Free space was `30,606,172,160` bytes before and after; source/archive/deletion/net-freed bytes are `0`. | **DONE:** bounded no-op. **NOT STARTED:** gzip/test/deletion because no source exists; deletion gate remains untouched. |
| Old MacBook | SSH still times out. The loaded launchd retry queue is at `runs=9`, exit `0`, `StartInterval=21600`, with `offline_retryable` receipts. | **IN-FLIGHT:** non-blocking retry; online deployment/canary is not started. |
| Drive plugin publisher | Launchd publisher is idle after `runs=3`, exit `0`, `StartInterval=21600`. Receipt `20260901T103707.805024Z-c2f89e5e.json`: 24 candidates, 24 uploaded, 0 skipped, 0 failed, `errors=[]`, target folder `1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV`. Connector metadata readback verified file `10C4tI7CAeFTn_rYSFICo_9lpdOQ86a0U` as exact-name `text/plain`, 3,475 bytes, exact parent. | **DONE:** one supervised batch and independent metadata proof. **IN-FLIGHT:** a newly collected chat appearing in Drive automatically. |

Controls are structure: every long canary is launchd-only; `.deployment-lease.json`
names the sole release owner; schema freeze is one live census, one canonical
validator, and one batched review wave; and findings are repaired once and
re-reviewed once. The Drive Desktop DMG route is superseded by the activated
plugin route. No raw bodies, indexes, databases, credentials, or quarantine
contents were opened.

## Historical reconciliation — 2026-09-01T08:45Z

This body-free checkpoint supersedes the older `04:21Z` block below. The
repaired code is committed locally as `4b665987ad9c06c618a73fc67e7b6004d1bd1881`
(`300ce290c9d75e4187a240770db7f9793c57d577`) and was published by the GitHub
plugin as `ec96761786196f58b8157de8dc917c85947a09b8` with the same tree. The
post-repair suite is 270 tests, OK. The active lease is still owned by
`codex:macbook`; no other session may deploy or publish.

| Host / route | Observed state | Honest classification |
|---|---|---|
| New MacBook | `com.mattrotundo.ai-chat-archive.new-macbook` is launchd-owned as PID 84541/PPID 1, `runs=11`, `StartInterval=21600`, and active. Receipt count is 170; the current run is the one post-repair full Codex revalidation and has not emitted a terminal receipt yet. | **IN-FLIGHT:** one supervised canary; do not kill, foreground, or retry it. |
| Mac Studio | Live checkout is `bb9a08570baffc2111e832ac7418bab9d33755af` (tree `8f4f0122…`), launchd collector idle at `runs=1`, last exit 0, `StartInterval=21600`, with 51 body-free receipts. | **IN-FLIGHT:** fast-forward to the published repaired tree and run one supervised canary only after New's terminal receipt. |
| Mac Mini | Exact `/Users/calrotundo/Library/Logs/CoreSimulator/CoreSimulator.prev.log` and `.gz` paths are absent. A single pre/post check measured 29,895,640 KiB = 30,613,135,360 bytes free both times; source, archive, deletion, and freed bytes are all 0. | **DONE:** non-destructive check. **GATED / NOT STARTED:** no gzip/test/deletion was possible without a source; never delete the original without Matt's gate. |
| Old MacBook | SSH is unreachable; `com.mattrotundo.ai-chat-archive.old-macbook-deploy-retry` remains a loaded 21,600-second launchd retry queue. | **IN-FLIGHT:** non-blocking retry; online deploy/canary is not started. |
| Drive plugin publisher | Target `AI Chat Archive` is `1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV`. Receipt `20260901T025751.237727Z-74cdf5d2.json` remains partial (8 uploads, 14 verified skips, 2 connector failures). | **IN-FLIGHT:** kick once after a healthy collector receipt and verify exact Drive metadata; automatic launchd-to-plugin publication is not yet proven. |

Controls are structure: every long canary is launchd-only; `.deployment-lease.json`
names the sole release owner; schema freeze is one live census, one canonical
validator, and one batched review wave; and findings are repaired once and
re-reviewed once. The Drive Desktop DMG route is superseded by the activated
plugin route. No raw bodies, indexes, databases, or credentials were opened.

## Historical reconciliation — 2026-09-01T04:21Z

This body-free readback supersedes the 2026-08-30 snapshot below. It records
the active deployment lease, launchd ownership, connector publication state,
and the Mini non-destructive disk check. No transcript body, database, index,
or credential was opened or copied into Git.

| Host / route | Observed state | Honest classification |
|---|---|---|
| New MacBook | Local checkout `e167feb0f5997458c2411195a4380b7d316ce72b`; fork ref `a3a8ad5e9da7eb0aa44cb03b5c2440f3d3b7530f`; both tree `8914a6e275cf898d84a3ec4ff7f26c6bce149b13`. `com.mattrotundo.ai-chat-archive.new-macbook` is launchd-owned (`PID 30001`, `PPID 1`, `runs=9`, `StartInterval=21600`, active) and is validating the pulled Studio shard. Its incoming staging currently contains 1,160 files and 2,166,120 KiB; the next receipt is not yet written. | **IN-FLIGHT:** current supervised transfer/validation; do not kill or foreground it. |
| Mac Studio | Clean checkout remains `9251ff6de160c8a1c4c4647a61ea9ec7c512f6dd`; fetched remote-tracking ref is `a3a8ad5e9da7eb0aa44cb03b5c2440f3d3b7530f`. The collector was booted out to release the reciprocal lock; the honest stop receipt is `20260901T024039.360623Z-bc4432a1.json` (`RunFailure`, publication not attempted). The last-good manifest remains 1,155 objects; reinstall waits until New finishes draining the shard. | **IN-FLIGHT:** fast-forward/reinstall the corrected pull-only config after New's receipt; never restart a competing long scan. |
| Mac Mini | A filename-only search of `/Users/calrotundo` found no `CoreSimulator.prev.log*`; no `gzip` process is present. Available space at the check was 30,289,448 KiB (31,016,394,752 bytes). No gzip output was created and no file was deleted: source bytes 0, archive bytes 0, deletion 0, freed 0. | **DONE:** non-destructive check. **GATED / NOT STARTED:** export, cleanup, canary, and schedule; do not touch the Mini again until the source/gate is clarified. |
| Old MacBook | SSH remains unreachable; `com.mattrotundo.ai-chat-archive.old-macbook-deploy-retry` remains a loaded `StartInterval=21600` retry queue with body-free offline receipts. | **IN-FLIGHT:** non-blocking retry; online deployment/canary is not started. |
| Drive plugin publisher | Target folder is `AI Chat Archive` (`1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV`). Launchd publisher receipt `20260901T025751.237727Z-74cdf5d2.json` recorded 8 uploads, 14 verified skips, and 2 `connector_error` failures. | **IN-FLIGHT:** rerun only after the collector receipt and lock release; automatic new-chat proof is not yet complete. |

The binding controls remain structural: every long canary runs under launchd,
never a foreground session; `.deployment-lease.json` names the single release
owner and other sessions stand down; a schema-freeze checkpoint precedes any
broad release (one live-schema census, one canonical validator, one review
wave); and any review wave batches findings into one repair and one re-review.

## Historical host state as of 2026-08-30T11:05:59Z

| Host | Verified rollout truth | Scheduler / blocker |
|---|---|---|
| New MacBook | Runtime `3c732d7`; docs baseline `754af13`, reconciliation committed locally as `021a4e0`; launchd label `com.mattrotundo.ai-chat-archive.new-macbook` is idle after `runs=4`, last exit 0, with `StartInterval=21600`. The three successive completed receipts are `20260829T195959.324876Z-be2608f7` (`20:19:56.940667Z`), `20260830T022016.788683Z-c11f0266` (`02:39:32.956025Z`), and `20260830T083957.226400Z-280640fe` (`09:08:58.239553Z`); the gaps are 6:19:36.015358 and 6:29:25.283528. The latest is zero-error `completed_with_absent_harnesses`, `blocked_no_drive_root`, Claude 1 conversation/0 new objects, Codex 4 conversations/4 new objects, Hermes 0/OpenClaw absent. Four latest redacted objects were imported once through the Drive plugin and verified. | Repeated six-hour elapsed collection proof and eleven-object plugin publication are **DONE**; automatic runtime Drive publication is **IN-FLIGHT** because the plugin has no launchd/shell bridge. |
| Mac Studio | Runtime `3c732d7`; clean checkout HEAD `5cd62da` (runtime file hashes match the reviewed pin); `com.mattrotundo.ai-chat-archive.mac-studio` is idle after `runs=2`, last exit 0, `StartInterval=21600`. The long process was launchd-owned (`PID 76865`, `PPID 1`) from `00:02:18` until completion. Receipt `20260830T040218.778731Z-c65bac22` collected at `07:38:20.693304+00:00` reports `completed_with_absent_harnesses`, errors `[]`, and runtime `publication=blocked_drive_unavailable`: Claude 18/16 new objects/319 redactions, Codex 71/62/881, Hermes 0/0, OpenClaw absent. Exactly 78 finalized objects were imported through the Drive plugin; one bounded repair/re-review recovered the three initially absent Codex objects. | Checkout, preflight, schedule, supervised scan, receipt, and 78-object plugin publication are **DONE**; runtime automatic Drive publication and the end-to-end new-chat proof remain **IN-FLIGHT**. |
| Mac Mini | Runtime `3c732d7`; no production archive label or process is loaded. A read-only SSH census at approximately `2026-08-30T11:05Z` found `6,458,476 KiB` available on `/System/Volumes/Data` (~6.16 GiB, 97% full). Mini logs were not read or touched; the older log-size figures in historical sections are not a current census. | Storage approval is required before any export, cleanup, canary, or schedule; no log was touched. |
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

At the 11:05Z reconciliation, plugin management reported Google Drive
enabled/installed, but this Codex session had no callable Drive or GitHub
connector tool. The bounded Drive test-write attempt returned
`TypeError: tools.mcp__codex_apps__google_drive is not a function`; no new
connector write or shell GitHub push was attempted. The latest fork ref seen by
`git ls-remote` was `2f466bd9d6041b8494eb23377b737d9f49c867d8`; the docs
baseline was `754af13bbcf62b3c2a1d87c4801f1773ceed6002` and the reconciliation
is committed locally as `021a4e0b1461e7bb542ea918c043e8cea9770fda`.

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
