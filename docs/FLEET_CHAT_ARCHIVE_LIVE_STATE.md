# Fleet chat archive live state

**Last reconciled:** 2026-08-29T14:54:14Z
**State:** documentation-only checkpoint; the deployment goal is paused at the manager's request.

This file is the repository's canonical rollout state. It records body-free
counts, hashes, paths, and lifecycle observations only. Raw conversation
bodies, indexes, and other private archive data remain outside Git.

## Release identity

| Item | Evidence |
|---|---|
| Reviewed release | `3c732d7b1031949bd18db90ae4ac40f667f6cfa7` (`Rollback cleanly on terminal hangup`) |
| Deployment branch | `matt/fleet-chat-archive-deployed` |
| Repository checkout | Clean at the reviewed release on this MacBook, Mac Studio, and Mac mini |
| Verification suite | `python3.14 -m unittest discover -s tests -p 'test*.py'` -> **263/263, OK** |
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

## Host matrix at the pause

| Host | Observed truth | Classification |
|---|---|---|
| New MacBook | Checkout is clean at `3c732d7`. The temporary `com.mattrotundo.ai-chat-archive.canary-final-3c-new` KeepAlive label is active (`runs=138`). Its receipt has 137 valid JSONL records, zero invalid lines, zero stderr bytes, and the latest completed run at 2026-08-29T14:41:25Z exited 0 with `completed_with_absent_harnesses`, `errors=0`, and `blocked_no_drive_root`. Claude and Hermes were absent in that incremental run; Codex collected 3 conversations/3 new objects; OpenClaw is not present. The production six-hour label and Old Mac retry label are disabled. | **DONE:** release deployed and canary evidence recorded. **IN-FLIGHT:** temporary canary remains loaded. **NOT STARTED:** production schedule enablement and OpenClaw host collection. |
| Mac Studio | SSH read-only check found a clean checkout at `3c732d7`. The temporary `com.mattrotundo.ai-chat-archive.canary-final-3c-studio` KeepAlive label is active (`runs=29`). Its receipt has 28 valid JSONL records, zero invalid lines, and the latest completed run at 2026-08-29T14:29:11Z exited 0 with `completed_with_absent_harnesses`, `errors=0`, and `blocked_drive_unavailable`; Claude 1, Codex 6, Hermes 0, OpenClaw absent. The last hub statuses were Mini `pending_manifest`, New `unreachable`, and Old `unreachable`. The production six-hour label is disabled. | **DONE:** release deployed and canary evidence recorded. **IN-FLIGHT:** temporary canary remains loaded. **NOT STARTED:** production schedule enablement, Drive publication, and OpenClaw host collection. |
| Mac mini | SSH read-only check found a clean checkout at `3c732d7`. No production archive label is enabled. At the check, free capacity was 7,687,332 KiB (about 7.33 GiB); `CoreSimulator.log` was 5,109,616,254 bytes with one open handle and `CoreSimulator.prev.log` was 15,403,577,516 bytes with zero open handles. No cleanup or real canary was started. | **DONE:** release deployed. **NOT STARTED:** storage action, real canary, and production schedule. |
| Old MacBook | SSH to `oldmac` timed out at the reconciliation check. No live source inventory, deployment, or canary proof exists. The earlier retry proof recorded run count 1 -> 2, exit 0, zero stderr delta, and `offline_retryable`/`ssh_unreachable`; the current retry label is disabled/unloaded on New. | **DONE:** retry behavior was previously proven. **NOT STARTED:** online deployment, live canary, and an active retry schedule. |

## DONE

- The predecessor checkout was audited and the Claude Code, Codex, OpenClaw,
  and Hermes adapters, content-addressed deduplication, credential redaction,
  provenance manifests, trusted remote stream, and terminal-hangup rollback
  were integrated at `3c732d7`.
- The release passed 263/263 tests, compile/diff checks, focused SIGHUP and
  rollback checks, and a fresh bundle-clone verification before deployment.
- The exact release was fast-forward deployed to the reachable New MacBook,
  Mac Studio, and Mac mini; each live checkout is currently clean at the same
  commit.
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

## IN-FLIGHT

- The temporary final-release canary labels on New and Studio are still
  loaded with KeepAlive. Their successful receipts are evidence of repeated
  collection, not evidence of a stable terminal state or production schedule.
  This documentation turn did not start another run or change their lifecycle.
- The manager pause is in effect. No Drive login, schedule enablement, Mini
  cleanup, Old Mac retry activation, or new product work is being performed.

## NOT STARTED

- Google Drive has not been installed or signed into on Studio; no File Provider
  mount, `AI Chat Archive` folder, published receipt, or new-chat-in-Drive proof
  exists.
- The Mini storage gate has not been approved or acted on, so its real canary
  and production schedule remain unstarted.
- Old MacBook has not returned online for live deployment or canary proof.
- The production six-hour archive schedules remain disabled/unloaded on the
  reachable hosts; the temporary canary labels are separate test jobs.
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

All paths above point to owner-only or temporary artifacts. They are references,
not instructions to copy raw conversation data into Git.
