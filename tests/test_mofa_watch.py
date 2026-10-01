import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src.fetch import NetworkPolicyError
from src.mofa_sources import DocumentLink
from src.mofa_watch import fetch_document, main, run


class RetiredWatchTests(unittest.TestCase):
    def test_old_run_and_dry_run_do_not_contact_sources_or_change_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'repo'
            root.mkdir()
            path = root / 'data/mofa/state.json'
            path.parent.mkdir(parents=True)
            path.write_bytes(b'previous evidence')
            report = Path(temp) / 'report.json'
            for dry_run in [False, True]:
                with patch('requests.Session.request', side_effect=AssertionError('no network')):
                    self.assertEqual(run(root, now=datetime.now(timezone.utc), dry_run=dry_run, report=report), 1)
                self.assertEqual(path.read_bytes(), b'previous evidence')
                self.assertEqual(json.loads(report.read_text())['mode'], 'manual_required')
                self.assertEqual(list(root.rglob('*.csv')), [])

    def test_fetch_helper_is_also_retired(self):
        with patch('requests.Session.request', side_effect=AssertionError('no network')):
            with self.assertRaises(NetworkPolicyError):
                fetch_document(Path('.'), link=DocumentLink('current_un', 'current_pdf',
                               'https://www.mofa.go.jp/un.pdf', 'UN'), previous={})

    def test_legacy_cli_fails_with_actionable_message(self):
        with tempfile.TemporaryDirectory() as temp, patch('sys.stderr') as stderr:
            self.assertEqual(main(['--root', temp, '--dry-run', '--from-month', '2026-07']), 1)
            self.assertIn('src.mofa_manual', ''.join(str(c) for c in stderr.write.call_args_list))
            self.assertFalse((Path(temp) / 'data').exists())
