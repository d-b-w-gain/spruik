import importlib.util
from pathlib import Path
import socket
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "signal_audiosocket_bridge",
    ROOT / "scripts" / "signal-audiosocket-bridge.py",
)
BRIDGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BRIDGE)


class SignalAudioSocketBridgeTests(unittest.TestCase):
    def test_extract_call_combines_rpc_result_and_event(self):
        message = {
            "result": {"callId": 123},
            "params": {"call": {"state": "CONNECTED"}},
        }
        self.assertEqual(
            {"callId": 123, "state": "CONNECTED"},
            BRIDGE.extract_call(message),
        )

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
