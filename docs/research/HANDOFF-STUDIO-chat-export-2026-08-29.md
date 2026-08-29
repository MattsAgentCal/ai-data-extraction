# HANDOFF — Studio successor for the fleet chat export

**Handoff date:** 2026-08-29
**Origin:** MacBook documentation turn
**Destination:** successor agent operating on the Mac Studio
**Current mode:** the deployment goal is resumed by the named lease owner;
this document is the durable handoff and does not transfer the lease. GitHub and
Drive connectors are active; the runtime File Provider mount and Mini disk
approval remain separate gates.

This is the complete context for a successor that has the repository but none of
the originating agent's conversation history. Treat the timestamps and host
observations below as the last verified snapshot, not as a substitute for a
fresh read-only check before any mutation.

### Matt-ratified structural addendum — 2026-08-29

The following are repository/runtime invariants, not advice or a chat promise:

- Every long-canary node is a launchd-owned service with an absolute plist,
  durable owner-only stdout/stderr, and read-backable exit/run state. A
  terminal, agent, or controlling chat session may install or inspect it but
  may never own the long-running process. A canary without a live `launchctl
  print` readback is not started or passed.
- The repo-root [`.deployment-lease.json`](../../.deployment-lease.json) is the
  machine-readable deployment lease. It names the one release owner, session,
  host, scope, and expiry. Every session reads and compares it before a host
  mutation, merge, restart, or connector write; a non-owner stands down, and a
  transfer is valid only as an owner-authored commit.
- Before any broad release, the tracked
  [`SCHEMA_FREEZE_CHECKPOINT_2026-08-29.md`](../SCHEMA_FREEZE_CHECKPOINT_2026-08-29.md)
  must contain exactly one live-schema census, one canonical validator, and
  one batched review wave. A new runtime requires a new checkpoint; this
  checkpoint cannot be edited in place to bless it.

At this amendment's readback, the lease still names
`codex:macbook`/`01a046f9-9427-7343-9221-4135b50bc30f` as the sole release owner.
At 2026-08-29T19:50:54Z, New and the Old retry queue were loaded locally under
launchd with `RunAtLoad=true`, `StartInterval=21600`; New was idle at `runs=1`,
exit 0. At the same readback, Studio's
`com.mattrotundo.ai-chat-archive.mac-studio` remained launchd-owned and
running (pid `14336`, `runs=1`, `RunAtLoad=true`, `StartInterval=21600`); its
latest persisted receipt was still `RunFailure`, so the canary is not a pass.
Mini had 7,489,840 KiB free with the active CoreSimulator log at
5,378,451,586 bytes and the closed `.prev` log at 15,403,577,516 bytes; no
cleanup was authorized. Old MacBook SSH timed out and remains queued.
No broad release or foreground canary was started for this amendment, and no
second schema census or review wave was opened.

### Matt-ratified execution addendum — structural gates, resumed 2026-08-29

This addendum is an execution contract, not advice. The remaining work is
allowed to advance only when these repository/runtime structures are true:

- **Long-canary node:** every long canary is a loaded launchd job with an
  absolute plist, owner-only durable logs, and a read-backable run/exit state.
  The collector process must be launchd-owned; a terminal, agent, or chat
  session may never own it. A foreground long canary is invalid, even if its
  output looks healthy.
- **Deployment lease:** before any deploy, merge, restart, or connector write,
  the session reads the repo-root [`.deployment-lease.json`](../../.deployment-lease.json).
  Its named owner is the sole release owner. Any other session reads the file
  and stands down; only an owner-authored commit may transfer the lease.
- **Schema freeze:** before any broad release, the tracked
  [`SCHEMA_FREEZE_CHECKPOINT_2026-08-29.md`](../SCHEMA_FREEZE_CHECKPOINT_2026-08-29.md)
  must record one live-schema census, one canonical validator, and exactly one
  review wave followed by one repair and one re-review. A different runtime
  requires a new checkpoint; no second wave may be opened for this one.

The successor must enforce these gates from the files and launchd readbacks,
not from a missing chat instruction. The current lease remains with
`codex:macbook`/`01a046f9-9427-7343-9221-4135b50bc30f`; do not mutate hosts or
the release while that lease is held.

### Matt-ratified manager addendum — structural enforcement readback, 2026-08-29T21:09:28Z

These controls are encoded in the repository and host job shape, not left as
advice for a future agent:

- **Every long-canary node is launchd-owned.** The New MacBook, Studio, and Old
  retry jobs are the concrete nodes currently represented by loaded plists;
  their absolute paths, owner-only logs, `RunAtLoad`, and `StartInterval` are
  read back with `launchctl print`. A terminal, agent, or chat session is never
  an eligible process owner. A long canary without that readback is invalid and
  must not be replaced with a foreground run.
- **The deployment lease is the owner gate.** The tracked repo-root
  [`.deployment-lease.json`](../../.deployment-lease.json) is the single
  machine-readable owner record. At this readback it names
  `codex:macbook`/`01a046f9-9427-7343-9221-4135b50bc30f` on `Mac.lan`; every
  session must compare its identity before deployment, restart, merge, or
  connector write. A different session stands down; only an owner-authored
  commit can transfer the lease.
- **The schema freeze is a broad-release checkpoint.** The tracked
  [`SCHEMA_FREEZE_CHECKPOINT_2026-08-29.md`](../SCHEMA_FREEZE_CHECKPOINT_2026-08-29.md)
  records one live-schema census, one canonical
  `archive_object_contract.validate_archive_object` validator, and one batched
  review wave followed by one repair and one re-review for `3c732d7`. This
  amendment opens no second census or review wave; any different runtime must
  create a new checkpoint before a broad release.

Execution remains read-only until the current launchd-owned Studio transaction
finishes or has one bounded diagnosis. No foreground canary, second Studio run,
lease transfer, broad release, or extra review wave is authorized by this
amendment.

### Post-amendment live readback — 2026-08-29T21:12:17Z

- **New MacBook:** the launchd label is idle at `runs=2`, exit 0. The latest
  body-free receipt remains `20260829T195959.324876Z-be2608f7`, with zero
  errors, `completed_with_absent_harnesses`, and `publication=blocked_no_drive_root`.
- **Mac Studio:** the launchd label is still `running`, `runs=1`, pid `14336`
  at the 21:09:53Z poll, with `last exit=(never exited)` and about 98.9% CPU.
  Receipt count remains 42; the latest persisted receipt remains
  `failed`/`RunFailure`, `publication=not_attempted`, and CloudStorage has
  zero Google Drive providers. No restart or foreground fallback was used.
- **Mac mini:** the 21:12:17Z SSH preflight reached `Cals-Mac-mini`, found
  7,385,588 KB free, and found no archive launchd label, plist, or process.
  The closed-log approval remains open; the active log was not touched.
- **Old MacBook:** the bounded SSH retry still timed out. The local retry label
  is loaded but idle at `runs=1`, exit 0; its latest body-free record is
  `offline_retryable` with `ssh_unreachable`. The queue remains non-blocking.
- **Drive connector:** the authenticated folder listing remains exactly four
  items. No new approved text artifact was present, so this readback performed
  no connector write. Runtime publication and the elapsed six-hour proof remain
  unproven.

This is a read-only status update under amendment commit
`8802714639e5d6f1cc4ab383d287ac71cb15e477`; it does not open a new schema
census, release, or review wave.

### Post-run live readback — 2026-08-29T22:03:47Z

- **Mac Studio:** the launchd-owned process `14336` completed and disappeared
  before the readback. `launchctl print gui/$(id -u)/com.mattrotundo.ai-chat-archive.mac-studio`
  shows `state=not running`, `runs=1`, `last exit code=0`, and
  `StartInterval=21600`. The newest body-free receipt is
  `20260829T184201.313238Z-c87faa38`, collected at
  `2026-08-29T22:01:36.872837Z`; it reports
  `completed_with_absent_harnesses`, zero errors, and
  `publication=blocked_drive_unavailable`. Claude collected 12 conversations
  with 7 new objects, Codex 51 with 50 new objects, Hermes had no
  conversations, and OpenClaw is absent. This is a successful supervised
  collection, not a Drive publication or six-hour elapsed-cycle proof.
- **New MacBook:** the label remains idle at `runs=2`, exit 0, with the prior
  zero-error receipt `20260829T195959.324876Z-be2608f7` and
  `publication=blocked_no_drive_root`.
- **Mac mini:** it remains reachable but unapproved for closed-log cleanup;
  no archive job or canary was started.
- **Old MacBook:** the bounded SSH retry still timed out; the New-host retry
  label remains the active non-blocking queue.
- **Google Drive:** the authenticated connector folder still has exactly four
  items (two receipt Docs and two redacted text canaries); no new approved text
  artifact was staged, so no connector write occurred. Runtime publication
  remains unproven because Studio has no File Provider root.

This readback does not change the deployment lease, reviewed runtime, schema
checkpoint, or review-wave count. The handoff's structural controls remain
binding: long canaries are launchd-owned, the repo lease has one release owner,
and a broad release requires one schema census, one canonical validator, and one
review wave.

### Current execution readback — 2026-08-29T21:35:42Z

- **Lease and release:** the repo-root lease remains unchanged and still names
  `codex:macbook` as sole release owner. The branch is clean and its fork ref
  was independently read back at `880358eb0026696deca6d6226a0cfd7a5f3a3bc4`.
  This readback made no deploy, merge, restart, connector write, or lease
  transfer.
- **New MacBook:** launchd label `com.mattrotundo.ai-chat-archive.new-macbook`
  remains idle at `runs=2`, exit 0. Receipt
  `20260829T195959.324876Z-be2608f7` is unchanged: zero errors,
  `completed_with_absent_harnesses`, and `blocked_no_drive_root`.
- **Mac Studio:** launchd still owns pid `14336` (PPID `1`), `runs=1`,
  elapsed `02:53:37` at the 21:35:38Z readback, and `StartInterval=21600`.
  A bounded sample at 21:35:42Z remained in regex redaction/JSON encoding;
  receipt count is 42, the latest persisted receipt remains
  `20260829T164025.921717Z-279ff85a`/`RunFailure`, and CloudStorage still has
  zero Google Drive providers. No restart or foreground fallback was used.
- **Mac mini:** the 21:33:49Z read-only census found `7,351,536 KB` free,
  active `CoreSimulator.log` at `5,470,568,332` bytes, and closed
  `CoreSimulator.prev.log` at `15,403,577,516` bytes. No archive label or
  process is loaded; Matt's closed-log approval remains open.
- **Old MacBook:** no new probe was performed in this readback; the last
  verified state remains the launchd retry queue with Old unreachable.
- **Drive:** the authenticated connector read back the exact four-item
  `AI Chat Archive` folder and found no new approved text artifact; no write
  occurred. Connector publication remains manual evidence, not runtime
  six-hour proof.

This readback changes no release, schema checkpoint, or review wave. Keep the
Studio process under launchd and leave it untouched until it exits or a single
bounded diagnosis justifies one repair.

### Resume execution readback — 2026-08-29T20:09:39Z

- New's second supervised refresh is still running as launchd pid `36868`
  (`runs=2`, `StartInterval=21600`); no new receipt has been emitted. The last
  receipt remains `20260829T184200.680506Z-3949348d`, zero-error
  `completed_with_absent_harnesses`, publication `blocked_no_drive_root`.
- Studio's first launchd-owned attempt remains pid `14336` (`runs=1`,
  `StartInterval=21600`); the latest persisted receipt remains
  `20260829T164025.921717Z-279ff85a` with `RunFailure`. Its current process is
  still active in redaction/validation; it is not a pass and was not restarted.
- The Drive connector canary is verified in folder
  `1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV`: file
  `1pLF5FhnQcMJ5yT28HXsnnHaQuqEJyR-5`, title `AI Chat Archive — Codex canary —
  c009ce3a`, `text/plain`, 654 bytes. This is an authenticated connector
  import of one approved redacted object, not automatic runtime publication.
- Mini remains unmodified: 7,489,840 KiB free; active
  `CoreSimulator.log` 5,378,451,586 bytes; closed `.prev` log
  15,403,577,516 bytes. Old SSH timed out; its New-host retry queue remains
  enabled. Both are still gates.

The next execution action remains a read-only poll of the two launchd-owned
refreshes. Do not stop Studio's active process or start a foreground fallback;
do not launch a second long canary while either transaction is active.

### Resume execution readback — 2026-08-29T20:26:10Z

This is the latest body-free execution readback and supersedes the earlier
20:09:39Z snapshot without rewriting that historical record:

- **New MacBook:** the second launchd-owned refresh completed at
  `2026-08-29T20:19:56.940667Z`. Receipt
  `20260829T195959.324876Z-be2608f7.json` reports
  `completed_with_absent_harnesses`, zero errors, and
  `publication=blocked_no_drive_root`; Claude collected 1 conversation,
  Codex 5, Hermes had no conversations, and OpenClaw is not present. The
  label is now idle at `runs=2`, exit 0. The current goal session is a new
  redacted object digest
  `d5883edd0e8b0a0f8d86ae477acb1e6e70a4c7bbe6e1ef2f6d38b8020c0c54a3`
  (49,484,530 bytes), retained outside Git.
- **Mac Studio:** at `20:26:10Z`,
  `com.mattrotundo.ai-chat-archive.mac-studio` is still launchd-owned,
  `runs=1`, pid `14336`, `StartInterval=21600`, and active. The latest
  persisted receipt remains `20260829T164025.921717Z-279ff85a.json` with
  `RunFailure`; no restart or foreground fallback was used. Studio still has
  zero Google Drive File Provider entries, so runtime publication is not
  proven. The active transaction remains in-flight and must be allowed to
  finish or be diagnosed once, boundedly, under the lease.
- **Google Drive connector:** the authenticated connector imported the current
  49,484,530-byte redacted goal-session object once, moved it into `AI Chat
  Archive`, and read back parent, MIME, and size. File ID
  `18kklPXiMM2bzF1ZU8tCzlJJ9k-HblbC_` was created at
  `2026-08-29T20:23:37.511Z`; the folder now lists exactly four items (two
  receipt Docs and two redacted text canaries). This is a verified connector
  publication, not proof that a launchd runtime cycle publishes automatically.
- **Mac mini:** the latest read-only census remains 7,489,840 KiB free;
  active `CoreSimulator.log` is 5,378,451,586 bytes and closed `.prev` is
  15,403,577,516 bytes. No cleanup, compression, canary, or schedule was
  started; Matt's closed-log approval remains open.
- **Old MacBook:** `ssh oldmac` remains unreachable. The New-host retry label
  is enabled and launchd-loaded at `RunAtLoad=true`, `StartInterval=21600`,
  with the prior retryable offline receipt; no live deployment exists.

The six-hour elapsed-cycle proof and a new chat appearing through runtime
Drive publication remain unproven. Keep the launchd process and lease rules
binding; do not claim completion from the connector canary alone.

### Studio bounded diagnosis — 2026-08-29T20:43:59Z

The requested two-hour read-only diagnosis left the transaction untouched:
launchd still owns pid `14336` (`runs=1`, `last exit=(never exited)`), elapsed
`02:01:58`, process state `R`, and about 98.8% CPU. A five-second sample was
dominated by Python `_sre` search/substitution and JSON encoding, with no
read/write/poll/kevent/sleep frames. The latest receipt remains
`20260829T164025.921717Z-279ff85a.json` (`RunFailure`, publication
`not_attempted`), and the `.run.lock` plus `.incoming/tmp26x16_d8` descriptors
remain open. This is active CPU-bound work, not evidence of an I/O hang; leave
the launchd transaction running and do not start a foreground replacement.

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
in Drive automatically. Test counts alone are not success. The current lease
owner is finishing reachable-host deployment; a Studio successor must read the
lease and stand down until an owner-committed transfer.

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
5. **One release owner per repo with a deployment lease.** The repo-root lease
   is authoritative. Its named owner is the sole release owner for this
   repository; currently that is `codex:macbook` session
   `01a046f9-9427-7343-9221-4135b50bc30f`. Before touching a host, read the
   lease; a Studio successor must stand down until an owner-committed transfer.
   Do not let other agents deploy, merge, restart, or overwrite this repo while
   the lease is held.
6. **Persistent supervision for long canaries.** A long canary must be owned
   by launchd, never by a terminal, chat session, or agent process. The
   launchd-owned node must have durable stdout, stderr, and read-backable run
   and exit state. The shipped six-hour archive plist is an interval supervisor
   (`RunAtLoad=true`, `StartInterval=21600`); it does not contain `KeepAlive`, so
   do not describe it as a KeepAlive job. If a long canary needs retry-on-exit,
   that retry policy must be explicit in its own launchd plist. The prior run
   had two canaries die with their controlling sessions; do not repeat that
   failure mode.
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

### Structural enforcement (artifacts, not advice)

| Control | Required repository/runtime structure | Read-only pass proof | Failure action |
|---|---|---|---|
| Long canary ownership | A launchd label and plist own every long canary. The plist uses absolute repo/config paths, owner-only stdout/stderr, `Umask=077`, and a background process. A terminal or agent may install/read the job but may not own the collector process. | `launchctl print gui/<uid>/<label>` shows the loaded service, process/run state, paths, and interval; the receipt and exit state are in the owner-only paths. | Do not call the canary started or passed; stop before any foreground fallback. |
| Release ownership | Repo-root [`.deployment-lease.json`](../../.deployment-lease.json) is the single source of owner, session, host, scope, and expiry. | Read the file and compare the current session/host before any deployment, restart, merge, or connector write. | Any non-owner session stands down; transfer requires an owner commit. |
| Broad-release schema | [`SCHEMA_FREEZE_CHECKPOINT_2026-08-29.md`](../SCHEMA_FREEZE_CHECKPOINT_2026-08-29.md) is a release gate, not a note. | The checkpoint contains exactly one live-schema census, one canonical `validate_archive_object` validator/hash, and one batched review-wave result. | No broad release; create a new checkpoint for a new runtime instead of editing this one in place. |

These structures are binding even when a chat message is unavailable. A loaded
plist without a supervised process readback, a lease that names another owner,
or a missing/future schema checkpoint is a hard stop.

## 3. Honest state during active deployment

Last live reconciliation was 2026-08-29T22:03:47Z. The canonical repository
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
- The latest documentation/readback commit before this post-run amendment is
  `1e3acf9b48c16d1d501570c52fb4d495f0a7b285`, pushed to and read back on the
  owned fork; this handoff amendment supersedes it after its own fork readback.
- New's latest body-free receipt is
  `20260829T195959.324876Z-be2608f7`, collected at
  `2026-08-29T20:19:56.940667Z`, with zero errors and
  `blocked_no_drive_root`; Claude collected 1 conversation, Codex 5, Hermes
  had none, and OpenClaw is absent. Its launchd label is idle at `runs=2`,
  exit 0.
- Studio's launchd-owned attempt completed with exit 0. Its newest body-free
  receipt is `20260829T184201.313238Z-c87faa38`, with zero errors and
  `publication=blocked_drive_unavailable`; the prior `RunFailure` is historical
  and no restart or foreground fallback was used.
- Studio's manifest-bound Claude index repair is complete; its interrupted-index
  backup is retained. The Old retry implementation was reviewed and its
  offline behavior was previously proven (run count 1 -> 2, exit 0, zero
  stderr delta, `offline_retryable`/`ssh_unreachable`).
- The owner-only Google Drive DMG is staged and verified on Studio. It is not
  installed and no provider is mounted.
- The preceding docs reconciliation is local commit
  `16889d0baf1086012691ac735ddcb0ca964e690b`, pushed to and read back at the
  owned-fork branch tip before this amendment. This handoff/live-state/receipt
  amendment supersedes it after its own commit and fork readback; do not infer
  a different remote SHA.
- The Drive connector created and verified the private folder `AI Chat Archive`
  (ID `1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV`), published/read back a body-free
  receipt doc (ID `1ovOGhi7EdwUbbBUbPliQS4DYQ-A7N8ny77xt5u5wElM`) plus a launchd
  schedule receipt doc (ID `1Q4FFT1aglyjwRx3olRlmlvtR1MFKbVcCs4R96xLX_r4`), and
  verified redacted Codex text canaries IDs
  `1pLF5FhnQcMJ5yT28HXsnnHaQuqEJyR-5` (654 bytes) and
  `18kklPXiMM2bzF1ZU8tCzlJJ9k-HblbC_` (49,484,530 bytes) in the folder.
  Runtime JSON/raw shard publication remains separate and blocked without the
  Studio File Provider.
- New and Studio have their production six-hour labels loaded by launchd with
  `RunAtLoad=true` and `StartInterval=21600`. New's current run and Studio's
  first run have completed with exit 0; neither has an elapsed six-hour proof or
  runtime Drive publication.

### IN-FLIGHT — do not call these complete

- Studio's newest persisted receipt is run
  `20260829T184201.313238Z-c87faa38`, collected at
  `2026-08-29T22:01:36.872837Z`, with
  `completed_with_absent_harnesses`, zero errors, and publication
  `blocked_drive_unavailable`. This closes the current supervised collection
  attempt; it is not a Drive or six-hour pass.
- A loaded six-hour plist proves persistent scheduling only. The next receipt
  after approximately 21,600 seconds and a new Drive object remain unproven.

### NOT STARTED — explicit gates, not implied completion

- Studio still has no proven local Google Drive File Provider mount. The
  connector folder and body-free receipt doc exist, but raw object publication
  and new-chat-in-Drive proof do not.
- Matt has not approved Mini log compression/cleanup. Mini's real canary and
  production schedule have not run.
- Old MacBook has not returned online for live deployment or canary proof. Its
  retry label is now enabled and loaded under launchd on New; one RunAtLoad
  attempt exited 0 with `offline_retryable`/`ssh_unreachable`. “Active retry
  queue” is not the same as online deployment/canary proof.
- New and Studio production six-hour archive schedules are loaded under launchd;
  the elapsed-cycle proof remains in-flight. New has one completed RunAtLoad
  scan, but no approximately 21,600-second follow-up receipt.
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
| Handoff parent | `a2b40c9165a82b022054d4d470bf371cbc9890b4` | Adds this handoff before the addendum. |
| Lease/schema addendum | `5cd62dab6e4b5898cddfc8404b398525636fde00` | Adds `.deployment-lease.json`, schema-freeze checkpoint, and the binding lease/launchd rules. |
| Structural handoff amendment | Local `9b747927b6217287e035eecbdee8e6309a9e7f4d`; owned-fork readback `56e8b7f5f8a7ad47acd36bcd0a901e95339d4f20` | Encodes the Matt-ratified launchd/lease/schema structures; ref independently verified. |
| Current live-state docs | Local `f455158563003a127ce234d48244cbc5480580dd`; owned-fork readback `19423503453ebf6371bff09b03361dd5fbafe417` | Records the connector canary and the 20:09:39Z host readback; successor should pull this tree. |
| Prior docs reconciliation | `16889d0baf1086012691ac735ddcb0ca964e690b` (owned-fork branch tip before this amendment) | Records New's completed second refresh, the 20:26:10Z Drive connector import, and the 20:43:59Z Studio CPU-bound launchd diagnosis. The current branch tip is the commit that carries this handoff amendment. |
| Ratified structural enforcement amendment | `8802714639e5d6f1cc4ab383d287ac71cb15e477` | Adds the manager's launchd-only, single-lease-owner, and schema-freeze structures to this handoff; pushed to the owned fork and read back. |
| Post-amendment live-state reconciliation | `6b5488a3938afa4d4c0b49a906f861d537b5d402` | Records the 21:09:53Z Studio and 21:12:17Z New/Mini/Old readbacks in the handoff, live-state, and receipt docs; superseded by the later readbacks below. |
| Latest execution readback docs before this amendment | `1e3acf9b48c16d1d501570c52fb4d495f0a7b285` | Records the 21:35:42Z Studio/Mini readback and handoff commit-map alignment; this post-run amendment supersedes it. |
| Superseded parent | `0e25987370aa32a93423201dc25d85d913d8c8ac` | Exact Hermes provider-metadata validation; retained in history, superseded by `3c732d7`. |

The owned fork branch is now published and read back through the GitHub
connector at
`https://github.com/MattsAgentCal/ai-data-extraction/tree/matt/fleet-chat-archive-deployed`.
The upstream repository remains read-only for this account; do not claim an
upstream branch or merge from this fork ref without a fresh permission/ref check.

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
| Special evidence | Production label loaded; Old retry unloaded | Claude restore backup, staged Drive DMG, and connector receipt | CoreSimulator logs and storage gate | SSH alias `oldmac`; offline |

The Studio-only repair backup is
`/Users/calstudio/.local/share/ai-chat-archive-repair-proof.BCwlg8/live-current-index.backup.json`.
The Studio-only installer is
`/Users/calstudio/Downloads/GoogleDrive-2026-08-28.dmg`. The Mini-only logs
are `/Users/calrotundo/Library/Logs/CoreSimulator/CoreSimulator.log` and
`CoreSimulator.prev.log`. These are host-local references, not Git inputs.

## 5. Exact per-machine state and receipts

### New MacBook (`newmac` / local originating host)

- The originating checkout is at docs commit
  `182114dd6cff89018761b72f5387dfc6ad6daa89` (runtime `3c732d7`).
- Production label `com.mattrotundo.ai-chat-archive.new-macbook` is loaded by
  launchd. `launchctl print` shows the collector as the supervised process; no terminal
  owns it. The label is idle at `runs=2`, exit 0.
- Current completed receipt: run ID
  `20260829T195959.324876Z-be2608f7`, collected at
  `2026-08-29T20:19:56.940667Z`, with status
  `completed_with_absent_harnesses`, errors 0, publication
  `blocked_no_drive_root`; Claude collected 1 conversation, Codex 5, Hermes
  had no conversations, and OpenClaw was `not_present_on_host`. The current
  goal session is redacted object digest `d5883edd…`, 49,484,530 bytes,
  retained outside Git. The six-hour elapsed proof is still absent.
- Old retry label `com.mattrotundo.ai-chat-archive.old-macbook-deploy-retry` is
  enabled and loaded under launchd with `RunAtLoad=true`, `StartInterval=21600`,
  `runs=1`, and exit 0 after its offline attempt. It is a queued retry, not an
  online deployment proof.

### Mac Studio (`studio` / successor host)

- Read-only SSH finds the shipped runtime at `3c732d7` and the checkout path
  `/Users/calstudio/Projects/ai-data-extraction`.
- Production label `com.mattrotundo.ai-chat-archive.mac-studio` is loaded by
  launchd with `RunAtLoad=true`, `StartInterval=21600`, `runs=1`,
  `state=not running`, and `last exit code=0` after the first supervised scan.
  `launchctl print` showed the collector as the supervised process; no terminal
  owned it.
- The newest persisted receipt is run ID
  `20260829T184201.313238Z-c87faa38`, collected at
  `2026-08-29T22:01:36.872837Z`; status/collection_status
  `completed_with_absent_harnesses`, errors empty, and publication
  `blocked_drive_unavailable`. Claude collected 12 conversations/7 new objects,
  Codex 51/50, Hermes had no conversations, and OpenClaw is absent. The prior
  `RunFailure` receipt and 21:35 CPU-bound sample are historical; no restart or
  foreground fallback was used.
- Hub statuses were New `pulled` (1131/1131), Mini `pending_manifest`, and Old
  `unreachable`. The connector receipt is in Drive folder
  `1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV`; no local File Provider mount is visible.

### Mac mini (`cals-mac-mini`)

- Read-only SSH found a clean checkout at `3c732d7`; no production archive label
  is enabled.
- The 21:59:33Z readback found 7,295,288 KB free. The active
  `CoreSimulator.log` is 5,493,598,617 bytes and the closed
  `CoreSimulator.prev.log` is 15,403,577,516 bytes. No cleanup, compression, or
  canary was started.
- No cleanup, compression, or real canary was started. The only proposed
  cleanup is the closed, zero-handle log; never delete or compress the active
  log without a separately reviewed safety decision.

### Old MacBook (`oldmac`)

- SSH timed out again at 22:03:47Z. No live inventory, deployment, or
  canary proof exists.
- The earlier final-pin retry proof advanced run count 1 -> 2, exited 0, had
  zero stderr delta, and reported `offline_retryable`/`ssh_unreachable`.
- The current retry label is enabled/loaded on New and its first RunAtLoad
  attempt exited 0 with the same retryable offline status. The scripted install
  command first failed closed and restored the disabled state; the exact plist
  was then enabled/bootstrap-loaded and read back successfully. Once Old
  returns, the Studio release owner must recheck identity, deploy the exact
  runtime pins, and prove the retry/canary before calling it healthy.

### Google Drive (`Studio only`)

- Staged installer: `/Users/calstudio/Downloads/GoogleDrive-2026-08-28.dmg`.
- Last verification: mode 0600, 141,267,496 bytes, SHA-256
  `fb6927060f8f20efb8ac2027d00a9c0787c111fa57c01fe6a29675afaf5c1178`, and
  `hdiutil verify` returned `VALID`.
- The Drive connector profile is authenticated. It created and verified private
  folder `AI Chat Archive` ID `1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV` and placed
  body-free receipt doc ID `1ovOGhi7EdwUbbBUbPliQS4DYQ-A7N8ny77xt5u5wElM` in
  it; metadata, folder listing, and document text read back successfully.
  It also imported the current New goal-session object once as redacted text,
  file ID `18kklPXiMM2bzF1ZU8tCzlJJ9k-HblbC_`, size 49,484,530 bytes, and
  read back its exact folder parent. The folder now has four items.
- Not started: local File Provider installation/login/mount, raw conversation
  object publication, and the new-chat-in-Drive proof. No raw local-path upload
  was attempted because the connector requires a runtime file reference.

## 6. Matt gates — surface these first

The connector gates are cleared. Surface the remaining human/runtime gates once,
first, and batched before a Studio successor takes the lease:

1. **Studio runtime Drive mount (two clicks):** open
   `/Users/calstudio/Downloads/GoogleDrive-2026-08-28.dmg`, double-click
   `GoogleDrive.pkg`, then complete the Google sign-in. If the staged DMG is
   unavailable, use Google's official installer:
   `https://dl.google.com/drive-file-stream/GoogleDrive.dmg`. Consequence:
   exactly one mounted provider can unlock the runtime's private `AI Chat Archive`
   publication gate. The connector folder/doc already exists, but do not claim
   raw Drive success before the provider and a `published` runtime receipt are
   visible.
2. **Mini log-compression approval:** approve only the closed, zero-handle
   `CoreSimulator.prev.log` (15,403,577,516 bytes) for compression/archive and
   removal if the reviewed operation requires it. Do not touch the active
   `CoreSimulator.log` (5,378,451,586 bytes, one open handle). Consequence:
   enough free space to run the Mini canary; no cleanup has occurred yet.

Surface both gates once, in this order, with the exact action and consequence.
Do not wait until after a canary or infrastructure change to mention them.

## 7. First three successor actions

The current owner has already started these bounded actions. A Studio successor
must first read the lease and continue only after an owner-committed transfer.

1. **Surface the two remaining gates and read the deployment lease.** Send the
   single batched Drive/Mini message above. The current lease names the MacBook
   owner; do not write a Studio lease or deploy until that owner transfers it in
   a commit.
2. **Perform a read-only, live-shaped preflight under the lease.** Verify the
   exact runtime hashes and clean worktree on Studio, New, and Mini; check
   `launchctl` labels and current temporary canary receipts; check source roots,
   SSH host identities, free disk, Drive provider count, and `.run.lock`/`.work`
   state. Recheck Old with a bounded timeout and record it as offline if it
   still fails. Do not start a long run or enable production infrastructure from
   a terminal.
3. **Read back the existing supervised checkpoint.** New and Studio already
   have production launchd supervisors with durable owner-only stdout/stderr and
   six-hour intervals. Do not start a second long canary. Wait for the current
   receipts, batch any findings into one review wave, make at most one bounded
   repair, and re-review once. Only a clean checkpoint can authorize the next
   infrastructure step.

## 8. Proving the automatic six-hour cycle on the shipped build

The proof target is not “launchd is installed.” The proof target is: a new
allowed chat is collected, merged, and appears in the Studio Drive folder after
the six-hour automation path, while the controlling session is gone.

Current execution evidence: New label
`com.mattrotundo.ai-chat-archive.new-macbook` and Studio label
`com.mattrotundo.ai-chat-archive.mac-studio` are both loaded by launchd with
`RunAtLoad=true` and `StartInterval=21600`. New's second RunAtLoad scan and
Studio's first scan have completed with exit 0; Studio's newest receipt has zero
errors but `publication=blocked_drive_unavailable`. This proves launchd
ownership, configured interval, and one successful supervised collection only.
It does not prove the elapsed six-hour cycle, runtime `published` status, or a
new chat visible in Drive. The two Drive connector text canaries are body-safe
manual imports and are not that proof.

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

For any new long canary, submit a dedicated launchd wrapper with absolute
repo/config paths, owner-only stdout/stderr, and an exit-code file; add an
explicit `KeepAlive`/retry policy when retry-on-exit is required. Record the
label and receipt directory before starting. Do not run the Python process from
a terminal or agent session.

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
- The supervisor is loaded and will survive logout; New and Studio production
  six-hour labels are active and their first scans are being observed under the
  deployment lease.

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
cleared, install (or read back) the per-user schedule on each authorized
reachable host:

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
