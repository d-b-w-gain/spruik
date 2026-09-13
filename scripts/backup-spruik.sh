#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
  echo "usage: $0 OUTPUT.tar.gz.age AGE_RECIPIENT" >&2
  exit 2
fi

output="$1"
recipient="$2"
case "$recipient" in
  age1*) ;;
  *) echo "AGE_RECIPIENT must be an age public recipient beginning with age1" >&2; exit 2 ;;
esac

if [ -e "$output" ]; then
  echo "Refusing to overwrite existing backup: $output" >&2
  exit 2
fi
command -v kubectl >/dev/null 2>&1 || { echo "kubectl is required" >&2; exit 1; }
command -v age >/dev/null 2>&1 || { echo "age is required" >&2; exit 1; }

umask 077
backup_tmp="$(mktemp -d)"
cleanup() {
  rm -rf -- "$backup_tmp"
}
trap cleanup EXIT HUP INT TERM

mkdir -p "$backup_tmp/manifests" "$backup_tmp/data"
kubectl -n telephony get configmap,secret,deployment,pvc -o yaml \
  > "$backup_tmp/manifests/telephony.yaml"
kubectl -n cnc-controller get deployment/signal-cli configmap/signal-call-bridge-runtime \
  secret/signal-call-bridge pvc/signal-cli-data -o yaml \
  > "$backup_tmp/manifests/signal.yaml"

asterisk_pod="$(kubectl -n telephony get pod -l app.kubernetes.io/name=asterisk -o jsonpath='{.items[0].metadata.name}')"
signal_pod="$(kubectl -n cnc-controller get pod -l app=signal-cli -o jsonpath='{.items[0].metadata.name}')"
test -n "$asterisk_pod"
test -n "$signal_pod"

kubectl -n telephony exec "$asterisk_pod" -c asterisk -- \
  tar -C /var/lib/spruik -cf - . > "$backup_tmp/data/spruik-data.tar"
kubectl -n telephony exec "$asterisk_pod" -c asterisk -- \
  tar -C /var/spool/asterisk/voicemail -cf - . > "$backup_tmp/data/voicemail.tar"
kubectl -n cnc-controller exec "$signal_pod" -- \
  tar -C /home/.local/share/signal-cli -cf - . > "$backup_tmp/data/signal-state.tar"

printf '%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$backup_tmp/created-at.txt"
tar -C "$backup_tmp" -czf - . | age -r "$recipient" -o "$output"
echo "Encrypted Spruik backup written to $output"
