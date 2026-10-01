from __future__ import annotations

import os
import io
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from src import notify


class NotifyDeliveryTest(unittest.TestCase):
    def test_http_delivery_error_is_recorded_without_exposing_webhook(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "outputs"
            log = io.StringIO()
            with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://example.com/secret-hook", "GITHUB_OUTPUT": str(output)}), patch("sys.argv", ["notify", "--kind", "failure"]), patch.object(notify, "post", side_effect=urllib.error.URLError("https://example.com/secret-hook")), patch("sys.stderr", log):
                self.assertEqual(notify.main(), 1)
            self.assertIn("notification_status=failed\n", output.read_text())
            self.assertNotIn("secret-hook", log.getvalue())

    def test_unconfigured_notification_is_explicitly_skipped_in_actions_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "outputs"
            with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "", "GITHUB_OUTPUT": str(output), "GITHUB_ACTIONS": "true"}), patch("sys.argv", ["notify", "--kind", "failure"]):
                self.assertEqual(notify.main(), 0)
            self.assertIn("notification_status=skipped\n", output.read_text())
            self.assertIn("notification_sent=false\n", output.read_text())

    def test_unsuccessful_configured_delivery_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "outputs"
            with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://example.com/hook", "GITHUB_OUTPUT": str(output)}), patch("sys.argv", ["notify", "--kind", "failure"]), patch.object(notify, "post", return_value=False):
                self.assertEqual(notify.main(), 1)
            self.assertIn("notification_status=failed\n", output.read_text())
            self.assertIn("notification_sent=false\n", output.read_text())

    def test_successful_delivery_is_recorded_separately_from_monitor_success(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "outputs"
            with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://example.com/hook", "GITHUB_OUTPUT": str(output)}), patch("sys.argv", ["notify", "--kind", "failure"]), patch.object(notify, "post", return_value=True):
                self.assertEqual(notify.main(), 0)
            self.assertIn("notification_status=sent\n", output.read_text())
            self.assertIn("notification_sent=true\n", output.read_text())


if __name__ == "__main__":
    unittest.main()
