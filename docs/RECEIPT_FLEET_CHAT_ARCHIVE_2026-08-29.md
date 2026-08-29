# Fleet chat archive documentation receipt — 2026-08-29

**Receipt scope:** reconcile repository documentation only. The deployment goal
was paused before this receipt was written; no collector, scheduler, Drive
installer, cleanup, or source archive was changed by this documentation task.

**Observed:** 2026-08-29T14:54:14Z, from the local MacBook plus read-only SSH
probes to Studio, Mini, and Old MacBook.

## Code and verification

| Check | Result |
|---|---|
| Checkout | `matt/fleet-chat-archive-deployed`, clean before documentation edits |
| Reviewed code commit | `3c732d7b1031949bd18db90ae4ac40f667f6cfa7` |
| Test command | `python3.14 -m unittest discover -s tests -p 'test*.py'` |
| Test result | 263 tests ran, `OK` |
| Bundle | `/tmp/ai-data-extraction-3c732d7.prSOXz/ai-data-extraction-3c732d7.bundle` |
| Bundle SHA-256 | `4531929ef667087d755dbdffb78054d3a64ec471885e4def7292f34023dcb295` |

The six deployed runtime hashes are recorded in the canonical
[`FLEET_CHAT_ARCHIVE_LIVE_STATE.md`](FLEET_CHAT_ARCHIVE_LIVE_STATE.md). No raw
conversation bytes were opened or copied into this receipt.

## Read-only fleet observations

| Host | Receipt/probe evidence |
|---|---|
| New MacBook | Clean `3c732d7` checkout. Temporary final-3c label `runs=138`; 137 valid JSONL receipt records, zero invalid lines, zero stderr bytes. Latest completed record: 2026-08-29T14:41:25Z, exit 0, `completed_with_absent_harnesses`, zero errors, `blocked_no_drive_root`; OpenClaw absent. Production archive and Old retry labels disabled. |
| Mac Studio | Clean `3c732d7` checkout. Temporary final-3c label `runs=29`; 28 valid JSONL receipt records, zero invalid lines, zero stderr bytes. Latest completed record: 2026-08-29T14:29:11Z, exit 0, `completed_with_absent_harnesses`, zero errors, `blocked_drive_unavailable`; last hub statuses Mini `pending_manifest`, New `unreachable`, Old `unreachable`. Production archive label disabled. |
| Mac mini | Clean `3c732d7` checkout. Free capacity 7,687,332 KiB at the probe. `CoreSimulator.log`: 5,109,616,254 bytes, one open handle. `CoreSimulator.prev.log`: 15,403,577,516 bytes, zero open handles. No cleanup or canary started. |
| Old MacBook | `ssh oldmac` timed out. No live deployment or canary proof. Current New-host retry label is disabled/unloaded; earlier offline retry proof is preserved as historical evidence in the live-state file. |
| Google Drive | Studio DMG exists at `/Users/calstudio/Downloads/GoogleDrive-2026-08-28.dmg`, mode 0600, 141,267,496 bytes, SHA-256 `fb6927060f8f20efb8ac2027d00a9c0787c111fa57c01fe6a29675afaf5c1178`; `hdiutil verify` reported `VALID`. Installation, login, mount, publication, and new-chat proof are absent. |

## Classification at pause

### DONE

- Predecessor hardening and the final SIGHUP-safe release are preserved at
  `3c732d7` with the 263-test verification and bundle hash above.
- The exact release is deployed to the reachable New MacBook, Mac Studio, and
  Mac mini, each clean at the reviewed commit.
- New and Studio have body-free, zero-error canary receipts; Drive is correctly
  reported blocked rather than silently treated as published.
- Studio's manifest-bound Claude index restore, the Old retry behavior proof,
  and the owner-only Drive DMG staging are retained as receipts/pointers.

### IN-FLIGHT

- Temporary final-3c KeepAlive canaries remain active on New and Studio. Their
  latest completed receipts are recorded above; a terminal quiescent state is
  not claimed.

### NOT STARTED

- Drive installation/login/provider discovery/publication/new-chat proof.
- Mini storage approval/cleanup, real canary, and production schedule.
- Old Mac online deployment/canary and active retry schedule.
- Production six-hour schedule enablement and OpenClaw host collection proof.
- Separate ranking, graph, or wiki entry: none exists in the tracked checkout.

This receipt intentionally contains no message content, credentials, or raw
archive object. It is safe to commit; the body-bearing receipt files remain
owner-only outside Git.
