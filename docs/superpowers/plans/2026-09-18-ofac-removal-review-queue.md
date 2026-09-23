# OFAC Party Removal Review Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Quarantine unapproved OFAC FixedRef removals for human review without failing routine monitoring, support approval application on a later `304 Not Modified` run, and approve the six verified 2026-09-18 SDN removals.

**Architecture:** A new `src/ofac_removal_queue.py` owns the strict append-preserving queue model and lifecycle. `src.ofac_removal` gains event-scoped optional authorization while retaining its existing strict API. `src.watch.run_ofac` reconciles the queue after a complete changed snapshot and processes already queued approvals in the unchanged path. The queue joins the existing atomic persistence generation.

**Tech Stack:** Python 3.9-compatible standard library, CSV, SHA256, dataclasses, existing `src.master`, `src.ofac_index`, `src.ofac_removal`, `src.persistence`, and `unittest` suites.

**Spec:** `docs/superpowers/specs/2026-09-18-ofac-removal-review-queue-design.md`

## Global Constraints

- Missing approval is a review state, not a watcher failure.
- No master deactivation occurs without an exact hash-pinned event approval.
- Partial/extra approvals, malformed ledgers, duplicate open queue rows, and unsafe queue transitions fail before caller objects or formal files are published.
- A wrong-hash row cannot authorize the event and leaves it pending.
- Reappeared parties cancel pending events before authorization.
- Queue, index, master, state, heartbeat, diff, dashboard, and application audit share one atomic persistence generation.
- Production behavior is implemented only after its focused test fails for the expected reason.
- All new syntax and type annotations run on Python 3.9.

---

### Task 1: Add the strict OFAC removal review queue

**Files:**
- Create: `src/ofac_removal_queue.py`
- Create: `tests/test_ofac_removal_queue.py`
- Create: `data/review/ofac_party_removal_queue.csv`

**Interfaces:**
- Produces: `QueueError`, `QueueDiff`, `load(path)`, `save(rows, path)`, `reconcile(rows, removed_parties, current_parties, snapshot_hashes, history, ts)`, `pending_groups(rows)`, and `mark_applied(rows, approved_keys, ts)`.
- Queue group keys are `(event_id, list_name, snapshot_sha256)` and values are exact FixedRef sets.

- [ ] **Step 1: Write queue lifecycle tests**

Cover a missing/empty queue, deterministic event creation, a multi-party event, same-event deduplication, later unrelated removals, reappearance cancellation, applied transitions, malformed schema/hash/status/timestamps, duplicate rows, and multiple open events for one party.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m unittest tests.test_ofac_removal_queue -v`

Expected: import failure because `src.ofac_removal_queue` does not exist.

- [ ] **Step 3: Implement the minimum queue model**

Use the exact CSV fields and statuses from the design. Derive `event_id` from canonical JSON containing the list, first snapshot hash, and sorted party IDs. Select a deterministic display name from history, preferring primary then strong rows. Mutate queue rows only after all inputs and transitions validate.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `python -m unittest tests.test_ofac_removal_queue -v`

Expected: all queue tests pass.

### Task 2: Authorize only complete pending events

**Files:**
- Modify: `src/ofac_removal.py`
- Modify: `tests/test_ofac_removal.py`

**Interfaces:**
- Produces: `load_available_approvals(path, pending_groups, history) -> list[Approval]`.
- Retains: `load_and_authorize(...)` and all current strict approval validation behavior for existing callers/tests.

- [ ] **Step 1: Write failing pending-event authorization tests**

Prove: no file/no matching hash returns no authorization; exact event approval passes; partial and extra rows at the event hash raise; wrong-hash rows do not authorize; duplicate/malformed rows raise; and party-name mismatch raises.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m unittest tests.test_ofac_removal.OfacPendingApprovalTest -v`

Expected: failure because `load_available_approvals` is missing.

- [ ] **Step 3: Implement event-scoped optional authorization**

Load and globally validate the approval ledger when it exists. For each pending event, compare only rows with its exact list and snapshot hash. Return nothing for zero matching rows, require exact party equality for a non-empty match, and reuse the existing FixedRef/name validation.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `python -m unittest tests.test_ofac_removal -v`

Expected: all old strict-gate and new optional-authorization tests pass.

### Task 3: Integrate changed-snapshot quarantine using TDD

**Files:**
- Modify: `src/watch.py`
- Modify: `src/dashboard.py`
- Modify: `tests/test_ofac_atomicity.py`
- Modify: `tests/test_dashboard_screening.py`

**Interfaces:**
- Adds: `OFAC_REMOVAL_QUEUE_REL`, `OFAC_REMOVAL_QUEUE`.
- Publishes: `opts["ofac_removal_queue_rows"]` after a successful staged transaction.
- Adds heartbeat/dashboard status: `review_required` / `掲載終了候補・要レビュー`.

- [ ] **Step 1: Replace the old missing-approval failure expectation with quarantine tests**

Assert that an unapproved changed snapshot returns normally, keeps the master row active, publishes the inactive history row plus a `PENDING_REVIEW` queue row, marks only the affected source `review_required`, and still permits ordinary additions/changes. Assert exact approval applies and marks the queue `APPLIED`. Preserve rollback tests for extra/partial approvals and downstream audit failures.

- [ ] **Step 2: Run the focused transaction tests and verify RED**

Run: `python -m unittest tests.test_ofac_atomicity.OfacRemovalTransactionTest tests.test_dashboard_screening -v`

Expected: missing approval still raises the old `ApprovalError`, and `review_required` has no dashboard label.

- [ ] **Step 3: Reconcile and authorize the queue in the fetched path**

Load the queue only after a complete snapshot is available. Reconcile against `IndexDiff.removed_parties` and current record party IDs before authorization. Merge with `delist=False`; pass only approved queued parties to `apply_to_master`; mark them applied only after removal/audit construction succeeds. Rewrite affected heartbeat rows after all transitions.

- [ ] **Step 4: Publish staged queue state and operational audit details**

Set queue/index/audit options only at the final transaction boundary. Add source-audit entries and logs for created, cancelled, pending, and approved counts. Return success when pending review is the only exceptional condition.

- [ ] **Step 5: Run the focused tests and verify GREEN**

Run: `python -m unittest tests.test_ofac_atomicity.OfacRemovalTransactionTest tests.test_dashboard_screening -v`

Expected: all changed-snapshot quarantine and rollback cases pass.

### Task 4: Apply later approvals on unchanged snapshots

**Files:**
- Modify: `src/watch.py`
- Modify: `tests/test_ofac_atomicity.py`

**Interfaces:**
- Extends the current `not fetched_any and not rollout_pending` branch to process open queue events before returning.

- [ ] **Step 1: Write failing `304` review tests**

Start from a persisted inactive index row and `PENDING_REVIEW` event. Assert no approval keeps the master active and reports review required. Add an exact approval without changing source hashes and assert the next all-`304` run deactivates the master, marks the event `APPLIED`, emits a diff/audit, and commits caller objects. Assert a cancelled or wrong-hash event cannot apply.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m unittest tests.test_ofac_atomicity.OfacRemovalUnchangedReviewTest -v`

Expected: the current early return ignores the newly added approval.

- [ ] **Step 3: Implement unchanged-snapshot approval processing**

Load history and queue after the fetch loop. Validate open events, apply exact approvals using the persisted history, run weak-alias reconciliation, build before/after hashes and audit rows, update queue statuses, and publish the same staged options used by the fetched path. Do not reparse partial cached snapshots.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `python -m unittest tests.test_ofac_atomicity.OfacRemovalUnchangedReviewTest -v`

Expected: all unchanged-snapshot review cases pass.

### Task 5: Persist queue state in the formal atomic generation

**Files:**
- Modify: `src/watch.py`
- Modify: `tests/test_persistence_atomicity.py`

**Interfaces:**
- Extends: `_persist_outputs_atomically(..., ofac_removal_queue_rows=None)`.
- Persists: `data/review/ofac_party_removal_queue.csv` through `ofac_removal_queue.save`.

- [ ] **Step 1: Add success and rollback assertions for the queue file**

Extend the existing full-generation test to load the persisted queue. Extend the injected state-replace failure test so a pre-existing queue is restored byte-for-byte together with master/index/audit/dashboard/state and no temp or backup file leaks.

- [ ] **Step 2: Run persistence tests and verify RED**

Run: `python -m unittest tests.test_persistence_atomicity -v`

Expected: the persistence helper does not accept or write queue rows.

- [ ] **Step 3: Register queue persistence before the state commit marker**

Add a full-file `FileWrite` for the queue whenever staged rows are provided. Pass the option from `main()` and keep the application audit append in the same generation.

- [ ] **Step 4: Run persistence tests and verify GREEN**

Run: `python -m unittest tests.test_persistence_atomicity -v`

Expected: success and injected rollback tests pass with no leaked artifacts.

### Task 6: Add the six approvals and operator documentation

**Files:**
- Modify: `data/review/ofac_party_removal_approvals.csv`
- Modify: `README.md`

**Interfaces:**
- Adds six `APPROVED` rows for SDN hash `8d64eef6932ad713709f1ea7cc0e0e003d68308eb9d1ac5637268d666fdc056f`.

- [ ] **Step 1: Append the exact user-approved manifest**

Use approver `kenmizuno-cpu`, timezone-qualified approval time, official URL `https://ofac.treasury.gov/recent-actions/20260918`, and the six names/FixedRefs listed in the design. Do not edit older approvals.

- [ ] **Step 2: Update the runbook**

Document pending review, dashboard status, continued monitoring, exact approval behavior, `304` application, reappearance cancellation, and the distinction between a review state and an acquisition/schema failure.

- [ ] **Step 3: Validate the literal ledger with focused tests**

Run a small Python command using the production loader and the six-party pending group/history from `ofac_alias_history.csv`; assert exactly six approvals are returned.

### Task 7: Reproduce the incident and run complete verification

**Files:**
- Verify only; no new production interfaces.

- [ ] **Step 1: Reproduce from run `35356582735` evidence**

Extract the evidence ZIP to a temporary directory. Parse its SDN Advanced and Classic files, combine them with the persisted Consolidated current set, update a copy of the repository history, and assert exactly the six approved FixedRefs are newly removed. Reconcile the queue and assert the committed ledger authorizes exactly that event.

- [ ] **Step 2: Run all repository test commands used by CI**

Run:

```bash
python -m tests.test_offline
python -m tests.test_mof_record_diff
python -m tests.test_mof_re_review
python -m unittest
```

Expected: every command passes with the updated totals.

- [ ] **Step 3: Verify Python 3.9 compatibility and repository hygiene**

Run an available Python 3.9 parser/test environment if installed; otherwise statically inspect new annotations and syntax against Python 3.9. Then run:

```bash
git diff --check
git status --short
git diff --stat
git diff -- src tests data/review README.md docs/superpowers
```

Expected: no whitespace errors, no temporary artifacts, and only planned files changed.

- [ ] **Step 4: Produce the handoff patch and Mac application commands**

Create a binary-safe `git diff` patch outside the repository and provide commands to apply it on a clean checkout, run the four CI test commands, review the diff, commit, push, and re-run the failed workflow. Do not push from the read-only environment.
