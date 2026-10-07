import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tests.test_crypto_addresses import xml
from src.crypto_watch import run

class WatchTests(unittest.TestCase):
    def test_revalidation_does_not_publish_an_official_change_and_repeat_is_idempotent(self):
        self.setup_source(xml(address='0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed'))
        initial=run(self.root,'2026-10-07T00:00:00Z')
        initial['rows'][0].update(validation='FORMAT_ONLY',review_reason='チェックサム未検証')
        (self.root/'data/crypto/dashboard.json').write_text(json.dumps(initial))
        updated=run(self.root,'2026-10-07T01:00:00Z')
        self.assertEqual(updated['new_event_count'],0)
        self.assertEqual(updated['events'],initial['events'])
        self.assertEqual(updated['new_validation_event_count'],1)
        self.assertEqual(updated['validation_events'][0]['kind'],'REVALIDATED')
        repeat=run(self.root,'2026-10-07T02:00:00Z')
        self.assertEqual(repeat['new_validation_event_count'],0)
        self.assertEqual(repeat['validation_events'],updated['validation_events'])

    def test_category_counts_preserve_legacy_aggregate(self):
        from src.crypto_watch import _counts
        rows=[]
        for category,state in [('INCONSISTENCY','INVALID'),('LIMITATION','FORMAT_ONLY'),('UNSUPPORTED','UNSUPPORTED')]:
            rows.append(dict(listing_status='LISTED',network='',symbol='TEST',normalized_address=category,
                             review_reason='確認',review_category=category,validation=state))
        c=_counts(rows)
        self.assertEqual(c['format_review'],3)
        self.assertEqual(c['inconsistency_review'],1)
        self.assertEqual(c['validation_limitations'],1)
        self.assertEqual(c['unsupported_review'],1)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root/'data').mkdir()
        self.body = xml()
        self.setup_source(self.body)

    def tearDown(self): self.tmp.cleanup()

    def setup_source(self, body):
        (self.root/'data/raw.xml.gz').write_bytes(gzip.compress(body))
        state={'ofac_sdn': {'raw_advanced':'data/raw.xml.gz', 'advanced_sha256':hashlib.sha256(body).hexdigest(),
                'advanced_url':'https://example.test/sdn.xml'}}
        (self.root/'data/state.json').write_text(json.dumps(state))

    def test_success_and_repeat_have_one_baseline_event(self):
        snap=run(self.root, '2026-10-07T00:00:00Z')
        self.assertEqual(snap['status'], 'SUCCESS')
        self.assertEqual(snap['counts']['listed_relations'], 1)
        repeat=run(self.root, '2026-10-07T01:00:00Z')
        self.assertEqual(len(repeat['events']), 1)
        self.assertEqual(repeat['new_event_count'], 0)

    def test_hash_mismatch_retains_last_good_and_marks_failed(self):
        good=run(self.root, '2026-10-07T00:00:00Z')
        (self.root/'data/raw.xml.gz').write_bytes(gzip.compress(b'broken'))
        failed=run(self.root, '2026-10-07T01:00:00Z')
        self.assertEqual(failed['status'], 'FAILED')
        self.assertEqual(failed['last_success'], good['last_success'])
        self.assertEqual(failed['rows'], good['rows'])

    def test_atomic_failure_does_not_publish_success(self):
        good=run(self.root, '2026-10-07T00:00:00Z')
        with patch('src.crypto_watch.atomic_replace_many', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): run(self.root, '2026-10-07T01:00:00Z')
        actual=json.loads((self.root/'data/crypto/dashboard.json').read_text())
        self.assertEqual(actual['generated_at'], good['generated_at'])

    def test_root_escape_is_blocked(self):
        (self.root/'data/state.json').write_text(json.dumps({'ofac_sdn':{'raw_advanced':'../outside.gz','advanced_sha256':'a'*64}}))
        snap=run(self.root,'2026-10-07T00:00:00Z')
        self.assertEqual(snap['status'],'FAILED')

    def test_invalid_deflate_updates_failure_and_preserves_last_good_rows(self):
        good=run(self.root,'2026-10-07T00:00:00Z')
        (self.root/'data/raw.xml.gz').write_bytes(bytes.fromhex('1f8b0800000000000003')+b'\x07\x00\x00')
        failed=run(self.root,'2026-10-07T01:00:00Z')
        self.assertEqual(failed['status'],'FAILED')
        self.assertEqual(failed['rows'],good['rows'])
        self.assertEqual(failed['last_success'],good['last_success'])
