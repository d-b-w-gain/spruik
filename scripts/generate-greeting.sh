#!/bin/sh
set -eu

profile="${1:-standard}"
daypart="${2:-day}"
call_id="$(printf '%s' "${3:-call}" | tr -cd '0-9A-Za-z._-')"
output_dir=/var/lib/asterisk/sounds/custom

while IFS= read -r agi_line && [ -n "$agi_line" ]; do
  :
done

case "$profile" in
  vip)
    message="${VIP_GREETING:-Good {daypart}, special caller. Please hold while we connect you.}"
    voice="${VIP_VOICE:-am_santa}"
    ;;
  standard)
    message="${STANDARD_GREETING:-Thanks for calling. Please hold while we connect your call.}"
    voice="${STANDARD_VOICE:-af_heart}"
    ;;
  *)
    message=""
    voice=""
    ;;
esac

message="$(printf '%s' "$message" | sed "s/{daypart}/$daypart/g")"
output_file="$output_dir/$profile-greeting.sln24"
temporary_file="$output_dir/$profile-greeting-$call_id.tmp"

if [ -n "$message" ] && \
  jq -n --arg input "$message" --arg voice "$voice" \
    '{model:"kokoro", input:$input, voice:$voice, response_format:"pcm", speed:1.0}' |
  curl --fail --silent --show-error --connect-timeout 3 --max-time 30 \
    -H 'Content-Type: application/json' \
    --data-binary @- \
    "${KOKORO_URL:-http://host.docker.internal:8880/v1/audio/speech}" \
    -o "$temporary_file" &&
  test -s "$temporary_file"; then
  mv "$temporary_file" "$output_file"
  printf 'SET VARIABLE TTS_FILE "custom/%s-greeting"\n' "$profile"
  IFS= read -r agi_response || true
else
  rm -f "$temporary_file"
  printf 'SET VARIABLE TTS_FILE ""\n'
  IFS= read -r agi_response || true
fi

