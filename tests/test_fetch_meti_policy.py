from __future__ import annotations

import unittest

from src import fetch as fetch_module
from src.fetch import Fetched
from src.sources import mof


class FakeResponse:
    def __init__(
        self,
        *,
        url,
        status_code,
        body=b"",
        headers=None,
    ):
        self.url = url
        self.status_code = status_code
        self.content = body
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP %s" % self.status_code)


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected outbound request: %s" % url)
        return self.responses.pop(0)


class FetchMetiNetworkPolicyTest(unittest.TestCase):
    def policy_error(self):
        error_type = getattr(fetch_module, "NetworkPolicyError", None)
        self.assertIsNotNone(
            error_type,
            "fetch.NetworkPolicyError is not implemented",
        )
        return error_type

    def test_direct_meti_hosts_are_blocked_before_session_get(self):
        for url in (
            "https://meti.go.jp/policy/anpo/list.csv",
            "https://www.meti.go.jp/policy/anpo/list.csv",
            "https://updates.meti.go.jp/policy/anpo/list.csv",
            "https://WWW.METI.GO.JP./policy/anpo/list.csv",
        ):
            with self.subTest(url=url):
                session = FakeSession([])
                with self.assertRaises(self.policy_error()):
                    fetch_module.fetch(url, session=session)
                self.assertEqual(session.calls, [])

    def test_redirect_to_meti_is_blocked_before_redirect_request(self):
        start = "https://www.mof.go.jp/list.csv"
        blocked = "https://cdn.meti.go.jp/list.csv"
        session = FakeSession([
            FakeResponse(
                url=start,
                status_code=302,
                headers={"Location": blocked},
            ),
            FakeResponse(url=blocked, status_code=200, body=b"forbidden"),
        ])

        with self.assertRaises(self.policy_error()):
            fetch_module.fetch(start, session=session)

        self.assertEqual([url for url, _ in session.calls], [start])

    def test_safe_redirect_is_followed_without_requests_auto_redirect(self):
        start = "https://www.mof.go.jp/old.csv"
        final = "https://www.mof.go.jp/new.csv"
        session = FakeSession([
            FakeResponse(
                url=start,
                status_code=302,
                headers={"Location": "/new.csv"},
            ),
            FakeResponse(url=final, status_code=200, body=b"ok"),
        ])

        fetched = fetch_module.fetch(start, session=session)

        self.assertEqual(fetched.body, b"ok")
        self.assertEqual(fetched.final_url, final)
        self.assertEqual(
            [url for url, _ in session.calls],
            [start, final],
        )
        self.assertTrue(
            all(
                call_kwargs.get("allow_redirects") is False
                for _, call_kwargs in session.calls
            )
        )

    def test_mof_absolute_meti_link_is_blocked_before_file_request(self):
        index_html = (
            '<a href="https://files.meti.go.jp/'
            'shisantouketsu20260926.csv">list</a>'
        ).encode("utf-8")
        session = FakeSession([
            FakeResponse(
                url=mof.INDEX_URL,
                status_code=200,
                body=index_html,
            ),
            FakeResponse(
                url=(
                    "https://files.meti.go.jp/"
                    "shisantouketsu20260926.csv"
                ),
                status_code=200,
                body=b"must not be requested",
            ),
        ])

        url, _, index = mof.discover(session=session)
        self.assertIsInstance(index, Fetched)
        self.assertEqual(len(session.calls), 1)
        with self.assertRaises(self.policy_error()):
            fetch_module.fetch(url, session=session)
        self.assertEqual(len(session.calls), 1)


if __name__ == "__main__":
    unittest.main()
