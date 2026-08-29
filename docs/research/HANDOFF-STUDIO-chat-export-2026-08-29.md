# HANDOFF — Studio successor for the fleet chat export

**Handoff date:** 2026-08-29
**Origin:** MacBook documentation turn
**Destination:** successor agent operating on the Mac Studio
**Current mode:** the deployment goal is resumed by the named lease owner;
this document is the durable handoff and does not transfer the lease.

This is the complete context for a successor that has the repository but none of
the originating agent's conversation history. Treat the timestamps and host
observations below as the last verified snapshot, not as a substitute for a
fresh read-only check before any mutation.

## 1. Goal and current intent

Matt's goal, in his words:

> On every machine (new macbook, old macbook, mac mini, mac studio), run
> `https://github.com/0xSero/ai-data-extraction` on every harness we have
> (Claude Code, Codex, OpenClaw, Hermes). Put the exports in a folder in Google
> Drive, and codify a pipeline so new chats get pulled into that Google Drive
> folder automatically.

The manager's operating intent is deploy-first: prove the shipped build on the
reachable New MacBook, Mac mini, and Mac Studio with real host canaries and
launchd; keep Google Drive publication gated on Matt's login; queue the Old
MacBook without blocking; and prove success by Matt watching a new chat appear
in Drive automatically. Test counts alone are not success. The goal is paused
for this documentation handoff. Do not start product work in this turn.

## 2. Binding operating rules

These rules are binding for the successor:

1. **Canary first, infrastructure second.** Do not enable a production
   six-hour schedule, add a remote, or call the rollout complete until a
   canary has passed on the relevant reachable host(s).
2. **Live-shaped preflight before every long run.** Recheck the exact checkout
   and six runtime hashes, source roots and harness inventory, free disk, Drive
   provider state, remote SSH identity, current lock/work state, launchd state,
   and owner-only receipt paths. A unit-test green result is not this preflight.
3. **One review wave per checkpoint.** Batch all findings, make one bounded
   repair, then run one re-review. Do not open parallel review waves or repair
   while a review is still running.
4. **Surface Matt gates first, once, batched, pre-staged to two clicks.** Put
   both outstanding gates in one message with the exact next click/action and
   consequence. Do not ask for the same gate repeatedly or hide it behind
   implementation work.
5. **One release owner per repo with a deployment lease.** The Studio
   successor is the sole release owner for this repository. Before touching a
   host, hold a human-visible lease naming owner, commit, scope, and expiry;
   stop if another lease or owner is active. Do not let other agents deploy,
   merge, restart, or overwrite this repo while the lease is held.
6. **Persistent supervision for long canaries.** A long canary must be owned
   by launchd (a persistent wrapper with durable stdout, stderr, exit status,
   and KeepAlive/retry behavior), never by a terminal, chat session, or agent
   process. The prior run had two canaries die with their controlling sessions;
   do not repeat that failure mode.
7. **Heartbeat.** If 24 hours pass without activity, send Matt one line with
   status or “closing, here's why.”

8. **Lease is structure, not advice.** The repo-root
   [`.deployment-lease.json`](../../.deployment-lease.json) names the one
   release owner, session, scope, commit, and expiry. Every session must read
   it before touching a host; a non-owner session must stand down. Lease
   transfer requires an owner commit.
9. **Schema freeze before a broad release.** The tracked
   [`SCHEMA_FREEZE_CHECKPOINT_2026-08-29.md`](../SCHEMA_FREEZE_CHECKPOINT_2026-08-29.md)
   is the gate record: one live-schema census, one canonical
   `validate_archive_object` validator, then exactly one batched review wave,
   one repair, and one re-review. A later release needs a new checkpoint; do
   not edit this record in place to bless a different runtime.

The preceding work had the best ratio of the three attempts, but still spent
17 review waves in 21 hours. That cadence is a process defect, not a reason to
continue it. Preserve the one-wave rule above.

## 3. Honest state at the pause

Last live reconciliation was 2026-08-29T14:54:14Z. The canonical repository
state is [`FLEET_CHAT_ARCHIVE_LIVE_STATE.md`](../FLEET_CHAT_ARCHIVE_LIVE_STATE.md)
and the body-free evidence receipt is
[`RECEIPT_FLEET_CHAT_ARCHIVE_2026-08-29.md`](../RECEIPT_FLEET_CHAT_ARCHIVE_2026-08-29.md).

### DONE — proven, with receipts

- Predecessor work was audited rather than rebuilt. The Claude Code, Codex,
  OpenClaw, and Hermes adapters, content-addressed deduplication, credential
  redaction, provenance manifests, trusted remote stream, and terminal-hangup
  rollback are integrated in reviewed release
  `3c732d7b1031949bd18db90ae4ac40f667f6cfa7`.
- The shipped release passed `python3.14 -m unittest discover -s tests -p
  'test*.py'`: **263/263, OK**. Compile/diff checks, focused SIGHUP/rollback
  checks, and a fresh bundle-clone check were also green before deployment.
- The exact release is deployed and clean at the last check on New MacBook,
  Mac Studio, and Mac mini. The Old MacBook was not reachable.
- New and Studio have body-free, zero-error canary receipts. They correctly
  report absent harnesses and blocked Drive publication rather than calling
  those states success.
- Studio's manifest-bound Claude index repair is complete; its interrupted-index
  backup is retained. The Old retry implementation was reviewed and its
  offline behavior was previously proven (run count 1 -> 2, exit 0, zero
  stderr delta, `offline_retryable`/`ssh_unreachable`).
- The owner-only Google Drive DMG is staged and verified on Studio. It is not
  installed and no provider is mounted.

### IN-FLIGHT — do not call these complete

- The temporary final-release canary labels remain loaded under KeepAlive on
  New and Studio. They are persistent test jobs, not production schedules.
  The last snapshot saw New at `runs=138` with 137 valid receipt records and
  Studio at `runs=29` with 28 valid receipt records. A terminal quiescent state
  is intentionally not claimed.
- New's latest completed receipt (2026-08-29T14:41:25Z) exited 0 with
  `completed_with_absent_harnesses`, zero errors, and `blocked_no_drive_root`.
  Studio's latest completed receipt (2026-08-29T14:29:11Z) exited 0 with the
  same collection status, zero errors, and `blocked_drive_unavailable`.
- The temporary canaries were already in flight when the manager paused the
  goal. This documentation turn did not start another run or change their
  lifecycle. Recheck them before acting.

### NOT STARTED — explicit gates, not implied completion

- Matt has not installed or signed into Google Drive on Studio. There is no
  File Provider mount, private `AI Chat Archive` folder, published receipt, or
  new-chat-in-Drive proof.
- Matt has not approved Mini log compression/cleanup. Mini's real canary and
  production schedule have not run.
- Old MacBook has not returned online for live deployment or canary proof. Its
  retry label is currently disabled/unloaded on New; “previously proven retry
  behavior” is not the same as “active queue.”
- Production six-hour archive schedules remain disabled/unloaded on the
  reachable hosts. The temporary final-release canaries are separate jobs.
- OpenClaw host collection is not proven on New or Studio because the source is
  absent there; Mini and Old have no canary proof.
- No separate tracked ranking, graph, or wiki entry exists in this checkout.
  Do not invent one; the live-state file and dated receipt are the durable
  repository record.

### Lease and schema checkpoint

- The active deployment lease is the repo-root `.deployment-lease.json`. At the
  time of this handoff it names `codex:macbook` session
  `01a046f9-9427-7343-9221-4135b50bc30f` as the sole release owner through
  `2026-08-30T16:49:17Z`. A Studio successor must read the file and stand down
  until the owner explicitly transfers it in a commit.
- The reviewed release's schema-freeze record is
  `docs/SCHEMA_FREEZE_CHECKPOINT_2026-08-29.md`. It records the one census,
  canonical validator hash, and one review wave. Any broad release after
  `3c732d7` is blocked until its own checkpoint is committed.

## 4. Exact file and commit map

### Repository and commits

| Artifact | Exact location / commit | Meaning on Studio |
|---|---|---|
| Canonical MacBook checkout | `/Users/mattrotundo/Projects/ai-data-extraction` | Originating checkout used for the documentation commit; this absolute path is MacBook-only. |
| Canonical Studio checkout | `/Users/calstudio/Projects/ai-data-extraction` | Use this path on the successor host. Verify it before use. |
| Mini checkout | `/Users/calrotundo/Projects/ai-data-extraction` | Remote path named by `configs/mac-studio.json`; verify independently. |
| Old checkout (expected) | `/Users/mattrotundo/Projects/ai-data-extraction` on `oldmac` | Expected path only; Old was offline at the last check. |
| Reviewed runtime release | `3c732d7b1031949bd18db90ae4ac40f667f6cfa7` | The only release to canary/deploy unless a new review checkpoint is completed. |
| Documentation commit | `5e982165e7ff4b05e10931a9466ce0888da52bc6` | Adds the live-state and receipt docs and corrects README/fleet-guide drift. |
| This handoff | The commit that adds this file (reported by the originating turn) | Use `git log --follow -- docs/research/HANDOFF-STUDIO-chat-export-2026-08-29.md` to identify it. |
| Superseded parent | `0e25987370aa32a93423201dc25d85d913d8c8ac` | Exact Hermes provider-metadata validation; retained in history, superseded by `3c732d7`. |

The previous attempt to push `5e98216` from the MacBook was rejected by GitHub:
HTTPS returned 403 for `MattsAgentCal`, and the SSH fallback was denied by the
available public key. Verify the Studio clone's actual `git log` and remote
refs; do not assume the branch is present on `origin` merely because it exists
in this handoff.

### Runtime files at `3c732d7`

These are the six files the Old retry deploy pins, plus the retry helper. Verify
the hashes on every host before a deployment lease is exercised.

| File | SHA-256 |
|---|---|
| `fleet_chat_archive.py` | `625e98c4f33aba2e57864f49c70992924491c78e3561dd2bf1360687da039a0b` |
| `archive_object_contract.py` | `f31e840f49fcc9f35dc8223d1d0da3a479ae6da2de7fc62fac73ef6e8521825a` |
| `extract_claude_code.py` | `cc09aa37295d98572fdffcf6d8ef465d340e9154c1722f85871991aa9af8512e` |
| `extract_codex.py` | `6b7132413ad3dc3042ca4644d9ae30062f2a972da48809cdca376c0f35f377e6` |
| `extract_openclaw.py` | `6049a3832abcddb380b9a9845e4cd1ef264467358be8ad8ce000a11da3e1b84b` |
| `extract_hermes.py` | `c45fb372457d02e1d0df510e96dc9b0592da41b7cea9828ea7636f9000e173cb` |
| `fleet_deploy_retry.py` | `767757728edad7e9cb972c6e1a276224b71051042d67d2f5823f02d3a819beb3` |

Repository documentation at the prior commit consists of `README.md`,
`docs/FLEET_CHAT_ARCHIVE.md`,
`docs/FLEET_CHAT_ARCHIVE_LIVE_STATE.md`, and
`docs/RECEIPT_FLEET_CHAT_ARCHIVE_2026-08-29.md`. The tests are under `tests/`;
the full suite count is 263. The host configs are
`configs/new-macbook.json`, `configs/mac-studio.json`, `configs/mac-mini.json`,
`configs/old-macbook.pending.json`, and
`configs/old-macbook.deploy.json`. Do not treat the pending Old config as a
deployment receipt.

### Paths that exist only on the originating MacBook

Do not expect these absolute paths to exist on Studio:

- `/tmp/ai-data-extraction-3c732d7.prSOXz/ai-data-extraction-3c732d7.bundle`
  (mode 0600, SHA-256
  `4531929ef667087d755dbdffb78054d3a64ec471885e4def7292f34023dcb295`).
- `/tmp/claude-goal-state.json` (a stale predecessor state file with
  `status: pursuing`; not canonical and not in Git).
- `/Users/mattrotundo/Projects/ai-data-extraction-hermes-sighup-final`
  (an isolated, clean `3c732d7` worktree used for review; not the Studio
  checkout).
- `/Users/mattrotundo/.local/share/ai-chat-archive-canary-final-3c.ABxGZG/`
  (New MacBook's temporary receipt stream) and
  `/Users/mattrotundo/.local/share/ai-chat-archive/spool/` (New's host-local
  archive/spool). Do not copy body-bearing contents into Git.

### Host-local paths and translation

| Evidence | MacBook/New | Studio | Mini | Old |
|---|---|---|---|---|
| Repo | `/Users/mattrotundo/Projects/ai-data-extraction` | `/Users/calstudio/Projects/ai-data-extraction` | `/Users/calrotundo/Projects/ai-data-extraction` | `/Users/mattrotundo/Projects/ai-data-extraction` (expected) |
| Spool root | `/Users/mattrotundo/.local/share/ai-chat-archive/spool` | `/Users/calstudio/.local/share/ai-chat-archive/spool` | `/Users/calrotundo/.local/share/ai-chat-archive/spool` | `/Users/mattrotundo/.local/share/ai-chat-archive/spool` (expected) |
| Temporary canary receipt | `/Users/mattrotundo/.local/share/ai-chat-archive-canary-final-3c.ABxGZG/stdout.jsonl` | `/Users/calstudio/.local/share/ai-chat-archive-canary-final-3c.4zhNaQ/stdout.jsonl` | none | none |
| Special evidence | New temporary label; production and Old retry labels disabled | Claude restore backup and staged Drive DMG | CoreSimulator logs and storage gate | SSH alias `oldmac`; offline |

The Studio-only repair backup is
`/Users/calstudio/.local/share/ai-chat-archive-repair-proof.BCwlg8/live-current-index.backup.json`.
The Studio-only installer is
`/Users/calstudio/Downloads/GoogleDrive-2026-08-28.dmg`. The Mini-only logs
are `/Users/calrotundo/Library/Logs/CoreSimulator/CoreSimulator.log` and
`CoreSimulator.prev.log`. These are host-local references, not Git inputs.

## 5. Exact per-machine state and receipts

### New MacBook (`newmac` / local originating host)

- Checkout is clean at `3c732d7`.
- Temporary label
  `com.mattrotundo.ai-chat-archive.canary-final-3c-new` is active under
  KeepAlive. At the last observation it showed `runs=138`; the receipt stream
  had 137 valid JSONL records, zero invalid lines, and zero stderr bytes.
- Latest completed receipt: run ID
  `20260829T143100.686004Z-7124dbe2`, collected at
  `2026-08-29T14:41:25.366392+00:00`; status
  `completed_with_absent_harnesses`, errors 0, publication
  `blocked_no_drive_root`. It reported Claude/Hermes with no conversations,
  Codex 3 conversations/3 new objects, and OpenClaw
  `not_present_on_host`.
- Production label `com.mattrotundo.ai-chat-archive.new-macbook` and Old retry
  label `com.mattrotundo.ai-chat-archive.old-macbook-deploy-retry` are disabled;
  the retry label was also absent when printed. Do not describe either as
  active.

### Mac Studio (`studio` / successor host)

- Read-only SSH found a clean checkout at `3c732d7`.
- Temporary label
  `com.mattrotundo.ai-chat-archive.canary-final-3c-studio` is active under
  KeepAlive. At the last observation it showed `runs=29`; the receipt stream
  had 28 valid JSONL records, zero invalid lines, and zero stderr bytes.
- Latest completed receipt: run ID
  `20260829T142123.868546Z-c5655519`, collected at
  `2026-08-29T14:29:11.388973+00:00`; status
  `completed_with_absent_harnesses`, errors 0, publication
  `blocked_drive_unavailable`. It reported Claude 1 conversation, Codex 6,
  Hermes 0, and OpenClaw `not_present_on_host`.
- Last hub statuses were Mini `pending_manifest`, New `unreachable`, and Old
  `unreachable`. Production label
  `com.mattrotundo.ai-chat-archive.mac-studio` is disabled.

### Mac mini (`cals-mac-mini`)

- Read-only SSH found a clean checkout at `3c732d7`; no production archive label
  is enabled.
- Free capacity was 7,687,332 KiB (about 7.33 GiB). The active
  `CoreSimulator.log` was 5,109,616,254 bytes with one open handle. The closed
  `CoreSimulator.prev.log` was 15,403,577,516 bytes with zero open handles.
- No cleanup, compression, or real canary was started. The only proposed
  cleanup is the closed, zero-handle log; never delete or compress the active
  log without a separately reviewed safety decision.

### Old MacBook (`oldmac`)

- SSH timed out at the reconciliation check. No live inventory, deployment, or
  canary proof exists.
- The earlier final-pin retry proof advanced run count 1 -> 2, exited 0, had
  zero stderr delta, and reported `offline_retryable`/`ssh_unreachable`.
- The retry is not active now: New's retry label is disabled/unloaded. Once Old
  returns, the Studio release owner must recheck identity, deploy the exact
  runtime pins, and prove the retry/canary before calling it queued and healthy.

### Google Drive (`Studio only`)

- Staged installer: `/Users/calstudio/Downloads/GoogleDrive-2026-08-28.dmg`.
- Last verification: mode 0600, 141,267,496 bytes, SHA-256
  `fb6927060f8f20efb8ac2027d00a9c0787c111fa57c01fe6a29675afaf5c1178`, and
  `hdiutil verify` returned `VALID`.
- Not started: installation, sign-in, File Provider discovery, private folder
  creation, publication, and the new-chat proof.

## 6. Matt gates — surface these first

Send Matt one batched message before any successor work. These are the only
outstanding human gates currently called out by the manager:

1. **Studio Google Drive (two clicks):** open
   `/Users/calstudio/Downloads/GoogleDrive-2026-08-28.dmg`, double-click
   `GoogleDrive.pkg`, then complete the Google sign-in. If the staged DMG is
   unavailable, use Google's official installer:
   `https://dl.google.com/drive-file-stream/GoogleDrive.dmg`. Consequence:
   exactly one mounted provider can unlock the private `AI Chat Archive`
   publication gate. Do not claim Drive success before the provider and a
   `published` receipt are visible.
2. **Mini log-compression approval:** approve only the closed, zero-handle
   `CoreSimulator.prev.log` (15,403,577,516 bytes) for compression/archive and
   removal if the reviewed operation requires it. Do not touch the active
   `CoreSimulator.log` (5,109,616,254 bytes, one open handle). Consequence:
   enough free space to run the Mini canary; no cleanup has occurred yet.

Surface both gates once, in this order, with the exact action and consequence.
Do not wait until after a canary or infrastructure change to mention them.

## 7. First three successor actions

Do exactly these three bounded actions when the goal is resumed; until then,
leave the deployment paused.

1. **Surface the two Matt gates and establish the deployment lease.** Send the
   single batched Drive/Mini message above. Then, on Studio, verify the clone
   path, branch, and `3c732d7` before writing a lease naming the Studio agent,
   this commit, the host scope, and an expiry. If either gate is pending, record
   it once and do not work around it.
2. **Perform a read-only, live-shaped preflight under the lease.** Verify the
   exact runtime hashes and clean worktree on Studio, New, and Mini; check
   `launchctl` labels and current temporary canary receipts; check source roots,
   SSH host identities, free disk, Drive provider count, and `.run.lock`/`.work`
   state. Recheck Old with a bounded timeout and record it as offline if it
   still fails. Do not start a long run or enable production infrastructure from
   a terminal.
3. **Run one supervised canary checkpoint.** Use one persistent launchd
   supervisor on Studio with durable owner-only stdout/stderr/exit receipt
   paths; do not let a controlling chat session own the process. Run the
   live-shaped canary on the shipped `3c732d7` build, batch all findings into
   one review wave, make at most one bounded repair, and re-review once. Only a
   clean checkpoint can authorize the next infrastructure step.

## 8. Proving the automatic six-hour cycle on the shipped build

The proof target is not “launchd is installed.” The proof target is: a new
allowed chat is collected, merged, and appears in the Studio Drive folder after
the six-hour automation path, while the controlling session is gone.

### A. Verify the shipped build and supervisor

On each reachable host, from its translated repo path:

```bash
git rev-parse HEAD
git status --short --untracked-files=all
shasum -a 256 fleet_chat_archive.py archive_object_contract.py \
  extract_claude_code.py extract_codex.py extract_openclaw.py extract_hermes.py
```

Require clean worktrees, exact `3c732d7`, and the hashes in §4. On Studio,
inspect the temporary canary only through launchd and its files, for example:

```bash
launchctl print gui/$(id -u)/com.mattrotundo.ai-chat-archive.canary-final-3c-studio
```

For any new long canary, submit a dedicated launchd wrapper with `KeepAlive`,
absolute repo/config paths, owner-only stdout/stderr, and an exit-code file.
Record the label and receipt directory before starting. Do not run the Python
process from a terminal or agent session.

### B. Run the live-shaped preflight, then one canary

Before the canary, check all of the following and record only body-free values:

- Config parses and points at the intended host/user/repo; no symlink or
  unexpected path substitution.
- Every source root and configured harness inventory is visible; missing
  harnesses are recorded as absent, never silently called collected.
- Remote aliases resolve to the intended pinned host identity; Old may be
  `unreachable` and must not block New/Studio/Mini.
- Free disk and object-size budget can hold the largest validated object; Mini's
  log gate must be approved before its canary.
- Drive has exactly zero or one provider; zero means publication remains
  blocked, multiple means ambiguous and blocked.
- No unrelated `.run.lock` or `.work` transaction is active; preserve a live
  transaction rather than starting over it.
- The supervisor is loaded and will survive logout; the production six-hour
  label is still disabled during this canary-first checkpoint.

Run the configured collection only through the supervisor. The repository's
manual command shape is:

```bash
python3 fleet_chat_archive.py run --config configs/mac-studio.json
```

The canary passes only when the body-free receipt is valid JSON, has zero
errors, and its collection/publication statuses explain every absent or blocked
source. A successful collection with `blocked_drive_unavailable` is not a
Drive success.

### C. Enable and prove the six-hour schedule only after the checkpoint

After the one-wave checkpoint is reviewed and the required Matt gates are
cleared, install the per-user schedule on each authorized reachable host:

```bash
python3 fleet_chat_archive.py install-launchd \
  --config configs/mac-studio.json --interval-seconds 21600
```

Use the corresponding host config on New and Mini. The generated label is
`com.mattrotundo.ai-chat-archive.<host_id>` and the plist must show
`RunAtLoad=true` and `StartInterval=21600`, with owner-only logs. Verify the
loaded service and plist from launchd, not from a shell transcript:

```bash
label=com.mattrotundo.ai-chat-archive.mac-studio
launchctl print gui/$(id -u)/$label
plutil -p "$HOME/Library/LaunchAgents/$label.plist" \
  | rg 'RunAtLoad|StartInterval|StandardOutPath|StandardErrorPath'
```

An immediate `launchctl kickstart -k` is only a smoke check. The six-hour proof
requires leaving the loaded plist in place, recording the pre-run launchd
`runs`/receipt count, and observing the next run after approximately 21,600
seconds (normal scheduling jitter allowed). Confirm that the receipt timestamp
and launchd run count advance after the controlling terminal/session is gone.

### D. Prove a new chat reaches Drive

Once Drive is mounted and the schedule is authorized:

1. Record a body-free baseline: current host index/object count, Studio
   `publish-manifest.json` digest, receipt count, and Drive folder listing.
2. Create one clearly identifiable test chat in an approved harness whose source
   is present. Keep its message text out of Git and receipts; retain only the
   resulting object digest/session hash in the private evidence note.
3. Let the next supervised six-hour cycle run. On the receipt, require the
   source to be `collected` or a valid cached `pulled` import, zero errors, and
   publication status `published`.
4. Verify the new content-addressed object and body-free receipt are present in
   the Studio folder:

   ```text
   /Users/calstudio/Library/CloudStorage/GoogleDrive-<account>/My Drive/AI Chat Archive
   ```

   Recompute the object hash, verify the receipt and final manifest bindings,
   and confirm the new object was not merely an old cached row. Check that the
   Drive folder listing, host index, and manifest all advance together.
5. Close the controlling terminal/agent session and observe another launchd
   cycle or an authorized `kickstart` smoke run. Only then is the supervisor
   survival proven. Report success to Matt only when he can see the new chat in
   Drive; otherwise report the exact blocked status.

Do not copy the body-bearing object or transcript into this handoff. Add only a
new body-free dated receipt with hashes, counts, statuses, and paths.

## 9. Stop conditions and handback

Stop and report to Matt instead of improvising if:

- the deployment lease is missing, expired, or held by another owner;
- a canary would be owned by a terminal/session rather than persistent launchd;
- a review discovers multiple independent findings after the one repair wave;
- Mini cleanup would touch the active open-handle log;
- Old remains unreachable (record retryable/offline and continue reachable hosts);
- Drive is zero-provider or ambiguous-provider;
- a receipt, index, manifest, object hash, or provenance binding disagrees; or
- a requested action would put raw conversation bodies, credentials, or mutable
  host-local archive data into Git or AgentBrain.

The next handback should name the lease owner, exact release commit, host-by-host
receipt paths, launchd labels and run counts, the two Matt gate decisions, and a
clear DONE / IN-FLIGHT / NOT STARTED split. If 24 hours pass without activity,
send Matt the required one-line heartbeat before closing.
