# Fleet chat archive live state

**Last reconciled:** 2026-08-30T08:14:18Z
**State:** active deployment; New's launchd schedule has completed an observed six-hour cycle (`runs=2→3`, exit 0, 6:19:36 between receipts) and seven newly staged redacted objects were published through the authenticated Drive plugin. Studio's second launchd-owned scan completed under PID 76865/PPID 1 and emitted zero-error receipt `20260830T040218.778731Z-c65bac22`; all 78 finalized Studio objects (16 Claude + 62 Codex) were imported and metadata-verified, bringing the folder to 90 items and 418,815,569 Studio bytes. Runtime automatic launchd-to-Drive publication remains unproven because the plugin is not a launchd executable and Studio has no File Provider. Mini remains paused pending Matt's closed-log approval and Old remains on its non-blocking retry queue. No new release or schema/review wave was opened.

This file is the repository's canonical rollout state. It records body-free
counts, hashes, paths, and lifecycle observations only. Raw conversation
bodies, indexes, and other private archive data remain outside Git.

## Release identity

| Item | Evidence |
|---|---|
| Reviewed release | `3c732d7b1031949bd18db90ae4ac40f667f6cfa7` (`Rollback cleanly on terminal hangup`) |
| Deployment branch | `matt/fleet-chat-archive-deployed` |
| Repository checkout | MacBook docs checkout at `182114d`; Studio runtime checkout at `3c732d7`; Mini clean at runtime `3c732d7` |
| Verification suite | `python3.14 -m unittest discover -s tests -p 'test*.py'` -> **263/263, OK** |
| GitHub publication | This final docs ref is local `42457cc8eba6b8cda7a50f597d6670a28089e47d`, published by the GitHub plugin as owned-fork commit `5fc48e9f07a15cbdf482a212df75b83268a2b7a6`, tree `a259691e74a7ca2b08efd2e17ca38eca0b6d6f22`, parent `e25458991eac2ccdd6ef7572e186857a7991679f`, `force=false`; recursive readback matched all 34 local blobs. |
| Drive connector publication | Folder `AI Chat Archive` ID `1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV`; New moved the folder 5→12, then Studio moved it 12→90. All 78 Studio object titles are unique, exact-parent, `text/plain`, and total 418,815,569 bytes; the three repaired Codex IDs and Drive IDs are in the dated receipt. |
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
| New MacBook | Runtime `3c732d7`; `com.mattrotundo.ai-chat-archive.new-macbook` is idle at `runs=3`, exit 0, `StartInterval=21600`. The prior receipt `20260829T195959.324876Z-be2608f7` was collected at `20:19:56.940667Z`; the next `20260830T022016.788683Z-c11f0266` at `02:39:32.956025Z` reports `completed_with_absent_harnesses`, zero errors, `blocked_no_drive_root`, Claude 1, Codex 6, Hermes none, OpenClaw absent. Seven new redacted objects from that receipt were imported once through the Drive plugin and verified in the target folder. | **DONE:** checkout, preflight, persistent schedule, six-hour elapsed collection proof, and seven-object plugin publication. **IN-FLIGHT:** automatic runtime Drive publication (the local provider is absent; plugin publication is separately verified). |
| Mac Studio | Runtime `3c732d7`; `com.mattrotundo.ai-chat-archive.mac-studio` is idle after `runs=2`, last exit 0, `StartInterval=21600`, and durable owner-only logs. The long process was launchd-owned (`PID 76865`, `PPID 1`) from `00:02:18` until completion. Receipt `20260830T040218.778731Z-c65bac22` collected at `2026-08-30T07:38:20.693304+00:00` reports `completed_with_absent_harnesses`, errors `[]`, and runtime `publication=blocked_drive_unavailable`; Claude 18/16 new objects/319 redactions, Codex 71/62/881, Hermes 0/0, OpenClaw absent. Exactly 78 finalized objects were imported through the Drive plugin, with one bounded three-file repair/re-review. | **DONE:** checkout, live-shaped preflight, persistent schedule, supervised scan, receipt, and 78-object plugin publication. **IN-FLIGHT:** runtime automatic Drive publication and the end-to-end “new chat appears in Drive” proof. |
| Mac mini | Clean at runtime `3c732d7`; no archive label is loaded. The latest read-only census found approximately 7.29 GB free, active `CoreSimulator.log` about 5.51 GB, and closed `CoreSimulator.prev.log` 15,403,577,516 bytes. | **DONE:** read-only disk census. **NOT STARTED:** approved cleanup, canary, schedule; Matt's approval is still required and no log was touched. |
| Old MacBook | `ssh oldmac` remains unreachable. New's retry label `com.mattrotundo.ai-chat-archive.old-macbook-deploy-retry` is enabled/loaded under launchd with `RunAtLoad=true`, `StartInterval=21600`, `runs=2`, and exit 0 after `offline_retryable`/`ssh_unreachable`. | **DONE:** retry behavior proof and active launchd queue. **IN-FLIGHT:** non-blocking offline retry. **NOT STARTED:** online deployment/canary. |

## DONE

- The predecessor checkout was audited and the Claude Code, Codex, OpenClaw,
  and Hermes adapters, content-addressed deduplication, credential redaction,
  provenance manifests, trusted remote stream, and terminal-hangup rollback
  were integrated at `3c732d7`.
- The release passed 263/263 tests, compile/diff checks, focused SIGHUP and
  rollback checks, and a fresh bundle-clone verification before deployment.
- The runtime release is deployed to the reachable New MacBook, Mac Studio, and
  Mac mini. The docs/addendum commit is published to the owned GitHub fork and
  pulled to Studio; the Studio and New worktrees are clean at that docs commit.
- Current body-free, zero-error supervised receipts were produced on New and
  Studio. New's two receipts are separated by 6:19:36 with launchd `runs=2→3`,
  proving one elapsed six-hour collection cycle after the controlling session
  was gone. Studio's second launchd attempt completed with 78 finalized objects;
  all were imported and metadata-verified through the Drive plugin.
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
  files once (including a single three-file repair/re-review), moved them into
  `AI Chat Archive`, and metadata-read back each exact parent, MIME, and size.
  The folder is now 90 items; the complete repair ID/size map is in the dated
  receipt. Runtime File Provider publication remains a separate status.
- The New six-hour follow-up receipt collected six Codex conversations and one
  Claude conversation with zero parse failures; its seven newly emitted objects
  were published through the Drive plugin. This is verified plugin publication
  of staged output, not proof that a local launchd process can invoke a plugin.

## IN-FLIGHT

- New's launchd label is idle after `runs=3`, exit 0, and its two receipts are
  6:19:36 apart; the elapsed six-hour collection proof is **DONE**. Studio's
  label is idle after `runs=2`, exit 0; its second receipt and 78-object Drive
  publication are **DONE**.
- Runtime automatic Drive publication and the success criterion “Matt watches a
  new chat appear in Drive” remain **IN-FLIGHT**: no local File Provider is
  present and a plugin call is not a launchd capability.

## NOT STARTED

- Studio File Provider installation/login/mount has not been proven. The Drive
  plugin folder contains the two receipt Docs, two prior text artifacts, seven
  New-host objects, and 78 Studio objects; no runtime launchd-to-Drive event has
  been proven.
- The Mini storage gate has not been approved or acted on, so its real canary
  and production schedule remain unstarted.
- Old MacBook has not returned online for live deployment or canary proof.
- New's approximately 21,600-second elapsed-cycle receipt exists and is
  documented above. Studio's second run and bounded post-receipt publication
  are complete; automatic plugin invocation remains unproven.
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
