from __future__ import annotations

import ast
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


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(
        path.read_text(encoding="utf-8"),
        filename=str(path),
    )
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def yaml_mapping_block(text: str, header: str) -> str:
    """Return one indentation-delimited YAML mapping without parsing YAML."""

    lines = text.splitlines()
    matches = [index for index, line in enumerate(lines) if line == header]
    if len(matches) != 1:
        raise AssertionError(
            "expected one YAML header %r, found %d"
            % (header, len(matches))
        )
    start = matches[0]
    indentation = len(header) - len(header.lstrip())
    block = [lines[start]]
    for line in lines[start + 1:]:
        current = len(line) - len(line.lstrip())
        if line.strip() and current <= indentation:
            break
        block.append(line)
    return "\n".join(block).rstrip()


class TestMetiNetworkPolicy(unittest.TestCase):
    def test_retired_network_modules_are_absent(self):
        for rel in (
            "src/meti_html.py",
            "src/meti_rss.py",
            "src/meti_rss_audit.py",
            "src/sources/meti.py",
        ):
            with self.subTest(path=rel):
                self.assertFalse((ROOT / rel).exists(), rel)

    def test_active_python_has_no_browser_compatible_meti_profile(self):
        for path in (ROOT / "src").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path):
                self.assertNotIn(
                    "browser_compatible_chrome",
                    text,
                    str(path),
                )
                self.assertNotIn(
                    "Chrome/146.0.0.0",
                    text,
                    str(path),
                )

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

    def test_manual_sla_workflow_is_local_only(self):
        path = WORKFLOWS / "watch-meti-manual-sla.yml"
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        lower = text.lower()
        forbidden = (
            "http",
            "curl",
            "wget",
            "requests",
            "meti.go.jp",
            "src.meti_html",
            "src.meti_rss",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, lower)

        permissions = yaml_mapping_block(text, "permissions:")
        verify = yaml_mapping_block(text, "  verify:")
        enforce = yaml_mapping_block(text, "  enforce:")

        self.assertEqual(
            permissions,
            "permissions:\n  contents: read",
        )
        self.assertEqual(text.count("contents: write"), 1)
        self.assertNotIn("contents: write", verify)
        self.assertIn(
            "    permissions:\n      contents: write",
            enforce,
        )
        self.assertIn("pull_request:", text)
        self.assertIn("ref: ${{ github.sha }}", verify)
        self.assertIn('python-version: "3.12"', verify)
        self.assertIn(
            "python -m unittest \\\n"
            "            tests.test_meti_manual_sla \\\n"
            "            tests.test_meti_network_policy \\\n"
            "            tests.test_meti_manual_event",
            verify,
        )

        self.assertIn(
            "    if: github.event_name != 'pull_request' "
            "&& startsWith(github.ref, 'refs/heads/')",
            enforce,
        )
        self.assertIn("    needs: verify", enforce)
        self.assertIn(
            "    concurrency:\n"
            "      group: sanctions-watch-commit\n"
            "      cancel-in-progress: false",
            enforce,
        )
        self.assertIn("ref: ${{ github.ref_name }}", enforce)

        check_index = enforce.index("      - name: 手動取込SLA判定")
        commit_index = enforce.index("      - name: SLA遷移をコミット")
        failure_index = enforce.index(
            "      - name: SLA異常をWorkflow失敗として確定"
        )
        self.assertLess(check_index, commit_index)
        self.assertLess(commit_index, failure_index)
        self.assertIn("        id: sla", enforce[check_index:commit_index])
        self.assertIn(
            "        continue-on-error: true",
            enforce[check_index:commit_index],
        )
        self.assertIn(
            "        run: python -m src.meti_manual_sla",
            enforce[check_index:commit_index],
        )
        self.assertIn("          git add -A data/", enforce)
        self.assertIn(
            "        if: steps.sla.outcome != 'success'",
            enforce[failure_index:],
        )

    def test_manual_lifecycle_modules_have_no_network_imports(self):
        forbidden = {
            "requests",
            "httpx",
            "urllib.request",
            "socket",
            "subprocess",
        }
        for rel in (
            "src/meti_manual_event.py",
            "src/meti_manual_sla.py",
        ):
            modules = imported_modules(ROOT / rel)
            found = {
                module
                for module in modules
                for blocked in forbidden
                if module == blocked
                or module.startswith(blocked + ".")
            }
            with self.subTest(path=rel):
                self.assertEqual(found, set())


if __name__ == "__main__":
    unittest.main()
