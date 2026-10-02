# Tamper matrix (B5 prototype)

What `tools/tamper.py` breaks, what an attacker needs to break it, which of the eight B4
checks notices, and how to put it back.

```
python tools/tamper.py <mode> --event <uid>      # tamper, then print the B4 report
python tools/tamper.py verify --event <uid>      # just the report (exit 1 if broken)
python tools/tamper.py list                      # what is tampered right now
python tools/tamper.py untamper --event <uid>    # restore; omit --event for everything
```

Every mode copies the files it touches into `data/tamper_backup/` **before** changing
anything, and records them in `manifest.json`, so `untamper` restores the vault
byte-for-byte.

## The matrix

| Attack | Attacker capability | First failing check | Why it is caught | Recovery |
|---|---|---|---|---|
| `naive_flip` | Write access to a vault file | `fetch_raw` (then `decrypt_segment`) | One flipped bit invalidates the AES-256-GCM tag, so the segment will not open at all | `untamper` |
| `insider_rewrite` | Write access **plus the KEK** — they decrypt, edit, re-encrypt and fix every hash inside the segment | `merkle_inclusion` | The re-sealed segment is internally perfect, but its digest is no longer the leaf the signed root commits to, and they cannot re-sign the root | `untamper` |
| `segment_delete` | Write access to the vault | `fetch_raw` | The event is simply gone; absence is as detectable as alteration, because the signed root still names the segment | `untamper` |
| `root_rewrite` | Write access to the ledger | `merkle_inclusion` (step 6), then `root_signature` (step 7) | The root payload is signed with Ed25519; editing it breaks the signature, and the private key is not in the ledger | `untamper` |

`insider_rewrite` is the one worth demoing. It is the attack that a
checksum-in-the-same-file design cannot catch: every local check passes, and only the
signature over an out-of-band Merkle root gives it away.

## What each mode leaves passing

Useful when narrating the demo — a failure is more convincing when the untouched checks
still pass:

| Mode | Passing checks | Failing checks |
|---|---|---|
| `naive_flip` | — (the segment cannot be read) | `fetch_raw`, `decrypt_segment`, and everything downstream |
| `insider_rewrite` | `fetch_raw`, `decrypt_segment`, `hash_raw`, `chain_walk`, `segment_digest` | `merkle_inclusion` |
| `segment_delete` | — | `fetch_raw` and everything downstream |
| `root_rewrite` | `fetch_raw`, `decrypt_segment`, `hash_raw`, `chain_walk`, `segment_digest` | `merkle_inclusion`, `root_signature` |

`immudb_verified` always reports `not_implemented` in this prototype and is excluded from
the overall verdict (see B3/B4 notes).

## Recovery

`untamper` restores the newest operation first and works for any mix of modes:

- a modified file is copied back from the backup and re-sealed read-only (`0444`);
- a deleted file is restored;
- a file the mode created where none existed is removed.

It is idempotent — running it twice reports `0` operations undone — and the tests assert
that a full vault snapshot is byte-identical before and after.

The demo-engine endpoints `POST /api/demo/tamper` and `/untamper` (IF-API-DEMO) are built and
are what `Shift+T` calls; they bridge straight to the functions below, so there is one
definition of each mode. A refusal comes back as HTTP 409 with its reason.

## Not in this prototype

- **ClickHouse tampering.** The lineage index is derived data; editing it does not touch
  the evidence, so it proves nothing about the vault.
- **immudb anchoring.** Until B3 stores roots in immudb, `root_rewrite` is only caught by
  the signature. With immudb, rewriting the local ledger would also contradict the
  external ledger.
- **Tampering an event whose window is not signed yet.** Refused, with the reason. Two things
  would go wrong otherwise: the Merkle and signature steps are `pending_seal` rather than
  failed, so nothing turns red and the demo's point is lost; and the integrity service may then
  sign the *tampered* digest into the root, after which `untamper` restores bytes that no longer
  match the signed leaf and verify stays red for the rest of the run.
- **Filesystem immutability.** `chattr +i` (B2 AC1) is not applied, so these modes only
  need `chmod`. On a host with immutability enabled, `naive_flip` and `segment_delete`
  would fail at the filesystem layer first — which is the point of that control.
