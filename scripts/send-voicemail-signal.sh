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

if [ -z "$recording" ] || [ ! -s "$recording" ] || \
  [ -z "$signal_api_url" ] || [ -z "$signal_number" ] || [ -z "$signal_recipient" ]; then
  set_agi_variable 0
  exit 0
fi

filename="$(basename "$recording" | tr -cd '0-9A-Za-z._-')"
message="New Spruik voicemail from ${caller:-unknown caller}."
response_file="${recording}.signal-response"
attachment="data:audio/wav;filename=${filename};base64,$(base64 "$recording" | tr -d '\r\n')"

http_code="$(jq -n \
  --arg number "$signal_number" \
  --arg recipient "$signal_recipient" \
  --arg message "$message" \
  --arg attachment "$attachment" \
  '{number:$number, recipients:[$recipient], message:$message, base64_attachments:[$attachment], notify_self:($number == $recipient)}' |
  curl --silent --show-error --connect-timeout 5 --max-time 90 \
    -H 'Content-Type: application/json' \
    --data-binary @- \
    --output "$response_file" \
    --write-out '%{http_code}' \
    "$signal_api_url" 2>/dev/null || printf '000')"

rm -f "$response_file"
case "$http_code" in
  2??)
    rm -f "$recording"
    set_agi_variable 1
    ;;
  *)
    printf 'VERBOSE "Signal voicemail delivery failed with HTTP status %s; recording retained" 1\n' "$http_code"
    IFS= read -r agi_response || true
    set_agi_variable 0
    ;;
esac

