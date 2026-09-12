from __future__ import annotations

import unittest

from src.fetch import Fetched
from src.sources import ofac


def fetched(body: bytes, content_type: str) -> Fetched:
    return Fetched(
        url="https://sanctionslistservice.ofac.treas.gov/api/download/SDN.CSV",
        body=body,
        sha256="test-sha256",
        filename="sdn.csv",
        http_status=200,
        final_url="https://example.test/sdn.csv",
        headers={"Content-Type": content_type},
    )


class OfacDownloadValidationTest(unittest.TestCase):

    def validator(self):
        validator = getattr(ofac, "validate_classic_download", None)
        self.assertIsNotNone(
            validator,
            "ofac.validate_classic_download がまだ実装されていない",
        )
        return validator

    def test_valid_classic_csv_is_accepted(self):
        body = (
            b'12345,"TEST PERSON","Individual","SDGT","-0-","-0-",'
            b'"-0-","-0-","-0-","-0-","-0-","-0-"\n'
        )

        self.validator()(
            fetched(body, "text/csv"),
            "SDN",
        )

    def test_valid_classic_csv_without_headers_is_accepted(self):
        body = (
            b'12345,"TEST PERSON","Individual","SDGT","-0-","-0-",'
            b'"-0-","-0-","-0-","-0-","-0-","-0-"\n'
        )

        class HeaderlessFetched:
            def __init__(self, raw: bytes):
                self.body = raw
                self.text = raw.decode("utf-8")

        self.validator()(
            HeaderlessFetched(body),
            "SDN",
        )

    def test_html_response_is_rejected(self):
        body = (
            b"<!doctype html>"
            b"<html><body>Service temporarily unavailable</body></html>"
        )

        with self.assertRaises(ofac.SchemaError):
            self.validator()(
                fetched(body, "text/html"),
                "SDN",
            )

    def test_json_error_response_is_rejected(self):
        body = b'{"error":"temporarily unavailable"}'

        with self.assertRaises(ofac.SchemaError):
            self.validator()(
                fetched(body, "application/json"),
                "SDN",
            )

    def test_empty_response_is_rejected(self):
        with self.assertRaises(ofac.SchemaError):
            self.validator()(
                fetched(b"", "text/csv"),
                "SDN",
            )


if __name__ == "__main__":
    unittest.main()
