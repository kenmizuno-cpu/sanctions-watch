from __future__ import annotations

import unittest

from src import source_audit as audit


SIGNED = (
    "https://example.s3.amazonaws.com/path/SDN.CSV"
    "?X-Amz-Expires=3600"
    "&X-Amz-Security-Token=SUPER-SECRET-TEMP-TOKEN"
    "&X-Amz-Credential=TEMP-CREDENTIAL"
    "&X-Amz-Signature=SECRET-SIGNATURE"
)


class Fetched:
    http_status = 200
    etag = '"etag"'
    last_modified = "Thu, 10 Sep 2026 18:31:55 GMT"
    sha256 = "abc123"
    url = (
        "https://sanctionslistservice.ofac.treas.gov/"
        "api/download/SDN.CSV"
    )
    final_url = SIGNED
    filename = "sdn.csv"
    raw_path = "data/raw/ofac_sdn/test.gz"


class Response:
    status_code = 503
    headers = {}
    url = SIGNED
    request = None


class FetchError(RuntimeError):
    pass


class SourceAuditUrlSafetyTest(unittest.TestCase):

    def test_signed_final_url_is_sanitized(self):
        row = audit.entry(
            source="ofac_sdn",
            document_role="classic_primary",
            status="fetched",
            fetched=Fetched(),
        )

        self.assertEqual(
            row["final_url"],
            "https://example.s3.amazonaws.com/path/SDN.CSV",
        )

        self.assertNotIn("X-Amz-", row["final_url"])
        self.assertNotIn("SUPER-SECRET", row["final_url"])

    def test_signed_response_url_is_sanitized_on_error(self):
        exc = FetchError("temporary failure")
        exc.response = Response()

        row = audit.error_entry(
            "ofac_sdn",
            "classic_primary",
            exc,
        )

        self.assertEqual(
            row["final_url"],
            "https://example.s3.amazonaws.com/path/SDN.CSV",
        )

        self.assertNotIn("X-Amz-", row["final_url"])
        self.assertEqual(
            row["url"],
            "https://example.s3.amazonaws.com/path/SDN.CSV",
        )
        self.assertNotIn("X-Amz-", row["url"])

    def test_signed_url_in_error_message_is_sanitized(self):
        exc = FetchError(
            "503 error while fetching "
            + SIGNED
        )

        row = audit.error_entry(
            "ofac_sdn",
            "classic_primary",
            exc,
        )

        self.assertNotIn(
            "X-Amz-",
            row["error_message"],
        )

        self.assertIn(
            "https://example.s3.amazonaws.com/path/SDN.CSV",
            row["error_message"],
        )

    def test_normal_query_url_is_preserved(self):
        value = "https://example.test/list?page=2&lang=en"

        safe = getattr(audit, "safe_audit_url", None)

        self.assertIsNotNone(
            safe,
            "safe_audit_url がまだ実装されていない",
        )

        self.assertEqual(
            safe(value),
            value,
        )


if __name__ == "__main__":
    unittest.main()
