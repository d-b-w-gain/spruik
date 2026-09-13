#!/bin/sh
set -eu

output_dir="${1:-$(pwd)/dist}"
mkdir -p "$output_dir"
output_dir="$(cd "$output_dir" && pwd)"

docker run --rm \
  --volume "$output_dir:/out" \
  rust:1.91-bookworm \
  bash -c '
    set -euo pipefail
    apt-get update
    apt-get install -y --no-install-recommends ca-certificates clang cmake git libpulse-dev pkg-config protobuf-compiler
    git clone --depth 1 --recurse-submodules --shallow-submodules https://github.com/visigoth/signal-call-tunnel.git /src
    cargo build --release --manifest-path /src/signal-call-tunnel/Cargo.toml
    install -m 0755 /src/signal-call-tunnel/target/release/signal-call-tunnel /out/signal-call-tunnel
  '

printf 'Built %s\n' "$output_dir/signal-call-tunnel"
