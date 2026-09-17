# METI Manual-First Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire all scheduled METI crawling and complete an auditable human-detection, browser-download, automated-validation, review, application, and local-only SLA workflow.

**Architecture:** Existing METI PDF validation, review, application planning, and atomic master application remain authoritative. A new manual lifecycle ledger records detection and state transitions, while a scheduled checker evaluates only repository state and never contacts METI. Scheduled HTML/RSS/index crawlers and the browser-compatible user-agent are removed after replacement tests pass.

**Tech Stack:** Python 3.9-compatible application code (the operator's Mac),
Python 3.12 in GitHub Actions, standard-library `unittest`, `requests` for
non-METI sources, `pdfplumber`, CSV/JSON state files, GitHub Actions, and the
existing `src.persistence.atomic_replace_many` transaction helper.

**Spec:** `docs/superpowers/specs/2026-09-17-meti-manual-first-monitoring-design.md`

## Global Constraints

- No scheduled job may send an HTTP request to a `meti.go.jp` host.
- No active METI code may use a browser-compatible user-agent, proxy, IP rotation, or headless browser.
- The official PDF is downloaded by a human in a normal browser.
- Automation starts at notice registration or local PDF intake.
- `DETECTED -> DIFFED` must complete within 60 minutes of the recorded detection time.
- No code may claim a 15-minute METI publication-detection SLA while METI provides no permitted reachable feed.
- PDF validation, parsing, or review failure must preserve the last applied master.
- Existing METI baseline, evidence, review, and apply fields remain backward compatible.
- Historical METI raw files and audit rows are retained.
- No automatic master import is introduced.
- New code must parse and pass locally on Python 3.9.6 as well as in the
  Python 3.12 Actions runtime; do not use standard-library APIs introduced
  after Python 3.9 without a compatibility fallback.

---

## File Structure

### New files

- `src/meti_manual_event.py` — pure lifecycle transitions, official-notice URL validation, atomic event persistence, and `check`/`detect` CLI.
- `src/meti_manual_sla.py` — local-state-only 60-minute SLA evaluation and transition persistence.
- `tests/test_meti_network_policy.py` — repository policy guard against scheduled METI network access.
- `tests/test_meti_manual_event.py` — lifecycle, URL, idempotency, and persistence tests.
- `tests/test_meti_manual_sla.py` — SLA boundary, breach, and recovery tests.
- `.github/workflows/watch-meti-manual-sla.yml` — 15-minute local-state SLA check with event-only commits.

### Modified files

- `.github/workflows/watch-jp.yml` — run MOF only.
- `src/dashboard.py` — build status rows from supplied heartbeat overrides and map manual METI states.
- `src/source_audit.py` — expose a path-based append function suitable for transaction staging.
- `src/meti_manual_import.py` — require the open detection for routine imports and commit lifecycle/output state atomically.
- `src/meti_review.py` — append approval/rejection lifecycle events atomically with the review decision.
- `src/meti_apply.py` — append apply lifecycle events inside the existing apply transaction.
- `src/watch.py` — remove the METI network runner and CLI choice.
- `src/notify.py` — remove the retired crawler-only METI notification kind.
- `.github/actions/run-watch/action.yml` — remove obsolete METI network notification output.
- `README.md` — document manual-first operation and commands.

### Retired files

- `.github/workflows/watch-meti-html.yml`
- `src/meti_html.py`
- `src/meti_rss.py`
- `src/meti_rss_audit.py`
- `src/sources/meti.py`
- `tests/test_meti_html.py`
- `tests/test_meti_rss.py`
- `tests/test_meti_rss_audit.py`

---

### Task 1: Commit the approved design and plan

**Files:**
- Create: `docs/superpowers/specs/2026-09-17-meti-manual-first-monitoring-design.md`
- Create: `docs/superpowers/plans/2026-09-17-meti-manual-first-monitoring.md`

**Interfaces:**
- Consumes: approved design from the project discussion.
- Produces: immutable implementation reference for all later tasks.

- [ ] **Step 1: Verify the repository and create a feature branch**

Run from the real repository:

```bash
git status --short --branch
```

Expected: `main` is clean. Stop and preserve any unexpected local changes before
continuing. Then run:

```bash
git fetch origin --prune
git switch main
git pull --ff-only origin main
git switch -c fix/meti-manual-first-monitoring
```

Expected: the new branch points at the latest clean `origin/main`.

- [ ] **Step 2: Add the approved spec and this plan**

Copy both reviewed Markdown files into the exact paths listed above. Verify:

```bash
test -s docs/superpowers/specs/2026-09-17-meti-manual-first-monitoring-design.md
test -s docs/superpowers/plans/2026-09-17-meti-manual-first-monitoring.md
grep -n '^\*\*Status:\*\* Approved' \
  docs/superpowers/specs/2026-09-17-meti-manual-first-monitoring-design.md
```

Expected: both files exist and the design status is `Approved`.

- [ ] **Step 3: Commit the documentation baseline**

```bash
git add \
  docs/superpowers/specs/2026-09-17-meti-manual-first-monitoring-design.md \
  docs/superpowers/plans/2026-09-17-meti-manual-first-monitoring.md
git commit -m "docs: define METI manual-first monitoring"
```

Expected: one documentation-only commit.

---

### Task 2: Stop scheduled METI network access with a policy test

**Files:**
- Create: `tests/test_meti_network_policy.py`
- Modify: `.github/workflows/watch-jp.yml`
- Delete: `.github/workflows/watch-meti-html.yml`

**Interfaces:**
- Consumes: current workflow tree.
- Produces: `TestMetiNetworkPolicy`, which later cleanup tasks extend.

- [ ] **Step 1: Write the failing workflow policy tests**

Create `tests/test_meti_network_policy.py`:

```python
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


class TestMetiNetworkPolicy(unittest.TestCase):
    def test_browser_html_workflow_is_retired(self):
        self.assertFalse((WORKFLOWS / "watch-meti-html.yml").exists())

    def test_scheduled_workflows_do_not_run_meti_network_modules(self):
        forbidden = (
            "src.meti_html",
            "src.meti_rss",
            "--sources meti",
            "--sources mof meti",
            "ml_index_release_atom.xml",
            "meti.go.jp",
        )
        paths = sorted({*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")})
        for path in paths:
            text = path.read_text(encoding="utf-8")
            if "schedule:" not in text:
                continue
            for token in forbidden:
                self.assertNotIn(token, text, f"{path}: {token}")

    def test_watch_jp_runs_mof_only(self):
        text = (WORKFLOWS / "watch-jp.yml").read_text(encoding="utf-8")
        self.assertIn("sources: mof", text)
        self.assertNotIn("sources: mof meti", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify the current implementation fails**

```bash
python3 -m unittest tests.test_meti_network_policy
```

Expected: failures identify `watch-meti-html.yml` and `sources: mof meti`.

- [ ] **Step 3: Remove the scheduled HTML workflow and change Japan monitoring to MOF only**

Delete `.github/workflows/watch-meti-html.yml`. In `.github/workflows/watch-jp.yml`, change the composite-action input to:

```yaml
with:
  sources: mof
  slack-webhook: ${{ secrets.SLACK_WEBHOOK_URL }}
```

Update its comment so it describes MOF monitoring only. Do not add a replacement METI network step.

- [ ] **Step 4: Run the policy and existing workflow-related tests**

```bash
python3 -m unittest tests.test_meti_network_policy
python3 -m tests.test_offline
```

Expected: PASS.

- [ ] **Step 5: Commit the emergency safety change**

```bash
git add .github/workflows/watch-jp.yml tests/test_meti_network_policy.py
git rm .github/workflows/watch-meti-html.yml
git commit -m "fix(meti): stop scheduled automated access"
```

---

### Task 3: Implement the pure manual lifecycle model

**Files:**
- Create: `src/meti_manual_event.py`
- Create: `tests/test_meti_manual_event.py`

**Interfaces:**
- Produces: `validate_notice_url(value: str) -> str`.
- Produces: `open_detection(state, events, *, operator, notice_url, title, detected_at, publication_at="", note="") -> tuple[dict, list[dict], str]`.
- Produces: `record_no_change(state, events, *, operator, source_url, checked_at, note="") -> tuple[dict, list[dict]]`.
- Produces: `advance(state, events, *, new_state, event_at, operator, source_url="", source_hash="", detail="", detection_id="") -> tuple[dict, list[dict], dict]`.
- Later tasks consume the same state/event structures; these signatures must remain stable.

- [ ] **Step 1: Write failing tests for URLs, transitions, and idempotency**

Create `tests/test_meti_manual_event.py` with a fixed UTC timestamp:

```python
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from src.meti_manual_event import (
    LifecycleError,
    advance,
    open_detection,
    record_no_change,
    validate_notice_url,
)


NOW = datetime(2026, 9, 17, 6, 0, tzinfo=timezone.utc)


class TestMetiManualEvent(unittest.TestCase):
    def test_validate_official_notice_url(self):
        self.assertEqual(
            validate_notice_url("https://www.meti.go.jp/press/example.html"),
            "https://www.meti.go.jp/press/example.html",
        )

    def test_reject_non_meti_notice_url(self):
        with self.assertRaises(LifecycleError):
            validate_notice_url("https://example.com/notice")

    def test_detection_opens_manual_fetch_required(self):
        state, events, detection_id = open_detection(
            {}, [], operator="kenmizuno-cpu",
            notice_url="https://www.meti.go.jp/press/example.html",
            title="外国ユーザーリストを改正しました",
            detected_at=NOW,
        )
        self.assertEqual(state["lifecycle_state"], "MANUAL_FETCH_REQUIRED")
        self.assertEqual(state["detection_id"], detection_id)
        self.assertEqual([e["state"] for e in events], ["DETECTED", "MANUAL_FETCH_REQUIRED"])

    def test_second_open_detection_is_blocked(self):
        state, events, _ = open_detection(
            {}, [], operator="kenmizuno-cpu",
            notice_url="https://www.meti.go.jp/press/one.html",
            title="更新1", detected_at=NOW,
        )
        with self.assertRaises(LifecycleError):
            open_detection(
                state, events, operator="kenmizuno-cpu",
                notice_url="https://www.meti.go.jp/press/two.html",
                title="更新2", detected_at=NOW,
            )

    def test_same_pending_notice_replay_is_idempotent(self):
        state, events, detection_id = open_detection(
            {}, [], operator="kenmizuno-cpu",
            notice_url="https://www.meti.go.jp/press/one.html",
            title="更新1", detected_at=NOW,
        )
        replay_state, replay_events, replay_id = open_detection(
            state, events, operator="kenmizuno-cpu",
            notice_url="https://www.meti.go.jp/press/one.html",
            title="更新1", detected_at=NOW + timedelta(minutes=1),
        )
        self.assertEqual(replay_id, detection_id)
        self.assertEqual(replay_state, state)
        self.assertEqual(replay_events, events)

    def test_duplicate_transition_is_idempotent(self):
        state, events, detection_id = open_detection(
            {}, [], operator="kenmizuno-cpu",
            notice_url="https://www.meti.go.jp/press/example.html",
            title="更新", detected_at=NOW,
        )
        first_state, first_events, first = advance(
            state, events, new_state="FILE_RECEIVED", event_at=NOW,
            operator="kenmizuno-cpu", detection_id=detection_id,
            source_hash="a" * 64,
        )
        second_state, second_events, second = advance(
            first_state, first_events, new_state="FILE_RECEIVED",
            event_at=NOW + timedelta(minutes=1),
            operator="kenmizuno-cpu", detection_id=detection_id,
            source_hash="a" * 64,
        )
        self.assertEqual(first, second)
        self.assertEqual(first_state, second_state)
        self.assertEqual(first_events, second_events)

    def test_no_change_cannot_close_pending_detection(self):
        state, events, _ = open_detection(
            {}, [], operator="kenmizuno-cpu",
            notice_url="https://www.meti.go.jp/press/example.html",
            title="更新", detected_at=NOW,
        )
        with self.assertRaises(LifecycleError):
            record_no_change(
                state, events, operator="kenmizuno-cpu",
                source_url="https://www.meti.go.jp/policy/anpo/law09-2.html",
                checked_at=NOW,
            )

    def test_invalid_transition_is_blocked(self):
        state, events, detection_id = open_detection(
            {}, [], operator="kenmizuno-cpu",
            notice_url="https://www.meti.go.jp/press/example.html",
            title="更新", detected_at=NOW,
        )
        with self.assertRaises(LifecycleError):
            advance(
                state, events, new_state="APPROVED", event_at=NOW,
                operator="kenmizuno-cpu", detection_id=detection_id,
            )

    def test_retry_after_blocked_starts_a_new_attempt(self):
        state, events, detection_id = open_detection(
            {}, [], operator="kenmizuno-cpu",
            notice_url="https://www.meti.go.jp/press/example.html",
            title="更新", detected_at=NOW,
        )
        state, events, first = advance(
            state, events, new_state="FILE_RECEIVED", event_at=NOW,
            operator="kenmizuno-cpu", detection_id=detection_id,
            source_hash="a" * 64,
        )
        state, events, _ = advance(
            state, events, new_state="BLOCKED", event_at=NOW,
            operator="kenmizuno-cpu", detection_id=detection_id,
            source_hash="a" * 64,
        )
        state, events, retry = advance(
            state, events, new_state="FILE_RECEIVED",
            event_at=NOW + timedelta(minutes=1),
            operator="kenmizuno-cpu", detection_id=detection_id,
            source_hash="a" * 64,
        )
        self.assertNotEqual(first["event_id"], retry["event_id"])
        self.assertEqual(state["pending_detection"]["attempt"], 2)
```

- [ ] **Step 2: Run the lifecycle tests and verify they fail on the missing module**

```bash
python3 -m unittest tests.test_meti_manual_event
```

Expected: import failure for `src.meti_manual_event`.

- [ ] **Step 3: Implement constants, validation, deterministic IDs, and transitions**

In `src/meti_manual_event.py`, define:

```python
EVENT_COLS = [
    "event_id", "event_at", "state", "detection_id",
    "source_url", "source_hash", "operator", "detail",
]

ALLOWED_TRANSITIONS = {
    "": {"CHECKED_NO_CHANGE", "DETECTED"},
    "CHECKED_NO_CHANGE": {"CHECKED_NO_CHANGE", "DETECTED"},
    "DETECTED": {"MANUAL_FETCH_REQUIRED"},
    "MANUAL_FETCH_REQUIRED": {"FILE_RECEIVED", "BLOCKED"},
    "FILE_RECEIVED": {"VALIDATED", "BLOCKED"},
    "VALIDATED": {"DIFFED", "BLOCKED"},
    "DIFFED": {"REVIEW_REQUIRED", "BLOCKED"},
    "REVIEW_REQUIRED": {"APPROVED", "REJECTED", "BLOCKED"},
    "APPROVED": {"APPLIED", "APPLIED_WITH_HOLDS", "BLOCKED"},
    "REJECTED": {"CHECKED_NO_CHANGE", "DETECTED"},
    "APPLIED": {"CHECKED_NO_CHANGE", "DETECTED"},
    "APPLIED_WITH_HOLDS": {"CHECKED_NO_CHANGE", "DETECTED"},
    "BLOCKED": {"FILE_RECEIVED", "REJECTED"},
}
```

Implement `validate_notice_url` with `urllib.parse.urlparse`: require HTTPS,
hostname exactly `meti.go.jp` or `www.meti.go.jp`, no userinfo, no explicit
port, and no fragment. Add table-driven rejection tests for HTTP, a lookalike
suffix such as `www.meti.go.jp.example.com`, userinfo, an explicit port, and a
fragment.

Use UTC `YYYY-MM-DDTHH:MM:SSZ` timestamps. For detection-bound lifecycle
events, build `event_id` as SHA-256 of the semantic transition key:

```text
detection_id|state|source_hash|attempt
```

This makes a replay idempotent even if the retry occurs at a later wall-clock
time. Set `pending_detection.attempt=0` at detection. Entering
`FILE_RECEIVED` from `MANUAL_FETCH_REQUIRED` increments it to 1; entering
`FILE_RECEIVED` from `BLOCKED` increments it again for a genuine retry. A replay
while already in the same attempt returns the existing event. Include the
attempt number in `detail` for audit readability. For `CHECKED_NO_CHANGE`,
which is a repeatable observation rather than a detection-bound transition,
use `state|source_url|event_at|operator` instead.
`open_detection` generates `detection_id` from the canonical notice URL, title,
and detection timestamp, writes `DETECTED` then `MANUAL_FETCH_REQUIRED`, and
sets `sla_due_at` to detection time plus 60 minutes. `advance` returns the
existing semantic event without mutation when its key already exists. A second
command for the same pending canonical URL and title returns the existing
detection even if the replay's wall-clock time differs; a different detection
is rejected while one is open.

Preserve all pre-existing import/review/apply fields when updating the snapshot.
`open_detection` resets only the new detection's `diffed_at`,
`sla_breached_at`, and `sla_recovered_at`, and stores this structure:

```python
state["pending_detection"] = {
    "detection_id": detection_id,
    "notice_url": canonical_notice_url,
    "title": title,
    "publication_at": publication_at,
    "note": note,
    "attempt": 0,
}
```

The `DETECTED` event stores title, publication time, and note as compact,
sorted-key JSON in `detail`. `record_no_change` sets `lifecycle_state`,
`last_manual_check_at`, and `last_manual_check_by`, but refuses to run while
`pending_detection` is non-empty. `BLOCKED` retains the pending detection for a
retry. `REJECTED`, `APPLIED`, and `APPLIED_WITH_HOLDS` append their event first
and then clear `pending_detection`; historical identity remains in the
append-only ledger.

- [ ] **Step 4: Run the lifecycle tests**

```bash
python3 -m unittest tests.test_meti_manual_event
```

Expected: PASS.

- [ ] **Step 5: Commit the lifecycle model**

```bash
git add src/meti_manual_event.py tests/test_meti_manual_event.py
git commit -m "feat(meti): add manual lifecycle model"
```

---

### Task 4: Add atomic lifecycle persistence, CLI, heartbeat, and dashboard projection

**Files:**
- Modify: `src/meti_manual_event.py`
- Modify: `src/dashboard.py`
- Modify: `src/source_audit.py`
- Modify: `tests/test_meti_manual_event.py`
- Modify: `tests/test_dashboard_screening.py`

**Interfaces:**
- Consumes: lifecycle functions from Task 3.
- Produces: `load_events(path: Path) -> list[dict]`.
- Produces: `LifecyclePaths.for_root(root: Path, now: datetime) -> LifecyclePaths` so tests and CLIs can redirect every output beneath a temporary root; the object retains `root` plus all six mutable target paths.
- Produces: `persist_lifecycle(*, state: dict, events: list[dict], heartbeat_status: str, dashboard_event: list[str] | None, audit_row: dict | None, now: datetime, paths: LifecyclePaths) -> None`.
- Produces: `main(argv: list[str] | None = None, *, paths: LifecyclePaths | None = None, now: datetime | None = None) -> int`; dependency injection is test-only, and omitted values resolve beneath the repository root and current UTC time.
- Produces: CLI subcommands `python3 -m src.meti_manual_event check` and `python3 -m src.meti_manual_event detect` with the arguments shown in Step 5.
- Produces: `dashboard.build_status_rows(root: Path, hb: list[dict], st: dict, *, now: datetime | None = None, meti_state: dict | None = None) -> list[list[str]]`, `dashboard.write_status_rows(path: Path, rows: list[list[str]]) -> Path`, and `dashboard.prepend_change_rows(path: Path, rows: list[list], *, when: str) -> Path`.
- Produces: `source_audit.append_rows(path: Path, entries: list[dict], *, now: datetime) -> Path | None` for transaction staging.

- [ ] **Step 1: Add failing atomic persistence and CLI tests**

Extend `tests/test_meti_manual_event.py` using `tempfile.TemporaryDirectory` and
`unittest.mock.patch`. Test that:

```python
def test_detect_persists_state_event_heartbeat_status_and_audit_atomically(self):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        paths = LifecyclePaths.for_root(root, NOW)
        rc = main([
            "detect",
            "--operator", "kenmizuno-cpu",
            "--notice-url", "https://www.meti.go.jp/press/example.html",
            "--title", "外国ユーザーリストを改正しました",
            "--detected-at", "2026-09-17T06:00:00Z",
        ], paths=paths, now=NOW)
        self.assertEqual(rc, 0)
        state = json.loads(paths.state.read_text(encoding="utf-8"))
        with paths.events.open(encoding="utf-8", newline="") as f:
            events = list(csv.DictReader(f))
        self.assertEqual(state["lifecycle_state"], "MANUAL_FETCH_REQUIRED")
        self.assertEqual(events[-1]["detection_id"], state["detection_id"])
        for path in (
            paths.heartbeat, paths.status, paths.changes, paths.audit,
        ):
            self.assertTrue(path.is_file(), str(path))

def test_atomic_failure_preserves_every_existing_file(self):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        paths = LifecyclePaths.for_root(root, NOW)
        seed_valid_generation(paths)
        before = {
            path: path.read_bytes()
            for path in paths.transaction_targets()
        }
        real_replace = persistence.os.replace
        calls = 0

        def fail_second_replace(src, dst):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected commit failure")
            return real_replace(src, dst)

        with patch.object(persistence.os, "replace", side_effect=fail_second_replace):
            with self.assertRaises(OSError):
                record_detection_fixture(paths, now=NOW)

        self.assertEqual(
            before,
            {path: path.read_bytes() for path in paths.transaction_targets()},
        )
```

Define `seed_valid_generation` in the test file to write valid headers using
`EVENT_COLS`, `state.HB_COLS`, `dashboard.STATUS_COLS`,
`dashboard.CHANGE_COLS`, and `source_audit.AUDIT_COLS`.
`record_detection_fixture` calls `main` with the same explicit arguments as the
first test. These helpers contain no production logic.

Add a dashboard test asserting heartbeat status mappings:

```python
for raw, label in {
    "manual_ok": "手動監視（正常）",
    "manual_pending": "要確認：正本取得待ち",
    "manual_review": "要レビュー",
    "manual_approved": "要確認：反映待ち",
    "manual_critical": "重大：更新候補未検証",
    "manual_blocked": "重大：正本解析BLOCKED",
    "manual_rejected": "要確認：取込却下",
}.items():
    self.assertEqual(STATUS_LABEL[raw], label)
```

Add table-driven dashboard projection assertions for these lifecycle states:

| State input | Additional input | Expected METI status |
|---|---|---|
| `CHECKED_NO_CHANGE` | `last_manual_check_at=2026-09-17T06:00:00Z` | `手動監視（正常）` |
| `MANUAL_FETCH_REQUIRED` | `sla_due_at` later than injected `now` | `要確認：正本取得待ち` |
| `REVIEW_REQUIRED` | `diffed_at` present | `要レビュー` |
| `APPROVED` | `diffed_at` present | `要確認：反映待ち` |
| `MANUAL_FETCH_REQUIRED` | `sla_breached_at` present | `重大：更新候補未検証` |
| `BLOCKED` | none | `重大：正本解析BLOCKED` |
| `REJECTED` | none | `要確認：取込却下` |

For each case, call `build_status_rows` with an injected UTC `now` and
`meti_state`, select the row whose first column is `経済産業省`, and assert the
second column. Also assert that `last_manual_check_at` populates `最終チェック` and
`publication_date`/`effective_date` populates `最終更新` without changing the
existing OFAC/MOF row order.

- [ ] **Step 2: Run the new tests and verify failure**

```bash
python3 -m unittest tests.test_meti_manual_event tests.test_dashboard_screening
```

Expected: failures for missing persistence functions and status labels.

- [ ] **Step 3: Extract path-based writers**

In `src/source_audit.py`, add:

```python
def append_rows(
    path: Path,
    entries: list[dict],
    *,
    now: datetime,
) -> Path | None:
    if not entries:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size:
        with path.open("r", encoding="utf-8", newline="") as f:
            actual = next(csv.reader(f), [])
        if actual != AUDIT_COLS:
            raise AuditSchemaError(
                "Source Audit Ledger の列構造が想定と異なる。"
                f" expected={AUDIT_COLS} actual={actual}"
            )
    new = not path.exists() or path.stat().st_size == 0
    checked_at = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=AUDIT_COLS, extrasaction="ignore", lineterminator="\n"
        )
        if new:
            writer.writeheader()
        for source_row in entries:
            row = {column: "" for column in AUDIT_COLS}
            row.update(source_row)
            row["checked_at"] = checked_at
            writer.writerow(row)
    return path
```

In `src/dashboard.py`, move the current source-order loop from `write_status`
into `build_status_rows(root, hb, st, *, now=None, meti_state=None)`. It returns
one list per source without the header. When `meti_state` is omitted, load
`data/manual/meti/state.json` if it exists so later MOF/OFAC runs retain the
manual METI projection. When it is supplied, use that in-memory generation.
For supplied heartbeat rows, inject `checked_at=now` when missing and select the
newer row by timestamp instead of ignoring it merely because historical data
exists. The METI row uses `last_manual_check_at` for `最終チェック` and the
manual state's `effective_date`, `publication_date`, or pending detection's
`publication_at` (in that precedence order) for `最終更新`.

Add `write_status_rows(path, rows)` that writes `STATUS_COLS` followed by those
rows using UTF-8 and `lineterminator="\n"`. Extract the body of
`append_changes` into `prepend_change_rows(path, rows, *, when)` so a staged
transaction path can preserve deduplication and `MAX_CHANGES`; retain
`append_changes(root, diff_rows, when=when)` as its backward-compatible wrapper. Make existing
`write_status` call the new helpers. Keep all current fallback rules and source
order unchanged.

- [ ] **Step 4: Implement atomic lifecycle persistence**

Use `persistence.atomic_replace_many` with `seed_existing=True` for append-only
CSV targets. The transaction includes:

```text
data/manual/meti/state.json
data/manual/meti/events.csv
data/heartbeat/YYYY-MM.csv
data/dashboard/status.csv
data/dashboard/changes.csv
data/source_audit/YYYY-MM.csv
```

Use `state.append_heartbeat` for the staged heartbeat file,
`dashboard.write_status_rows` for the staged status file, and
`dashboard.prepend_change_rows` for staged changes, and
`source_audit.append_rows` for the staged audit file. Build status rows with the
new in-memory manual state rather than re-reading the not-yet-committed target.
Represent a lifecycle dashboard event with exactly the five pre-timestamp
columns expected by `append_changes`:

```python
["経済産業省", kind, "外国ユーザーリスト", before, after]
```

Pass the event time to `prepend_change_rows` as a JST
`YYYY-MM-DD HH:MM:SS` string.
The `detect` command writes heartbeat status `manual_pending`; `check` writes
`manual_ok`.

- [ ] **Step 5: Implement the CLI**

Support these exact forms:

```bash
python3 -m src.meti_manual_event check \
  --operator kenmizuno-cpu \
  --source-url https://www.meti.go.jp/policy/anpo/law09-2.html \
  --note "公式ページをブラウザ確認、更新なし"

python3 -m src.meti_manual_event detect \
  --operator kenmizuno-cpu \
  --notice-url https://www.meti.go.jp/press/example.html \
  --title "外国ユーザーリストを改正しました" \
  --publication-at 2026-09-17T06:00:00Z
```

Both commands also accept deterministic test/incident timestamps:
`check --checked-at <UTC-ISO>` and `detect --detected-at <UTC-ISO>`. When those
arguments are omitted, use the injected/current UTC time. Print
`DETECTION_ID=<id>` for `detect`. Validate the official HTTPS host for both
commands, but do not import a network client, invoke a subprocess, or open any
URL.

- [ ] **Step 6: Run focused tests**

```bash
python3 -m unittest \
  tests.test_meti_manual_event \
  tests.test_dashboard_screening \
  tests.test_source_audit_url_safety \
  tests.test_persistence_atomicity
```

Expected: PASS.

- [ ] **Step 7: Commit the operator boundary**

```bash
git add \
  src/meti_manual_event.py src/dashboard.py src/source_audit.py \
  tests/test_meti_manual_event.py tests/test_dashboard_screening.py
git commit -m "feat(meti): persist manual detection lifecycle"
```

---

### Task 5: Connect PDF intake to the open detection atomically

**Files:**
- Modify: `src/meti_manual_import.py`
- Modify: `tests/test_meti_manual_import.py`
- Create: `tests/test_meti_manual_import_lifecycle.py`

**Interfaces:**
- Consumes: `advance`, `load_events`, and transaction writers from `src.meti_manual_event`.
- Produces: `--detection-id` CLI input for routine imports.
- Produces: lifecycle sequence `FILE_RECEIVED -> VALIDATED -> DIFFED -> REVIEW_REQUIRED`.

- [ ] **Step 1: Write failing detection-link tests**

Create `tests/test_meti_manual_import_lifecycle.py` with class
`TestMetiManualImportLifecycle`. Use temporary paths and a synthetic parsed
record set by patching `extract_pdf`; do not require a real PDF fixture for
lifecycle behavior.

Implement seven tests with the following exact setup and assertions:

| Test | Setup | Required assertion |
|---|---|---|
| `test_routine_import_requires_matching_open_detection` | Existing `current_source_hash`; state is `MANUAL_FETCH_REQUIRED`; CLI receives a different ID | exit `2`; state/events bytes unchanged |
| `test_matching_detection_reaches_review_required` | Matching open ID; patched `extract_pdf` returns 100 valid records | exit `0`; final state `REVIEW_REQUIRED`; four expected events |
| `test_validation_failure_records_blocked_and_preserves_applied_fields` | Matching ID; patched extractor raises `PdfStructureError` | exit `2`; lifecycle `BLOCKED`; prior applied hash/path fields unchanged |
| `test_persistence_failure_rolls_back_current_state_and_ledgers` | Matching ID; fail the second `os.replace` | exception; all transaction-target bytes equal their pre-run values |
| `test_existing_historical_baseline_without_detection_remains_readable` | No `current_source_hash`; no detection ID; patched extractor returns 100 records | exit `0`; baseline report created and no master file written |
| `test_same_hash_for_new_detection_is_still_diffed` | Open detection B; downloaded hash equals the last applied detection A | exit `0`; detection B reaches `REVIEW_REQUIRED` with a zero record diff |
| `test_same_hash_replay_for_same_detection_is_byte_idempotent` | Detection and source hash already at `REVIEW_REQUIRED`; replay identical command | exit `0`; every transaction target remains byte-identical |

For every fixture, create a valid local PDF (`b"%PDF-1.7\n" + b"x" * 20000`),
patch module path constants below the temporary root, and pass an official PDF
URL. Use `Record` objects numbered 1 through 100 so existing minimum-record and
sequence guards remain active.

For the success case, assert the appended states are exactly:

```python
["FILE_RECEIVED", "VALIDATED", "DIFFED", "REVIEW_REQUIRED"]
```

and `state["diffed_at"]` is populated while `approved` and `applied` remain
`False`.

Extend the existing malformed-header, image-only, wrong-count, and changed-table
schema tests so each also supplies a matching open detection and asserts a
single `BLOCKED` event. In every case, compare the applied/master fields and the
previous records/evidence files byte-for-byte before and after the failed run.
Extend PDF URL tests to reject HTTP, lookalike hosts, userinfo, explicit ports,
fragments, paths outside `/policy/anpo/`, and non-PDF paths while retaining the
two exact official hostnames.

- [ ] **Step 2: Run the new tests and verify failure**

```bash
python3 -m unittest tests.test_meti_manual_import_lifecycle
```

Expected: failure because `--detection-id` and lifecycle integration do not exist.

- [ ] **Step 3: Add detection validation before mutating repository files**

Add `--detection-id`. For a non-baseline import, require all of:

```python
state["lifecycle_state"] == "MANUAL_FETCH_REQUIRED"
state["detection_id"] == args.detection_id
state["pending_detection"]["detection_id"] == args.detection_id
```

Do not allow an empty ID once `current_source_hash` already exists. Preserve
duplicate-SHA idempotency only when the same detection already reached
`REVIEW_REQUIRED` or later. A new detection with the same PDF hash still runs
validation and records `DIFFED`; otherwise it could remain pending until the
SLA breaches.

Keep `validate_source_url` restricted to HTTPS PDF paths beneath
`/policy/anpo/` on the two exact METI hostnames, and add explicit-port rejection
to match the notice-URL safety boundary.

- [ ] **Step 4: Stage the complete import generation**

Refactor direct writers into writer functions that accept a destination path.
Compute records, diff, evidence rows, report, updated state, lifecycle rows,
dashboard rows, heartbeat row, and audit row in memory first. Build the new
state from a copy of the existing snapshot so fields outside the import stage
are not accidentally discarded; explicitly replace the current candidate
paths/hash/count and reset its review decision to `REVIEW_REQUIRED`,
`approved=False`, and `applied=False`. Commit this exact mutable generation with
`persistence.atomic_replace_many`:

```text
data/manual/meti/state.json
data/manual/meti/events.csv
data/manual/meti/text/<generation>.txt
data/manual/meti/records/<generation>.csv
data/manual/meti/diffs/<generation>.csv
data/manual/meti/reports/<generation>.json
data/evidence/meti_foreign_user_list.csv
data/heartbeat/YYYY-MM.csv
data/dashboard/status.csv
data/dashboard/changes.csv
data/source_audit/YYYY-MM.csv
```

The raw PDF copy remains append-only evidence and is created before parsing.
On parse failure, append `BLOCKED` atomically with its report, dashboard event,
audit row, and heartbeat status `manual_blocked`. Do not replace the previous
records/evidence generation. A transaction failure may leave the content-
addressed raw PDF, but none of the mutable generation may advance.

- [ ] **Step 5: Run import and persistence tests**

```bash
python3 -m unittest \
  tests.test_meti_manual_import \
  tests.test_meti_manual_import_lifecycle \
  tests.test_persistence_atomicity
```

Expected: PASS.

- [ ] **Step 6: Commit intake integration**

```bash
git add \
  src/meti_manual_import.py \
  tests/test_meti_manual_import.py \
  tests/test_meti_manual_import_lifecycle.py
git commit -m "feat(meti): bind manual PDF intake to detection"
```

---

### Task 6: Connect review and apply to lifecycle events

**Files:**
- Modify: `src/meti_review.py`
- Modify: `src/meti_apply.py`
- Modify: `tests/test_meti_review.py`
- Modify: `tests/test_meti_apply.py`

**Interfaces:**
- Consumes: lifecycle `advance` and event ledger.
- Produces: `APPROVED`/`REJECTED` after review persistence succeeds.
- Produces: `APPLIED`/`APPLIED_WITH_HOLDS` inside the existing apply transaction.
- Produces: matching heartbeat/status projections for each durable review/apply state.

- [ ] **Step 1: Write failing review lifecycle tests**

Add assertions to `tests/test_meti_review.py`:

```python
def assert_last_lifecycle_event(self, path, expected_state, expected_hash):
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    self.assertEqual(rows[-1]["state"], expected_state)
    self.assertEqual(rows[-1]["source_hash"], expected_hash)
    self.assertEqual(rows[-1]["operator"], "reviewer")
```

Use the existing `make_fixture` and `patches` helpers. Extend the fixture state
with a fixed `detection_id` and `lifecycle_state="REVIEW_REQUIRED"`, patch the
event-ledger path, call `mr.decide`, and invoke `assert_last_lifecycle_event`
with `APPROVED` and `REJECTED` respectively. Assert the corresponding heartbeat
status (`manual_approved` or `manual_rejected`) and METI dashboard label. For the
failure case, inject an `os.replace` error during the review transaction and
compare the state, review ledger, lifecycle ledger, heartbeat, status, audit,
and dashboard-change bytes with copies captured before the call.

- [ ] **Step 2: Write failing apply lifecycle tests**

Add to `tests/test_meti_apply.py`:

```python
def read_last_state(path):
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))[-1]["state"]
```

Build one existing apply fixture with a weak hold and assert
`read_last_state(events_path) == "APPLIED_WITH_HOLDS"`. Build a no-hold fixture
and assert `APPLIED`. Assert heartbeat `manual_ok` and dashboard status
`手動監視（正常）` in both cases. For rollback, capture event-ledger, heartbeat,
status, and master bytes; inject a failure after the master replacement, call
`apply_verified`, and assert every captured file is restored. Reuse existing
`plan_row`, `master_row`, and transaction-injection helpers rather than
duplicating plan semantics. Select `APPLIED_WITH_HOLDS` when any of
`held_weak_alias`, `held_multi_party`, `held_review`, `held_invalid`, or
`legacy_unresolved` is nonzero; otherwise select `APPLIED`.

- [ ] **Step 3: Run focused tests and verify failure**

```bash
python3 -m unittest tests.test_meti_review tests.test_meti_apply
```

Expected: lifecycle-event assertions fail.

- [ ] **Step 4: Make review decision and lifecycle event one transaction**

Build the updated review state, review ledger row, review artifact, lifecycle
event, source-audit row, heartbeat row, status projection, and dashboard-change
row before persistence. Use heartbeat `manual_approved` for approval and
`manual_rejected` for rejection. Commit them with `atomic_replace_many`. Do not
append `APPROVED` or `REJECTED` before `verify_snapshot` succeeds.

- [ ] **Step 5: Include the event ledger in the existing apply rollback set**

Add `data/manual/meti/events.csv`, the current monthly heartbeat, and dashboard
status to the files captured before apply. Stage the final lifecycle event with
state, master, dashboard, screening, apply ledger, source audit, heartbeat
`manual_ok`, dashboard status, and dashboard changes. The transaction journal
must reach `COMMITTED` before reporting success.

Compute one `apply_state` before building any artifact. Use it consistently for
`state.apply_status`, application JSON/ledger `status`, lifecycle event, source-
audit status, dashboard-change wording, and printed result. Existing behavior
that always labels a no-hold application `APPLIED_WITH_HOLDS` must be corrected
to `APPLIED`.

- [ ] **Step 6: Run review/apply regression tests**

```bash
python3 -m unittest \
  tests.test_meti_review \
  tests.test_meti_apply_plan \
  tests.test_meti_apply \
  tests.test_meti_reconciliation_audit
```

Expected: PASS.

- [ ] **Step 7: Commit review/apply integration**

```bash
git add \
  src/meti_review.py src/meti_apply.py \
  tests/test_meti_review.py tests/test_meti_apply.py
git commit -m "feat(meti): audit review and apply lifecycle"
```

---

### Task 7: Add local-only SLA enforcement

**Files:**
- Create: `src/meti_manual_sla.py`
- Create: `tests/test_meti_manual_sla.py`
- Create: `.github/workflows/watch-meti-manual-sla.yml`
- Modify: `tests/test_meti_network_policy.py`

**Interfaces:**
- Consumes: manual state and persistence interfaces.
- Produces: `evaluate(state: dict, now: datetime) -> str` returning `healthy`, `pending`, `breached`, `recovered`, or `blocked`.
- Produces: `run(*, paths: LifecyclePaths, now: datetime) -> int` and `main(argv: list[str] | None = None, *, paths: LifecyclePaths | None = None, now: datetime | None = None) -> int`.
- Produces: CLI exit `0` for healthy/pending/recovered, `2` for breached or blocked, and `1` for invalid state.

- [ ] **Step 1: Write failing SLA boundary tests**

Create `tests/test_meti_manual_sla.py`:

```python
from datetime import datetime, timedelta, timezone
import unittest

from src.meti_manual_sla import evaluate


DETECTED = datetime(2026, 9, 17, 6, 0, tzinfo=timezone.utc)


class TestMetiManualSla(unittest.TestCase):
    def state(self):
        return {
            "lifecycle_state": "MANUAL_FETCH_REQUIRED",
            "detected_at": "2026-09-17T06:00:00Z",
            "sla_due_at": "2026-09-17T07:00:00Z",
        }

    def test_one_second_before_due_is_pending(self):
        self.assertEqual(evaluate(self.state(), DETECTED + timedelta(minutes=60, seconds=-1)), "pending")

    def test_exact_due_time_is_breached(self):
        self.assertEqual(evaluate(self.state(), DETECTED + timedelta(minutes=60)), "breached")

    def test_diffed_before_due_is_healthy(self):
        state = self.state()
        state.update(lifecycle_state="REVIEW_REQUIRED", diffed_at="2026-09-17T06:40:00Z")
        self.assertEqual(evaluate(state, DETECTED + timedelta(minutes=60)), "healthy")

    def test_blocked_is_blocked(self):
        state = self.state()
        state["lifecycle_state"] = "BLOCKED"
        self.assertEqual(evaluate(state, DETECTED + timedelta(minutes=30)), "blocked")

    def test_closed_check_without_sla_timestamps_is_healthy(self):
        self.assertEqual(
            evaluate({"lifecycle_state": "CHECKED_NO_CHANGE"}, DETECTED),
            "healthy",
        )

    def test_first_post_breach_diff_is_recovered(self):
        state = self.state()
        state.update(
            lifecycle_state="REVIEW_REQUIRED",
            diffed_at="2026-09-17T07:05:00Z",
            sla_breached_at="2026-09-17T07:00:00Z",
            sla_recovered_at="",
        )
        self.assertEqual(
            evaluate(state, DETECTED + timedelta(minutes=66)),
            "recovered",
        )

    def test_recorded_recovery_is_healthy(self):
        state = self.state()
        state.update(
            lifecycle_state="REVIEW_REQUIRED",
            diffed_at="2026-09-17T07:05:00Z",
            sla_breached_at="2026-09-17T07:00:00Z",
            sla_recovered_at="2026-09-17T07:06:00Z",
        )
        self.assertEqual(
            evaluate(state, DETECTED + timedelta(minutes=67)),
            "healthy",
        )
```

Add persistence tests using `LifecyclePaths.for_root`:

1. Seed a valid pending detection whose due time equals the injected `now`.
   `run` returns `2`, sets `sla_breached_at`, writes heartbeat
   `manual_critical`, and prepends exactly one critical dashboard-change row.
2. Capture all transaction-target bytes, call `run` again one minute later,
   and assert exit `2` with every byte unchanged.
3. Change only the state to `REVIEW_REQUIRED` with `diffed_at` after the breach.
   `run` returns `0`, sets `sla_recovered_at`, writes heartbeat
   `manual_review`, and prepends one recovery dashboard-change row.
4. Capture bytes and replay the recovered state; assert exit `0` and no writes.
5. Seed `BLOCKED`; assert exit `2` and no duplicate lifecycle or dashboard row,
   because import already persisted the blocked transition atomically.

- [ ] **Step 2: Run the SLA tests and verify missing-module failure**

```bash
python3 -m unittest tests.test_meti_manual_sla
```

Expected: import failure for `src.meti_manual_sla`.

- [ ] **Step 3: Implement evaluation and transition-only persistence**

`evaluate` parses only state timestamps and follows this order:

1. an unknown/missing lifecycle state raises `SlaStateError`;
2. `BLOCKED` returns `blocked` even if no SLA timestamps exist;
3. a populated `diffed_at` with an earlier breach and no recovery returns
   `recovered`;
4. any populated `diffed_at` otherwise returns `healthy`;
5. legacy-compatible closed states `CHECKED_NO_CHANGE`, `REJECTED`, `APPLIED`,
   and `APPLIED_WITH_HOLDS` return `healthy`;
6. `DETECTED`, `MANUAL_FETCH_REQUIRED`, `FILE_RECEIVED`, and `VALIDATED`
   require valid `detected_at` and `sla_due_at`; before the due time they return
   `pending`, and at or after it they return `breached`;
7. `DIFFED`, `REVIEW_REQUIRED`, or `APPROVED` without a valid `diffed_at`
   raises `SlaStateError`.

The CLI performs no network operations. At first breach it sets
`sla_breached_at`, projects heartbeat `manual_critical`, rewrites status, and
adds one dashboard-change row in one `atomic_replace_many` transaction. On
first recovery it sets `sla_recovered_at`, projects the lifecycle-appropriate
heartbeat (`manual_review` for `REVIEW_REQUIRED`), and adds one recovery row in
one transaction. Breach and recovery do not invent additional lifecycle states
or duplicate `events.csv`; the already-recorded `DIFFED` event is the recovery
evidence. Repeated checks with no transition produce zero file writes.

- [ ] **Step 4: Add the scheduled workflow**

Create `.github/workflows/watch-meti-manual-sla.yml` with a read-only pull
request verification path and the scheduled enforcement path:

```yaml
name: watch-meti-manual-sla
on:
  schedule:
    - cron: "5,20,35,50 * * * *"
  workflow_dispatch:
  pull_request:
permissions:
  contents: read
```

Add a `verify` job for every event: check out the exact ref, set up Python 3.12,
and run `tests.test_meti_manual_sla`, `tests.test_meti_network_policy`, and
`tests.test_meti_manual_event`. Add a second `enforce` job guarded by
`github.event_name != 'pull_request'` and `needs: verify`, with job-level
`contents: write` and
job-level `concurrency.group: sanctions-watch-commit` and
`concurrency.cancel-in-progress: false` so it serializes with existing writer
workflows without delaying the read-only PR job. It runs
`python -m src.meti_manual_sla` with `continue-on-error: true`, commits `data/`
only when changed, pushes with the existing three-attempt rebase loop, and then
fails if the SLA step outcome was not `success`. The pull-request job never
writes or pushes, which allows this new workflow to be validated before it
exists on the default branch.

- [ ] **Step 5: Extend the policy test**

Assert the new workflow contains no `http`, `curl`, `wget`, `requests`,
`meti.go.jp`, `src.meti_html`, or `src.meti_rss` token. It may use GitHub's own
checkout/setup actions and `git pull`/`git push`. Parse imports in
`src/meti_manual_event.py` and `src/meti_manual_sla.py` with `ast` and reject
network-capable modules `requests`, `httpx`, `urllib.request`, `socket`, and
`subprocess`; `urllib.parse` remains allowed for URL validation.

- [ ] **Step 6: Run SLA and policy tests**

```bash
python3 -m unittest \
  tests.test_meti_manual_sla \
  tests.test_meti_network_policy \
  tests.test_meti_manual_event
```

Expected: PASS.

- [ ] **Step 7: Commit SLA monitoring**

```bash
git add \
  src/meti_manual_sla.py \
  tests/test_meti_manual_sla.py \
  tests/test_meti_network_policy.py \
  .github/workflows/watch-meti-manual-sla.yml
git commit -m "feat(meti): enforce manual intake SLA"
```

---

### Task 8: Remove retired crawlers and update active interfaces

**Files:**
- Delete: `src/meti_html.py`
- Delete: `src/meti_rss.py`
- Delete: `src/meti_rss_audit.py`
- Delete: `src/sources/meti.py`
- Delete: `tests/test_meti_html.py`
- Delete: `tests/test_meti_rss.py`
- Delete: `tests/test_meti_rss_audit.py`
- Modify: `src/watch.py`
- Modify: `src/notify.py`
- Modify: `.github/actions/run-watch/action.yml`
- Modify: `README.md`
- Modify: `tests/test_offline.py`
- Modify: `tests/test_meti_network_policy.py`

**Interfaces:**
- Consumes: replacement manual lifecycle and SLA workflows.
- Produces: `src.watch` choices `all`, `mof`, and `ofac` only; `all` means MOF and OFAC.

- [ ] **Step 1: Add failing active-code policy assertions**

Extend `tests/test_meti_network_policy.py`:

```python
def test_retired_network_modules_are_absent(self):
    for rel in (
        "src/meti_html.py", "src/meti_rss.py", "src/meti_rss_audit.py",
        "src/sources/meti.py",
    ):
        self.assertFalse((ROOT / rel).exists(), rel)

def test_active_python_has_no_browser_compatible_meti_profile(self):
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("browser_compatible_chrome", text, str(path))
        self.assertNotIn("Chrome/146.0.0.0", text, str(path))
```

- [ ] **Step 2: Run the policy test and verify it fails**

```bash
python3 -m unittest tests.test_meti_network_policy
```

Expected: failures list the retired modules and browser profile.

- [ ] **Step 3: Remove retired modules and tests**

Use `git rm` for the seven files listed under **Delete**. Do not remove any
`data/raw/meti_*`, `data/meti_html`, `data/meti_rss`, `data/source_audit`,
`data/heartbeat`, or dashboard history.

- [ ] **Step 4: Remove the METI runner from `src.watch`**

Remove the `meti` source import, `run_meti`, the `meti` runner entry, METI schema
exception branch, `meti_notice`, and `meti_updated` output. Update the module
usage text and argparse choices. Preserve MOF and OFAC behavior byte-for-byte
outside necessary import/control-flow edits.

- [ ] **Step 5: Remove obsolete notification plumbing**

Delete the `経産省の更新を通知` step from
`.github/actions/run-watch/action.yml` and change its `sources` input description
to `mof / ofac / all`. In `src/notify.py`, remove the crawler-only `meti` choice
and message branch; manual lifecycle transitions are already projected to the
dashboard.

In `tests/test_offline.py`, remove `meti` from the `src.sources` import and
delete the retired HTML-signature assertions. Preserve the generic source-audit
coverage by replacing its use of `meti.SchemaError` and `meti.Blocked` with
small test-local exception classes; the blocked fixture class retains a
`fetched` attribute so `source_audit.error_entry` HTTP/hash extraction is still
tested. Keep the historical raw-pruning tests that merely use `"meti"` as a
directory name.

- [ ] **Step 6: Update README**

Document:

```text
1. Receive/check an official METI notice.
2. Run meti_manual_event detect and copy DETECTION_ID.
3. Download the PDF in a normal browser.
4. Run meti_manual_import with --detection-id and existing source/date/count inputs.
5. Run meti_review status, then approve/reject.
6. Build/apply the existing plan only after approval.
```

State explicitly that automated crawling and browser-UA impersonation are
unsupported. Document that at least one named operational owner must maintain a
subscription to METI's official notice channel; mailbox subscription and
coverage are human controls, while the repository SLA begins only when
`detect` records the notice.

- [ ] **Step 7: Run policy and watch regressions**

```bash
python3 -m unittest \
  tests.test_meti_network_policy \
  tests.test_ofac_atomicity \
  tests.test_persistence_atomicity
python3 -m tests.test_offline
```

Expected: PASS.

- [ ] **Step 8: Commit crawler retirement**

```bash
git add src/watch.py .github/actions/run-watch/action.yml README.md \
  src/notify.py tests/test_offline.py tests/test_meti_network_policy.py
git commit -m "refactor(meti): retire blocked network crawlers"
```

---

### Task 9: Migrate status, run full regression, and verify production behavior

**Files:**
- Modify: generated `data/manual/meti/state.json`
- Create/Modify: generated `data/manual/meti/events.csv`
- Modify: generated heartbeat, dashboard, and source-audit CSVs

**Interfaces:**
- Consumes: completed manual lifecycle implementation.
- Produces: first `CHECKED_NO_CHANGE` manual heartbeat without changing the METI master generation.

- [ ] **Step 1: Capture pre-migration integrity values**

```bash
SW_VERIFY_DIR="$(mktemp -d)"
shasum -a 256 data/master/master.csv > "$SW_VERIFY_DIR/master.before"
shasum -a 256 data/evidence/meti_foreign_user_list.csv > "$SW_VERIFY_DIR/evidence.before"
cp data/manual/meti/state.json "$SW_VERIFY_DIR/meti-state.before.json"
```

Keep this temporary directory through the verification steps.

- [ ] **Step 2: Record the first manual-mode check**

```bash
python3 -m src.meti_manual_event check \
  --operator kenmizuno-cpu \
  --source-url https://www.meti.go.jp/policy/anpo/law09-2.html \
  --note "自動取得を廃止し、公式通知・ブラウザ確認による手動監視へ移行"
```

Expected: lifecycle `CHECKED_NO_CHANGE`, heartbeat `manual_ok`, dashboard status
`手動監視（正常）`, and no master write.

- [ ] **Step 3: Verify protected data is unchanged**

```bash
shasum -a 256 data/master/master.csv > "$SW_VERIFY_DIR/master.after"
shasum -a 256 data/evidence/meti_foreign_user_list.csv > "$SW_VERIFY_DIR/evidence.after"
diff -u "$SW_VERIFY_DIR/master.before" "$SW_VERIFY_DIR/master.after"
diff -u "$SW_VERIFY_DIR/evidence.before" "$SW_VERIFY_DIR/evidence.after"
```

Expected: both diffs are empty.

Compare protected state fields as well:

```bash
python3 - "$SW_VERIFY_DIR/meti-state.before.json" \
  data/manual/meti/state.json <<'PY'
import json
import sys
from pathlib import Path

before = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
after = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
protected = (
    "current_source_hash",
    "current_raw_path",
    "current_records_path",
    "current_report_path",
    "current_diff_path",
    "current_record_count",
    "review_status",
    "approved",
    "applied",
)
changed = {key: (before.get(key), after.get(key)) for key in protected
           if before.get(key) != after.get(key)}
assert not changed, changed
assert after.get("current_record_count") == 835, after.get("current_record_count")
PY
```

Expected: exit 0; the applied 835-record generation is unchanged while the new
manual-monitoring fields coexist in the state snapshot.

- [ ] **Step 4: Run the complete verification suite**

```bash
python3 -m unittest
python3 -m tests.test_offline
python3 -m tests.test_mof_record_diff
python3 -m tests.test_mof_re_review
```

Expected: every command exits 0.

- [ ] **Step 5: Run the end-to-end dry-run fixture**

```bash
python3 -m unittest \
  tests.test_meti_manual_import_lifecycle.TestMetiManualImportLifecycle.test_matching_detection_reaches_review_required
```

Expected: a temporary detection proceeds through `FILE_RECEIVED`, `VALIDATED`,
`DIFFED`, and `REVIEW_REQUIRED`; the real repository data and master are not
modified.

- [ ] **Step 6: Review the exact repository change set**

```bash
git status --short
git diff --check
git diff --stat origin/main...HEAD
git diff origin/main...HEAD -- .github src tests README.md docs
git diff -- data/
```

Expected: no unrelated files, no whitespace errors, no METI historical evidence deletion.

- [ ] **Step 7: Commit migration evidence**

```bash
git add data/
git commit -m "chore(meti): initialize manual monitoring state"
```

- [ ] **Step 8: Push the feature branch and open the verification PR**

```bash
git push -u origin fix/meti-manual-first-monitoring
gh pr create \
  --base main \
  --head fix/meti-manual-first-monitoring \
  --title "METI monitoring: manual-first lifecycle" \
  --body "Retires automated METI access and adds audited manual intake plus local-only SLA enforcement."
```

Wait for the pull-request run of `watch-meti-manual-sla` and verify that its
read-only `verify` job succeeds. Inspect the workflow diff and confirm
`watch-jp.yml` supplies `sources: mof` only. Do not try to dispatch the new
workflow from the feature branch: GitHub only accepts `workflow_dispatch` for a
new workflow after that workflow exists on the default branch.

- [ ] **Step 9: Final pre-merge verification and approval checkpoint**

```bash
git status --short --branch
git log --oneline --decorate origin/main..HEAD
git diff --check origin/main...HEAD
git diff --name-status origin/main...HEAD
```

Expected: clean feature branch with the documentation, safety, lifecycle,
integration, SLA, retirement, and migration commits in order.

Pause here for human review and merge approval. Do not merge merely because the
local and pull-request tests passed.

- [ ] **Step 10: Run the production workflows after the approved merge**

After the branch is merged and the new workflow exists on `main`, run:

```bash
git switch main
git pull --ff-only origin main
gh workflow run watch-jp.yml --ref main
gh workflow run watch-meti-manual-sla.yml --ref main
gh run list --workflow watch-jp.yml --branch main --limit 1
gh run list --workflow watch-meti-manual-sla.yml --branch main --limit 1
```

Wait for both latest runs and inspect their logs. `watch-jp` must execute MOF
only. The METI SLA workflow must make no request to a METI host and must create
no data commit when there is no SLA transition. If either check fails, stop the
rollout and revert the merge rather than weakening the no-network policy.
