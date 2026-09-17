# OFAC Party Removal Approval Design

## Incident

The 2026-09-16 OFAC SDN snapshot removed three previously current parties:

- `SDN / 10982` — `VEGA SANCHEZ, Jose Raul`
- `SDN / 40948` — `BOMATTER, Hans Peter`
- `SDN / 51041` — `MODULSAN MAKINA KESICI TAKIM VE DISLI SANAYI TICARET LIMITED SIRKETI`

The existing FixedRef safety gate correctly stopped the workflow. The approved SDN Advanced XML content hash is `8f81d70e91b4481bd04ac0acebb896d9743095170250dda89c0704f579fab6b3`. The primary evidence is `https://ofac.treasury.gov/recent-actions/20260916`.

## Goal

Allow only an explicitly approved, hash-pinned OFAC party-removal set to pass the existing fail-closed gate, retain party and alias history, update the screening master party-aware, and persist an atomic application audit.

## Safety invariants

1. Missing approval, a partial approval, an extra approved FixedRef, a snapshot-hash mismatch, a duplicate approval, an invalid schema, or a non-Treasury evidence URL stops processing before state, master, party index, or heartbeat is committed.
2. Approval matching is exact per `(list, Advanced XML SHA256)`; the approved FixedRef set must equal the detected removal set for that list.
3. Removed party and alias rows are retained in `ofac_alias_history.csv` with `party_current=0` and `alias_current=0`.
4. A master name keeps its OFAC source when the same `match_key` remains a current strong alias or primary name of another OFAC party.
5. Otherwise only the OFAC source/category is removed. Other sources remain active. A row with no remaining source is retained and marked inactive with the existing `DELISTED` reason.
6. Weak aliases do not independently keep an active OFAC screening source.
7. Approval application, master, state, party index, heartbeat, diff, and dashboard projections are committed as one persistence generation.
8. The application audit records approver, approval time, official URL, applied time, exact per-name effects, and before/after logical-state SHA256 values for master and party index.

## Approval ledger

Path: `data/review/ofac_party_removal_approvals.csv`

Columns:

```text
list,snapshot_sha256,party_id,party_name,decision,approved_by,approved_at,official_url,notes
```

Only `decision=APPROVED` is accepted. Every row must have a 64-character lowercase SHA256, a non-empty party ID/name/approver/time, and an HTTPS evidence URL hosted by `treasury.gov` or a subdomain.

## Application audit

Path: `data/review/ofac_party_removal_audit.csv`

One row is appended for every applied party approval. `effects_json` contains each affected `match_key`, display name, action, and before/after audited master fields. The ledger is written inside the same atomic persistence transaction as the master and party index.

## Runtime flow

1. Fetch and validate complete SDN and Consolidated snapshots as today.
2. Load the persisted party index and hash its pre-update logical state.
3. Apply the complete snapshot to an in-memory index and detect removed FixedRefs.
4. If none were removed, continue unchanged.
5. If removals exist, load and strictly validate the approval ledger against the actual per-list removal set and the fetched Advanced XML hashes.
6. Merge current strong names without name-based delisting.
7. Remove OFAC only for match keys belonging solely to approved removed parties; preserve names backed by another current strong OFAC party or another source.
8. Build application-audit rows with before/after hashes and effects.
9. Publish staged state/master/index/audit objects only after all checks succeed.
10. Persist all formal outputs atomically.

## Non-goals

- General automatic OFAC delisting without human approval.
- Automatic approval generation from the Recent Actions page.
- Full Phase 6 lifecycle management for every future source.
- Deleting historical party, alias, or master rows.
