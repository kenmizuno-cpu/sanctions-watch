import csv
import gzip
import importlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src import mofa_documents as D
from src.fetch import sha256


def pdf(label='A'):
    """A real one-page PDF, with a variable comment to exercise hash changes."""
    chunks = [b'%PDF-1.4\n% ' + label.encode() + b'\n']
    offsets = [0]
    for i, body in enumerate([
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Resources << >> >>',
    ], 1):
        offsets.append(sum(map(len, chunks)))
        chunks.append(str(i).encode() + b' 0 obj\n' + body + b'\nendobj\n')
    xref = sum(map(len, chunks))
    chunks.append(b'xref\n0 4\n0000000000 65535 f \n')
    chunks.extend(f'{offset:010} 00000 n \n'.encode() for offset in offsets[1:])
    chunks.append(f'trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    return b''.join(chunks)


class ManualTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('src.mofa_manual'), 'manual entry point is required')
        self.M = importlib.import_module('src.mofa_manual')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'repo'
        self.root.mkdir()
        self.file = Path(self.tmp.name) / 'download.pdf'
        self.file.write_bytes(pdf())
        self.now = datetime(2026, 10, 1, tzinfo=timezone.utc)
        self.meta = dict(operator='Ken', when=self.now, note='ブラウザで公式資料を取得')
        self.url = 'https://www.mofa.go.jp/un.pdf'

    def take(self, **kw):
        args = dict(file=self.file, role='current_un', source_url=self.url, **self.meta)
        args.update(kw)
        return self.M.import_file(self.root, **args)

    def bundle(self):
        return D.load_bundle(self.root)

    def test_init_creates_empty_projection_without_claiming_a_check(self):
        with patch('requests.Session.request', side_effect=AssertionError('no network')):
            self.M.initialize(self.root, **self.meta)
        s, e, q = self.bundle()
        self.assertEqual((e, q), ([], []))
        self.assertFalse(s['baseline_complete'])
        self.assertEqual(s['last_complete_scan_at'], '')
        self.assertEqual(s['families']['mofa_catalog']['last_manual_check_at'], '')
        rows = list(csv.reader(io.StringIO((self.root / 'data/dashboard/status.csv').read_text())))
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[-2][1], '自動取得不可・手動確認待ち')
        self.assertEqual(len(list(csv.reader(io.StringIO((self.root / 'data/dashboard/mofa_documents.csv').read_text())))), 1)

    def test_import_is_network_free_hash_bound_and_partial(self):
        with patch('requests.Session.request', side_effect=AssertionError('no network')):
            result = self.take()
        s, e, q = self.bundle()
        self.assertEqual(result['exit_code'], 0)
        r = s['resources']['current_un']
        self.assertEqual(gzip.decompress((self.root / r['raw_path']).read_bytes()), pdf())
        self.assertEqual(r['sha256'], sha256(pdf()))
        self.assertEqual(q[0]['event_type'], 'BASELINED')
        self.assertEqual(q[0]['reviewer'], '')
        self.assertEqual(q[0]['applied'], 'false')
        self.assertEqual(s['families']['mofa_catalog']['status'], 'manual_pending')
        self.assertEqual(s['families']['mofa_catalog']['last_manual_check_at'], '')
        audit = list(csv.DictReader(io.StringIO((self.root / 'data/source_audit/2026-10.csv').read_text())))
        self.assertEqual(audit[-1]['http_status'], '')
        ops = list(csv.DictReader(io.StringIO((self.root / self.M.OPERATIONS_PATH).read_text())))
        self.assertEqual(ops[-1]['operator'], 'Ken')
        self.assertEqual(ops[-1]['source_hash'], r['sha256'])

    def test_same_document_and_a_b_a_preserve_reviews(self):
        self.take()
        self.take()
        s, e, q = self.bundle()
        self.assertEqual(len(q), 1)
        D.review_document(self.root, event_id=q[0]['event_id'], source_hash=q[0]['source_hash'],
                          reviewer='Reviewer', when=self.now, note='確認済み')
        self.file.write_bytes(pdf('B'))
        self.take()
        self.file.write_bytes(pdf())
        self.take()
        s, e, q = self.bundle()
        self.assertEqual(len(q), 3)
        self.assertEqual(len({r['event_id'] for r in q}), 3)
        self.assertEqual(q[0]['review_status'], 'REVIEWED_DOCUMENT')
        self.assertTrue(all(r['reviewer'] == '' for r in q[1:]))

    def test_changed_url_same_content_is_reviewed_separately(self):
        self.take()
        self.take(source_url='https://www.mofa.go.jp/un-new.pdf')
        self.assertEqual(self.bundle()[2][-1]['event_type'], 'LINK_CHANGED_SAME_CONTENT')

    def test_failure_retains_valid_cache_and_check_cannot_clear_it(self):
        self.take()
        before = self.bundle()[0]['resources']['current_un'].copy()
        self.file.write_bytes(b'<html>Access denied</html>')
        result = self.take(when=self.now + timedelta(minutes=1))
        self.assertEqual(result['exit_code'], 1)
        s, _, q = self.bundle()
        self.assertEqual(s['resources']['current_un'], before)
        self.assertEqual(len(q), 1)
        self.assertEqual(s['families']['mofa_catalog']['status'], 'manual_error')
        self.M.record_check(self.root, family='catalog', result='checked', **dict(self.meta, when=self.now + timedelta(minutes=2)))
        self.assertEqual(self.bundle()[0]['families']['mofa_catalog']['status'], 'manual_error')
        self.file.write_bytes(pdf())
        self.take(when=self.now + timedelta(minutes=3))
        self.assertEqual(self.bundle()[0]['families']['mofa_catalog']['status'], 'manual_pending')
        self.assertEqual(len(list((self.root / 'data/raw/mofa').glob('*'))), 1)

    def test_missing_oversize_truncated_and_wrong_hash_fail(self):
        for content, kw in [(b'%PDF-1.4\n%%EOF', {}), (pdf(), {'expected_sha256': '0' * 64})]:
            with self.subTest(content=content[:20], kw=kw):
                self.file.write_bytes(content)
                self.assertEqual(self.take(**kw)['exit_code'], 1)
        self.assertEqual(self.take(file=self.file.with_name('missing.pdf'))['exit_code'], 1)
        self.file.write_bytes(pdf())
        with patch.object(self.M, 'PDF_LIMIT', 20):
            self.assertEqual(self.take()['exit_code'], 1)
        self.assertEqual(self.bundle()[2], [])

    def test_required_metadata_url_and_stale_operations_fail_before_writes(self):
        for kw in [dict(operator=''), dict(note=''), dict(when=self.now.replace(tzinfo=None)),
                   dict(source_url='https://evil.test/un.pdf'), dict(role='attachment_pdf'),
                   dict(publication_date='2026-02-30')]:
            with self.subTest(kw=kw), self.assertRaises((ValueError, RuntimeError)):
                self.take(**kw)
        self.assertFalse((self.root / 'data').exists())
        self.take()
        with self.assertRaises(ValueError):
            self.take(when=self.now - timedelta(seconds=1))

    def test_manual_check_is_separate_from_import_and_reviews_refresh_status(self):
        self.take()
        self.take(role='current_1373', source_url='https://www.mofa.go.jp/1373.pdf')
        s, e, q = self.bundle()
        self.assertEqual(s['families']['mofa_catalog']['status'], 'manual_review')
        self.M.record_check(self.root, family='catalog', result='checked', **self.meta)
        for r in q:
            D.review_document(self.root, event_id=r['event_id'], source_hash=r['source_hash'],
                              reviewer='Reviewer', when=self.now, note='資料内容確認')
        s, e, q = self.bundle()
        self.assertEqual(s['families']['mofa_catalog']['status'], 'manual_checked')
        self.assertFalse(s['baseline_complete'])
        self.assertEqual(s['last_complete_scan_at'], '')
        self.M.record_check(self.root, family='catalog', result='pending', **self.meta)
        self.assertEqual(self.bundle()[0]['families']['mofa_catalog']['status'], 'manual_pending')

    def test_notice_requires_attachments_but_never_downloads_them(self):
        notice = Path(self.tmp.name) / 'notice.html'
        notice.write_text('<html><div id="maincontents"><h1>制裁</h1><p>資産凍結</p><a href="/annex.pdf">別添</a></div></html>')
        u = 'https://www.mofa.go.jp/mofaj/press/release/n.html'
        with patch('requests.Session.request', side_effect=AssertionError('no network')):
            self.take(file=notice, role='notice', source_url=u)
        s, e, q = self.bundle()
        self.assertEqual(s['families']['mofa_press']['status'], 'manual_pending')
        self.take(role='attachment_pdf', source_url='https://www.mofa.go.jp/annex.pdf', notice_url=u)
        self.assertEqual(self.bundle()[0]['families']['mofa_press']['status'], 'manual_review')

    def test_dry_run_and_other_outputs_unchanged(self):
        for rel in ['data/master.csv', 'data/dashboard/changes.csv', 'data/ofac_index.json', 'dist/internal_import_latest.xlsx']:
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'preserved')
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(self.take(dry_run=True)['exit_code'], 0)
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.take()
        self.assertTrue(all((self.root / p).read_bytes() == data for p, data in before.items()))

    def test_prior_gap_and_pending_history_are_not_silently_cleared(self):
        state, events, queue = D.empty_bundle()
        gap = {'from': '2026-07', 'to': '2026-09', 'detected_at': '2026-10-01T00:00:00Z'}
        state['coverage_gap'] = gap.copy()
        state['pending_notices'] = [{'key': 'old-pending'}]
        D.save_bundle(self.root, state, events, queue)
        self.M.record_check(self.root, family='press', result='checked', **self.meta)
        state = self.bundle()[0]
        self.assertEqual(state['coverage_gap'], gap)
        self.assertEqual(state['pending_notices'], [{'key': 'old-pending'}])
        self.assertEqual(state['families']['mofa_press']['status'], 'COVERAGE_GAP')

    def test_corrupt_operation_history_stops_without_rewrite(self):
        self.take()
        path = self.root / self.M.OPERATIONS_PATH
        path.write_text(','.join(self.M.OPERATION_COLS) + '\n')
        before = (self.root / D.STATE_PATH).read_bytes()
        with self.assertRaises(D.DocumentStateError):
            self.take()
        self.assertEqual((self.root / D.STATE_PATH).read_bytes(), before)

    def test_operation_month_uses_utc_and_role_key_cannot_be_reused(self):
        self.take(when=datetime(2026, 10, 1, 0, 10, tzinfo=timezone(timedelta(hours=9))))
        self.assertTrue((self.root / 'data/source_audit/2026-09.csv').exists())
        with self.assertRaises(ValueError):
            self.take(role='notice', source_url='https://www.mofa.go.jp/n.html', document_key='current_un')

    def test_later_notice_import_requires_a_new_attachment_confirmation(self):
        notice = Path(self.tmp.name) / 'notice.html'
        notice.write_text('<html><div id="maincontents"><h1>制裁</h1><a href="/annex.pdf">別添</a></div></html>')
        url = 'https://www.mofa.go.jp/n.html'
        self.take(file=notice, role='notice', source_url=url)
        self.take(role='attachment_pdf', source_url='https://www.mofa.go.jp/annex.pdf', notice_url=url)
        self.assertEqual(self.bundle()[0]['families']['mofa_press']['status'], 'manual_review')
        self.take(file=notice, role='notice', source_url=url, when=self.now + timedelta(minutes=1))
        self.assertEqual(self.bundle()[0]['families']['mofa_press']['status'], 'manual_pending')

    def test_notice_can_move_url_with_a_stable_document_key(self):
        notice = Path(self.tmp.name) / 'notice.html'
        notice.write_text('<html><div id="maincontents"><h1>制裁</h1></div></html>')
        self.take(file=notice, role='notice', source_url='https://www.mofa.go.jp/old.html')
        key = self.bundle()[2][0]['document_key']
        self.take(file=notice, role='notice', source_url='https://www.mofa.go.jp/new.html', document_key=key)
        self.assertEqual(self.bundle()[2][-1]['event_type'], 'LINK_CHANGED_SAME_CONTENT')

    def test_legacy_pending_notice_is_archived_after_valid_import(self):
        from dataclasses import asdict
        from src.mofa_sources import DocumentLink, _key
        url = 'https://www.mofa.go.jp/n.html'
        link = DocumentLink(_key('notice', url), 'notice', url, '制裁', baseline=True)
        state, events, queue = D.empty_bundle()
        state['pending_notices'] = [asdict(link)]
        D.save_bundle(self.root, state, events, queue)
        notice = Path(self.tmp.name) / 'notice.html'
        notice.write_text('<html><div id="maincontents"><h1>制裁</h1></div></html>')
        self.take(file=notice, role='notice', source_url=url)
        state, _, queue = self.bundle()
        self.assertEqual(state['pending_notices'], [])
        history = state['manual']['pending_notice_history']
        self.assertEqual(history[0]['notice']['key'], link.key)
        self.assertEqual(history[0]['source_hash'], queue[0]['source_hash'])
        self.assertEqual(queue[0]['event_type'], 'BASELINED')
        self.assertEqual(state['families']['mofa_press']['status'], 'manual_review')

    def test_legacy_unavailable_attachment_remains_pending_after_queue_resolution(self):
        from src.mofa_sources import DocumentLink
        state, events, queue = D.empty_bundle()
        link = DocumentLink('legacy-attachment', 'attachment_pdf', 'https://www.mofa.go.jp/annex.pdf',
                            '別添', notice_url='https://www.mofa.go.jp/n.html')
        D.mark_unavailable(state, events, queue, link=link, when=self.now, reason='old 403')
        D.save_bundle(self.root, state, events, queue)
        self.M.record_check(self.root, family='press', result='checked', **self.meta)
        D.review_document(self.root, event_id=queue[0]['event_id'], source_hash=queue[0]['source_hash'],
                          reviewer='Reviewer', when=self.now, note='エラーを確認')
        self.assertEqual(self.bundle()[0]['families']['mofa_press']['status'], 'manual_pending')
        self.take(role='attachment_pdf', source_url=link.url, notice_url=link.notice_url, document_key=link.key)
        self.assertEqual(self.bundle()[0]['families']['mofa_press']['status'], 'manual_review')

    def test_commit_failure_rolls_back_formal_generation(self):
        self.take()
        formal = [D.STATE_PATH, D.EVENT_PATH, D.QUEUE_PATH, self.M.OPERATIONS_PATH,
                  'data/dashboard/status.csv', 'data/dashboard/mofa_documents.csv',
                  'data/heartbeat/2026-10.csv', 'data/source_audit/2026-10.csv']
        before = {p: (self.root / p).read_bytes() for p in formal}
        real_replace = os.replace
        failed = False
        def replace(src, dst):
            nonlocal failed
            if Path(dst) == self.root / D.QUEUE_PATH and not failed:
                failed = True
                raise OSError('simulated disk failure')
            return real_replace(src, dst)
        self.file.write_bytes(pdf('B'))
        with patch('src.persistence.os.replace', side_effect=replace), self.assertRaises(OSError):
            self.take()
        self.assertTrue(failed)
        self.assertEqual(before, {p: (self.root / p).read_bytes() for p in formal})


if __name__ == '__main__':
    unittest.main()
