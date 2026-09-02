# HANDOFF — Studio successor for the fleet chat export

**Handoff date:** 2026-08-29
**Last verified:** 2026-09-02T04:50Z (MacBook + GitHub/Drive plugins + Studio/Mini/Old read-only)
**Origin:** MacBook documentation turn
**Destination:** successor agent operating on the Mac Studio
**Current mode:** the deployment goal is resumed by the named lease owner;
this document is the durable handoff and does not transfer the lease. Matt's
GitHub and Google Drive plugins are active; the superseded Desktop-DMG install
gate must not be re-opened. The current local release is
`963342806620c126ba4a4afdd2a94fa37507033f` / tree
`e992d33f0a00d2fc1fa781b7219017eb386df9b5`, published by GitHub-plugin commit
`1303414310a1193e812dfcd3a7f7e53703f35b30`. New and Studio collectors are
healthy launchd jobs; the supervised publisher route is live but its latest
run had one historical metadata failure, so the end-to-end “new chat appears in
Drive automatically” proof remains in flight. Mini's requested closed log and
archive are absent and no disk mutation occurred; Old remains offline on its
retry queue.

Historical Mini-only readback at `2026-09-01T11:19:05Z` is retained below the
current checkpoint as a prior receipt: both requested paths were absent, and the guarded no-op measured
`30,604,537,856` free bytes (`29,887,244 KiB`). No gzip, `gzip -t`,
spot-decompress, or deletion ran; exact operation delta and net freed space are
`0` bytes. This does not authorize deletion or change the single outstanding
Mini gate.

This is the complete context for a successor that has the repository but none of
the originating agent's conversation history. Treat the timestamps and host
observations below as the last verified snapshot, not as a substitute for a
fresh read-only check before any mutation.

## Current successor handoff — 2026-09-02T04:50Z

### 1. Matt's goal and current intent

Matt's goal, in his words, is to run
`https://github.com/0xSero/ai-data-extraction` on the new MacBook, old MacBook,
Mac Mini, and Mac Studio across Claude Code, Codex, OpenClaw, and Hermes; put
privacy-safe exports in Google Drive; automate new-chat ingestion; and prove a
new chat appears in Drive automatically. Current intent is deploy-first:
reachable hosts and the six-hour launchd path must be real and observed; Old is
queued without blocking; Mini stays paused and destructive deletion remains
Matt-gated. Google Drive Desktop installation is superseded: Matt's activated
Codex GitHub and Google Drive plugins are the publication route.

### 2. Honest state

**DONE with receipts**

- Prior adapter/pipeline work, content-addressed dedupe, credential redaction,
  provenance manifests, trusted remote stream, lease, schema-freeze controls,
  and launchd collectors are in the checkout. The new supervised publisher
  runtime is local commit `aff135274f26469012e117c1977a641fd8569999`; the lease
  file update is commit `963342806620c126ba4a4afdd2a94fa37507033f` and its
  `commit` field names the runtime. Focused tests pass
  `19/19`; full suite `282/282`.
- GitHub plugin publication succeeded non-force: remote commit
  `1303414310a1193e812dfcd3a7f7e53703f35b30`, tree
  `e992d33f0a00d2fc1fa781b7219017eb386df9b5`, matching local
  `HEAD^{tree}`. The remote parent was `9aec39fb0f9b12623532cf5c8be922781699901d`.
- Drive plugin access and the materialization route are proven by an
  independent exact-folder metadata readback: object
  `8ef66ffab4606d80134252cc4040042d4ab753da58167ec66956c4dbee39f6b1.json` is
  Drive file `1-g2pg5Ce498zwWDG2kd0MyUL57FKEoJv`, `text/plain`, `67687` bytes,
  parent `AI Chat Archive` (`1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV`).
- New collector is launchd-owned and healthy: `runs=13`, exit `0`, interval
  `21600`; receipt `20260901T211000.917786Z-97b49fd7` finished at
  `2026-09-01T23:21:40Z` with Claude `3/2` new, Codex `11/11`, Hermes `4/3`,
  and OpenClaw inventory-only.
- Studio collector is launchd-owned and healthy: `runs=3`, exit `0`, interval
  `21600`; receipt `20260902T013528.694544Z-bb6f6ea0` has zero errors with
  Claude `14/8` new, Codex `463/463`, Hermes `186/1`, and OpenClaw absent.

**IN-FLIGHT**

- New's launchd publisher run `20260902T042550.910403Z-3ad4684c` validated both
  shards and ended partial with 23 exact metadata-verified skips, one failed
  historical object, and zero new uploads. It was started before the receipt
  transport-field repair; the next run uses the committed model-turn route.
- A fresh Codex canary source exists at
  `/Users/mattrotundo/.codex/sessions/2026/09/01/rollout-2026-09-01T23-25-06-01a06026-1f96-7fd1-a036-053d75e35a7f.jsonl`
  (`124807` bytes, mtime `2026-09-01T23:25:14-0400`), after the latest New
  collector receipt. The natural six-hour collector receipt, publisher
  selection, and Drive readback chain are not yet proven.
- Old MacBook is unreachable; its loaded New-host retry remains
  `offline_retryable` / `ssh_unreachable` at `StartInterval=21600`.

**NOT STARTED / GATED**

- Mini export, four-harness canary, launchd schedule, and Drive publication
  remain paused by instruction. At the exact expected path,
  `CoreSimulator.prev.log` and `.gz` are absent; active `CoreSimulator.log`
  was untouched. Free space observed was `27,667,742,720` bytes (`25.767593
  GiB`), archive bytes `0`, freed bytes `0`. No gzip, `gzip -t`, spot
  decompress, or deletion ran. There is no source to delete; do not infer a
  successful compression.
- Old's online deployment and full four-host/four-harness completion remain
  unverified. OpenClaw is inventory-only/absent on the reachable New and Studio
  hosts, so “every harness” is not yet a completion claim.

### 3. Exact file and commit map

The MacBook-only checkout is `/Users/mattrotundo/Projects/ai-data-extraction`;
the current branch is `matt/fleet-chat-archive-deployed`. The Studio clone is
`/Users/calstudio/Projects/ai-data-extraction` and currently has the older
collector-equivalent checkout `68c02d1953b428b2ecf6443e26a56560bb81f436`;
the Mini clone is `/Users/calrotundo/Projects/ai-data-extraction` at
`3c732d7b1031949bd18db90ae4ac40f667f6cfa7`. The Old path is unknown until SSH
recovers. The MacBook-only app-server socket is
`/Users/mattrotundo/.codex/app-server-control/app-server-control.sock`; the
MacBook-only publisher plist is
`/Users/mattrotundo/Library/LaunchAgents/com.mattrotundo.ai-chat-archive.drive-publisher.plist`.
Collector plists are host-local under each user's `Library/LaunchAgents`.

The release-owner file is repo-root `.deployment-lease.json`; it names
`codex:macbook` / `Mac.lan`, branch, scope, expiry, and commit
`963342806620c126ba4a4afdd2a94fa37507033f`. The main runtime files are
`drive_plugin_publisher.py`,
`configs/new-macbook-drive-publisher.json`, and
`tests/test_drive_plugin_publisher.py`; current docs are this handoff,
`docs/FLEET_CHAT_ARCHIVE.md`, `docs/FLEET_CHAT_ARCHIVE_LIVE_STATE.md`,
`docs/RECEIPT_FLEET_CHAT_ARCHIVE_2026-08-29.md`, and
`docs/SCHEMA_FREEZE_CHECKPOINT_2026-09-01.md`. Raw spool objects, indexes,
receipts containing local paths, session files, and plugin transcripts are
host-local and must not be copied into Git or AgentBrain.

### 4. Open Matt gates

- **Mini destructive gate:** deletion of a protected/closed log is still
  Matt-controlled. The requested source is currently absent, so the safe
  action is no-op; never re-ask or improvise a substitute. The paused Mini
  export/canary also remains outside the shipped completion claim.
- **Drive Desktop gate:** superseded and closed as a path. The activated Codex
  Google Drive plugin is now the route; its small write and exact readback are
  already proven. Do not wait for or install a DMG.
- **Studio disk gate:** current read-only evidence shows ample free space and a
  healthy collector; do not resurrect historical quarantine cleanup unless a
  fresh check creates a separate explicit gate.

### 5. Binding execution structure

These are enforced structure, not advice:

1. Canary-first before infrastructure. Run a live-shaped preflight against the
   exact source/index/manifest/Drive metadata shape before any long run.
2. Every long canary node runs under persistent launchd, never a foreground
   session. The plist must be read back with owner-only logs, absolute paths,
   `Umask=077`, and a visible run/exit transition.
3. `.deployment-lease.json` names the single release owner for this repo. Any
   other session reads it and stands down. Transfer requires an owner commit;
   only the lease owner may release, restart, merge, or publish.
4. Before a broad release, perform one live-schema census and use one canonical
   validator, then ONE review wave. Batch findings, make one repair, and do one
   re-review; do not open another wave for the same checkpoint.
5. Surface Matt gates first, once, batched, and pre-staged to two clicks. Do not
   let a gate silently become the last item.

### 6. Successor's first three actions

1. Read `.deployment-lease.json`, confirm the owner is still `codex:macbook`
   / `Mac.lan`, and stand down if it differs. Read the current New launchd
   labels and latest body-free receipts; do not touch Mini or start a duplicate
   publisher.
2. Preserve the New collector and publisher schedules. Let a fresh chat be
   collected on a natural six-hour transition; record the run-count/receipt
   delta and object digest, then let the next launchd publisher run select it.
3. Independently search the exact Drive folder and read metadata for that digest
   (name, ID, `text/plain`, byte size, exact parent), then update this handoff
   and receipts. Only after that proof should any Studio/Mini/Old deployment
   decision be surfaced; Mini remains paused.

## Current reconciliation — 2026-09-01T11:53Z (supersedes the 11:31Z snapshot)

This body-free checkpoint records the persistent canary preflight. At
`2026-09-01T11:49:53.147944Z`, Codex thread
`01a05ccd-e6b1-7510-82fa-e2296481b2f1` completed without tools as a real
persistent session. The source file remains host-local under
`/Users/mattrotundo/.codex/sessions/2026/09/01`, has `16` JSONL records,
`134,708` bytes, and SHA-256
`8a76ae73ad5de14d330e99374ee3553c2206a55bfe98741b37322b161a2ae5e3`. The
collector and publisher were not manually kicked; their next natural windows
are approximately `2026-09-01T16:22:36Z` and `2026-09-01T16:37:07Z`.

| Machine | Evidence | Classification |
|---|---|---|
| New MacBook | At `2026-09-01T11:53:49Z`, collector `runs=11` and publisher `runs=3` were idle, both exit `0`, `StartInterval=21600`; the post-canary receipt is pending. | **IN-FLIGHT:** preserve the natural cycle and correlate one new object through Drive. |
| Mac Studio | At `2026-09-01T11:52:53Z`, free space was `127,312 KiB`; the shipped label was idle at `runs=1`, exit `143`. | **DISK-GATED:** no retry or quarantine mutation. |
| Mac Mini | At `2026-09-01T11:48:38Z`, the requested source and `.gz` were absent; free space was `29,876,336 KiB` = `30,593,368,064` bytes and source/archive/operation bytes were `0`. | **PAUSED:** no export/canary/schedule; deletion gate untouched. |
| Old MacBook | At `2026-09-01T11:52:53Z`, SSH remained unreachable; retry label `runs=9`, exit `0`, `StartInterval=21600`. | **IN-FLIGHT:** nonblocking retry. |

The 11:31Z reconciliation remains below as historical evidence.

## Historical reconciliation — 2026-09-01T11:31Z (superseded)

This was the latest body-free handoff checkpoint at 11:31Z. Its parent docs ref is the
GitHub-plugin commit `f23912ffaed783ee5be84b441980f9dc68d818cf`, tree
`2f68e6c0dcc2193bec6d0ab3128ab0d9c1b24c35`, parent `68c02d1…`; the local
checkout has the same tree. The activated Drive plugin canary wrote
`ai-chat-archive-plugin-canary-20260901.txt`, read back exact metadata
(`text/plain`, `18,167` bytes, target parent), and deleted only that canary.
The production launchd publisher's previous receipt remains `24/24/0/0`.

### Current per-machine state

| Machine | Evidence | Classification |
|---|---|---|
| New MacBook | At `2026-09-01T11:31:12Z`, collector `runs=11` and publisher `runs=3` were idle, last exit `0`, and both six-hour plists remained loaded. The Drive-canary child left ephemeral Codex thread `01a05cb9-bfca-7252-a5c1-cc0e514b5d90` as the new-chat candidate. | **IN-FLIGHT:** preserve the next natural cycle; correlate its new object and Drive metadata without a manual kick. |
| Mac Studio | At `2026-09-01T11:26:37Z`, free space was `973,732 KiB`; quarantine was `73,136,828 KiB` / `22,501` files and `.work` was empty. The shipped label is idle after launchd `SIGTERM` (`runs=1`, exit `143`). | **DISK-GATED:** do not retry or touch quarantine without explicit authorization. |
| Mac Mini | At `2026-09-01T11:24:16Z`, the requested source and `.gz` were absent; free space was `29,893,336 KiB` = `30,610,776,064` bytes. Operation delta and net freed bytes are `0`; no log mutation occurred. | **PAUSED:** no export/canary/schedule; deletion gate remains untouched. |
| Old MacBook | SSH remains unreachable; retry label remains loaded at `StartInterval=21600`. | **IN-FLIGHT:** nonblocking retry; online deployment is unstarted. |

### Current gates and next proof

The two outstanding operational gates are (1) Studio disk restoration/cleanup
authorization for the existing quarantine and (2) Matt's still-unresolved
Mini log decision. The Drive Desktop route remains superseded; plugin access is
verified, but a new chat appearing after a natural six-hour collection and
publisher cycle is still not proven. The successor must retain the loaded New
plists, capture the next terminal receipt, match one newly emitted object to a
Drive metadata readback, then update this handoff. No foreground canary or
manual kick counts as the automatic proof.

## Historical reconciliation — 2026-09-01T11:14Z

This is the successor's current body-free checkpoint. The repository is clean
at local commit `88d65a7677dbdb483b36a85ff59b907231727502` / tree
`5e33dd15aeb26ecbe059ac6cf83338442a21c493`; the owned-fork branch was
read back at `68c02d1953b428b2ecf6443e26a56560bb81f436` with that same tree,
using `force=false`. The one operational lock-wait repair is covered by
focused `9/9` tests and a `272`-test full suite. The lease remains active for
`codex:macbook`; a Studio successor must stand down unless the owner commits a
transfer.

### Honest per-machine state

| Machine | Current evidence | Classification |
|---|---|---|
| New MacBook | `com.mattrotundo.ai-chat-archive.new-macbook` is idle after `runs=11`, exit `0`, `StartInterval=21600`. Receipt `20260901T064318.917191Z-a3e5881a` at `2026-09-01T10:22:36.435118Z` is zero-error `completed_with_absent_harnesses`: Claude 31/1 new object, Codex 1,224/17, Hermes 4/2, OpenClaw absent/inventory-only; present-harness quality is complete. | **DONE:** terminal shipped-tree canary. **IN-FLIGHT:** wait for a second natural interval to strengthen the six-hour proof. |
| Mac Studio | `/Users/calstudio/Projects/ai-data-extraction` is clean at `68c02d1…` / tree `5e33dd15…`. The launchd-owned canary (`PID 54195`, `PPID 1`) was stopped through launchd `SIGTERM` after free space fell below 2 GiB; receipt `20260901T104451.299032Z-40cc7d40` is `failed`/`RunFailure`, exit `143`. `.work` staging and `.run.lock` are clean. Existing `/Users/calstudio/.local/share/ai-chat-archive/spool/quarantine` is `74,892,111,872` bytes (`73,136,828 KiB`) across `22,501` files. | **IN-FLIGHT / DISK-GATED:** do not retry until quarantine cleanup is explicitly authorized; do not delete or compress it as part of this handoff. |
| Mac Mini | `Cals-Mac-mini.local` has no requested `CoreSimulator.prev.log` or `.gz`; current free space before/after the guarded check was `30,606,172,160` bytes. Source/archive/deletion/net-freed bytes are all `0`; no gzip or test ran. | **DONE:** non-destructive no-op. **NOT STARTED/GATED:** no export, canary, or schedule; deletion remains untouched. |
| Old MacBook | `ssh oldmac` still times out. New's retry label is loaded under launchd with `runs=9`, exit `0`, `StartInterval=21600`, and `offline_retryable` receipts. | **IN-FLIGHT:** non-blocking retry. **NOT STARTED:** online deployment/canary. |

### Drive publication and automatic-cycle proof

The launchd publisher on the MacBook completed one supervised run: receipt
`20260901T103707.805024Z-c2f89e5e.json` has `24` candidates, `24` uploads,
`0` skips, `0` failures, and `errors=[]` to `AI Chat Archive`
(`1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV`). Connector readback for file
`10C4tI7CAeFTn_rYSFICo_9lpdOQ86a0U` verified exact name, `text/plain`, size
`3475`, and exact parent. This proves staged plugin publication under launchd;
it does not yet prove that a newly created chat, collected on a natural
six-hour boundary, appears in Drive. The successor must leave the New job
loaded at `StartInterval=21600`, capture the next terminal receipt without a
manual kick, then correlate one new object through the publisher receipt and a
Drive metadata readback. Studio publication should follow only after its disk
gate clears.

### Binding execution rules

These are structural requirements, not suggestions:

1. Every long canary node runs under persistent launchd; never a foreground
   terminal or transient controlling session.
2. `.deployment-lease.json` names the single release owner. Every other
   session reads it and stands down; transfer requires an owner-authored commit.
3. Before any broad release, perform one live-schema census, use one canonical
   validator, and run ONE batched review wave. Batch findings, make one repair,
   and do one re-review.
4. Surface Matt gates first, once, batched, and pre-staged to two clicks. The
   Drive Desktop route is superseded by the activated plugin. The Mini deletion
   gate remains untouched; never re-ask it.

### First three actions for the successor

1. Read `.deployment-lease.json`, record the active owner, and leave the
   Studio job idle while the disk gate is unresolved. Do not delete or compress
   quarantine data or Mini data.
2. Preserve the New launchd schedule and wait for the next natural run; record
   its receipt/run-count transition and calculate the elapsed interval against
   `20260901T064318.917191Z-a3e5881a`.
3. After a new object is emitted, let the launchd publisher process its batch,
   read back one exact Drive metadata record, and then update this handoff and
   the receipt before any Studio retry.

## Historical reconciliation — 2026-09-01T08:45Z

This is the latest body-free snapshot. It supersedes the dated readbacks below;
those remain historical receipts and must not be used as current host state.

### 1. Goal and current intent

Matt's goal is to run `https://github.com/0xSero/ai-data-extraction` on every
machine (new MacBook, old MacBook, Mac mini, Mac Studio) and every approved
harness (Claude Code, Codex, OpenClaw, Hermes), place redacted exports in the
private Google Drive `AI Chat Archive` folder, and make new chats appear there
automatically. The current intent is deploy-first: finish the reachable-host
pipeline and prove the automatic six-hour path; keep Old retryable and keep
Mini deletion gated. The Google Drive Desktop route is superseded by Matt's
activated Codex plugin route.

### 2. Honest state (with receipts)

**DONE**

- The audited adapters, content-addressed objects, redaction, provenance,
  trusted remote stream, lease, schema-freeze checkpoint, and launchd-owned
  collectors are in the repaired tree. Local commit is
  `4b665987ad9c06c618a73fc67e7b6004d1bd1881`; the GitHub-plugin fork ref is
  `ec96761786196f58b8157de8dc917c85947a09b8`; both have tree
  `300ce290c9d75e4187a240770db7f9793c57d577`.
- The post-repair suite passed 270 tests (`python3 -m unittest discover -s
  tests -q`). The one bounded `realtime_item` repair and one re-review are
  recorded in `docs/SCHEMA_FREEZE_CHECKPOINT_2026-09-01.md`.
- The Drive plugin target folder is `AI Chat Archive`, ID
  `1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV`; probe object
  `19kCcCqMlXNNowcXoXsZe9swvPUC95wNF` remains as the small connector test
  write. Publisher receipt `20260901T025751.237727Z-74cdf5d2.json` recorded
  8 uploads, 14 metadata-verified skips, and 2 connector failures; automatic
  launchd-to-plugin publication is not yet proven.
- Old's launchd retry queue is loaded at `StartInterval=21600` and reports
  retryable SSH-offline receipts.

**IN-FLIGHT**

- New's `com.mattrotundo.ai-chat-archive.new-macbook` is launchd-owned,
  PID `84541`, PPID `1`, `runs=11`, `StartInterval=21600`, and active. It is
  performing the post-repair full Codex revalidation; receipt count is 170 and
  no terminal receipt exists yet. Do not kill it or run a foreground
  replacement. Wait for its receipt and lock release.
- After that receipt, fast-forward Studio from `bb9a08570baffc2111e832ac7418bab9d33755af`
  (tree `8f4f0122…`) to the published repaired ref `ec96761786196f58b8157de8dc917c85947a09b8`,
  reinstall `com.mattrotundo.ai-chat-archive.mac-studio` from the corrected
  pull-only config, and read back PID/PPID, `runs`, exit code, and interval.
  Studio is currently idle at `runs=1`, last exit 0, with 51 body-free
  receipts; its failed stop receipt is
  `20260901T024039.360623Z-bc4432a1.json` and the last-good manifest is
  retained.
- Complete one fresh launchd-owned Codex canary, kick only the supervisors,
  publish its staged object via the Drive plugin worker, and read back exact
  Drive parent/MIME/size. This is the remaining success proof; publisher
  `connector_error` failures must be retried only in the bounded scheduled
  batch, not via a second review wave.

**NOT STARTED / GATED**

- Mini export, canary, and schedule are not started. The exact requested source
  `/Users/calrotundo/Library/Logs/CoreSimulator/CoreSimulator.prev.log` and
  output `.gz` are absent on `Cals-Mac-mini.local`; a filename-only search
  under its Logs tree also found no rotated copy. The single pre/post disk
  check measured 29,895,640 KiB = 30,613,135,360 bytes free both before and
  after. Therefore source 0 bytes + archive 0 bytes + deletion 0 bytes =
  operation delta/freed 0 bytes. No gzip, `gzip -t`, spot-decompress, or
  deletion ran. The sole deletion gate remains untouched; never touch the
  active Mini log.
- Old online deployment/canary is not started while SSH is unreachable.
- OpenClaw collection is not proven on New or Studio because it is absent on
  those hosts; Hermes reports no conversations on Studio. Do not inflate
  absent-harness counts into collection success.

### 3. Exact file / commit / host map

| Item | New MacBook path / commit | Studio translation / state |
|---|---|---|
| Repo | `/Users/mattrotundo/Projects/ai-data-extraction`, `4b665987…`, tree `300ce290…`; GitHub ref `ec967617…` | `/Users/calstudio/Projects/ai-data-extraction`, currently `bb9a085…` / tree `8f4f0122…`; fast-forward to `ec967617…` only after New's terminal receipt |
| Collector spool | `/Users/mattrotundo/.local/share/ai-chat-archive/spool` | `/Users/calstudio/.local/share/ai-chat-archive/spool` |
| New collector plist | `/Users/mattrotundo/Library/LaunchAgents/com.mattrotundo.ai-chat-archive.new-macbook.plist` | N/A |
| Studio collector plist | N/A | `/Users/calstudio/Library/LaunchAgents/com.mattrotundo.ai-chat-archive.mac-studio.plist` (currently booted out) |
| Drive publisher | `/Users/mattrotundo/Library/LaunchAgents/com.mattrotundo.ai-chat-archive.drive-publisher.plist`; config `configs/new-macbook-drive-publisher.json` | Drive plugin account is used through the worker; no Desktop File Provider is required |
| Lease | Repo-root `.deployment-lease.json`, owner `codex:macbook`, host `Mac.lan`, active through `2026-09-02T02:26:04Z` | A Studio successor must read and stand down unless the owner commits a transfer |
| Current Studio receipt | N/A | No new receipt since the idle stop; `20260901T024039.360623Z-bc4432a1.json` is the honest failed stop, with 51 body-free receipts and the last-good manifest retained |

These MacBook paths are host-local and do not imply that private spool data is
in Git. Only code, configs, receipts' body-free facts, and this handoff are
tracked.

### 4. Matt gates — surface first, once, batched

The two original gates were surfaced first: (1) Google Drive install/login on
Studio, and (2) Mini closed-log compression/deletion approval. Gate (1) is now
superseded by Matt's activated GitHub and Google Docs/Drive plugins; the existing
small probe is the plugin test write and publication must use that route, not
the Desktop DMG. Gate (2) remains open only for deletion of a closed log; the
non-destructive gzip check found no source and touched nothing. Do not re-ask
either gate or send a new prompt. If Matt later sends a one-word Mini approval,
handle only the explicitly approved deletion after a fresh source/path check.

### 5. Binding execution structure

These are controls, not advice:

1. Every long canary node runs under a persistent `launchd` job; never a
   foreground terminal or transient controlling session. The two historical
   canaries died with their controlling sessions, so a successor must not
   repeat that shape.
2. `.deployment-lease.json` names the single release owner. Every other session
   reads it and stands down; the owner holds the deployment lease for GitHub,
   Drive publication, canaries, and Mini coordination. Transfer requires an
   owner-authored commit.
3. Before any broad release, perform exactly one live-schema census and use
   exactly one canonical validator, then run ONE batched review wave. Batch all
   findings, make one repair, and do one re-review. The prior run's 17 review
   waves in 21 hours is a process failure, not a template for the successor.
4. Surface Matt gates first, once, batched, and pre-stage any action to two
   clicks. Do not hide a gate until the end of the run.

### 6. First three successor actions

1. Read `.deployment-lease.json`, record the owner/expiry, and record the
   already-batched Mini deletion gate without asking Matt again; explicitly
   note that the Drive Desktop gate is superseded. Do not mutate the Mini.
2. Wait for the New launchd receipt, then fast-forward Studio to `ec967617…`,
   reinstall its launchd collector from `configs/mac-studio.json`, and verify
   the loaded job. Do not run a foreground collector or open another review
   wave.
3. Create one fresh non-ephemeral Codex canary, kick New's launchd collector,
   wait for its body-free receipt/index row, kick the launchd Drive publisher,
   and read back the canary's exact Drive metadata. Record only session ID,
   object digest, receipt path, launchd PID/PPID, interval, Drive ID, MIME,
   parent, and size.

### 7. Automatic six-hour proof on the shipped build

The proof is a new approved chat appearing in Drive after the supervised path,
not a test count. Require: the New collector `RunAtLoad=true` and
`StartInterval=21600`; a launchd-owned run with a new body-free receipt and
index row; the launchd publisher receipt naming the same object and Drive ID;
and a plugin metadata readback with exact parent, `text/plain` MIME, and byte
size. Record the pre/post receipt counts, launchd `runs`, PID/PPID, and UTC
timestamps. A manual plugin upload is not automatic proof. The current
publisher's first scheduled receipt is the baseline; the fresh canary and
metadata readback are still required. If any shard is absent/offline, record
that status and continue reachable hosts without changing the lease or gates.

### Current successor snapshot — 2026-08-30T11:05:59Z

This section is the current truth and supersedes older dated readbacks below;
those sections remain historical receipts. The active lease is still
`codex:macbook` session `01a046f9-9427-7343-9221-4135b50bc30f` on `Mac.lan`,
expiring `2026-08-30T16:49:17Z`; a Studio successor must read it and stand down
until an owner-authored transfer. The last clean local tip before this snapshot
was `754af13bbcf62b3c2a1d87c4801f1773ceed6002`; the latest fork branch ref
seen by `git ls-remote` was `2f466bd9d6041b8494eb23377b737d9f49c867d8`.
This session had no callable GitHub connector tool, so it did not push or
perform a new recursive readback. The content reconciliation commit was local
`92bc5fcf35212b96484b98e0a6dd54e28d83fb8f`, published as fork commit
`e25458991eac2ccdd6ef7572e186857a7991679f`. The preceding ref-pinning commit
was local `42457cc8eba6b8cda7a50f597d6670a28089e47d`, published as fork commit
`5fc48e9f07a15cbdf482a212df75b83268a2b7a6`. The previous final docs ref was local
`14d4ea3ab3cb2071fc6001ce37b60821abd643c2`, published by the GitHub plugin as
fork commit `819e385306cb3c56099662f27dd55d7ddee3b247` (tree
`63a421df9576483967a557605a80692166eccd3f`, parent
`419df3dbe390fe1a10e4b5171ae8661f9d7c52d2`, `force=false`); recursive readback
matched all 34 local blobs. Upstream was untouched.

| Machine | Current observed state | Honest classification |
|---|---|---|
| New MacBook | Runtime `3c732d7`; docs baseline `754af13`, reconciliation committed locally as `021a4e0`; launchd label `com.mattrotundo.ai-chat-archive.new-macbook` is idle after `runs=4`, last exit 0, `StartInterval=21600`. Receipts `20260829T195959.324876Z-be2608f7` (`20:19:56.940667Z`), `20260830T022016.788683Z-c11f0266` (`02:39:32.956025Z`), and `20260830T083957.226400Z-280640fe` (`09:08:58.239553Z`) have gaps 6:19:36.015358 and 6:29:25.283528; all are zero-error `completed_with_absent_harnesses`. The latest has four new Codex objects, all four imported and exact-parent/MIME/size verified in Drive. | **DONE:** repeated six-hour launchd collection proof and eleven-object Drive-plugin publication. **IN-FLIGHT:** automatic runtime Drive publication; no launchd-callable plugin bridge exists. |
| Mac Studio | Runtime `3c732d7`; clean checkout HEAD `5cd62da` (runtime file hashes match the reviewed pin); launchd label `com.mattrotundo.ai-chat-archive.mac-studio` is idle after `runs=2`, last exit 0, `StartInterval=21600`; the long process was launchd-owned (`PID 76865`, `PPID 1`) and ran from `00:02:18` until completion. Receipt `20260830T040218.778731Z-c65bac22` was collected at `07:38:20.693304Z`, generated a zero-error `completed_with_absent_harnesses` result, and reports `publication=blocked_drive_unavailable`: Claude 18/16 new objects/319 redactions, Codex 71/62/881, Hermes 0/0, OpenClaw absent. Exactly 78 finalized objects (16 Claude + 62 Codex) were imported through the Drive plugin; one bounded repair/review recovered the three initially absent Codex objects. | **DONE:** checkout, live-shaped preflight, launchd supervision, second scan, receipt, and 78-object plugin publication. **IN-FLIGHT:** runtime automatic Drive publication and the end-to-end “new chat appears in Drive” proof; the plugin is not a launchd executable. |
| Mac Mini | SSH is reachable, runtime `3c732d7`; no archive label or process is loaded. A read-only SSH census at approximately `2026-08-30T11:05Z` found `6,458,476 KiB` available on `/System/Volumes/Data` (~6.16 GiB, 97% full). Mini logs were not read or touched; the older log-size figures below are historical, not a current census. | **NOT STARTED:** export, canary, schedule, or cleanup. Matt has not approved touching the closed log; no Mini log was changed. |
| Old MacBook | `ssh oldmac` remains unreachable. New's retry label is loaded at `StartInterval=21600`, `runs=3`, exit 0; logs report `offline_retryable`/`ssh_unreachable`. | **DONE:** non-blocking retry queue. **IN-FLIGHT:** queued retry. **NOT STARTED:** online inventory, deployment, or canary. |

The authenticated Drive plugin test write succeeded before publication: body-free
canary ID `1kFQrT2pFI2qSZl2A1Fovv-qyagvod47l` was imported into `AI Chat Archive`
and read back with its exact parent, `text/plain` MIME, and 125-byte size. The
seven earlier New-host objects were imported exactly once, then the 78 finalized
Studio objects, then the four objects from New receipt
`20260830T083957.226400Z-280640fe` were imported exactly once. The folder count
moved from 5 to 12 after the first New wave, then to 90 after Studio, and now to
94 (92 `text/plain` files plus two receipt Docs; 634,718,155 listed bytes). All
78 Studio titles and the four latest New titles are unique, exact-parent,
`text/plain`, and size-verified. The three repaired Codex files were
`2de79d52481e6d99d01cd84a6c1c5f59b2378a74b178450b6a884ecb073f8bd1.json` →
`1LebzFHzi7w7jaLBm0bsnIHp8B7cac6l5`,
`71b6360fca5000ea459b6351da25eab58e09fc809b6fd757eb208daae2adc050.json` →
`1Y7MaXw399RPkHWnXFsbkcX9szHlD0C5R`, and
`e85139b62736f876f5e455d164328c66a38d19b4cb75e725d3ec1ca8f9e5f2a7.json` →
`1ZmLz3ersWITKgsTRLjZXQ-XvRNd_EooO`. Their complete ID/size map is in the dated receipt
[`RECEIPT_FLEET_CHAT_ARCHIVE_2026-08-29.md`](../RECEIPT_FLEET_CHAT_ARCHIVE_2026-08-29.md).
No raw DB/index/state files were uploaded. The plugin is an agent capability,
not a launchd executable; `codex mcp list` has no Google Drive server and no
shell `mcp`, `gdrive`, or `rclone` bridge exists. Therefore the plugin
publication does not by itself prove an automatic launchd-to-Drive event.
At the 2026-08-30T11:05:59Z reconciliation, plugin management still reported
Google Drive enabled/installed, but this Codex session had no callable Drive or
GitHub connector tool. The bounded Drive test-write attempt returned
`TypeError: tools.mcp__codex_apps__google_drive is not a function`; no shell
push or substitute bridge was used. Hold only the additional publication step.

**Done with receipts:** reviewed runtime `3c732d7`, 263/263 tests and bundle
proof, lease/schema/launchd structures, reachable New and Studio supervised
scans, three New six-hour collection cycles, Studio's final receipt, all 78
Studio Drive imports plus the single three-file repair/re-review, the four
latest New Drive imports, GitHub plugin ref readback, and Old retry queue.
**In flight:** runtime automatic Drive publication/end-to-end new-chat proof,
Mini approval, and Old's non-blocking retry. **Not started:** Mini
export/cleanup/canary/schedule, Old online deployment/canary, and OpenClaw host
collection where its source is absent. No separate tracked ranking, graph, or
wiki entry exists.

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

### Historical execution readback — 2026-08-29T21:35:42Z

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
4. **Surface Matt gates first, once, batched, pre-staged to two clicks.** At
   the original resume, the Drive Desktop install/login and Mini log decisions
   were batched. Matt then activated the GitHub/Drive plugins, superseding the
   Desktop gate; record that change and surface only the still-open Mini
   decision once, with its exact action and consequence. Do not ask for a
   superseded gate repeatedly or hide a live gate behind implementation work.
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

## Historical record — pre-2026-09-01 repair

Everything below this marker preserves earlier detailed receipts and path
translations. The current 08:45Z goal, DONE / IN-FLIGHT / NOT STARTED split,
lease, gates, file map, and successor actions are the sections above; when an
older number conflicts with them, use the current snapshot above.

### Historical honest state during active deployment

The current live reconciliation is 2026-08-30T09:40:49Z. The canonical
repository state is [`FLEET_CHAT_ARCHIVE_LIVE_STATE.md`](../FLEET_CHAT_ARCHIVE_LIVE_STATE.md)
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
  `20260830T083957.226400Z-280640fe`, collected at
  `2026-08-30T09:08:58.239553Z`, with zero errors and
  `blocked_no_drive_root`; Claude collected 1 conversation with 0 new objects,
  Codex 4 conversations with 4 new objects, Hermes had none, and OpenClaw is
  absent. Its launchd label is idle at `runs=4`, exit 0. The three receipts are
  separated by 6:19:36.015358 and 6:29:25.283528.
- Studio's launchd-owned second attempt completed with exit 0. Its newest
  body-free receipt is `20260830T040218.778731Z-c65bac22`, collected at
  `2026-08-30T07:38:20.693304+00:00`, with zero errors and
  `publication=blocked_drive_unavailable`; Claude produced 16 new objects and
  Codex 62, with Hermes empty and OpenClaw absent. The process was PID `76865`,
  PPID 1, and no restart or foreground fallback was used.
- Studio's manifest-bound Claude index repair is complete; its interrupted-index
  backup is retained. The Old retry implementation was reviewed and its
  offline behavior was previously proven (run count 1 -> 2, exit 0, zero
  stderr delta, `offline_retryable`/`ssh_unreachable`).
- The owner-only Google Drive DMG is a historical staging artifact. Matt's
  activated Drive plugin is the active route; no Desktop install is required,
  and no local File Provider is mounted on Studio.
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
  `18kklPXiMM2bzF1ZU8tCzlJJ9k-HblbC_` (49,484,530 bytes) in the folder. It
  then imported seven earlier New objects, 78 Studio objects, and four latest
  New objects; the folder is now 94 items (92 `text/plain` files plus two
  receipt Docs; 634,718,155 listed bytes), and the Studio set totals
  418,815,569 bytes. Runtime JSON/raw shard publication remains separate and
  blocked without a launchd-callable Drive bridge or the Studio File Provider.
- New and Studio have their production six-hour labels loaded by launchd with
  `RunAtLoad=true` and `StartInterval=21600`. New's three receipts prove
  repeated elapsed cycles; Studio's second receipt and bounded plugin
  publication are complete. Automatic launchd-to-Drive publication remains
  unproven because `codex mcp list` has no Google Drive server and no shell
  `mcp`, `gdrive`, or `rclone` bridge exists.

### IN-FLIGHT — do not call these complete

- Studio's newest persisted receipt is run
  `20260830T040218.778731Z-c65bac22`, collected at
  `2026-08-30T07:38:20.693304+00:00`, with
  `completed_with_absent_harnesses`, zero errors, and publication
  `blocked_drive_unavailable`. Its 78 finalized objects were subsequently
  imported and metadata-verified in Drive, including one bounded retry/re-review
  for three Codex files.
- A loaded six-hour plist proves persistent scheduling only. New's repeated
  elapsed collection cycles are proven; automatic launchd-to-Drive publication
  remains unproven because the plugin cannot be invoked by launchd.

### NOT STARTED — explicit gates, not implied completion

- Studio still has no proven local Google Drive File Provider mount. The
  connector folder and body-free receipt doc plus 78 Studio objects and 11 New
  objects exist, but automatic new-chat-in-Drive proof does not.
- Matt has not approved Mini log compression/cleanup. Mini's real canary and
  production schedule have not run.
- Old MacBook has not returned online for live deployment or canary proof. Its
  retry label is now enabled and loaded under launchd on New; one RunAtLoad
  attempt exited 0 with `offline_retryable`/`ssh_unreachable`. “Active retry
  queue” is not the same as online deployment/canary proof.
- New and Studio production six-hour archive schedules are loaded under launchd.
  New's three receipts have gaps 6:19:36.015358 and 6:29:25.283528 with
  launchd `runs=2→3→4`, exit 0, proving repeated elapsed collection cycles
  after the controlling session was gone. Studio's second run completed at
  `runs=2`, exit 0, and its 78-object set is published through the plugin; this
  still does not prove automatic plugin invocation.
- OpenClaw host collection is not proven on New or Studio because the source is
  absent there; Mini and Old have no canary proof.
- No separate tracked ranking, graph, or wiki entry exists in this checkout.
  Do not invent one; the live-state file and dated receipt are the durable
  repository record.

### Lease, schema checkpoint, and current plugin route

- The active deployment lease is the repo-root `.deployment-lease.json`. At the
  time of this handoff it names `codex:macbook` session
  `01a046f9-9427-7343-9221-4135b50bc30f` as the sole release owner through
  `2026-08-30T16:49:17Z`. A Studio successor must read the file and stand down
  until the owner explicitly transfers it in a commit.
- The reviewed release's schema-freeze record is
  `docs/SCHEMA_FREEZE_CHECKPOINT_2026-08-29.md`. It records the one census,
  canonical validator hash, and one review wave. Any broad release after
  `3c732d7` is blocked until its own checkpoint is committed.
- The Desktop Drive DMG path is superseded. The authenticated Drive plugin test
  write succeeded (body-free canary ID `1kFQrT2pFI2qSZl2A1Fovv-qyagvod47l`,
  `text/plain`, 125 bytes, exact target parent), followed by exactly seven
  earlier New-host redacted object imports, 78 Studio imports, and four latest
  New imports. The `AI Chat Archive` folder moved from five to twelve items
  after the first New wave, to 90 after Studio, and to 94 after the latest New
  wave; every imported file's parent, MIME, and byte size was read back. This
  plugin publication is a verified staged-output receipt, not evidence that
  launchd can call the plugin.

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
| Historical docs/runtime readback | Local `bb8308ca1d27e5f6959a19300d71076844c3081c`; remote `3e377e92646675db1d70c47036a66dee16ad6ede` | Historical five-doc tree. The current tracked tree has six documentation files, including this handoff and `SCHEMA_FREEZE_CHECKPOINT_2026-08-29.md`. |
| Content reconciliation docs | Local `92bc5fcf35212b96484b98e0a6dd54e28d83fb8f`; owned-fork plugin commit `e25458991eac2ccdd6ef7572e186857a7991679f`; tree `3286505a316937204804235f4374423998cddd3d`, parent `3232cab277c9b72904abc9955d2e8edde8bc5f37`, `force=false` | 2026-08-30 Studio receipt/Drive repair reconciliation content; recursive readback matched 34/34 blobs and upstream was untouched. |
| Ref-pinning docs | Local `42457cc8eba6b8cda7a50f597d6670a28089e47d`; owned-fork plugin commit `5fc48e9f07a15cbdf482a212df75b83268a2b7a6`; tree `a259691e74a7ca2b08efd2e17ca38eca0b6d6f22`, parent `e25458991eac2ccdd6ef7572e186857a7991679f`, `force=false` | Pins the content reconciliation ref; recursive readback matched 34/34 blobs and upstream was untouched. |
| Previous final docs ref | Local `14d4ea3ab3cb2071fc6001ce37b60821abd643c2`; owned-fork plugin commit `819e385306cb3c56099662f27dd55d7ddee3b247`; tree `63a421df9576483967a557605a80692166eccd3f`, parent `419df3dbe390fe1a10e4b5171ae8661f9d7c52d2`, `force=false` | Metadata-only ref receipt for the prior handoff/live-state/README/receipt tree; recursive readback matched 34/34 blobs and upstream was untouched. The current local docs tip is recorded in the snapshot above. |
| Superseded parent | `0e25987370aa32a93423201dc25d85d913d8c8ac` | Exact Hermes provider-metadata validation; retained in history, superseded by `3c732d7`. |

The owned fork branch is now published and read back through the GitHub plugin at
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

The current values are the 2026-08-30 snapshot at the top of this handoff;
the detailed subsections below preserve the earlier receipts and path
translations. When they differ, use the current snapshot and the dated receipt
commit, not the historical numbers.

### New MacBook (`newmac` / local originating host)

- The originating checkout is at docs commit
  `182114dd6cff89018761b72f5387dfc6ad6daa89` (runtime `3c732d7`).
- Production label `com.mattrotundo.ai-chat-archive.new-macbook` is loaded by
  launchd. `launchctl print` shows the collector as the supervised process; no
  terminal owns it. The label is idle at `runs=4`, exit 0.
- The three current receipts are `20260829T195959.324876Z-be2608f7`,
  `20260830T022016.788683Z-c11f0266`, and
  `20260830T083957.226400Z-280640fe`, collected at
  `20:19:56.940667Z`, `02:39:32.956025Z`, and `09:08:58.239553Z`. Their gaps
  are 6:19:36.015358 and 6:29:25.283528; all are zero-error
  `completed_with_absent_harnesses` with `blocked_no_drive_root`. The latest
  collected one Claude conversation with 0 new objects and four Codex
  conversations with 4 new objects; Hermes had no conversations and OpenClaw
  was absent. Seven earlier plus four latest redacted objects were imported
  through the Drive plugin and verified in `AI Chat Archive`.
- Old retry label `com.mattrotundo.ai-chat-archive.old-macbook-deploy-retry` is
  enabled and loaded under launchd with `RunAtLoad=true`, `StartInterval=21600`,
  `runs=1`, and exit 0 after its offline attempt. It is a queued retry, not an
  online deployment proof.

### Mac Studio (`studio` / successor host)

- Read-only SSH finds the shipped runtime at `3c732d7` and the checkout path
  `/Users/calstudio/Projects/ai-data-extraction`.
- Production label `com.mattrotundo.ai-chat-archive.mac-studio` is loaded by
  launchd with `RunAtLoad=true`, `StartInterval=21600`, `runs=2`,
  `state=not running`, and last exit code 0. The completed long scan was
  launchd-owned as PID `76865` (PPID 1); no terminal owned it and no foreground
  fallback was used.
- The newest persisted receipt is run ID
  `20260830T040218.778731Z-c65bac22`, collected at
  `2026-08-30T07:38:20.693304+00:00`; status/collection_status
  `completed_with_absent_harnesses`, errors empty, and publication
  `blocked_drive_unavailable`. Claude collected 18 conversations/16 new
  objects, Codex 71/62, Hermes had no conversations, and OpenClaw is absent.
  All 78 finalized objects were imported and metadata-verified through the
  Drive plugin, including one bounded retry/re-review for three Codex files.
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
- The Drive plugin profile is authenticated. It created and verified private
  folder `AI Chat Archive` ID `1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV`, and the test
  write `1kFQrT2pFI2qSZl2A1Fovv-qyagvod47l` was read back as `text/plain`, 125
  bytes, with the exact target parent. It then imported exactly seven earlier
  New-host redacted objects, 78 Studio objects, and four latest New-host
  objects; the folder count moved from 5 to 12 after the first New wave, to 90
  after Studio, and to 94 after the latest New wave. Every imported file's
  parent, MIME, and byte size was verified; the 78-object Studio set totals
  418,815,569 bytes and the folder lists 634,718,155 bytes total.
  The Desktop DMG path is superseded; no local File Provider is required for
  this plugin route.
- Not started: an end-to-end automatic launchd-to-Drive event. The runtime still
  reports `blocked_drive_unavailable`; `codex mcp list` has no Google Drive
  server and no shell `mcp`, `gdrive`, or `rclone` bridge exists. Plugin imports
  are separately verified staged output, not a launchd capability.

## 6. Matt gates — surface these first

The original two-gate message was surfaced first and is retained in the
historical readbacks. The facts changed: the Desktop Google Drive DMG install
gate is superseded by Matt's activated Codex GitHub/Drive plugins, and the Drive
plugin test write plus seven earlier New-object, 78 Studio-object, and four
latest New-object publications are verified. Do not ask Matt to install the DMG
or wait on it.

The one open Matt decision is:

1. **Mini log-compression approval:** approve only the closed, zero-handle
   `CoreSimulator.prev.log` (15,403,577,516 bytes) for compression/archive and
   removal if the reviewed operation requires it. Do not touch the active
   `CoreSimulator.log` (about 5.51 GB; one open handle observed). Consequence:
   enough free space to run the Mini canary; until the one-word approval arrives,
   Mini export, cleanup, canary, and schedule stay paused.

Current plugin publication has no additional human gate: use only the exact
object paths named by a completed, zero-error receipt, and read back each Drive
file's target parent, MIME, and byte size. This is a staged plugin action, not an
automatic launchd-to-plugin capability; `codex mcp list` has no Google Drive
server and no shell `mcp`, `gdrive`, or `rclone` bridge exists.

## 7. First three successor actions

The current owner has already started these bounded actions. A Studio successor
must first read the lease and continue only after an owner-committed transfer.

1. **Read the lease and surface the one open gate.** Confirm
   `.deployment-lease.json` still names `codex:macbook` as sole release owner;
   surface the already-batched Mini closed-log approval once. The Desktop Drive
   install/login gate is superseded by the activated plugin route, so do not
   repeat that request or install the DMG.
2. **Re-read the completed Studio receipt and Drive listing.** Confirm
   `20260830T040218.778731Z-c65bac22`, launchd `runs=2`, exit 0, the 78
   exact-parent `text/plain` objects, and the current 94-item folder listing.
   Do not retry the three repaired Codex files or open a second bulk wave; a
   future long canary must be a new launchd-owned transaction.
3. **Reconfirm the completed New proof and retry queue.** Verify the three New
   receipts remain 6:19:36.015358 and 6:29:25.283528 apart (`runs=2→3→4`, exit
   0), the seven earlier plus four latest Drive-plugin objects remain in `AI
   Chat Archive`, and Old's launchd retry remains enabled and non-blocking. Keep
   Mini untouched until approval; batch any new findings into one review wave,
   one repair, and one re-review.

## 8. Proving the automatic six-hour cycle on the shipped build

The proof target is not “launchd is installed.” The proof target is: a new
allowed chat is collected, merged, and appears in the Studio Drive folder after
the six-hour automation path, while the controlling session is gone.

Current execution evidence: New label
`com.mattrotundo.ai-chat-archive.new-macbook` and Studio label
`com.mattrotundo.ai-chat-archive.mac-studio` are both loaded by launchd with
`RunAtLoad=true` and `StartInterval=21600`. New's receipts at
`20:19:56.940667Z`, `02:39:32.956025Z`, and `09:08:58.239553Z` are separated by
6:19:36.015358 and 6:29:25.283528; launchd advanced `runs=2→3→4`, each exit 0,
and all three have zero errors. Seven earlier plus four latest redacted
objects are verified in Drive through the plugin. This proves repeated elapsed
six-hour collection plus staged plugin publication.
Studio's second launchd run completed as receipt
`20260830T040218.778731Z-c65bac22` with zero errors and 78 finalized objects;
the plugin import and the one three-file repair/re-review are complete. A plugin
import is not a launchd capability, so end-to-end automatic launchd-to-Drive
remains unproven.

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

For the optional local File Provider route, once exactly one Drive provider is
mounted and the schedule is authorized:

1. Record a body-free baseline: current host index/object count, Studio
   `publish-manifest.json` digest, receipt count, and Drive folder listing.
2. Create one clearly identifiable test chat in an approved harness whose source
   is present. Keep its message text out of Git and receipts; retain only the
   resulting object digest/session hash in the private evidence note.
3. Let the next supervised six-hour cycle run. On the receipt, require the
   source to be `collected` or a valid cached `pulled` import, zero errors, and
   publication status `published`. For the active plugin route, instead use a
   completed receipt's explicit object set and perform one bounded plugin import
   wave; do not claim it was automatic.
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
   survival proven. Report success to Matt only when he can see a new chat in
   Drive through the claimed route; otherwise report the exact blocked status
   and keep the plugin publication and automatic-cycle claims separate.

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
