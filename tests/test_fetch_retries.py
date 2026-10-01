from __future__ import annotations

import unittest
from unittest.mock import patch

import requests

from src import fetch as transport


URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV"


def response(status, *, headers=None, body=b"list"):
    value = requests.Response()
    value.status_code = status
    value.url = URL
    value.headers.update(headers or {})
    value._content = body
    value._content_consumed = True
    return value


class SequenceSession:
    def __init__(self, values):
        self.values = iter(values)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        value = next(self.values)
        if isinstance(value, Exception):
            raise value
        return value


class FetchRetriesTest(unittest.TestCase):
    def test_transient_503_then_timeout_recovers_and_retains_conditional_headers(self):
        session = SequenceSession([response(503), requests.ReadTimeout("temporary"), response(304)])
        with patch("time.sleep") as sleep:
            result = transport.fetch(URL, prev={"etag": '"v1"', "sha256": "old"}, session=session)
        self.assertTrue(result.not_modified)
        self.assertEqual(result.sha256, "old")
        self.assertEqual(len(session.calls), 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 4])
        self.assertTrue(all(opts["headers"]["If-None-Match"] == '"v1"' and opts["allow_redirects"] is False for _, opts in session.calls))

    def test_transient_http_retries_are_bounded_to_three_requests(self):
        for status in (429, 502, 503, 504):
            with self.subTest(status=status):
                session = SequenceSession([response(status) for _ in range(4)])
                with patch("time.sleep"), self.assertRaises(requests.HTTPError):
                    transport.fetch(URL, session=session)
                self.assertEqual(len(session.calls), 3)

    def test_short_retry_after_is_honored(self):
        session = SequenceSession([response(429, headers={"Retry-After": "7"}), response(200)])
        with patch("time.sleep") as sleep:
            self.assertEqual(transport.fetch(URL, session=session).body, b"list")
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [7])

    def test_long_retry_after_fails_without_violating_server_cooldown(self):
        session = SequenceSession([response(429, headers={"Retry-After": "300"}), response(200)])
        with patch("time.sleep") as sleep, self.assertRaises(requests.HTTPError):
            transport.fetch(URL, session=session)
        self.assertEqual(len(session.calls), 1)
        sleep.assert_not_called()

    def test_permanent_http_and_certificate_errors_are_not_retried(self):
        for first in (response(400), response(403), response(404), requests.exceptions.SSLError("certificate invalid")):
            session = SequenceSession([first, response(200)])
            with patch("time.sleep") as sleep, self.assertRaises(requests.RequestException):
                transport.fetch(URL, session=session)
            self.assertEqual(len(session.calls), 1)
            sleep.assert_not_called()

    def test_retry_redirect_cannot_bypass_existing_network_policy(self):
        redirected = response(302, headers={"Location": "https://www.meti.go.jp/blocked"})
        session = SequenceSession([response(503), redirected, response(200)])
        with patch("time.sleep"), self.assertRaises(transport.NetworkPolicyError):
            transport.fetch(URL, session=session)
        self.assertEqual([url for url, _ in session.calls], [URL, URL])


if __name__ == "__main__":
    unittest.main()
