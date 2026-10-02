import csv
import io
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src import mofa_documents as documents, mofa_manual as manual
from src.dashboard import STATUS_COLS
from tests.test_mofa_manual import pdf


class MofaStatusIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'data/dashboard/status.csv'
        self.path.parent.mkdir(parents=True)
        self.before = [
            ['財務省', '変更なし', '2026-10-02 07:52:20', '令和8年10月2日', '14959', '99abe1f87f5c'],
            ['経済産業省', '自動取得不可', '2026-09-28 09:51:38', '', '', ''],
            ['OFAC SDN', '掲載終了候補・要レビュー', '2026-10-02 08:56:19', '2026-10-02 01:36:11', '89824', '302ac5948298'],
            ['OFAC Consolidated', '変更なし', '2026-10-02 08:56:19', '2026-09-15 03:35:48', '3371', '43033832780a'],
        ]
        self.meta = dict(operator='Ken', when=datetime(2026, 10, 2, tzinfo=timezone.utc), note='資料取込の確認')
        self.write_rows(self.before)

    def write_rows(self, rows):
        with self.path.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream, lineterminator='\n')
            writer.writerow(STATUS_COLS)
            writer.writerows(rows)

    def other_rows(self):
        rows = list(csv.reader(io.StringIO(self.path.read_text(encoding='utf-8'))))
        return [row for row in rows[1:] if not row[0].startswith('外務省')]

    def test_import_and_document_review_preserve_other_source_observations(self):
        download = self.root / 'download.pdf'
        download.write_bytes(pdf())
        result = manual.import_file(self.root, file=download, role='current_un',
                                    source_url='https://www.mofa.go.jp/current.pdf', **self.meta)
        self.assertEqual(self.other_rows(), self.before)
        state, events, queue = documents.load_bundle(self.root)
        self.assertEqual(queue[0]['review_status'], 'REVIEW_REQUIRED_DOCUMENT')
        documents.review_document(self.root, event_id=result['event_id'], source_hash=result['source_hash'],
                                  reviewer='Ken', when=self.meta['when'], note='資料のみ確認')
        self.assertEqual(self.other_rows(), self.before)
        self.assertEqual(documents.load_bundle(self.root)[2][0]['review_status'], 'REVIEWED_DOCUMENT')

    def test_invalid_existing_status_is_not_silently_replaced(self):
        self.path.write_text('unexpected,columns\nkeep,this\n', encoding='utf-8')
        before = self.path.read_bytes()
        with self.assertRaises(documents.DocumentStateError):
            manual.initialize(self.root, **self.meta)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((self.root / 'data/mofa/state.json').exists())

    def test_final_document_review_refreshes_mofa_status_and_preserves_other_rows(self):
        download = self.root / 'download.pdf'
        download.write_bytes(pdf())
        for role in ['current_un', 'current_1373']:
            manual.import_file(self.root, file=download, role=role,
                               source_url='https://www.mofa.go.jp/' + role + '.pdf', **self.meta)
        manual.record_check(self.root, family='catalog', result='checked', **self.meta)
        for row in documents.load_bundle(self.root)[2]:
            documents.review_document(self.root, event_id=row['event_id'], source_hash=row['source_hash'],
                                      reviewer='Ken', when=self.meta['when'], note='資料を確認')
        self.assertEqual(documents.load_bundle(self.root)[0]['families']['mofa_catalog']['status'], 'manual_checked')
        self.assertEqual(self.other_rows(), self.before)
        with self.path.open(encoding='utf-8', newline='') as stream:
            rows = {row[0]: row for row in list(csv.reader(stream))[1:]}
        self.assertEqual(rows['外務省（現行リスト）'][1], '手動確認済み')

    def test_missing_other_source_observation_stops_without_repairing_it(self):
        self.write_rows(self.before[:-1])
        before = self.path.read_bytes()
        with self.assertRaises(documents.DocumentStateError):
            manual.initialize(self.root, **self.meta)
        self.assertEqual(self.path.read_bytes(), before)

    def test_dry_run_rejects_invalid_status_without_changing_original_files(self):
        download = self.root / 'download.pdf'
        download.write_bytes(pdf())
        self.path.write_text('unexpected,columns\nkeep,this\n', encoding='utf-8')
        before = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        with self.assertRaises(documents.DocumentStateError):
            manual.import_file(self.root, file=download, role='current_un', dry_run=True,
                               source_url='https://www.mofa.go.jp/current.pdf', **self.meta)
        after = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(after, before)

    def test_duplicate_source_observation_is_not_silently_selected(self):
        self.write_rows(self.before + [self.before[1]])
        before = self.path.read_bytes()
        with self.assertRaises(documents.DocumentStateError):
            manual.initialize(self.root, **self.meta)
        self.assertEqual(self.path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
