# Fleet chat archive

This local extension turns the upstream one-shot extractors into a recurring private archive for four explicitly approved harnesses: Claude Code, Codex, OpenClaw, and Hermes. No other source kind is accepted; in particular, Messages/iMessage is not supported. The implementation is locally tested; the fleet rollout table below is the source of truth for live readiness.

## Data path

1. Each Mac collects its local harness data every six hours into an owner-only spool at `~/.local/share/ai-chat-archive/spool`. Unchanged transcript files are fingerprinted and skipped; changed sessions are streamed one at a time.
2. Conversations are credential-redacted locally, staged until parsing is complete, stored as immutable content-addressed objects, and indexed separately by host and harness.
3. The always-on Mac Studio pulls the other host shards over SSH without deleting anything.
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
- Remote shards are staged, symlink-rejected, content-hash verified, and merged without overwriting a differing immutable object. A process lock serializes scheduled and manual runs.
- Publication positively requires a mounted path below the current user's `~/Library/CloudStorage/GoogleDrive-*`. Production configs cannot bypass this gate.
- Existing and newly copied objects are verified against their content-addressed filenames and approved harness provenance before publication; receipts, indexes, and the final manifest are verified after copying.
- Sync is additive. No pipeline command deletes a source chat, spool object, remote shard, or Drive object.

## Host state as of 2026-08-28

| Host | Reachability | Claude | Codex | OpenClaw | Hermes | Drive |
|---|---|---|---|---|---|---|
| New MacBook | reachable; deployment pending | configured | configured | absent | configured | not installed |
| Mac mini | reachable; deployment pending | configured | configured | configured | configured (main + Cal profile) | not installed |
| Mac Studio | reachable; deployment pending | configured | configured | absent | configured (main + Cal profile) | not installed |
| Old MacBook | offline | pending live proof | pending live proof | pending live proof | pending live proof | unverified |

`configs/old-macbook.pending.json` is a placeholder, not a deployment receipt. It must be checked against the live host before installation.

## Commands

Run one configured collection:

```bash
python3 fleet_chat_archive.py run --config configs/new-macbook.json
```

Install or refresh the per-user six-hour launchd job:

```bash
python3 fleet_chat_archive.py install-launchd --config configs/new-macbook.json --interval-seconds 21600
```

The Studio config also polls `newmac`, `cals-mac-mini`, and `oldmac`. An offline remote is recorded as `unreachable`; it does not fail or block collection from the other hosts.

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
remain blocked as ambiguous instead of guessing. The next scheduled Studio run
will pull current remote shards and publish them. Completion requires a new chat
object and its body-free receipt to appear in the mounted Drive folder, pass the
built-in content-hash verification, and report `published`. This gate has not yet
been run against a live Google Drive mount.
