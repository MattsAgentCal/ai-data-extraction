# Fleet chat archive live state

**Last reconciled:** 2026-08-29T19:00:00Z
**State:** active deployment; the MacBook lease owner is running the reachable-host canaries and Drive connector publication. Mini disk approval and Old MacBook reachability remain external gates.

This file is the repository's canonical rollout state. It records body-free
counts, hashes, paths, and lifecycle observations only. Raw conversation
bodies, indexes, and other private archive data remain outside Git.

## Release identity

| Item | Evidence |
|---|---|
| Reviewed release | `3c732d7b1031949bd18db90ae4ac40f667f6cfa7` (`Rollback cleanly on terminal hangup`) |
| Deployment branch | `matt/fleet-chat-archive-deployed` |
| Repository checkout | MacBook and Studio clean at docs commit `5cd62dab6e4b5898cddfc8404b398525636fde00`; Mini clean at runtime `3c732d7` |
| Verification suite | `python3.14 -m unittest discover -s tests -p 'test*.py'` -> **263/263, OK** |
| GitHub connector publication | Fork branch `MattsAgentCal/ai-data-extraction:matt/fleet-chat-archive-deployed` read back at connector commit `249082dba1cd3d7909eacb98f583c99717e12c91`; tree `80dcb504ef1b3131547c0db6414a3bd570e4ce68` matches local `7dbc965` content |
| Drive connector publication | Folder `AI Chat Archive` ID `1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV`; receipt docs `1ovOGhi7EdwUbbBUbPliQS4DYQ-A7N8ny77xt5u5wElM` and `1Q4FFT1aglyjwRx3olRlmlvtR1MFKbVcCs4R96xLX_r4`; folder listing and bodies read back successfully |
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
| New MacBook | Clean at `5cd62da` (runtime `3c732d7`). `com.mattrotundo.ai-chat-archive.new-macbook` is loaded under launchd, `runs=1`, `state=running`, and `StartInterval=21600`; its first live scan is in-flight. Last completed receipt: `2026-08-29T16:31:24.467081Z`, zero errors, `blocked_no_drive_root`, OpenClaw absent. | **DONE:** checkout, preflight, persistent schedule. **IN-FLIGHT:** first supervised scan and six-hour elapsed proof. |
| Mac Studio | Clean at `5cd62da` (runtime `3c732d7`). `com.mattrotundo.ai-chat-archive.mac-studio` is loaded under launchd, `runs=1`, `state=running`, and `StartInterval=21600`; its first live scan is in-flight. Last completed receipt: `2026-08-29T16:40:20.517359Z`, zero errors, `blocked_drive_unavailable`; New pulled 1131/1131, Mini `pending_manifest`, Old unreachable. | **DONE:** checkout, preflight, persistent schedule, connector folder/receipt doc. **IN-FLIGHT:** File Provider mount, raw shard publication, first supervised scan, and six-hour elapsed proof. |
| Mac mini | Clean at runtime `3c732d7`; no archive label is loaded. Free capacity `7,569,896 KiB`; active `CoreSimulator.log` `5,309,541,094` bytes, closed `CoreSimulator.prev.log` `15,403,577,516` bytes. | **DONE:** read-only disk census. **NOT STARTED:** approved cleanup, canary, schedule. |
| Old MacBook | `ssh oldmac` timed out. No live source inventory, deployment, or canary proof exists. Earlier retry proof recorded run count 1 -> 2, exit 0, zero stderr delta, `offline_retryable`/`ssh_unreachable`; retry label remains unloaded on New. | **DONE:** retry behavior proof. **IN-FLIGHT:** queued retry. **NOT STARTED:** online deployment/canary. |

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
- Body-free canary receipts were produced on New and Studio with zero parsed
  errors. They correctly report absent harnesses and blocked Drive publication
  rather than treating those conditions as success.
- The Studio Claude index repair was completed with a manifest-bound restore;
  the interrupted index backup remains at
  `/Users/calstudio/.local/share/ai-chat-archive-repair-proof.BCwlg8/live-current-index.backup.json`.
- The Old Mac retry implementation was reviewed and its offline behavior was
  proven without blocking the reachable-host rollout. It is not currently
  loaded.
- The owner-only Studio Google Drive DMG was staged and verified. Its observed
  size is 141,267,496 bytes and its SHA-256 is
  `fb6927060f8f20efb8ac2027d00a9c0787c111fa57c01fe6a29675afaf5c1178`.
- The GitHub connector created/published the docs tree and read back branch-tip
  commit `249082d` (the tree matches local commit `7dbc965` content); the owned
  fork ref is verified. The Drive connector created and verified the private `AI Chat
  Archive` folder and two body-free receipt docs inside it; no raw local-path
  upload was attempted.

## IN-FLIGHT

- The first production six-hour launchd labels on New and Studio are loaded with
  durable owner-only logs and currently executing their first live scans. Their
  launchd configuration proves persistent supervision and a 21,600-second
  interval; it does not yet prove an elapsed six-hour cycle.
- The connector receipt is published, but the runtime still reports
  `blocked_no_drive_root`/`blocked_drive_unavailable` because Studio has no local
  File Provider mount. Raw shard publication and the new-chat-in-Drive proof are
  in-flight.

## NOT STARTED

- Studio File Provider installation/login/mount has not been proven. The Drive
  connector folder and body-free receipt doc exist, but no raw conversation
  shard has been published and no new-chat-in-Drive proof exists.
- The Mini storage gate has not been approved or acted on, so its real canary
  and production schedule remain unstarted.
- Old MacBook has not returned online for live deployment or canary proof.
- The six-hour labels are loaded on New and Studio, but their first runs have not
  completed and no approximately 21,600-second elapsed-cycle receipt exists yet.
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

All paths above point to owner-only or temporary artifacts. They are references,
not instructions to copy raw conversation data into Git.
