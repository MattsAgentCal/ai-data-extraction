# Fleet chat archive live state

**Last reconciled:** 2026-08-29T21:12:17Z
**State:** active deployment; New's second launchd refresh completed and the authenticated Drive connector folder still contains the two approved redacted text canaries. Studio's launchd run remains active after a prior `RunFailure`; runtime File Provider publication, the six-hour elapsed proof, Mini disk approval, and Old MacBook reachability remain separate gates. The 21:12 readback opened no new release or review wave.

This file is the repository's canonical rollout state. It records body-free
counts, hashes, paths, and lifecycle observations only. Raw conversation
bodies, indexes, and other private archive data remain outside Git.

## Release identity

| Item | Evidence |
|---|---|
| Reviewed release | `3c732d7b1031949bd18db90ae4ac40f667f6cfa7` (`Rollback cleanly on terminal hangup`) |
| Deployment branch | `matt/fleet-chat-archive-deployed` |
| Repository checkout | MacBook docs checkout at `cab761c`; Studio runtime checkout at `3c732d7`; Mini clean at runtime `3c732d7` |
| Verification suite | `python3.14 -m unittest discover -s tests -p 'test*.py'` -> **263/263, OK** |
| GitHub connector publication | Handoff structural amendment local `9b747927b6217287e035eecbdee8e6309a9e7f4d` is published on the owned fork at `56e8b7f5f8a7ad47acd36bcd0a901e95339d4f20`, parent `aa9bf992f5a0404dd124e85e20b8b0bce3e0a001`; ref read back independently. |
| Drive connector publication | Folder `AI Chat Archive` ID `1V7Ir654dXlGUcpmR6A0IYCB7FOSwEETV`; receipt docs `1ovOGhi7EdwUbbBUbPliQS4DYQ-A7N8ny77xt5u5wElM` and `1Q4FFT1aglyjwRx3olRlmlvtR1MFKbVcCs4R96xLX_r4`; redacted text canaries `1pLF5FhnQcMJ5yT28HXsnnHaQuqEJyR-5` (654 bytes) and `18kklPXiMM2bzF1ZU8tCzlJJ9k-HblbC_` (49,484,530 bytes) moved into the folder and metadata/listing read back. |
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
| New MacBook | Runtime `3c732d7`; `com.mattrotundo.ai-chat-archive.new-macbook` completed its second launchd-owned refresh at `2026-08-29T20:19:56.940667Z`. Receipt `20260829T195959.324876Z-be2608f7` reports `completed_with_absent_harnesses`, zero errors, `blocked_no_drive_root`; Claude 1 conversation, Codex 5, Hermes none, OpenClaw absent. The label is idle at `runs=2`, exit 0, `StartInterval=21600`. The current goal session object is digest `d5883edd…` and 49,484,530 bytes outside Git. | **DONE:** checkout, preflight, persistent schedule, two successful supervised scans, current-session collection. **IN-FLIGHT:** six-hour elapsed proof and runtime Drive publication. |
| Mac Studio | Runtime `3c732d7`; `com.mattrotundo.ai-chat-archive.mac-studio` is loaded under launchd, `runs=1`, `state=running`, pid `14336`, and `StartInterval=21600` at the 21:09:53Z poll. Latest persisted receipt `20260829T164025.921717Z-279ff85a` failed with `RunFailure`; the current launchd attempt remains in-flight and CPU-bound in redaction/JSON serialization. Receipt count is 42 and CloudStorage has zero `GoogleDrive-*` providers. | **DONE:** checkout, preflight, persistent schedule, connector folder/receipt docs, two verified redacted connector text canaries. **IN-FLIGHT:** current supervised scan, File Provider mount, raw shard publication, and six-hour elapsed proof. |
| Mac mini | Clean at runtime `3c732d7`; no archive label is loaded. At 21:12:17Z, free capacity was `7,385,588 KB`; active `CoreSimulator.log` was `5,451,026,129` bytes and closed `CoreSimulator.prev.log` was `15,403,577,516` bytes. | **DONE:** read-only disk census. **NOT STARTED:** approved cleanup, canary, schedule. |
| Old MacBook | `ssh oldmac` timed out at 21:12:17Z. No live source inventory, deployment, or canary proof exists. New's retry label `com.mattrotundo.ai-chat-archive.old-macbook-deploy-retry` remains enabled/loaded under launchd with `RunAtLoad=true`, `StartInterval=21600`, `runs=1`, and exit 0 after `offline_retryable`/`ssh_unreachable`. | **DONE:** retry behavior proof and active launchd queue. **IN-FLIGHT:** offline retry. **NOT STARTED:** online deployment/canary. |

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
- A current body-free, zero-error supervised receipt was produced on New. Studio
  has prior zero-error receipts, but the latest persisted attempt failed and the
  current launchd attempt has not completed.
- The Studio Claude index repair was completed with a manifest-bound restore;
  the interrupted index backup remains at
  `/Users/calstudio/.local/share/ai-chat-archive-repair-proof.BCwlg8/live-current-index.backup.json`.
- The Old Mac retry implementation was reviewed and its offline behavior was
  proven without blocking the reachable-host rollout. Its retry plist is now
  enabled/loaded under launchd; the first queued attempt exited 0 with the
  retryable offline status.
- The owner-only Studio Google Drive DMG was staged and verified. Its observed
  size is 141,267,496 bytes and its SHA-256 is
  `fb6927060f8f20efb8ac2027d00a9c0787c111fa57c01fe6a29675afaf5c1178`.
- The GitHub connector publication is now read back at owned-fork commit
  `56e8b7f5f8a7ad47acd36bcd0a901e95339d4f20`, whose tree contains the
  handoff structural amendment. The Drive connector created and verified the
  private folder and two receipt Docs, then imported and moved one approved
  654-byte redacted Codex text canary (`1pLF5FhnQcMJ5yT28HXsnnHaQuqEJyR-5`)
  into that folder. JSON/raw archive upload remains unsupported by the
  connector, and runtime File Provider publication is separate.
- The New second launchd refresh completed with zero errors and collected the
  current goal session as redacted object `d5883edd…` (49,484,530 bytes).
- The authenticated Drive connector imported that object once as text, moved
  it to the exact `AI Chat Archive` folder, and read back file ID
  `18kklPXiMM2bzF1ZU8tCzlJJ9k-HblbC_`, MIME `text/plain`, and size
  49,484,530 bytes. This is connector publication evidence only.

## IN-FLIGHT

- The production six-hour launchd labels on New and Studio are loaded with
  durable owner-only logs. New's second launchd-owned refresh completed with
  exit 0; Studio's first launchd-owned attempt (pid `14336`) remains active.
  The launchd configuration proves persistent supervision and a
  21,600-second interval; it does not yet prove an elapsed six-hour cycle.
- The connector canary is visible in Drive, but runtime publication still
  reports `blocked_no_drive_root`/`blocked_drive_unavailable` because Studio has
  no local File Provider mount. The second connector canary is visible, but
  raw shard publication and the automatic new-chat-in-Drive proof remain
  in-flight.

## NOT STARTED

- Studio File Provider installation/login/mount has not been proven. The Drive
  connector folder and body-free receipt docs plus one redacted text canary
  exist, but no runtime raw conversation shard has been published and no
  automatic new-chat-in-Drive proof exists.
- The Mini storage gate has not been approved or acted on, so its real canary
  and production schedule remain unstarted.
- Old MacBook has not returned online for live deployment or canary proof.
- The six-hour labels are loaded on New and Studio; New's RunAtLoad refreshes
  have completed, but no approximately 21,600-second elapsed-cycle receipt
  exists yet.
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
