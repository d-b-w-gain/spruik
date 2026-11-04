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

    def _announce_broker(self):
        broker = object.__new__(BRIDGE.CallBroker)
        broker.account = "spruik"
        broker.recipient = "recipient"
        broker._call_slot = __import__("threading").Lock()
        return broker

    def test_announce_speaks_twice_after_answer_and_hangs_up(self):
        broker = self._announce_broker()
        call = MagicMock()
        with patch.object(BRIDGE, "SignalCall", return_value=call), \
                patch.object(BRIDGE, "synthesize", return_value=b"\x01\x02") as speak, \
                patch.object(BRIDGE, "play_pcm") as play, \
                patch.object(BRIDGE.time, "sleep"):
            self.assertEqual((200, "answered"), broker.announce("Coffee is waiting", "af_bella"))
        speak.assert_called_once_with("Coffee is waiting", "af_bella")
        call.start.assert_called_once()
        audio = play.call_args.args[1]
        self.assertEqual(2, audio.count(b"\x01\x02"))
        call.stop.assert_called_once()
        self.assertTrue(broker._call_slot.acquire(blocking=False))

    def test_announce_reports_unanswered_and_frees_the_line(self):
        broker = self._announce_broker()
        call = MagicMock()
        call.start.side_effect = TimeoutError("Signal call was not answered")
        with patch.object(BRIDGE, "SignalCall", return_value=call), \
                patch.object(BRIDGE, "synthesize", return_value=b"\x00"), \
                patch.object(BRIDGE, "play_pcm") as play:
            self.assertEqual((504, "unanswered"), broker.announce("Coffee is waiting"))
        play.assert_not_called()
        call.stop.assert_called_once()
        self.assertTrue(broker._call_slot.acquire(blocking=False))

    def test_announce_does_not_interrupt_a_live_call(self):
        broker = self._announce_broker()
        broker._call_slot.acquire()
        with patch.object(BRIDGE, "SignalCall") as make_call:
            self.assertEqual((409, "busy"), broker.announce("Coffee is waiting"))
        make_call.assert_not_called()

    def test_announce_rejects_empty_or_long_text(self):
        broker = self._announce_broker()
        self.assertEqual(400, broker.announce("   ")[0])
        self.assertEqual(400, broker.announce("x" * 401)[0])

    def test_outgoing_call_plays_into_its_signal_input_device(self):
        call = BRIDGE.SignalCall("spruik", "recipient")
        call.call_id = 7
        with patch.object(BRIDGE.subprocess, "run") as run:
            BRIDGE.play_pcm(call, b"\x00\x00")
        self.assertIn("--device=sink_for_signal_input_7", run.call_args.args[0])

    def test_negative_call_ids_map_to_the_tunnels_unsigned_device_names(self):
        # Seen live: signal-cli reported -7635838990837394565; the tunnel's sink was
        # sink_for_signal_input_10810905082872157051.
        call = BRIDGE.SignalCall("spruik", "recipient")
        call.call_id = -7635838990837394565
        with patch.object(BRIDGE.subprocess, "run") as run:
            BRIDGE.play_pcm(call, b"\x00\x00")
        self.assertIn(
            "--device=sink_for_signal_input_10810905082872157051", run.call_args.args[0]
        )

    def test_reported_device_name_is_preferred(self):
        call = BRIDGE.SignalCall("spruik", "recipient")
        call.call_id = 1
        call.input_device_name = "signal_input_custom"
        with patch.object(BRIDGE.subprocess, "run") as run:
            BRIDGE.play_pcm(call, b"\x00\x00")
        self.assertIn("--device=sink_for_signal_input_custom", run.call_args.args[0])

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
