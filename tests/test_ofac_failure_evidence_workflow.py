from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ACTION = ROOT / ".github" / "actions" / "run-watch" / "action.yml"


class OfacFailureEvidenceWorkflowTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.text = ACTION.read_text(encoding="utf-8")

    def step(self, name: str) -> str:
        marker = f"    - name: {name}\n"
        start = self.text.find(marker)

        self.assertNotEqual(
            start,
            -1,
            f"{name} step が存在しない",
        )

        end = self.text.find(
            "\n    - name:",
            start + len(marker),
        )

        if end == -1:
            end = len(self.text)

        return self.text[start:end]

    def test_failure_evidence_prepare_step_exists(self):
        step = self.step("OFAC 失敗証跡を準備")

        self.assertIn(
            "if: failure() && "
            "(contains(inputs.sources, 'ofac') || inputs.sources == 'all')",
            step,
        )

    def test_failure_evidence_upload_step_exists(self):
        step = self.step("OFAC 失敗証跡を保存")

        self.assertIn(
            "if: failure() && "
            "(contains(inputs.sources, 'ofac') || inputs.sources == 'all')",
            step,
        )

        self.assertIn(
            "uses: actions/upload-artifact@v4",
            step,
        )

    def test_failure_evidence_contains_primary_source_material(self):
        step = self.step("OFAC 失敗証跡を準備")

        required_paths = [
            "data/source_audit/",
            "data/heartbeat/",
            "data/state.json",
            "data/dashboard/status.csv",
            "data/raw/ofac_sdn/",
            "data/raw/ofac_sdn_advanced/",
            "data/raw/ofac_cons/",
            "data/raw/ofac_cons_advanced/",
        ]

        for path in required_paths:
            with self.subTest(path=path):
                self.assertIn(path, step)

        self.assertIn(
            'EVIDENCE="failure-evidence"',
            step,
        )

    def test_failure_artifact_is_retained_for_90_days(self):
        step = self.step("OFAC 失敗証跡を保存")

        self.assertIn(
            "retention-days: 90",
            step,
        )

        self.assertIn(
            "if-no-files-found: error",
            step,
        )

        self.assertIn(
            "path: failure-evidence/",
            step,
        )

    def test_failure_manifest_records_run_context(self):
        step = self.step("OFAC 失敗証跡を準備")

        required = [
            "GITHUB_RUN_ID",
            "GITHUB_RUN_ATTEMPT",
            "GITHUB_SHA",
            "git status --short",
        ]

        for item in required:
            with self.subTest(item=item):
                self.assertIn(item, step)


if __name__ == "__main__":
    unittest.main()
