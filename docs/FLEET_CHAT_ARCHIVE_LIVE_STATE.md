# Fleet chat archive live state

**Last reconciled:** 2026-08-30T05:36:21Z
**State:** active deployment; New's launchd schedule has now completed an observed six-hour cycle (`runs=2→3`, exit 0, 6:19:36 between receipts) and seven newly staged redacted objects were published through the authenticated Drive plugin. The Drive folder now has 12 verified items. Studio's second launchd-owned scan is still running (`runs=2`, PID 76865, PPID 1); its prior receipt is zero-error but runtime publication remains `blocked_drive_unavailable`. Mini remains paused pending Matt's closed-log approval and Old remains on its non-blocking retry queue. No new release or review wave was opened.

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
| GitHub publication | GitHub plugin Git-data workflow (`force=false`) read back owned-fork branch `matt/fleet-chat-archive-deployed` at remote commit `3e377e92646675db1d70c47036a66dee16ad6ede`; tree `d0ffc52708dd1b28e32be2fdf845f8a47ba5f939`, parent `0fa4b4b96fd8a7db1ece075a135c892feaa38cbb`. Local equivalent docs commit is `bb8308ca1d27e5f6959a19300d71076844c3081c`. |
| Drive connector publication | Folder `AI Chat Archive` ID `1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV`; pre-list count 5, final count 12. The seven New-host object IDs, titles, MIME types, and exact byte sizes are recorded in the 2026-08-30 receipt below; each has the exact target-folder parent and `text/plain` MIME. |
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
| Mac Studio | Runtime `3c732d7`; `com.mattrotundo.ai-chat-archive.mac-studio` is loaded under launchd with `runs=2`, `state=running`, PID `76865`, PPID 1, `last exit code=0`, `StartInterval=21600`, and durable owner-only logs. The newest persisted receipt remains `20260829T184201.313238Z-c87faa38`, collected at `2026-08-29T22:01:36.872837Z`, with `completed_with_absent_harnesses`, zero errors, and `blocked_drive_unavailable`; the second scan is still in-flight. CloudStorage has zero `GoogleDrive-*` providers. | **DONE:** checkout, live-shaped preflight, persistent schedule, and one successful supervised scan. **IN-FLIGHT:** second launchd scan, Studio object publication through the Drive plugin after receipt completion, and runtime File Provider publication. |
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
  was gone. Studio's second launchd attempt remains in-flight.
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
- The GitHub plugin read back the owned-fork ref at
  `0fa4b4b96fd8a7db1ece075a135c892feaa38cbb`. The Drive plugin imported exactly
  seven New-host redacted object files once, moved them into `AI Chat Archive`,
  and metadata-read back each exact parent, MIME, and size (folder count 5→12).
  The complete ID/size map is in the dated receipt. Runtime File Provider
  publication remains a separate status.
- The New six-hour follow-up receipt collected six Codex conversations and one
  Claude conversation with zero parse failures; its seven newly emitted objects
  were published through the Drive plugin. This is verified plugin publication
  of staged output, not proof that a local launchd process can invoke a plugin.

## IN-FLIGHT

- New's launchd label is idle after `runs=3`, exit 0, and its two receipts are
  6:19:36 apart; the elapsed six-hour collection proof is **DONE**. The Studio
  label is still running at `runs=2` under launchd; its second receipt is
  **IN-FLIGHT**.
- Seven New-host redacted objects are **DONE** through the Drive plugin, with
  exact metadata/listing verification. The runtime's automatic Drive
  publication is **IN-FLIGHT** because no local File Provider is present and a
  plugin call is not a launchd capability.

## NOT STARTED

- Studio File Provider installation/login/mount has not been proven. The Drive
  plugin folder contains the two receipt Docs, two prior text artifacts, and
  seven newly published New-host objects; no runtime Studio shard has been
  published and no end-to-end automatic launchd-to-Drive event has been proven.
- The Mini storage gate has not been approved or acted on, so its real canary
  and production schedule remain unstarted.
- Old MacBook has not returned online for live deployment or canary proof.
- New's approximately 21,600-second elapsed-cycle receipt exists and is
  documented above. Studio's second launchd run is still active; its next
  receipt is required before object publication can be selected safely.
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

## Current execution readback — 2026-08-29T22:03:47Z

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
