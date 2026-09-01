# Fleet chat archive live state

**Last reconciled:** 2026-09-01T11:31Z
**Current truth (supersedes the older one-line state below):** shipped tree
`5e33dd15…` is local commit `88d65a7…` and GitHub-plugin commit
`68c02d1…`; New's terminal receipt is clean under launchd; the Drive publisher
completed one supervised 24-object batch with zero errors and the active plugin
canary write/readback/cleanup succeeded; Studio is on the shipped tree but its
canary was stopped safely at disk pressure and must not be retried until the
existing quarantine is handled; Mini's requested source is absent and no bytes
changed; Old remains on its retry queue. The next natural New interval is
preserved for the automatic new-chat proof.
**Historical state (pre-repair):** the earlier deployment tree was `8914a6e275cf898d84a3ec4ff7f26c6bce149b13` (local `e167feb0f5997458c2411195a4380b7d316ce72b`, fork `a3a8ad5e9da7eb0aa44cb03b5c2440f3d3b7530f`). Its New collector was PID 30001/PPID 1 (`runs=9`), and Studio was booted out while that transfer drained. The earlier Drive publisher receipt recorded 8 uploads, 14 verified skips, and 2 connector failures. The Mini filename-only check found no source, so no gzip or deletion occurred. This line is retained only as a historical receipt; the current truth is the 11:31Z block below.

This file is the repository's canonical rollout state. It records body-free
counts, hashes, paths, and lifecycle observations only. Raw conversation
bodies, indexes, and other private archive data remain outside Git.

## Latest live reconciliation — 2026-09-01T11:31Z

This body-free checkpoint supersedes the 11:14Z block below. Its parent
GitHub-plugin documentation ref `f23912ffaed783ee5be84b441980f9dc68d818cf` has tree
`2f68e6c0dcc2193bec6d0ab3128ab0d9c1b24c35`. The Drive plugin canary wrote
`ai-chat-archive-plugin-canary-20260901.txt`, read back exact metadata (ID,
name, `text/plain`, `18,167` bytes, target parent), and deleted only that
canary. New launchd baselines are collector `runs=11` and publisher `runs=3`;
both labels remain loaded at `StartInterval=21600`, with no manual kick.

| Check | Body-free evidence |
|---|---|
| New natural-cycle baseline | At `2026-09-01T11:31:12Z`, the collector and publisher were idle with last exit `0`. The Drive-canary child created ephemeral Codex thread `01a05cb9-bfca-7252-a5c1-cc0e514b5d90`; its object/Drive correlation is intentionally pending the next natural cycle. |
| Studio disk gate | At `2026-09-01T11:26:37Z`, free space was `973,732 KiB`; `.work` was `0 KiB`; quarantine was `73,136,828 KiB` across `22,501` files. The launchd label is idle after `runs=1`, exit `143`; no retry is safe. |
| Mini | At `2026-09-01T11:24:16Z`, the exact requested source and `.gz` were absent. Free space was `29,893,336 KiB` = `30,610,776,064` bytes; operation delta and net freed bytes remain `0`. |
| Old | SSH remains unreachable; the launchd retry queue is loaded and nonblocking. |

The 11:14Z table below is retained as historical evidence.

## Historical live reconciliation — 2026-09-01T11:14Z

| Check | Body-free evidence |
|---|---|
| Release identity | Local `88d65a7677dbdb483b36a85ff59b907231727502` / tree `5e33dd15aeb26ecbe059ac6cf83338442a21c493`; GitHub-plugin ref `68c02d1953b428b2ecf6443e26a56560bb81f436` has the same tree and was updated with `force=false`. The active lease still names `codex:macbook` on `Mac.lan`. |
| Verification | Focused publisher tests `9/9` and full suite `272` tests, OK, after the one lock-wait repair; `git diff --check` passed. |
| New MacBook | `com.mattrotundo.ai-chat-archive.new-macbook` is idle after `runs=11`, last exit `0`, `StartInterval=21600`. Latest receipt `20260901T064318.917191Z-a3e5881a` collected at `2026-09-01T10:22:36.435118Z` is `completed_with_absent_harnesses`, errors `[]`: Claude 31/1 new object, Codex 1,224/17, Hermes 4/2, OpenClaw absent/inventory-only; all present-harness quality statuses are complete. |
| Mac Studio | Checkout is clean at `68c02d1953b428b2ecf6443e26a56560bb81f436` / tree `5e33dd15…`. Its single shipped-tree canary ran under launchd as PID `54195`, PPID `1`, then was terminated via launchd `SIGTERM` when free space fell below 2 GiB; receipt `20260901T104451.299032Z-40cc7d40` is honestly `failed`/`RunFailure`, launchd last exit `143`, and the lock/staging directory are clean. Existing quarantine consumes `74,892,111,872` bytes (`73,136,828 KiB`) across `22,501` files; no quarantine data was deleted or compressed. |
| Drive plugin publisher | Launchd `com.mattrotundo.ai-chat-archive.drive-publisher` completed `runs=3`, last exit `0`, `StartInterval=21600`. Receipt `20260901T103707.805024Z-c2f89e5e.json` reports `24` candidates, `24` uploaded, `0` skipped, `0` failed, and `errors=[]` to folder `1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV`. Connector readback for file `10C4tI7CAeFTn_rYSFICo_9lpdOQ86a0U` verified its exact name, `text/plain`, size `3475`, and parent folder. |
| Mac Mini | On `Cals-Mac-mini.local`, `/Users/calrotundo/Library/Logs/CoreSimulator/CoreSimulator.prev.log` and `.gz` are absent. Free space was `30,606,172,160` bytes both before and after the guarded check; source/archive/deletion/net-freed bytes are `0`. No gzip, `gzip -t`, spot-decompress, or deletion ran. |
| Old MacBook | `ssh oldmac` still times out. `com.mattrotundo.ai-chat-archive.old-macbook-deploy-retry` is loaded under launchd with `runs=9`, last exit `0`, `StartInterval=21600`, and repeated `offline_retryable`/`ssh_unreachable` receipts. |

The Studio disk condition is an operational gate, not permission to delete
unrelated quarantine data. Keep the job idle until that data is explicitly
handled; do not create a second canary. The structural controls remain binding:
every long canary is launchd-only; `.deployment-lease.json` names one release
owner; schema freeze is one live census, one canonical validator, and one
batched review wave; and findings receive one repair and one re-review.

### Mini-only non-destructive refresh — 2026-09-01T11:19:05Z

The exact requested source and `.gz` sibling remain absent on
`Cals-Mac-mini.local`. A fresh read-only `df` sample reported
`29,887,244 KiB` = `30,604,537,856` free bytes; no write, gzip test, spot
decompress, or deletion occurred, so operation delta and net freed bytes remain
`0`.

## Historical reconciliation — 2026-09-01T08:45Z

| Check | Body-free evidence |
|---|---|
| Release identity | Local commit `4b665987ad9c06c618a73fc67e7b6004d1bd1881`, tree `300ce290c9d75e4187a240770db7f9793c57d577`; GitHub-plugin commit `ec96761786196f58b8157de8dc917c85947a09b8` is a non-force fast-forward from `bb9a08570baffc2111e832ac7418bab9d33755af` and has the same tree. Lease owner remains `codex:macbook` on `Mac.lan`. |
| New MacBook | Launchd label active as PID 84541/PPID 1, `runs=11`, `StartInterval=21600`; receipt count 170 and the post-repair full Codex revalidation has not emitted its terminal receipt. |
| Mac Studio | Reachable checkout is idle at `bb9a08570baffc2111e832ac7418bab9d33755af` (tree `8f4f0122…`), launchd `runs=1`, last exit 0, `StartInterval=21600`; fast-forward/reinstall waits for New's receipt. |
| Drive plugin | Target folder `AI Chat Archive` ID `1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV`; prior publisher receipt `20260901T025751.237727Z-74cdf5d2.json` remains partial (8 uploads, 14 verified skips, 2 connector failures). |
| Mac Mini | Exact closed-log and `.gz` paths are absent. A single pre/post check measured 29,895,640 KiB = 30,613,135,360 bytes free both times; source/archive/deletion/freed bytes all 0. No gzip, `gzip -t`, spot-decompress, or deletion ran. |
| Old MacBook | SSH remains unreachable; the loaded 21,600-second retry queue is unchanged and non-blocking. |

Controls remain structural: long canaries are launchd-only; the repo lease names
one release owner; schema freeze is one live census, one canonical validator,
and one batched review wave; and findings get one repair and one re-review.

### Mini-only refresh — 2026-09-01T09:16:53Z

The latest read-only Mini check again found no
`/Users/calrotundo/Library/Logs/CoreSimulator/CoreSimulator.prev.log` or `.gz`
file. No gzip, archive test, spot-decompress, or deletion ran. The two `df`
samples were exactly `30,620,397,568` bytes free (`29,902,732 KiB`) before and
after the guarded no-op; operation delta and net freed space are `0` bytes.

## Historical reconciliation — 2026-09-01T04:21Z

| Check | Body-free evidence |
|---|---|
| Release identity | Local commit `e167feb0f5997458c2411195a4380b7d316ce72b`; fork ref `a3a8ad5e9da7eb0aa44cb03b5c2440f3d3b7530f`; both resolve to tree `8914a6e275cf898d84a3ec4ff7f26c6bce149b13`. Active lease remains `codex:macbook` on `Mac.lan`; no transfer occurred. |
| New MacBook | Launchd label active as PID 30001/PPID 1, `runs=9`, `StartInterval=21600`. Current Studio incoming shard is 1,160 files / 2,166,120 KiB; receipt not yet emitted. |
| Mac Studio | Launchd collector is intentionally absent after bootout; latest stop receipt is `20260901T024039.360623Z-bc4432a1.json` (`RunFailure`, publication not attempted). Checkout is clean at `9251ff6`; fetched corrected ref is `a3a8ad5e…`; reinstall follows New completion. |
| Drive plugin | Folder `AI Chat Archive` ID `1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV`. Publisher receipt `20260901T025751.237727Z-74cdf5d2.json`: 8 uploaded, 14 skipped after metadata verification, 2 `connector_error`; no bodies were sent in the prompt. |
| Mac Mini | No `CoreSimulator.prev.log*` exists under `/Users/calrotundo`; no gzip ran. Available space was 30,289,448 KiB (31,016,394,752 bytes). Source/archive/deletion/freed bytes are all 0. The sole deletion gate remains untouched. |
| Old MacBook | SSH unreachable; launchd retry queue remains loaded at 21,600 seconds with retryable offline receipts. |

The controls are enforced as structure: long canaries are launchd-only; the
repo-root deployment lease names the sole release owner; schema freeze requires
one live census, one canonical validator, and one review wave before broad
release; and review findings are batched into one repair and one re-review.

## Release identity

| Item | Evidence |
|---|---|
| Reviewed release | `3c732d7b1031949bd18db90ae4ac40f667f6cfa7` (`Rollback cleanly on terminal hangup`) |
| Deployment branch | `matt/fleet-chat-archive-deployed` |
| Repository checkout | New MacBook docs baseline was `754af13`; reconciliation commit is `021a4e0`; Studio checkout at `5cd62da` (runtime file hashes match reviewed `3c732d7`); Mini clean at runtime `3c732d7` |
| Verification suite | `python3.14 -m unittest discover -s tests -p 'test*.py'` -> **263/263, OK** |
| GitHub publication | The latest fork branch ref read back with `git ls-remote` is `2f466bd9d6041b8494eb23377b737d9f49c867d8`; the docs baseline before this reconciliation was `754af13bbcf62b3c2a1d87c4801f1773ceed6002`, now committed locally as `021a4e0b1461e7bb542ea918c043e8cea9770fda`. This session has no callable GitHub connector tool, so the new commit is not pushed/read back; prior connector receipts remain the publication evidence. |
| Drive connector publication | Folder `AI Chat Archive` ID `1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV`; New moved the folder 5→12, Studio moved it 12→90, and the latest New wave moved it 90→94. All 78 Studio and four latest New object titles are unique, exact-parent, `text/plain`, and size-verified; the repaired and latest Codex ID/size maps are in the dated receipt. |
| Fresh bundle proof | `/tmp/ai-data-extraction-3c732d7.prSOXz/ai-data-extraction-3c732d7.bundle`; SHA-256 `4531929ef667087d755dbdffb78054d3a64ec471885e4def7292f34023dcb295` |

The deployed runtime pins are:

| File | SHA-256 |
|---|---|
| `fleet_chat_archive.py` | `625e98c4f33aba2e57864f49c70992924491c78e3561dd2bf1360687da039a0b` |
| `archive_object_contract.py` | `f31e840f49fcc9f35dc8223d1d0da3a479ae6da2de7fc62fac73ef6e8521825a` |
| `extract_claude_code.py` | `cc09aa37295d98572fdffcf6d8ef465d340e9154c1722f85871991aa9af8512e` |
| `extract_codex.py` | `6b7132413ad3dc3042ca4644d9ae30062f2a972da48809cdca376c0f35f377e6` |
| `extract_openclaw.py` | `6049a3832abcddb380b9a9845e4cd1ef264467358be8ad8ce000a11da3e1b84b` |
| `extract_hermes.py` | `c45fb372457d02e1d0df510e96dc9b0592da41b7cea9828ea7636f9000e173cb` |

## Host matrix during active deployment

| Host | Observed truth | Classification |
|---|---|---|
| New MacBook | Runtime `3c732d7`; docs baseline `754af13`, reconciliation committed locally as `021a4e0`; `com.mattrotundo.ai-chat-archive.new-macbook` is idle after `runs=4`, last exit 0, `StartInterval=21600`. Receipts `20260829T195959.324876Z-be2608f7`, `20260830T022016.788683Z-c11f0266`, and `20260830T083957.226400Z-280640fe` were collected at `20:19:56.940667Z`, `02:39:32.956025Z`, and `09:08:58.239553Z`, with gaps 6:19:36.015358 and 6:29:25.283528. The latest reports `completed_with_absent_harnesses`, zero errors, `blocked_no_drive_root`, Claude 1 conversation/0 new objects, Codex 4 conversations/4 new objects, Hermes none, OpenClaw absent. Four latest redacted objects were imported once through the Drive plugin and verified in the target folder. | **DONE:** checkout, preflight, persistent schedule, repeated six-hour elapsed collection proof, and eleven-object plugin publication. **IN-FLIGHT:** automatic runtime Drive publication (the local provider and a launchd-callable plugin bridge are absent; plugin publication is separately verified). |
| Mac Studio | Runtime `3c732d7`; clean checkout HEAD `5cd62da` (runtime file hashes match the reviewed pin); `com.mattrotundo.ai-chat-archive.mac-studio` is idle after `runs=2`, last exit 0, `StartInterval=21600`, and durable owner-only logs. The long process was launchd-owned (`PID 76865`, `PPID 1`) from `00:02:18` until completion. Receipt `20260830T040218.778731Z-c65bac22` collected at `2026-08-30T07:38:20.693304+00:00` reports `completed_with_absent_harnesses`, errors `[]`, and runtime `publication=blocked_drive_unavailable`; Claude 18/16 new objects/319 redactions, Codex 71/62/881, Hermes 0/0, OpenClaw absent. Exactly 78 finalized objects were imported through the Drive plugin, with one bounded three-file repair/re-review. | **DONE:** checkout, live-shaped preflight, persistent schedule, supervised scan, receipt, and 78-object plugin publication. **IN-FLIGHT:** runtime automatic Drive publication and the end-to-end “new chat appears in Drive” proof. |
| Mac mini | Clean at runtime `3c732d7`; no archive label is loaded. A read-only SSH census at approximately `2026-08-30T11:05Z` found `6,458,476 KiB` available on `/System/Volumes/Data` (~6.16 GiB, 97% full). Mini logs were not read or touched; the older log-size figures in historical sections are not a current census. | **DONE:** read-only disk census. **NOT STARTED:** approved cleanup, canary, schedule; Matt's approval is still required and no log was touched. |
| Old MacBook | `ssh oldmac` remains unreachable. New's retry label `com.mattrotundo.ai-chat-archive.old-macbook-deploy-retry` is enabled/loaded under launchd with `RunAtLoad=true`, `StartInterval=21600`, `runs=3`, and exit 0 after `offline_retryable`/`ssh_unreachable`. | **DONE:** retry behavior proof and active launchd queue. **IN-FLIGHT:** non-blocking offline retry. **NOT STARTED:** online deployment/canary. |

## DONE

- The predecessor checkout was audited and the Claude Code, Codex, OpenClaw,
  and Hermes adapters, content-addressed deduplication, credential redaction,
  provenance manifests, trusted remote stream, and terminal-hangup rollback
  were integrated at `3c732d7`.
- The release passed 263/263 tests, compile/diff checks, focused SIGHUP and
  rollback checks, and a fresh bundle-clone verification before deployment.
- The runtime release is deployed to the reachable New MacBook, Mac Studio, and
  Mac mini. New's docs baseline `754af13` plus the local reconciliation commit
  `021a4e0` is clean; Studio's checkout is clean at `5cd62da` while its deployed
  runtime file hashes match `3c732d7`.
  The latest docs were not pulled to Studio during this readback.
- Current body-free, zero-error supervised receipts were produced on New and
  Studio. New's three receipts are separated by 6:19:36.015358 and
  6:29:25.283528 with launchd `runs=2→3→4`, proving repeated elapsed six-hour
  collection cycles after the controlling session was gone. Studio's second
  launchd attempt completed with 78 finalized objects; all were imported and
  metadata-verified through the Drive plugin.
- The Studio Claude index repair was completed with a manifest-bound restore;
  the interrupted index backup remains at
  `/Users/calstudio/.local/share/ai-chat-archive-repair-proof.BCwlg8/live-current-index.backup.json`.
- The Old Mac retry implementation was reviewed and its offline behavior was
  proven without blocking the reachable-host rollout. Its retry plist is now
  enabled/loaded under launchd; the first queued attempt exited 0 with the
  retryable offline status.
- The dated Studio Google Drive DMG remains a historical staging artifact only;
  the active route is the authenticated Google Drive plugin and no Desktop
  install is required for this turn.
- The GitHub plugin previously read back the owned-fork ref at
  `21f8bca5e92f38033cc9e553df796f9a17c76e6c`. The Drive plugin imported exactly
  seven New-host redacted object files once, then all 78 Studio redacted object
  files once (including a single three-file repair/re-review), and the four
  objects from New receipt `20260830T083957.226400Z-280640fe` once. All were
  moved into `AI Chat Archive` and metadata-read back with exact parent, MIME,
  and size. The folder is now 94 items (92 `text/plain` files plus two receipt
  Docs; 634,718,155 listed bytes); the complete New and Studio ID/size maps are
  in the dated receipt. Runtime File Provider publication remains a separate
  status.
- The New follow-up receipts collected one Claude conversation and six Codex
  conversations, then one Claude conversation and four Codex conversations, all
  with zero parse failures; the latest four newly emitted objects were published
  through the Drive plugin. This is verified plugin publication of staged
  output, not proof that a local launchd process can invoke a plugin.
- Plugin management currently reports Google Drive enabled/installed, but this
  Codex session has no callable Drive or GitHub connector tool. The attempted
  body-free Drive test call returned
  `TypeError: tools.mcp__codex_apps__google_drive is not a function`; the prior
  Drive/GitHub receipts remain the durable publication evidence.

## IN-FLIGHT

- New's launchd label is idle after `runs=4`, last exit 0; its three completed
  receipts have gaps 6:19:36.015358 and 6:29:25.283528, so repeated elapsed
  six-hour collection proof is **DONE**. Studio's
  label is idle after `runs=2`, exit 0; its second receipt and 78-object Drive
  publication are **DONE**.
- Runtime automatic Drive publication and the success criterion “Matt watches a
  new chat appear in Drive” remain **IN-FLIGHT**: no local File Provider is
  present and a plugin call is not a launchd capability. This reconciliation's
  GitHub push and any additional Drive write are held solely until the callable
  connector tools appear; no shell push or substitute bridge was used.

## NOT STARTED

- Studio File Provider installation/login/mount has not been proven. The Drive
  plugin folder contains the two receipt Docs, two prior text artifacts, seven
  earlier New-host objects, four latest New-host objects, and 78 Studio objects;
  no runtime launchd-to-Drive event has been proven.
- The Mini storage gate has not been approved or acted on, so its real canary
  and production schedule remain unstarted.
- Old MacBook has not returned online for live deployment or canary proof.
- New's repeated approximately 21,600-second elapsed-cycle receipts and
  bounded post-receipt publication are documented above. Studio's second run
  and bounded post-receipt publication are complete; automatic plugin
  invocation remains unproven because `codex mcp list` has no Google Drive
  server and no shell `mcp`, `gdrive`, or `rclone` bridge exists.
- OpenClaw host collection has not been proven on New or Studio because the
  source is absent there; Mini and Old have no canary proof.
- This checkout contains no separate tracked ranking, graph, or wiki entry for
  this rollout. No synthetic ranking or graph/wiki record was created; this
  file and the dated receipt below are the durable repository record.

## Receipt and provenance pointers

- New raw body-free receipt: `/Users/mattrotundo/.local/share/ai-chat-archive-canary-final-3c.ABxGZG/stdout.jsonl`.
- Studio raw body-free receipt:
  `/Users/calstudio/.local/share/ai-chat-archive-canary-final-3c.4zhNaQ/stdout.jsonl`.
- Dated Drive installer: `/Users/calstudio/Downloads/GoogleDrive-2026-08-28.dmg`.
- Release bundle: `/tmp/ai-data-extraction-3c732d7.prSOXz/ai-data-extraction-3c732d7.bundle`.
- Structured evidence and read-only observation details:
  [`RECEIPT_FLEET_CHAT_ARCHIVE_2026-08-29.md`](RECEIPT_FLEET_CHAT_ARCHIVE_2026-08-29.md).
- Connector Drive receipts: folder ID `1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV`,
  docs `1ovOGhi7EdwUbbBUbPliQS4DYQ-A7N8ny77xt5u5wElM` and
  `1Q4FFT1aglyjwRx3olRlmlvtR1MFKbVcCs4R96xLX_r4`.

## Historical execution readback — 2026-08-29T22:03:47Z

- New's launchd label remains idle at `runs=2`, exit 0; its latest receipt is
  unchanged (`20260829T195959.324876Z-be2608f7`, zero errors,
  `blocked_no_drive_root`).
- Studio's launchd label completed its first attempt: the 22:03:47Z readback
  shows `state=not running`, `runs=1`, and exit 0. Its newest body-free receipt
  is `20260829T184201.313238Z-c87faa38`, with zero errors and
  `publication=blocked_drive_unavailable`; no restart, kill, foreground
  fallback, or second long canary was used.
- The Drive connector read back the exact four-item folder and found no new
  approved text artifact; no connector write was performed. Runtime
  publication is still not proven because Studio has zero File Provider roots.
- This readback changes no release, lease, schema checkpoint, or review-wave
  state.

All paths above point to owner-only or temporary artifacts. They are references,
not instructions to copy raw conversation data into Git.
