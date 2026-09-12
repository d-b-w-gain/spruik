#!/bin/sh
set -eu

kokoro_url="${KOKORO_URL:-http://host.docker.internal:8880/v1/audio/speech}"
sounds_dir=/var/lib/asterisk/sounds/custom
moh_dir=/var/lib/asterisk/moh/spruik
settings_file="${SPRUIK_SETTINGS_FILE:-/var/lib/spruik/settings.json}"

configured_value() {
  prompt_name="$1"
  field_name="$2"
  fallback="$3"
  if [ -s "$settings_file" ]; then
    saved="$(jq -r --arg prompt "$prompt_name" --arg field "$field_name" '.[$prompt][$field] // empty' "$settings_file" 2>/dev/null || true)"
    if [ -n "$saved" ]; then
      printf '%s' "$saved"
      return
    fi
  fi
  printf '%s' "$fallback"
}

generate_prompt() {
  prompt_name="$1"
  prompt_text="$2"
  prompt_voice="$3"
  output_file="$4"
  temporary_file="${output_file}.tmp"

  echo "Generating $prompt_name with Kokoro"
  jq -n --arg input "$prompt_text" --arg voice "$prompt_voice" \
    '{model:"kokoro", input:$input, voice:$voice, response_format:"pcm", speed:1.0}' |
    curl --fail --silent --show-error --retry 10 --retry-delay 2 --retry-all-errors --max-time 120 \
      -H 'Content-Type: application/json' \
      --data-binary @- \
      "$kokoro_url" \
      -o "$temporary_file"
  test -s "$temporary_file"
  mv "$temporary_file" "$output_file"
}

mkdir -p "$sounds_dir" "$moh_dir"
generate_prompt standard-greeting \
  "$(configured_value standard text "${STANDARD_GREETING:-Thanks for calling. Please hold while we connect your call.}")" \
  "$(configured_value standard voice "${STANDARD_VOICE:-af_heart}")" \
  "$sounds_dir/standard-greeting.sln24"
generate_prompt hold-promotion \
  "$(configured_value hold text "${HOLD_PROMO:-While we connect your call, thanks for holding. We will be with you shortly.}")" \
  "$(configured_value hold voice "${HOLD_VOICE:-af_heart}")" \
  "$moh_dir/hold-promotion.sln24"
generate_prompt voicemail-greeting \
  "$(configured_value voicemail text "${VOICEMAIL_GREETING:-Nobody is available to take your call. Please leave a message after the tone, then hang up when you are finished.}")" \
  "$(configured_value voicemail voice "${VOICEMAIL_VOICE:-af_heart}")" \
  "$sounds_dir/voicemail-greeting.sln24"
generate_prompt thank-you \
  "$(configured_value thank_you text "${THANK_YOU_MESSAGE:-Thank you. Your message has been recorded.}")" \
  "$(configured_value thank_you voice "${THANK_YOU_VOICE:-af_heart}")" \
  "$sounds_dir/thank-you.sln24"
