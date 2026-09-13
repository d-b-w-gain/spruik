from pathlib import Path
from http.server import ThreadingHTTPServer
import json
import subprocess
import tempfile
import threading
import unittest
from unittest import mock
import urllib.error
import urllib.request

from manager import server


class ManagerTests(unittest.TestCase):
    def test_prompt_validation_accepts_plain_text_and_known_voice_shape(self):
        self.assertEqual(
            server.validate_prompt({"text": "Hello there.", "voice": "af_heart"}),
            ("Hello there.", "af_heart"),
        )

    def test_prompt_validation_rejects_markup_in_voice_name(self):
        with self.assertRaises(ValueError):
            server.validate_prompt({"text": "Hello", "voice": "../../voice"})

    def test_recording_path_cannot_escape_voicemail_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(server, "VOICEMAIL_DIR", Path(directory)):
                self.assertIsNone(server.safe_recording("../secret.wav"))
                self.assertIsNotNone(server.safe_recording("20260913-120000-1.2.wav"))

    def test_status_returns_counts_without_exposing_raw_cli_output(self):
        results = iter(
            [
                subprocess.CompletedProcess([], 0, " trunk Registered ", ""),
                subprocess.CompletedProcess(
                    [], 0, "Contact:  101/sip:101@example Avail 1.0\n", ""
                ),
                subprocess.CompletedProcess([], 0, "PJSIP/101-a!context!s\n", ""),
            ]
        )
        with mock.patch.object(server, "run_asterisk", side_effect=lambda _: next(results)):
            status = server.asterisk_status()
        self.assertTrue(status["trunkRegistered"])
        self.assertTrue(status["endpoints"]["101"])
        self.assertFalse(status["endpoints"]["102"])
        self.assertEqual(status["activeChannels"], 1)
        self.assertNotIn("raw", status)

    def test_admin_token_is_not_persisted_by_browser_code(self):
        self.assertNotIn("localStorage", server.INDEX_HTML)
        self.assertNotIn("sessionStorage", server.INDEX_HTML)

    def test_event_history_contains_no_caller_field(self):
        with tempfile.TemporaryDirectory() as directory:
            history = Path(directory) / "call-history.jsonl"
            with mock.patch.object(server.time, "time", return_value=1234):
                server.append_event(history, "answered", "signal")
            self.assertEqual(
                server.read_events(history),
                [{"timestamp": 1234, "outcome": "answered", "route": "signal"}],
            )
            self.assertNotIn("caller", history.read_text(encoding="utf-8"))

    def test_retry_delivers_then_removes_retained_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            recording = Path(directory) / "message.wav"
            recording.write_bytes(b"wave")
            delivery_history = Path(directory) / "delivery-history.jsonl"
            with mock.patch.object(
                server, "DELIVERY_HISTORY_FILE", delivery_history
            ), mock.patch.object(server, "send_signal") as send:
                server.retry_voicemail(recording)
            send.assert_called_once_with("Retried Spruik voicemail.", recording)
            self.assertFalse(recording.exists())
            self.assertEqual(
                server.read_events(delivery_history)[0]["outcome"], "delivered"
            )

    def test_failed_retry_retains_recording_and_increments_attempts(self):
        with tempfile.TemporaryDirectory() as directory:
            recording = Path(directory) / "message.wav"
            recording.write_bytes(b"wave")
            delivery_history = Path(directory) / "delivery-history.jsonl"
            with mock.patch.object(
                server, "DELIVERY_HISTORY_FILE", delivery_history
            ), mock.patch.object(
                server, "send_signal", side_effect=RuntimeError("offline")
            ):
                with self.assertRaises(RuntimeError):
                    server.retry_voicemail(recording)
            self.assertTrue(recording.exists())
            metadata = json.loads(
                Path(str(recording) + ".delivery.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metadata["attempts"], 1)

    def test_http_api_requires_the_admin_token(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(httpd.shutdown)
        base_url = f"http://127.0.0.1:{httpd.server_port}"

        with mock.patch.object(server, "ADMIN_TOKEN", "test-token"):
            with self.assertRaises(urllib.error.HTTPError) as denied:
                urllib.request.urlopen(f"{base_url}/api/status", timeout=2)
            self.assertEqual(denied.exception.code, 401)

            request = urllib.request.Request(
                f"{base_url}/api/status",
                headers={"Authorization": "Bearer test-token"},
            )
            with urllib.request.urlopen(request, timeout=2) as response:
                payload = response.read()
            self.assertIn(b'"asterisk": "offline"', payload)


if __name__ == "__main__":
    unittest.main()
