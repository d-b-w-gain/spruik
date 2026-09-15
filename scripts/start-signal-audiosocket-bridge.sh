#!/bin/sh
set -eu

config_dir="${SIGNAL_BRIDGE_CONFIG_DIR:-/run/config/signal-call-bridge}"

read_setting() {
    setting_name="$1"
    default_value="$2"
    setting_path="$config_dir/$setting_name"
    if [ -r "$setting_path" ]; then
        cat "$setting_path"
    else
        printf '%s' "$default_value"
    fi
}

export SIGNAL_CALL_ACCOUNT="$(cat /run/secrets/signal-call-bridge/account)"
export SIGNAL_CALL_RECIPIENT="$(cat /run/secrets/signal-call-bridge/recipient)"
export SIGNAL_INBOUND_CALLS_ENABLED="$(read_setting inbound-calls-enabled false)"
export SIGNAL_INBOUND_GREETING="$(read_setting inbound-greeting 'Spruik is online. Signal voice is connected. The inbound call path is working.')"
export SIGNAL_INBOUND_VOICE="$(read_setting inbound-voice af_heart)"
export KOKORO_URL="$(read_setting kokoro-url 'http://kokoro-tts.tts.svc.cluster.local:8880/v1/audio/speech')"
allowed_callers="$(read_setting inbound-allowed-callers '')"
if [ -n "$allowed_callers" ]; then
    export SIGNAL_INBOUND_ALLOWED_CALLERS="$allowed_callers"
fi

exec /usr/bin/python3 /usr/local/bin/signal-audiosocket-bridge.py
