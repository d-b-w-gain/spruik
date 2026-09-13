#!/usr/bin/env python3
"""Send a redacted delivery test through a configured signal-cli REST API."""

import json
import os
import urllib.request


def required(name):
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"{name} is not configured")
    return value


def main():
    api_url = required("SIGNAL_API_URL")
    sender = required("SIGNAL_NUMBER")
    recipient = required("SIGNAL_RECIPIENT")
    message = os.environ.get(
        "SIGNAL_TEST_MESSAGE",
        "Spruik delivery test: voicemail notifications are now addressed to this Signal chat.",
    )
    payload = json.dumps(
        {"number": sender, "recipients": [recipient], "message": message}
    ).encode("utf-8")
    request = urllib.request.Request(
        api_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status not in (200, 201):
            raise RuntimeError(f"Signal API returned HTTP {response.status}")
        print(f"Signal delivery test accepted (HTTP {response.status})")


if __name__ == "__main__":
    main()
