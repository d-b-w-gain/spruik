#!/bin/sh
set -u

recording="${1:-}"
caller="$(printf '%s' "${2:-unknown}" | tr -cd '0-9')"
signal_api_url="${SIGNAL_API_URL:-}"
signal_number="${SIGNAL_NUMBER:-}"
signal_recipient="${SIGNAL_RECIPIENT:-$signal_number}"

while IFS= read -r agi_line && [ -n "$agi_line" ]; do
  :
done

set_agi_variable() {
  printf 'SET VARIABLE SIGNAL_SENT "%s"\n' "$1"
  IFS= read -r agi_response || true
}

if [ -z "$recording" ] || [ -z "$signal_api_url" ] || \
  [ -z "$signal_number" ] || [ -z "$signal_recipient" ]; then
  set_agi_variable 0
  exit 0
fi

filename="$(basename "$recording" | tr -cd '0-9A-Za-z._-')"
response_file="${recording}.signal-response"
if [ -s "$recording" ]; then
  message="New Spruik voicemail from ${caller:-unknown caller}."
else
  message="Missed Spruik call from ${caller:-unknown caller}; no voicemail was recorded."
fi

http_code="$(python3 - "$signal_number" "$signal_recipient" "$message" "$recording" "$filename" <<'PY' |
import base64
import json
from pathlib import Path
import sys

number, recipient, message, recording, filename = sys.argv[1:]
payload = {
    "number": number,
    "recipients": [recipient],
    "message": message,
    "notify_self": number == recipient,
}
recording_path = Path(recording)
if recording_path.is_file() and recording_path.stat().st_size:
    encoded = base64.b64encode(recording_path.read_bytes()).decode("ascii")
    payload["base64_attachments"] = [
        f"data:audio/wav;filename={filename};base64,{encoded}"
    ]
json.dump(payload, sys.stdout, separators=(",", ":"))
PY
  curl --silent --show-error --connect-timeout 5 --max-time 90 \
    -H 'Content-Type: application/json' \
    --data-binary @- \
    --output "$response_file" \
    --write-out '%{http_code}' \
    "$signal_api_url" 2>/dev/null || printf '000')"

rm -f "$response_file"
case "$http_code" in
  2??)
    python3 /usr/local/bin/log-spruik-event.py delivery delivered signal 2>/dev/null || true
    rm -f "${recording}.delivery.json"
    rm -f "$recording"
    set_agi_variable 1
    ;;
  *)
    python3 - "$recording" "$http_code" <<'PY'
import json
from pathlib import Path
import sys
import time

recording, http_status = sys.argv[1:]
path = Path(recording + ".delivery.json")
try:
    previous = json.loads(path.read_text(encoding="utf-8"))
except (FileNotFoundError, json.JSONDecodeError, OSError):
    previous = {}
path.write_text(json.dumps({
    "attempts": int(previous.get("attempts", 0)) + 1,
    "lastAttempt": int(time.time()),
    "lastStatus": "failed",
    "httpStatus": http_status,
}, separators=(",", ":")) + "\n", encoding="utf-8")
PY
    python3 /usr/local/bin/log-spruik-event.py delivery retained signal 2>/dev/null || true
    printf 'VERBOSE "Signal voicemail delivery failed with HTTP status %s; notification retained for retry" 1\n' "$http_code"
    IFS= read -r agi_response || true
    set_agi_variable 0
    ;;
esac
