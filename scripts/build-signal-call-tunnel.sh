#!/bin/sh
set -eu

output_dir="${1:-$(pwd)/dist}"
source_repo="${SIGNAL_CALL_TUNNEL_REPO:-https://github.com/visigoth/signal-call-tunnel.git}"
source_ref="${SIGNAL_CALL_TUNNEL_REF:-main}"
mkdir -p "$output_dir"
output_dir="$(cd "$output_dir" && pwd)"

docker run --rm \
  --env SIGNAL_CALL_TUNNEL_REPO="$source_repo" \
  --env SIGNAL_CALL_TUNNEL_REF="$source_ref" \
  --volume "$output_dir:/out" \
  rust:1.91-bookworm \
  bash -c '
    set -euo pipefail
    apt-get update
    apt-get install -y --no-install-recommends ca-certificates clang cmake git libpulse-dev pkg-config protobuf-compiler
    git clone --recurse-submodules "$SIGNAL_CALL_TUNNEL_REPO" /src
    git -C /src checkout "$SIGNAL_CALL_TUNNEL_REF"
    git -C /src submodule update --init --recursive
    cargo build --release --manifest-path /src/signal-call-tunnel/Cargo.toml
    install -m 0755 /src/signal-call-tunnel/target/release/signal-call-tunnel /out/signal-call-tunnel
  '

printf 'Built %s\n' "$output_dir/signal-call-tunnel"
