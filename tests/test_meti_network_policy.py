from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


def scheduled_workflows():
    paths = {
        *WORKFLOWS.glob("*.yml"),
        *WORKFLOWS.glob("*.yaml"),
    }
    return [
        path
        for path in sorted(paths)
        if "schedule:" in path.read_text(encoding="utf-8")
    ]


class TestMetiNetworkPolicy(unittest.TestCase):
    def test_browser_html_workflow_is_retired(self):
        self.assertFalse(
            (WORKFLOWS / "watch-meti-html.yml").exists()
        )

    def test_scheduled_workflows_do_not_run_meti_network_modules(self):
        forbidden = (
            "src.meti_html",
            "src.meti_rss",
            "--sources meti",
            "--sources mof meti",
            "ml_index_release_atom.xml",
            "meti.go.jp",
        )

        for path in scheduled_workflows():
            text = path.read_text(encoding="utf-8")

            for token in forbidden:
                with self.subTest(path=path.name, token=token):
                    self.assertNotIn(token, text)

    def test_watch_jp_runs_mof_only(self):
        text = (
            WORKFLOWS / "watch-jp.yml"
        ).read_text(encoding="utf-8")

        self.assertRegex(
            text,
            r"(?m)^\s+sources:\s+mof\s*$",
        )
        self.assertNotIn("sources: mof meti", text)


if __name__ == "__main__":
    unittest.main()
