# METI Manual-First Monitoring Design

**Date:** 2026-09-17
**Status:** Approved
**Repository snapshot reviewed:** `main` at `f0e8c66` (code unchanged from
`8df4cd8`; the six intervening commits update dashboard, heartbeat, and source
audit data only)

## 1. Goal

Complete the METI phase of `sanctions-watch` without evading the Ministry of
Economy, Trade and Industry's automated-access controls.

The target operating model is:

1. A human receives or finds an official METI notice.
2. The notice is registered as `DETECTED`.
3. A human downloads the official PDF in a normal browser.
4. Automation validates, archives, parses, diffs, and prepares review evidence.
5. A human approves or rejects the result.
6. Only an approved result can update the master.

No scheduled job may impersonate a browser, rotate IP addresses, use a proxy,
or otherwise work around METI's refusal of automated requests.

Until METI provides a machine-readable endpoint that is both reachable and
permitted for this use, the system does not claim a 15-minute automatic
publication-detection SLA. The 60-minute processing SLA starts when an operator
records the official notice as `DETECTED`, not when METI first publishes it.

## 2. Current State

The repository already has a mature manual PDF path:

- `src/meti_manual_import.py`
  - validates the PDF header, size, text volume, page count, table schema,
    sequence numbers, expected count, source URL, and SHA-256;
  - archives the official PDF and extracted text;
  - creates normalized records and a record-level diff;
  - writes METI Source Evidence Ledger rows;
  - always stops at `REVIEW_REQUIRED` and does not update the master.
- `src/meti_review.py`
  - verifies the complete snapshot before `APPROVED` or `REJECTED`;
  - verifies the raw PDF hash, report, records, diff, evidence, and source URL;
  - leaves `applied=False` after approval.
- `src/meti_apply_plan.py` and `src/meti_apply.py`
  - classify safe additions, tags, no-ops, weak aliases, collisions, and holds;
  - use an atomic master replacement, transaction journal, dashboard consistency
    verification, and rollback.
- The current production evidence shows a reviewed 835-record METI baseline and
  an applied result with holds.

The repository also has three automated network paths:

1. `.github/workflows/watch-jp.yml` calls `src.watch --sources mof meti` every
   six hours. The METI runner uses an identified monitoring user-agent and
   records WAF refusal as `blocked`.
2. `src/meti_rss.py` and `src/meti_rss_audit.py` implement an official RSS
   sensor. Production history contains successful responses, stale-feed
   findings, timeouts, and HTTP 403 responses. No active RSS workflow exists in
   the reviewed snapshot.
3. `.github/workflows/watch-meti-html.yml` runs every 15 minutes. Its
   `src/meti_html.py` request deliberately uses a normal Chrome user-agent
   because identified automated user-agents receive 403 or empty responses.

The third path conflicts directly with the approved no-circumvention policy.
The first path is transparent but repeatedly performs a request that is already
known to be refused. Neither belongs in the final operating model.

## 3. Design Decisions

### 3.1 Manual-first is the supported METI mode

METI will be represented as a supported manual source, not as a broken
automatic source. `自動取得不可` remains historical evidence but is not the
normal future status label.

Official human-oriented notices, including METI's mail distribution service,
may be used as detection signals. The authoritative evidence remains the
official METI PDF downloaded by an operator.

Operationally, at least one responsible mailbox must subscribe to the official
notice channel. Subscription ownership and coverage are human controls outside
the repository; the recorded `DETECTED` event is the software audit boundary.

### 3.2 Remove all scheduled METI network access

The following active behavior will be retired:

- remove `meti` from the scheduled `watch-jp` source list;
- disable and then remove `watch-meti-html.yml`;
- remove the Chrome-compatible request profile;
- remove the active METI HTML/RSS network runners and their network-specific
  tests after replacement tests cover the manual lifecycle;
- remove `meti` from `src.watch` runner choices so an operator cannot
  accidentally restart the retired crawler.

Historical raw files, source-audit rows, heartbeat rows, dashboard events, and
state files will not be deleted. They remain audit evidence.

### 3.3 Register human detection as a first-class event

Add `src/meti_manual_event.py` with two operator commands:

```text
check   Record a completed manual official-source check with no update found.
detect  Record an official update notice and open a pending intake.
```

`detect` requires:

- operator identifier;
- official METI notice URL;
- notice title;
- detection time, defaulting to the current time;
- optional publication time and note.

The notice URL must be HTTPS on `meti.go.jp` or `www.meti.go.jp`. Unlike the
existing PDF URL validator, it may refer to an official HTML notice.

The command writes an immutable lifecycle event and updates the METI manual
state atomically. It never fetches the URL.

### 3.4 Persist an explicit lifecycle

Add `data/manual/meti/events.csv` as an append-only event ledger with these
columns:

```text
event_id,event_at,state,detection_id,source_url,source_hash,operator,detail
```

Supported lifecycle states are:

```text
CHECKED_NO_CHANGE
DETECTED
MANUAL_FETCH_REQUIRED
FILE_RECEIVED
VALIDATED
DIFFED
REVIEW_REQUIRED
APPROVED
REJECTED
APPLIED
APPLIED_WITH_HOLDS
BLOCKED
```

`data/manual/meti/state.json` remains the current-state snapshot for backward
compatibility. It gains:

- `lifecycle_state`;
- `last_manual_check_at`;
- `last_manual_check_by`;
- `pending_detection`;
- `detection_id`;
- `detected_at`;
- `diffed_at`;
- `sla_due_at`;
- `sla_breached_at`;
- `sla_recovered_at`.

Existing source hash, record path, report path, review, approval, and apply
fields remain unchanged.

### 3.5 Connect the existing intake, review, and apply stages

For routine updates, `meti_manual_import` accepts a `--detection-id` that must
match the open detection. The first historical baseline remains valid without
this field; future non-baseline imports require it.

During a successful import, the existing pipeline records:

```text
FILE_RECEIVED -> VALIDATED -> DIFFED -> REVIEW_REQUIRED
```

On validation or schema failure it records `BLOCKED`, preserves the pending
detection, archives available failure evidence, and leaves the current approved
master and current applied state unchanged.

`meti_review` appends `APPROVED` or `REJECTED`. `meti_apply` appends `APPLIED`
or `APPLIED_WITH_HOLDS` only after its existing transaction succeeds.

Duplicate commands are idempotent. Replaying the same transition for the same
detection and source hash must not create a second ledger event or change the
current state.

### 3.6 Enforce the METI SLA without contacting METI

Add `src/meti_manual_sla.py`. It reads only repository state and never performs
a network request.

The SLA is:

```text
DETECTED -> DIFFED within 60 minutes
```

A lightweight scheduled workflow runs the SLA checker every 15 minutes. It
commits data only when the SLA changes state:

- first breach;
- recovery after a diff is produced;
- a newly blocked intake.

It does not write routine 15-minute heartbeat commits.

### 3.7 Dashboard behavior

The METI source status is derived from the manual lifecycle:

| Lifecycle | Dashboard status |
|---|---|
| `CHECKED_NO_CHANGE`, `APPLIED`, `APPLIED_WITH_HOLDS` | `手動監視（正常）` |
| `DETECTED`, `MANUAL_FETCH_REQUIRED` within SLA | `要確認：正本取得待ち` |
| `REVIEW_REQUIRED` | `要レビュー` |
| `APPROVED` | `要確認：反映待ち` |
| SLA exceeded before `DIFFED` | `重大：更新候補未検証` |
| `BLOCKED` | `重大：正本解析BLOCKED` |
| `REJECTED` | `要確認：取込却下` |

The status row shows the last human check time as `最終チェック` and the
official document publication/effective date as `最終更新` where available.

Lifecycle transitions also create concise rows in
`data/dashboard/changes.csv`. A transition is written once, not once per
scheduled SLA check.

## 4. Data Integrity and Failure Handling

The existing fail-closed rules remain mandatory:

- a blocked or malformed PDF cannot update the master;
- an extraction count of zero or below the lower bound is an error;
- a changed six-column table schema is an error;
- a mismatched expected count is an error;
- an image-only PDF is an error;
- a non-METI or non-HTTPS source URL is an error;
- a review snapshot hash mismatch is an error;
- a pending or rejected review cannot be applied.

Mutable import outputs are committed with `src.persistence.atomic_replace_many`
or an equivalent transaction boundary. The transaction covers the current
state, lifecycle ledger, evidence ledger, records, diff, report, source audit,
and dashboard changes. The raw PDF is append-only evidence: if later processing
fails, it remains available and is referenced by the failure report.

No failure path may replace the last successfully applied master or describe a
stale file as a successful current fetch.

## 5. Files

### Retired from active operation

- `.github/workflows/watch-meti-html.yml`
- `src/meti_html.py`
- `src/meti_rss.py`
- `src/meti_rss_audit.py`
- `src/sources/meti.py`
- their network-sensor unit tests
- the `run_meti` runner and `meti` CLI choice in `src/watch.py`

These files may be removed after the manual lifecycle replacement tests pass.
Git history and all existing data evidence remain available.

### Added

- `src/meti_manual_event.py`
- `src/meti_manual_sla.py`
- `tests/test_meti_manual_event.py`
- `tests/test_meti_manual_sla.py`
- `.github/workflows/watch-meti-manual-sla.yml`
- `data/manual/meti/events.csv` when the first event is recorded

### Modified

- `.github/workflows/watch-jp.yml`
- `src/watch.py`
- `src/notify.py`
- `src/dashboard.py`
- `src/meti_manual_import.py`
- `src/meti_review.py`
- `src/meti_apply.py`
- corresponding existing tests
- `README.md`

## 6. Testing

Tests must prove:

1. No scheduled workflow sends a request to `meti.go.jp`.
2. No active METI code contains or uses a browser-compatible user-agent.
3. The scheduled Japan workflow monitors MOF without METI.
4. Notice and PDF URLs accept only the appropriate official METI HTTPS paths.
5. Detection creates one pending intake and a deterministic event.
6. Invalid state transitions are blocked.
7. Duplicate commands are idempotent.
8. A valid PDF reaches `REVIEW_REQUIRED` but does not alter the master.
9. A malformed, image-only, wrong-count, or changed-schema PDF reaches
   `BLOCKED` and preserves the previous current state.
10. Review and apply append the correct lifecycle events only after their
    existing verification and transaction succeed.
11. The SLA boundary behaves correctly immediately before and after 60 minutes.
12. A breach produces one critical dashboard event and recovery produces one
    recovery event.
13. Dashboard status and the lifecycle snapshot agree.
14. Existing OFAC, MOF, dashboard, screening, METI review, and METI apply tests
    continue to pass.

Verification on the full repository is:

```text
python3 -m unittest
python3 -m tests.test_offline
python3 -m tests.test_mof_record_diff
python3 -m tests.test_mof_re_review
```

The final production check requires:

- a successful `watch-jp` run with MOF only;
- a successful METI manual-SLA workflow run with no network access;
- a dry-run fixture proving detection through `REVIEW_REQUIRED`;
- confirmation that the current METI master, evidence, review, and apply state
  are unchanged by the migration.

## 7. Rollout

1. Commit the design and implementation plan.
2. Add policy tests that fail while the Chrome-UA workflow and scheduled METI
   request remain active.
3. Remove METI from scheduled network monitoring and retire the HTML workflow.
4. Add the manual event ledger and lifecycle state.
5. Connect import, review, and apply to lifecycle events.
6. Add the local-only SLA checker and dashboard mapping.
7. Run the full regression suite and compare pre/post METI master hashes and
   record counts.
8. Run the production workflows and inspect generated audit/dashboard rows.
9. Merge only after the no-network policy test, lifecycle tests, and existing
   sanctions-list tests all pass.

## 8. Completion Criteria

The METI phase is complete when:

- GitHub Actions performs no automated request to a METI host;
- no browser identity is used to bypass METI access controls;
- a human can register an official update notice in one command;
- a human-downloaded official PDF is automatically validated, archived,
  parsed, diffed, and queued for review;
- detection-to-diff SLA breaches become a single critical dashboard event;
- every state from detection through application is auditable;
- failures preserve the last approved and applied generation;
- the existing 835-record applied baseline remains unchanged after migration.
