#!/bin/sh
set -eu

export PBX_LOCAL_NET="${PBX_LOCAL_NET:-192.0.2.0/24}"
export PBX_ADVERTISED_ADDRESS="${PBX_ADVERTISED_ADDRESS:-192.0.2.10}"
export TIMEZONE="${TIMEZONE:-Australia/Sydney}"
export RING_SECONDS="${RING_SECONDS:-35}"
export SIP_TRUNK_HOST="${SIP_TRUNK_HOST:-sip.example.invalid}"
export SIP_TRUNK_USERNAME="${SIP_TRUNK_USERNAME:-replace-me}"
export SIP_TRUNK_PASSWORD="${SIP_TRUNK_PASSWORD:-replace-me}"
export SIP_TRUNK_MATCH_IP="${SIP_TRUNK_MATCH_IP:-198.51.100.10}"
export EXTENSION_101_PASSWORD="${EXTENSION_101_PASSWORD:-replace-me}"
export EXTENSION_102_PASSWORD="${EXTENSION_102_PASSWORD:-replace-me}"
export VIP_NUMBER_LOCAL="${VIP_NUMBER_LOCAL:-0400000000}"
export VIP_NUMBER_INTL="${VIP_NUMBER_INTL:-61400000000}"

for name in SIP_TRUNK_PASSWORD EXTENSION_101_PASSWORD EXTENSION_102_PASSWORD; do
  value="$(printenv "$name")"
  case "$value" in
    *'
'*) echo "$name must not contain a newline" >&2; exit 1 ;;
  esac
done

cp /opt/spruik/config/asterisk.conf /opt/spruik/config/logger.conf \
  /opt/spruik/config/modules.conf /opt/spruik/config/rtp.conf \
  /opt/spruik/config/musiconhold.conf /etc/asterisk/

envsubst '${PBX_LOCAL_NET} ${PBX_ADVERTISED_ADDRESS} ${SIP_TRUNK_HOST} ${SIP_TRUNK_USERNAME} ${SIP_TRUNK_PASSWORD} ${SIP_TRUNK_MATCH_IP} ${EXTENSION_101_PASSWORD} ${EXTENSION_102_PASSWORD}' \
  < /opt/spruik/config/pjsip.conf.template > /etc/asterisk/pjsip.conf

envsubst '${TIMEZONE} ${RING_SECONDS} ${VIP_NUMBER_LOCAL} ${VIP_NUMBER_INTL}' \
  < /opt/spruik/config/extensions.conf.template > /etc/asterisk/extensions.conf

chmod 0600 /etc/asterisk/pjsip.conf
/usr/local/bin/generate-static-prompts.sh
exec "$@"
