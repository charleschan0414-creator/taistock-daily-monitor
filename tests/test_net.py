import unittest
from urllib.error import HTTPError
from unittest.mock import patch
from taistock.net import HttpClient, DataSourceError


class NetworkTests(unittest.TestCase):
    @patch("taistock.net.time.sleep")
    @patch("taistock.net.urlopen")
    def test_429_honors_retry_after_then_succeeds(self, opening, sleeping):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok": true}'
        error = HTTPError("https://example.test", 429, "rate limited", {"Retry-After": "4"}, None)
        opening.side_effect = [error, response]
        self.assertEqual(HttpClient(retries=2).json("https://example.test"), {"ok": True})
        sleeping.assert_called_once_with(4.0)

    @patch("taistock.net.urlopen")
    def test_uses_browser_compatible_user_agent_to_avoid_twse_redirect_loop(self, opening):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok": true}'
        def reject_custom_agent(request, timeout):
            self.assertEqual(request.get_header("User-agent"), "Mozilla/5.0")
            return response
        opening.side_effect = reject_custom_agent
        self.assertTrue(HttpClient(retries=1).json("https://www.twse.com.tw/test")["ok"])

    @patch("taistock.net.time.sleep")
    @patch("taistock.net.urlopen")
    def test_retries_transient_failure_then_decodes_json(self, opening, _sleep):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok": true}'
        opening.side_effect = [TimeoutError("slow"), response]
        self.assertEqual(HttpClient(retries=2).json("https://example.test"), {"ok": True})
        self.assertEqual(opening.call_count, 2)
        request = opening.call_args.args[0]
        self.assertTrue(request.get_header("User-agent").startswith("Mozilla/5.0"))
        self.assertEqual(request.get_header("Accept-encoding"), "identity")

    @patch("taistock.net.time.sleep")
    @patch("taistock.net.urlopen", side_effect=TimeoutError("slow"))
    def test_reports_source_error_after_retries(self, opening, _sleep):
        with self.assertRaisesRegex(DataSourceError, "example.test"):
            HttpClient(retries=2).json("https://example.test")


if __name__ == "__main__": unittest.main()
