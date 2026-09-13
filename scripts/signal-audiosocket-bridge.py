#!/usr/bin/env python3
"""Bridge one Asterisk AudioSocket call to signal-cli's call tunnel audio."""

import json
import os
import select
import socket
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


REST_ACCOUNTS_URL = os.environ.get(
    "SIGNAL_ACCOUNTS_URL", "http://127.0.0.1:8080/v1/accounts"
)
JSON_RPC_ADDRESS = ("127.0.0.1", int(os.environ.get("SIGNAL_JSON_RPC_PORT", "6001")))
CONTROL_ADDRESS = ("0.0.0.0", int(os.environ.get("SIGNAL_BRIDGE_HTTP_PORT", "9091")))
AUDIO_ADDRESS = ("0.0.0.0", int(os.environ.get("SIGNAL_BRIDGE_AUDIO_PORT", "9092")))
RING_SECONDS = int(os.environ.get("SIGNAL_RING_SECONDS", "40"))
CLAIM_SECONDS = int(os.environ.get("SIGNAL_AUDIO_CLAIM_SECONDS", "15"))
PULSE_SERVER = os.environ.get("PULSE_SERVER", "unix:/run/spruik-pulse/native")


def get_account():
    with urllib.request.urlopen(REST_ACCOUNTS_URL, timeout=5) as response:
        accounts = json.load(response)
    if not accounts:
        raise RuntimeError("No signal-cli account is registered")
    first = accounts[0]
    if isinstance(first, str):
        return first
    for key in ("number", "account"):
        if first.get(key):
            return first[key]
    raise RuntimeError("The account endpoint returned an unknown format")


def extract_call(message):
    result = message.get("result") or {}
    params = message.get("params") or {}
    call = params.get("call") or params.get("result") or params
    if not isinstance(call, dict):
        call = {}
    if isinstance(result, dict):
        call = {**result, **call}
    return call


class SignalCall:
    def __init__(self, account, recipient):
        self.account = account
        self.recipient = recipient
        self.connection = None
        self.stream = None
        self.call_id = None
        self._write_lock = threading.Lock()

    def _send(self, method, params, request_id):
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": request_id,
        }
        with self._write_lock:
            self.stream.write((json.dumps(payload) + "\n").encode())

    def start(self):
        self.connection = socket.create_connection(JSON_RPC_ADDRESS, timeout=5)
        self.connection.settimeout(None)
        self.stream = self.connection.makefile("rwb", buffering=0)
        self._send("subscribeCallEvents", {"account": self.account}, "subscribe")
        response = json.loads(self.stream.readline())
        if response.get("error"):
            raise RuntimeError("Signal call-event subscription failed")

        self._send(
            "startCall",
            {"account": self.account, "recipient": self.recipient},
            "start-call",
        )
        deadline = time.monotonic() + RING_SECONDS
        while time.monotonic() < deadline:
            readable, _, _ = select.select([self.connection], [], [], 1)
            if not readable:
                continue
            line = self.stream.readline()
            if not line:
                raise RuntimeError("signal-cli closed the call connection")
            message = json.loads(line)
            if message.get("error"):
                raise RuntimeError("Signal rejected the call request")
            call = extract_call(message)
            if call.get("callId") is not None:
                self.call_id = call["callId"]
            state = call.get("state")
            if state:
                print(f"Signal call state: {state}", flush=True)
            if state == "CONNECTED":
                if self.call_id is None:
                    raise RuntimeError("Connected Signal call has no call ID")
                return
            if state == "ENDED":
                reason = call.get("reason", "unknown")
                raise RuntimeError(f"Signal call ended before connection ({reason})")
        raise TimeoutError("Signal call was not answered")

    def stop(self):
        if self.stream is not None and self.call_id is not None:
            try:
                self._send(
                    "hangupCall",
                    {"account": self.account, "callId": self.call_id},
                    "hangup",
                )
            except (BrokenPipeError, OSError):
                pass
        if self.stream is not None:
            try:
                self.stream.close()
            except OSError:
                pass
        if self.connection is not None:
            try:
                self.connection.close()
            except OSError:
                pass


class CallBroker:
    def __init__(self):
        self.account = get_account()
        self.recipient = os.environ.get("SIGNAL_CALL_RECIPIENT", "")
        if not self.recipient:
            raise RuntimeError("SIGNAL_CALL_RECIPIENT is not configured")
        self._call_slot = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending = None

    def start_call(self):
        if not self._call_slot.acquire(blocking=False):
            return 409, "busy"
        call = SignalCall(self.account, self.recipient)
        try:
            call.start()
        except TimeoutError as error:
            print(str(error), flush=True)
            call.stop()
            self._call_slot.release()
            return 504, "unavailable"
        except Exception as error:
            print(f"Signal call failed: {error}", flush=True)
            call.stop()
            self._call_slot.release()
            return 503, "unavailable"

        claimed = threading.Event()
        with self._pending_lock:
            self._pending = (call, claimed)
        threading.Thread(
            target=self._expire_unclaimed,
            args=(call, claimed),
            daemon=True,
        ).start()
        return 200, "connected"

    def _expire_unclaimed(self, call, claimed):
        if claimed.wait(CLAIM_SECONDS):
            return
        with self._pending_lock:
            if self._pending is not None and self._pending[0] is call:
                self._pending = None
                call.stop()
                self._call_slot.release()
                print("Connected Signal call expired before AudioSocket attached", flush=True)

    def claim_call(self):
        with self._pending_lock:
            if self._pending is None:
                return None
            call, claimed = self._pending
            self._pending = None
            claimed.set()
            return call

    def finish_call(self, call):
        call.stop()
        self._call_slot.release()


def read_exact(connection, size):
    chunks = []
    remaining = size
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def send_audio(connection, audio):
    connection.sendall(bytes((0x10,)) + len(audio).to_bytes(2, "big") + audio)


def bridge_audio(connection, call):
    pulse_env = {**os.environ, "PULSE_SERVER": PULSE_SERVER}
    playback_device = f"sink_for_signal_input_{call.call_id}"
    record_device = f"signal_output_{call.call_id}.monitor"
    common = ["--raw", "--rate=8000", "--channels=1", "--format=s16le"]
    playback = subprocess.Popen(
        ["pacat", "--playback", f"--device={playback_device}", *common],
        stdin=subprocess.PIPE,
        env=pulse_env,
    )
    record = subprocess.Popen(
        ["parec", f"--device={record_device}", *common],
        stdout=subprocess.PIPE,
        env=pulse_env,
    )
    stopped = threading.Event()

    def copy_downlink():
        try:
            while not stopped.is_set():
                audio = record.stdout.read(320)
                if not audio:
                    break
                send_audio(connection, audio)
        except (BrokenPipeError, ConnectionError, OSError):
            pass
        finally:
            stopped.set()

    downlink = threading.Thread(target=copy_downlink, daemon=True)
    downlink.start()
    connection.setblocking(True)
    print("AudioSocket bridge connected", flush=True)
    try:
        while not stopped.is_set():
            readable, _, _ = select.select([connection], [], [], 0.25)
            if not readable:
                continue
            header = read_exact(connection, 3)
            if header is None:
                break
            frame_type = header[0]
            length = int.from_bytes(header[1:3], "big")
            payload = read_exact(connection, length)
            if payload is None or frame_type == 0x00:
                break
            if frame_type == 0x10:
                playback.stdin.write(payload)
                playback.stdin.flush()
    finally:
        stopped.set()
        for process in (playback, record):
            process.terminate()
        for process in (playback, record):
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        downlink.join(timeout=2)
        print("AudioSocket bridge disconnected", flush=True)


def serve_audio(broker):
    with socket.create_server(AUDIO_ADDRESS) as listener:
        print(f"AudioSocket listening on port {AUDIO_ADDRESS[1]}", flush=True)
        while True:
            connection, _ = listener.accept()
            call = broker.claim_call()
            if call is None:
                connection.close()
                continue
            try:
                bridge_audio(connection, call)
            except Exception as error:
                print(f"AudioSocket bridge failed: {error}", flush=True)
            finally:
                connection.close()
                broker.finish_call(call)


class ControlHandler(BaseHTTPRequestHandler):
    broker = None

    def do_GET(self):
        if self.path == "/health":
            self._respond(200, "ok")
            return
        if self.path == "/call":
            status, body = self.broker.start_call()
            self._respond(status, body)
            return
        self._respond(404, "not found")

    def _respond(self, status, body):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format_string, *args):
        return


def main():
    broker = CallBroker()
    ControlHandler.broker = broker
    threading.Thread(target=serve_audio, args=(broker,), daemon=True).start()
    server = ThreadingHTTPServer(CONTROL_ADDRESS, ControlHandler)
    print(f"Signal call control listening on port {CONTROL_ADDRESS[1]}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
