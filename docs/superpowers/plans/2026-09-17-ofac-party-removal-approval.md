# OFAC Party Removal Approval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an exact, hash-pinned human-approval path for OFAC FixedRef removals and safely apply the approved 2026-09-16 three-party deletion.

**Architecture:** A focused `src/ofac_removal.py` module owns approval schema validation, exact-set authorization, party-aware master effects, logical-state hashes, and the application-audit CSV. `src/watch.py` keeps the existing staged OFAC transaction, calls the module only when `IndexDiff.removed_parties` is non-empty, and passes the audit rows into the existing multi-file atomic persistence generation.

**Tech Stack:** Python 3.12 standard library, CSV/JSON/SHA256, existing `src.master`, `src.ofac_index`, `src.persistence`, and `unittest` suites.

**Spec:** `docs/superpowers/specs/2026-09-17-ofac-party-removal-approval-design.md`

## Global Constraints

- Unapproved, partial, extra, duplicate, malformed, or hash-mismatched removals must fail closed.
- Historical party, alias, and master rows must never be deleted.
- Approval matching uses the Advanced XML SHA256 and exact `(list, party_id)` set.
- Same-name current strong OFAC parties and non-OFAC sources must be preserved.
- Approval application audit must be in the same atomic generation as master, state, party index, heartbeat, diff, and dashboard outputs.
- Production behavior is implemented only after its test has failed for the expected reason.

---

### Task 1: Strict approval parsing and exact-set authorization

**Files:**
- Create: `src/ofac_removal.py`
- Create: `tests/test_ofac_removal.py`

**Interfaces:**
- Produces: `ApprovalError`, `Approval`, `load_and_authorize(path, removed_parties, snapshot_hashes, history) -> list[Approval]`.
- Consumes: `removed_parties: set[tuple[str, str]]`, `snapshot_hashes: dict[str, str]`, and the current in-memory OFAC history.

- [ ] **Step 1: Write failing approval-gate tests**

Add table-driven tests with literal fixtures proving: exact approval passes; missing file, partial set, extra party, wrong hash, duplicate row, wrong header, non-`APPROVED` decision, non-official URL, and party-name/FixedRef mismatch raise `ApprovalError`.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python3 -m unittest tests.test_ofac_removal.OfacRemovalApprovalTest -v`

Expected: import or attribute failure because `src.ofac_removal` and `load_and_authorize` do not exist.

- [ ] **Step 3: Implement the minimum strict loader and authorizer**

Use these exact approval columns:

```python
APPROVAL_FIELDS = [
    "list", "snapshot_sha256", "party_id", "party_name", "decision",
    "approved_by", "approved_at", "official_url", "notes",
]
```

Validate every CSV row, group detected removals per list, select rows pinned to that list's fetched Advanced XML hash, and require exact FixedRef-set equality. Validate the named party against at least one history row for the same `(list, party_id)`.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `python3 -m unittest tests.test_ofac_removal.OfacRemovalApprovalTest -v`

Expected: all approval-gate tests pass.

### Task 2: Party-aware master removal and evidence hashes

**Files:**
- Modify: `src/ofac_removal.py`
- Modify: `tests/test_ofac_removal.py`

**Interfaces:**
- Produces: `master_state_sha256(rows) -> str`, `index_state_sha256(history) -> str`, and `apply_to_master(rows, history, removed_parties, ts) -> tuple[master.Diff, list[dict]]`.
- Effects use actions `deactivated`, `removed_ofac_source`, `kept_current_ofac_party`, `master_row_absent`, or `ofac_source_absent`.

- [ ] **Step 1: Write failing party-aware removal tests**

Cover literal cases for: OFAC-only name becomes inactive; another source remains active; another current strong OFAC party keeps the OFAC source; a weak-only surviving alias does not keep the active source; all aliases of one removed FixedRef are handled; and missing master rows remain auditable without being created.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python3 -m unittest tests.test_ofac_removal.OfacRemovalApplicationTest -v`

Expected: failure because hashing and `apply_to_master` are missing.

- [ ] **Step 3: Implement deterministic hashes and minimal master mutation**

Hash sorted, field-restricted logical records with UTF-8 canonical JSON. Derive candidate match keys only from rows whose `(list, party_id)` is in the approved removal set. Preserve a candidate when any other row with the same match key is `party_current=1`, `alias_current=1`, and `low_quality!=1`. Otherwise remove only OFAC categories/source and reuse `master.DELISTED` when no source remains.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `python3 -m unittest tests.test_ofac_removal.OfacRemovalApplicationTest -v`

Expected: all party-aware application tests pass.

### Task 3: Atomic application-audit persistence

**Files:**
- Modify: `src/ofac_removal.py`
- Modify: `src/watch.py`
- Modify: `tests/test_ofac_removal.py`
- Modify: `tests/test_persistence_atomicity.py`

**Interfaces:**
- Produces: `build_audit_rows(...) -> list[dict]` and `append_audit(path, rows, applied_at) -> None`.
- Extends: `watch._persist_outputs_atomically(..., ofac_removal_audit_rows=None)`.

- [ ] **Step 1: Write failing audit and persistence tests**

Assert the audit header, approver/evidence fields, exact `effects_json`, applied timestamp, and four before/after hashes. Extend the persistence test so a successful generation writes the audit and an injected later replacement failure restores or removes it with every other formal output.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python3 -m unittest tests.test_ofac_removal tests.test_persistence_atomicity -v`

Expected: failures for missing audit functions and persistence argument.

- [ ] **Step 3: Implement audit rows and atomic writer registration**

Use exact audit columns declared in `src.ofac_removal.py`; validate any existing audit header before appending. Register its `FileWrite` with `seed_existing=True` before the state commit marker in `_persist_outputs_atomically`.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `python3 -m unittest tests.test_ofac_removal tests.test_persistence_atomicity -v`

Expected: all focused tests pass with no leaked `*.tmp` or `*.bak` files.

### Task 4: Wire the approval gate into the staged OFAC transaction

**Files:**
- Modify: `src/watch.py`
- Modify: `tests/test_ofac_atomicity.py`
- Create: `data/review/ofac_party_removal_approvals.csv`
- Modify: `README.md`

**Interfaces:**
- Consumes: `load_and_authorize`, `apply_to_master`, state hashes, and audit builders from Tasks 1–3.
- Publishes: `opts["ofac_removal_audit_rows"]` only after all OFAC merge and audit construction succeeds.

- [ ] **Step 1: Write failing OFAC transaction tests**

Add tests proving unapproved removal still leaves caller state/master/heartbeat/options untouched; exact approval commits the updated index and deactivated master rows together; wrong hash and extra approval remain blocked; and a failure after removal application rolls back all staged caller objects.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python3 -m unittest tests.test_ofac_atomicity -v`

Expected: the exact approval case still raises the existing FixedRef disappearance error.

- [ ] **Step 3: Integrate authorization and party-aware application**

Capture persisted master/index logical hashes before mutation, map list labels to `st[key]["advanced_sha256"]`, authorize before passing the old removal gate, merge current strong records, apply only the approved party removals, run weak-alias reconciliation, build audit rows, and publish staged outputs only at the final commit point.

- [ ] **Step 4: Add the approved 2026-09-16 manifest and operator documentation**

Create three `APPROVED` rows pinned to `8f81d70e91b4481bd04ac0acebb896d9743095170250dda89c0704f579fab6b3`, approver `kenmizuno-cpu`, and `https://ofac.treasury.gov/recent-actions/20260916`. Document that future removal events require a new hash-pinned exact set and must never edit an old approval row.

- [ ] **Step 5: Run the focused tests and verify GREEN**

Run: `python3 -m unittest tests.test_ofac_atomicity tests.test_ofac_removal -v`

Expected: all tests pass.

### Task 5: Reproduce the incident and run the complete regression suite

**Files:**
- Verify only; no new production files.

**Interfaces:**
- Consumes: the downloaded run `35167283700` failure-evidence snapshot and the committed repository state.
- Produces: verification evidence for the exact three-party incident.

- [ ] **Step 1: Run an in-memory actual-data reproduction**

Parse the archived SDN Advanced/Classic files from run `35167283700`, combine the saved Consolidated snapshot, update a copy of `ofac_alias_history.csv`, and assert the detected set is exactly `{("SDN", "10982"), ("SDN", "40948"), ("SDN", "51041")}`. Authorize it with the committed ledger and verify five affected master match keys lose OFAC, with no unrelated master mutation.

- [ ] **Step 2: Run every repository test command used by GitHub Actions**

Run:

```bash
python3 -m tests.test_offline
python3 -m tests.test_mof_record_diff
python3 -m tests.test_mof_re_review
python3 -m unittest
```

Expected: `215/215`, `18/18`, `7/7`, and the complete unittest suite all pass.

- [ ] **Step 3: Verify repository hygiene and review the diff**

Run:

```bash
git diff --check
git status --short
git diff --stat
git diff -- src/ofac_removal.py src/watch.py tests data/review README.md
```

Expected: no whitespace errors, no temporary/backup artifacts, and only the planned files changed.

- [ ] **Step 4: Commit the verified feature**

```bash
git add src/ofac_removal.py src/watch.py tests/test_ofac_removal.py tests/test_ofac_atomicity.py tests/test_persistence_atomicity.py data/review/ofac_party_removal_approvals.csv README.md docs/superpowers
git commit -m "fix(ofac): apply approved party removals safely"
```
