#!/usr/bin/env python3
"""Place a short experimental Signal call from the configured signal-cli account."""

import json
import os
import select
import socket
import time
import urllib.request


REST_ACCOUNTS_URL = "http://127.0.0.1:8080/v1/accounts"
JSON_RPC_ADDRESS = ("127.0.0.1", 6001)
RING_SECONDS = 30


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


def send(stream, method, params, request_id):
    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "id": request_id,
    }
    stream.write((json.dumps(payload) + "\n").encode())


def safe_status(message):
    error = message.get("error")
    if error:
        return f"RPC error {error.get('code', 'unknown')}"

    result = message.get("result") or {}
    params = message.get("params") or {}
    call = params.get("call") or params.get("result") or params
    if not isinstance(call, dict):
        call = {}
    if isinstance(result, dict):
        call = {**result, **call}

    state = call.get("state")
    reason = call.get("reason") or params.get("reason")
    call_id = call.get("callId")
    event = message.get("method")
    details = [item for item in (event, state) if item]
    if reason:
        details.append(f"reason={reason}")
    if call_id is not None:
        details.append("call-id-received")
    return " / ".join(details) or "RPC response received"


def main():
    account = get_account()
    recipient = os.environ.get("SIGNAL_CALL_RECIPIENT", account)
    call_id = None
    deadline = time.monotonic() + RING_SECONDS

    with socket.create_connection(JSON_RPC_ADDRESS, timeout=5) as connection:
        connection.settimeout(None)
        stream = connection.makefile("rwb", buffering=0)

        send(stream, "subscribeCallEvents", {"account": account}, "subscribe")
        line = stream.readline()
        if not line:
            raise RuntimeError("signal-cli closed the JSON-RPC connection")
        subscription = json.loads(line)
        print(f"Subscribe: {safe_status(subscription)}", flush=True)
        if subscription.get("error"):
            return 2

        send(
            stream,
            "startCall",
            {"account": account, "recipient": recipient},
            "start-call",
        )
        print(f"Call requested; observing for {RING_SECONDS} seconds", flush=True)

        while time.monotonic() < deadline:
            readable, _, _ = select.select([connection], [], [], 1)
            if not readable:
                continue
            line = stream.readline()
            if not line:
                break
            message = json.loads(line)
            print(safe_status(message), flush=True)
            result = message.get("result") or {}
            params = message.get("params") or {}
            call = params.get("call") or params.get("result") or params
            for candidate in (result, call):
                if isinstance(candidate, dict) and candidate.get("callId") is not None:
                    call_id = candidate["callId"]
            if message.get("error"):
                return 3
            state = call.get("state") if isinstance(call, dict) else None
            if state == "ENDED":
                # Keep the RPC subscription alive for the full experiment. This
                # distinguishes a remote Signal hangup from our client exiting.
                call_id = None

        if call_id is not None:
            send(
                stream,
                "hangupCall",
                {"account": account, "callId": call_id},
                "hangup",
            )
            print("Hangup requested", flush=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
