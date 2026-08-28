# Fleet chat archive

This local extension turns the upstream one-shot extractors into a recurring private archive for four explicitly approved harnesses: Claude Code, Codex, OpenClaw, and Hermes. No other source kind is accepted; in particular, Messages/iMessage is not supported. The implementation is locally tested; the fleet rollout table below is the source of truth for live readiness.

## Data path

1. When its reviewed scheduler is enabled, each Mac collects local harness data every six hours into an owner-only spool at `~/.local/share/ai-chat-archive/spool`. Unchanged transcript files are fingerprinted and skipped; changed sessions are streamed one at a time.
2. Conversations are normalized into the closed archive-object v2 contract, credential-redacted locally, staged until parsing is complete, stored as immutable content-addressed objects, and indexed separately by host and harness. Message content and normalized tool/event `payload` values are the only intentionally opaque body-bearing fields; their wrappers remain exact and bounded.
3. The Mac Studio invokes the same trusted Python pipeline on each source host over authenticated SSH. The source validates one manifest-bound shard under its archive lock and emits a bounded binary stream; the Studio stages and validates that stream before an additive merge.
4. Once Google Drive is signed in on the Studio, the Studio publishes the fleet tree into one private `AI Chat Archive` folder. Until then, collection continues locally and receipts say exactly why publication is blocked.

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
- Existing and newly copied objects are verified against their content-addressed filenames and approved harness provenance before publication; receipts, indexes, and the final manifest are verified after copying.
- Sync is additive. No pipeline command deletes a source chat, spool object, remote shard, or Drive object.

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
the protocol magic. It then takes and revalidates a fresh no-follow object
snapshot immediately before emitting that body. The receiver uses one
end-to-end monotonic deadline, writes into an owner-only incoming directory,
rejects malformed, duplicate, traversing, extra, or oversized frames, then runs
the full shard validator before transactional merge.

Ordinary indexes never cross SSH. The source emits a fixed-format transport
projection containing the object digest, approved source enum, and a SHA-256
commitment to the native session ID. Current Codex rows additionally carry the
source digest, a commitment to `installation`, and an explicit schema
discriminator. The receiver validates each v2 object, checks those commitments,
reconstructs the ordinary canonical index (including the unrestricted native
ID and Codex installation from the sanitized body), and requires its hash to
equal the original manifest binding. Thus source-controlled session IDs and
installation paths do not appear in pre-body transport metadata.

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

## Host state as of 2026-08-28

| Host | Verified rollout truth | Scheduler / blocker |
|---|---|---|
| New MacBook | Prior local canary green | Scheduler disabled pending reviewed trusted-stream release |
| Mac Studio | Prior local canary green | Scheduler disabled pending reviewed trusted-stream release |
| Mac mini | Storage-blocked; no current release proof | Clear storage and rerun reviewed canary before enabling |
| Old MacBook | Offline | Requires live deployment and canary proof after it returns |

`configs/old-macbook.pending.json` is a placeholder, not a deployment receipt. It must be checked against the live host before installation.

Google Drive is absent on the Studio and remains a human-gated completion step.
No scheduler should be enabled merely to test Drive publication.

Existing v1 objects are not trusted or silently migrated by the new stream.
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

Do not run the install command for the current rollout until the trusted-stream
commit has been reviewed and deployed to the exact configured helper paths.

The Studio config polls `newmac`, `cals-mac-mini`, and `oldmac` only when its
scheduler or a reviewed manual run is active. An offline remote is recorded as
`unreachable`; it does not invalidate a previously verified cached shard.

## Google Drive completion gate

On the Mac Studio, open the pre-staged installer at
`/Users/calstudio/Downloads/GoogleDrive-2026-08-28.dmg`, open
`GoogleDrive.pkg`, and sign in. If that dated file is unavailable, use Google's
official installer: <https://dl.google.com/drive-file-stream/GoogleDrive.dmg>.

The Studio config uses `drive_root: "auto"`. Once exactly one live Google Drive
File Provider appears, the pipeline creates and uses this private folder:

```text
/Users/calstudio/Library/CloudStorage/GoogleDrive-<account>/My Drive/AI Chat Archive
```

Zero providers remain blocked as unavailable; multiple signed-in Google accounts
remain blocked as ambiguous instead of guessing. After a reviewed release,
deployment, explicit human sign-in, and scheduler enablement, a Studio run can
pull current remote shards and publish them. Completion requires a new chat
object and its body-free receipt to appear in the mounted Drive folder, pass the
built-in content-hash verification, and report `published`. This human gate has
not yet been run against a live Google Drive mount.
