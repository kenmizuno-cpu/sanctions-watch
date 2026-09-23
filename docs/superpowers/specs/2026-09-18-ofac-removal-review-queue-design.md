# OFAC Party Removal Review Queue Design

## Incident

The 2026-09-18 SDN snapshot removed six previously current OFAC parties. The
existing hash-pinned approval gate correctly prevented an unreviewed screening
deactivation, but it also rejected the entire OFAC transaction. As a result,
the watcher repeatedly fails on every run until the approval ledger is edited.

The detected removal set is:

- `SDN / 33451` — `People's Front for Democracy and Justice`
- `SDN / 33534` — `W Kidan Hagos Ghebrehiwet`
- `SDN / 33535` — `Nemariam Abraha Kassa`
- `SDN / 33597` — `Eritrean Defense Forces`
- `SDN / 33918` — `Red Sea Trading Corporation`
- `SDN / 33919` — `Hidri Trust`

The SDN Advanced XML snapshot SHA256 is
`8d64eef6932ad713709f1ea7cc0e0e003d68308eb9d1ac5637268d666fdc056f`.
The official evidence is
`https://ofac.treasury.gov/recent-actions/20260918`.

## Goal

Advance a complete, validated OFAC snapshot even when a party removal has not
yet been approved, while keeping every affected screening name active until a
human approval exactly matches the queued removal event. A later run must be
able to apply the approval even when OFAC returns `304 Not Modified`.

## Safety invariants

1. Detection and application are separate transitions. Detection may advance
   source state and party history; application requires explicit approval.
2. An unapproved party remains active in the screening master. The watcher
   reports `review_required` instead of failing solely because approval is
   absent.
3. Approval is exact per queued `(list, snapshot_sha256, event_id)` set. A
   partial or extra FixedRef for the event is an error and publishes nothing.
4. A wrong snapshot hash never authorizes an application. It leaves the event
   pending and the screening names active.
5. Malformed or duplicate approval/queue records remain hard failures.
6. If any queued party reappears before approval, its original batch event is
   atomically marked `CANCELLED_REAPPEARED`; its old approval cannot be
   applied. Still-absent members are re-queued as a new exact event pinned to
   the current complete snapshot.
7. Repeated runs and later unrelated source snapshots do not create duplicate
   open events for the same `(list, party_id)`.
8. Current additions, alias changes, state, party history, heartbeat, and
   dashboard projection continue to publish while removals wait for review.
9. Approved removals retain all party, alias, and master history. Only the
   existing party-aware OFAC source/category transition is applied.
10. Queue state, party index, master, state, heartbeat, diff, dashboard, and
    approval-application audit are persisted in one atomic generation.
11. The implementation remains compatible with Python 3.9.

## Review queue

Path: `data/review/ofac_party_removal_queue.csv`

Columns:

```text
event_id,list,snapshot_sha256,party_id,party_name,status,detected_at_ms,last_seen_at_ms,resolved_at_ms,resolution
```

An `event_id` is the SHA256 of the list, the first detecting Advanced XML
snapshot hash, and the sorted FixedRef set detected together. All rows in one
event share the same `event_id` and snapshot hash.

Statuses:

- `PENDING_REVIEW`: still absent and not yet exactly approved.
- `APPLIED`: an exact approval was applied to the screening master.
- `CANCELLED_REAPPEARED`: the FixedRef returned before approval.

`snapshot_sha256` never changes after detection. `last_seen_at_ms` may advance
when a later complete snapshot confirms that a pending party remains absent.

## Approval behavior

The existing `data/review/ofac_party_removal_approvals.csv` remains the only
human authorization input. For each open queue event:

- no rows at the event hash means “not approved yet”;
- an exact set at the event hash authorizes the event;
- a non-empty partial or extra set at the event hash raises `ApprovalError`;
- rows at other hashes do not authorize the event;
- every approved party name must match its FixedRef history.

This preserves fail-closed behavior at the irreversible business transition:
the watcher can continue, but no screening deactivation occurs without the
exact approval.

## Runtime flow

### Changed source snapshot

1. Fetch and validate the complete Classic primary, Classic alias, and
   Advanced XML snapshot for SDN and Consolidated.
2. Update an in-memory party/alias history and detect newly absent FixedRefs.
3. Reconcile the persistent review queue: create new events, deduplicate
   existing events, and cancel pending rows that reappeared.
4. Validate any available exact approvals for all open events.
5. Merge current strong names with `delist=False`, so unapproved missing names
   remain active.
6. Apply only the approved queued parties using the existing party-aware
   removal logic; then mark those queue rows `APPLIED` and build the audit.
7. Report each source with remaining open events as `review_required`.
8. Atomically persist the complete generation.

### Unchanged source snapshot

1. Load the persisted review queue and party history after all documents are
   confirmed unchanged.
2. Re-evaluate approvals for open events against their original event hashes.
3. If an exact approval now exists, apply it to the master, mark the event
   `APPLIED`, append the audit, and atomically persist the generation.
4. Otherwise keep the names active and emit `review_required` without failing.

## Operational visibility

`data/dashboard/status.csv` renders `review_required` as
`掲載終了候補・要レビュー`. The source audit records queue detection,
pending counts, cancellations, and successful approval validation. Normal
acquisition/schema/coverage failures remain workflow errors.

## Non-goals

- Automatically approving or deactivating an OFAC party.
- Scraping a Recent Actions page to create approvals.
- Deleting historical party, alias, queue, audit, or master rows.
- Changing the removal policy for non-OFAC sources.
