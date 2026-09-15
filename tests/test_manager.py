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

    def test_automatic_retry_uses_bounded_backoff(self):
        with tempfile.TemporaryDirectory() as directory:
            recording = Path(directory) / "message.wav"
            recording.write_bytes(b"wave")
            with mock.patch.object(server, "AUTO_RETRY_DELAYS", (60, 300, 900)), mock.patch.object(
                server, "AUTO_RETRY_MAX_ATTEMPTS", 4
            ):
                self.assertFalse(
                    server.automatic_retry_due(
                        recording,
                        {"attempts": 1, "lastAttempt": 1000},
                        now=1059,
                    )
                )
                self.assertTrue(
                    server.automatic_retry_due(
                        recording,
                        {"attempts": 1, "lastAttempt": 1000},
                        now=1060,
                    )
                )
                self.assertTrue(
                    server.automatic_retry_due(
                        recording,
                        {"attempts": 2, "lastAttempt": 1000},
                        now=1300,
                    )
                )
                self.assertFalse(
                    server.automatic_retry_due(
                        recording,
                        {"attempts": 4, "lastAttempt": 0},
                        now=999999,
                    )
                )

    def test_manual_retry_remains_available_after_exhaustion(self):
        with tempfile.TemporaryDirectory() as directory:
            recording = Path(directory) / "message.wav"
            recording.write_bytes(b"wave")
            server.save_delivery_state(recording, 8, "exhausted", last_attempt=1000)
            delivery_history = Path(directory) / "delivery-history.jsonl"
            with mock.patch.object(
                server, "DELIVERY_HISTORY_FILE", delivery_history
            ), mock.patch.object(server, "send_signal"):
                server.retry_voicemail(recording, "manual")
            self.assertFalse(recording.exists())
            self.assertEqual(
                server.read_events(delivery_history)[0]["route"], "signal-manual"
            )

    def test_signal_command_accepts_only_allowlisted_direct_slash_commands(self):
        payload = {
            "jsonrpc": "2.0",
            "method": "receive",
            "params": {
                "account": "+61000000000",
                "envelope": {
                    "sourceNumber": "+61000000001",
                    "timestamp": 1234,
                    "dataMessage": {"timestamp": 1234, "message": "/status"},
                },
            },
        }
        with mock.patch.object(server, "SIGNAL_NUMBER", "+61000000000"), mock.patch.object(
            server, "SIGNAL_COMMAND_ALLOWED_SENDER", "+61000000001"
        ):
            event = server.command_event(payload)
            self.assertEqual(event["message"], "/status")
            payload["params"]["envelope"]["dataMessage"]["groupInfo"] = {"groupId": "x"}
            self.assertIsNone(server.command_event(payload))
            payload["params"]["envelope"]["dataMessage"].pop("groupInfo")
            payload["params"]["envelope"]["sourceNumber"] = "+61000000002"
            self.assertIsNone(server.command_event(payload))

    def test_command_deduplication_persists_only_a_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "signal-command-state.json"
            with mock.patch.object(server, "SIGNAL_COMMAND_STATE_FILE", state):
                self.assertTrue(server.remember_command("abc123"))
                self.assertFalse(server.remember_command("abc123"))
            content = state.read_text(encoding="utf-8")
            self.assertIn("abc123", content)
            self.assertNotIn("source", content)

    def test_status_command_contains_operational_summary_without_contacts(self):
        status = {
            "asterisk": "online",
            "trunkRegistered": True,
            "signalBridge": "online",
            "signalMessaging": "online",
            "activeChannels": 2,
        }
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            server, "VOICEMAIL_DIR", Path(directory)
        ), mock.patch.object(server, "asterisk_status", return_value=status):
            response = server.command_response("/status")
        self.assertIn("Carrier trunk: online", response)
        self.assertIn("Active channels: 2", response)
        self.assertNotIn("caller", response.lower())

    def test_signal_payload_can_reply_to_an_allowlisted_sender(self):
        with mock.patch.object(server, "SIGNAL_API_URL", "http://signal.invalid"), mock.patch.object(
            server, "SIGNAL_NUMBER", "+61000000000"
        ):
            payload = server.signal_payload("reply", recipient="+61000000001")
        self.assertEqual(payload["recipients"], ["+61000000001"])

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

    def test_signal_webhook_requires_its_separate_token(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(httpd.shutdown)
        base_url = f"http://127.0.0.1:{httpd.server_port}"
        body = json.dumps({"method": "receive", "params": {}}).encode("utf-8")

        with mock.patch.object(server, "SIGNAL_COMMANDS_ENABLED", True), mock.patch.object(
            server, "SIGNAL_COMMAND_WEBHOOK_TOKEN", "webhook-token"
        ), mock.patch.object(server, "enqueue_signal_command", return_value=True):
            denied = urllib.request.Request(
                f"{base_url}/api/signal/events/wrong-token",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as response:
                urllib.request.urlopen(denied, timeout=2)
            self.assertEqual(response.exception.code, 404)

            accepted = urllib.request.Request(
                f"{base_url}/api/signal/events/webhook-token",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(accepted, timeout=2) as response:
                self.assertEqual(response.status, 200)


if __name__ == "__main__":
    unittest.main()
