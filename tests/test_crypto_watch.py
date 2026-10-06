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
