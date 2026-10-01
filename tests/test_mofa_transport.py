import unittest
from src.fetch import fetch, NetworkPolicyError
from src.mofa_sources import validate_mofa_url
from tests.test_fetch_meti_policy import FakeSession, FakeResponse

class StreamResponse(FakeResponse):
    closed = False
    def iter_content(self, chunk_size):
        for i in range(0, len(self.content), 3):
            yield self.content[i:i+3]
    def close(self):
        self.closed = True

class TransportTests(unittest.TestCase):
    def test_mofa_url_rules(self):
        for u in ['http://www.mofa.go.jp/a','https://evil.test/a','https://a@www.mofa.go.jp/a','https://www.mofa.go.jp:443/a','https://www.mofa.go.jp./a','https://www.mofa.go.jp:bad/a']:
            with self.subTest(u=u), self.assertRaises(NetworkPolicyError): validate_mofa_url(u)
        for u in ['https://www.mofa.go.jp/a','https://mofa.go.jp/a']:
            self.assertEqual(validate_mofa_url(u),u)
    def test_redirect_is_rejected_before_second_request(self):
        u='https://www.mofa.go.jp/a'
        s=FakeSession([StreamResponse(url=u,status_code=302,headers={'Location':'https://www.mof.go.jp/a'})])
        with self.assertRaises(NetworkPolicyError): fetch(u,session=s,url_validator=validate_mofa_url)
        self.assertEqual(len(s.calls),1)
    def test_stream_stops_at_limit(self):
        for headers in [{}, {'Content-Length':'100'}]:
            r=StreamResponse(url='https://www.mofa.go.jp/a',status_code=200,body=b'123456789',headers=headers)
            with self.assertRaises(ValueError): fetch(r.url,session=FakeSession([r]),max_bytes=5,url_validator=validate_mofa_url)
            self.assertTrue(r.closed)
    def test_existing_meti_block_still_runs(self):
        s=FakeSession([])
        with self.assertRaises(NetworkPolicyError): fetch('https://www.meti.go.jp/a',session=s,url_validator=lambda u:u)
        self.assertFalse(s.calls)
