#!/bin/sh
set -eu

kokoro_url="${KOKORO_URL:-http://host.docker.internal:8880/v1/audio/speech}"
sounds_dir=/var/lib/asterisk/sounds/custom
moh_dir=/var/lib/asterisk/moh/spruik

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
  "${STANDARD_GREETING:-Thanks for calling. Please hold while we connect your call.}" \
  "${STANDARD_VOICE:-af_heart}" \
  "$sounds_dir/standard-greeting.sln24"
generate_prompt hold-promotion \
  "${HOLD_PROMO:-While we connect your call, thanks for holding. We will be with you shortly.}" \
  "${HOLD_VOICE:-af_heart}" \
  "$moh_dir/hold-promotion.sln24"
generate_prompt voicemail-greeting \
  "${VOICEMAIL_GREETING:-Nobody is available to take your call. Please leave a message after the tone, then hang up when you are finished.}" \
  "${VOICEMAIL_VOICE:-af_heart}" \
  "$sounds_dir/voicemail-greeting.sln24"
generate_prompt thank-you \
  "${THANK_YOU_MESSAGE:-Thank you. Your message has been recorded.}" \
  "${THANK_YOU_VOICE:-af_heart}" \
  "$sounds_dir/thank-you.sln24"

