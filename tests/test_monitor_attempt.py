from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def module(name):
    # An explicit assertion keeps missing implementation distinct from bad fixtures.
    from importlib.util import find_spec
    assert find_spec(name) is not None, f"{name} is not implemented"
    return importlib.import_module(name)


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


class MonitorAttemptTest(unittest.TestCase):
    def test_failed_process_records_failure_without_claiming_a_successful_check(self):
        m = module("src.monitor_attempt")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(os.environ, {"GITHUB_RUN_ID": "123", "GITHUB_REPOSITORY": "test/repo", "GITHUB_RUN_ATTEMPT": "2"}):
                code = m.run_attempt(root, [sys.executable, "-c", "raise SystemExit(7)"], ["ofac"])
            self.assertEqual(code, 7)
            record = json.loads((root / "data/monitoring/ofac_attempt.json").read_text())
            self.assertEqual(record["outcome"], "failure")
            self.assertEqual(record["exit_code"], 7)
            self.assertEqual(record["run_url"], "https://github.com/test/repo/actions/runs/123")
            self.assertEqual(record["run_attempt"], "2")
            self.assertNotIn("last_success_at", record)

    def test_successful_process_updates_attempt_result_and_inherits_github_outputs(self):
        m = module("src.monitor_attempt")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "github-output"
            command = [sys.executable, "-c", "import os; open(os.environ['GITHUB_OUTPUT'], 'a').write('has_diff=true\\n')"]
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}):
                self.assertEqual(m.run_attempt(root, command, ["ofac"]), 0)
            record = json.loads((root / "data/monitoring/ofac_attempt.json").read_text())
            self.assertEqual(record["outcome"], "success")
            self.assertEqual(output.read_text(), "has_diff=true\n")


class PersistMonitorFailureTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.remote = base / "origin.git"
        self.root = base / "work"
        subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(self.remote)], check=True, capture_output=True)
        subprocess.run(["git", "clone", str(self.remote), str(self.root)], check=True, capture_output=True)
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.com")
        (self.root / "data/master").mkdir(parents=True)
        (self.root / "data/master/master.csv").write_text("original master\n")
        (self.root / "data/state.json").write_text('{"original":true}\n')
        git(self.root, "add", "data")
        git(self.root, "commit", "-m", "initial")
        git(self.root, "push", "origin", "main")
        self.initial = git(self.root, "rev-parse", "HEAD")

    def write_attempt(self, **extra):
        record = {"outcome": "failure", "exit_code": 1, "run_id": "123", "run_attempt": "1", "started_at": "2026-10-01T09:00:00Z", "finished_at": "2026-10-01T09:01:00Z", "sources": ["ofac"], "run_url": "https://github.com/test/repo/actions/runs/123", **extra}
        path = self.root / "data/monitoring/ofac_attempt.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record) + "\n")

    def test_failure_push_preserves_remote_and_local_master_state(self):
        m = module("src.persist_monitor_failure")
        self.write_attempt()
        (self.root / "data/master/master.csv").write_text("partial master\n")
        (self.root / "data/state.json").write_text("partial state\n")
        git(self.root, "add", "data/master/master.csv")  # even staged partial data must not leak
        self.assertTrue(m.persist_failure(self.root, run_id="123"))
        self.assertEqual(git(self.remote, "show", "main:data/master/master.csv"), "original master")
        self.assertEqual(git(self.remote, "show", "main:data/state.json"), '{"original":true}')
        self.assertEqual(git(self.remote, "diff", "--name-only", self.initial, "main"), "data/monitoring/ofac_attempt.json")
        self.assertEqual((self.root / "data/master/master.csv").read_text(), "partial master\n")
        self.assertEqual(git(self.root, "diff", "--cached", "--name-only"), "data/master/master.csv")

    def test_old_attempt_from_another_run_is_not_committed(self):
        m = module("src.persist_monitor_failure")
        self.write_attempt(run_id="122")
        self.assertFalse(m.persist_failure(self.root, run_id="123"))
        self.assertEqual(git(self.remote, "rev-parse", "main"), self.initial)

    def test_successful_attempt_is_not_pushed_from_the_failure_path(self):
        m = module("src.persist_monitor_failure")
        self.write_attempt(outcome="success", exit_code=0)
        self.assertFalse(m.persist_failure(self.root, run_id="123"))
        self.assertEqual(git(self.remote, "rev-parse", "main"), self.initial)

    def test_remote_newer_attempt_is_not_overwritten_by_delayed_failure_save(self):
        m = module("src.persist_monitor_failure")
        self.write_attempt(run_id="124", finished_at="2026-10-01T09:02:00Z")
        git(self.root, "add", "data/monitoring/ofac_attempt.json")
        git(self.root, "commit", "-m", "newer attempt")
        git(self.root, "push", "origin", "main")
        remote_head = git(self.remote, "rev-parse", "main")
        self.write_attempt()
        self.assertFalse(m.persist_failure(self.root, run_id="123"))
        self.assertEqual(git(self.remote, "rev-parse", "main"), remote_head)


if __name__ == "__main__":
    unittest.main()
