# Fleet chat archive documentation receipt — 2026-08-29

**Receipt scope:** body-free deployment and publication evidence. The reviewed
runtime remains `3c732d7`; docs/addendum commit `5cd62da` is published to the
owned GitHub fork, New and Studio six-hour launchd jobs are loaded, and the Drive
connector folder/receipt doc are verified. Mini cleanup and Old MacBook remain
gated/offline.

**Observed:** 2026-08-29T18:45:00Z, from the local MacBook plus SSH probes to
Studio and Mini, launchd readback, GitHub connector readback, and Drive connector
readback. The two first launchd scans were still running at the observation.

## Code and verification

| Check | Result |
|---|---|
| Checkout | `matt/fleet-chat-archive-deployed`, clean after the docs/addendum commit |
| Reviewed code commit | `3c732d7b1031949bd18db90ae4ac40f667f6cfa7` |
| Docs/addendum commit | `5cd62dab6e4b5898cddfc8404b398525636fde00` |
| Test command | `python3.14 -m unittest discover -s tests -p 'test*.py'` |
| Test result | 263 tests ran, `OK` |
| GitHub connector | Fork branch read back at `5cd62da`; tree `16cd1967c3fe1b19607e6933d1b9b4f44b499a96` |
| Drive connector | Folder `AI Chat Archive` ID `1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV`; receipt doc ID `1ovOGhi7EdwUbbBUbPliQS4DYQ-A7N8ny77xt5u5wElM`; metadata and body read back |
| Bundle | `/tmp/ai-data-extraction-3c732d7.prSOXz/ai-data-extraction-3c732d7.bundle` |
| Bundle SHA-256 | `4531929ef667087d755dbdffb78054d3a64ec471885e4def7292f34023dcb295` |

The six deployed runtime hashes are recorded in the canonical
[`FLEET_CHAT_ARCHIVE_LIVE_STATE.md`](FLEET_CHAT_ARCHIVE_LIVE_STATE.md). No raw
conversation bytes were opened or copied into this receipt.

## Read-only fleet observations

| Host | Receipt/probe evidence |
|---|---|
| New MacBook | Clean at `5cd62da` (runtime `3c732d7`). `com.mattrotundo.ai-chat-archive.new-macbook` loaded under launchd with `RunAtLoad=true`, `StartInterval=21600`, `runs=1`, and active first scan. Last completed receipt: `2026-08-29T16:31:24.467081Z`, zero errors, `blocked_no_drive_root`; OpenClaw absent. |
| Mac Studio | Clean at `5cd62da` (runtime `3c732d7`). `com.mattrotundo.ai-chat-archive.mac-studio` loaded under launchd with `RunAtLoad=true`, `StartInterval=21600`, `runs=1`, and active first scan. Last completed receipt: `2026-08-29T16:40:20.517359Z`, zero errors, `blocked_drive_unavailable`; hub New pulled 1131/1131, Mini pending, Old unreachable. |
| Mac mini | Clean `3c732d7` checkout. Free capacity 7,569,896 KiB at the current probe. `CoreSimulator.log`: 5,309,541,094 bytes and active. `CoreSimulator.prev.log`: 15,403,577,516 bytes and closed. No cleanup or canary started. |
| Old MacBook | `ssh oldmac` timed out. No live deployment or canary proof. Current New-host retry label is disabled/unloaded; earlier offline retry proof is preserved as historical evidence in the live-state file. |
| Google Drive | Connector profile is Matt Rotundo. Exact folder `AI Chat Archive` was created under My Drive and read back. Receipt doc `1ovOGhi7Edw...` is in that folder with body-free text. Studio File Provider installation/mount and raw object publication remain absent. |

## Classification at pause

### DONE

- Predecessor hardening and the final SIGHUP-safe release are preserved at
  `3c732d7` with the 263-test verification and bundle hash above.
- The runtime release is deployed to reachable New, Studio, and Mini; docs and
  binding controls are at `5cd62da` on New/Studio and on the fork branch.
- New and Studio have body-free, zero-error receipts; launchd supervisors are
  loaded at the six-hour interval and Drive is correctly reported blocked by the
  runtime rather than silently treated as published.
- The connector published the body-free fleet receipt doc into the private Drive
  folder and read it back; this is a connector receipt, not raw-shard or
  automatic-cycle proof.
- Studio's manifest-bound Claude index restore, the Old retry behavior proof,
  and the owner-only Drive DMG staging are retained as receipts/pointers.

### IN-FLIGHT

- The first production six-hour launchd scans on New and Studio are still active;
  their completion receipts and the approximately 21,600-second elapsed-cycle
  proof are not yet available.
- Runtime File Provider publication, a new chat appearing in Drive, and the
  connector-to-launchd handoff remain in-flight.

### NOT STARTED

- Studio File Provider installation/login/provider discovery, raw-shard
  publication, and new-chat-in-Drive proof.
- Mini storage approval/cleanup, real canary, and production schedule.
- Old Mac online deployment/canary and active retry schedule.
- A completed six-hour elapsed-cycle receipt and OpenClaw host collection proof.
- Separate ranking, graph, or wiki entry: none exists in the tracked checkout.

This receipt intentionally contains no message content, credentials, or raw
archive object. It is safe to commit; the body-bearing receipt files remain
owner-only outside Git.
