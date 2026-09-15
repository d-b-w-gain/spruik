import importlib.util
import json
import os
from pathlib import Path
import socket
import unittest
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "signal_audiosocket_bridge",
    ROOT / "scripts" / "signal-audiosocket-bridge.py",
)
BRIDGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BRIDGE)


class SignalAudioSocketBridgeTests(unittest.TestCase):
    def test_multiple_accounts_require_explicit_sender(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.read.return_value = json.dumps(["sender", "recipient"]).encode()
        with patch.object(BRIDGE.urllib.request, "urlopen", return_value=response):
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "multiple accounts"):
                    BRIDGE.get_account()

    def test_configured_sender_is_selected_from_multiple_accounts(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.read.return_value = json.dumps(["sender", "recipient"]).encode()
        with patch.object(BRIDGE.urllib.request, "urlopen", return_value=response):
            with patch.dict(os.environ, {"SIGNAL_CALL_ACCOUNT": "sender"}, clear=True):
                self.assertEqual("sender", BRIDGE.get_account())

    def test_extract_call_combines_rpc_result_and_event(self):
        message = {
            "result": {"callId": 123},
            "params": {"call": {"state": "CONNECTED"}},
        }
        self.assertEqual(
            {"callId": 123, "state": "CONNECTED"},
            BRIDGE.extract_call(message),
        )

    def test_inbound_call_accepts_only_allowlisted_caller(self):
        broker = object.__new__(BRIDGE.CallBroker)
        broker.account = "spruik"
        broker.allowed_inbound_callers = {"allowed"}
        broker._call_slot = __import__("threading").Lock()
        broker._inbound_lock = __import__("threading").Lock()
        broker._inbound = None
        listener = MagicMock()

        with patch.object(broker, "_serve_inbound_call") as serve:
            broker.handle_inbound_event(
                {
                    "callId": 42,
                    "state": "RINGING_INCOMING",
                    "number": "allowed",
                    "isOutgoing": False,
                },
                listener,
            )
            for _ in range(100):
                if serve.called:
                    break
                __import__("time").sleep(0.001)

        self.assertIsNotNone(broker._inbound)
        serve.assert_called_once()
        listener.send.assert_not_called()

    def test_inbound_call_rejects_caller_outside_allowlist(self):
        broker = object.__new__(BRIDGE.CallBroker)
        broker.account = "spruik"
        broker.allowed_inbound_callers = {"allowed"}
        broker._call_slot = __import__("threading").Lock()
        broker._inbound_lock = __import__("threading").Lock()
        broker._inbound = None
        listener = MagicMock()

        broker.handle_inbound_event(
            {
                "callId": 43,
                "state": "RINGING_INCOMING",
                "number": "stranger",
                "isOutgoing": False,
            },
            listener,
        )

        self.assertIsNone(broker._inbound)
        listener.send.assert_called_once_with(
            "rejectCall",
            {"account": "spruik", "callId": 43},
            "reject-inbound-43",
        )

    def test_inbound_listener_ignores_outgoing_call_events(self):
        broker = object.__new__(BRIDGE.CallBroker)
        broker.account = "spruik"
        broker.allowed_inbound_callers = {"allowed"}
        broker._call_slot = __import__("threading").Lock()
        broker._inbound_lock = __import__("threading").Lock()
        broker._inbound = None

        broker.handle_inbound_event(
            {
                "callId": 44,
                "state": "RINGING_OUTGOING",
                "number": "allowed",
                "isOutgoing": True,
            },
            MagicMock(),
        )

        self.assertIsNone(broker._inbound)

    def test_send_audio_uses_audiosocket_slin8_frame(self):
        sender, receiver = socket.socketpair()
        try:
            BRIDGE.send_audio(sender, b"\x01\x02\x03\x04")
            self.assertEqual(b"\x10\x00\x04\x01\x02\x03\x04", receiver.recv(7))
        finally:
            sender.close()
            receiver.close()

    def test_read_exact_handles_split_tcp_reads(self):
        sender, receiver = socket.socketpair()
        try:
            sender.sendall(b"ab")
            sender.sendall(b"cde")
            self.assertEqual(b"abcde", BRIDGE.read_exact(receiver, 5))
        finally:
            sender.close()
            receiver.close()


if __name__ == "__main__":
    unittest.main()
