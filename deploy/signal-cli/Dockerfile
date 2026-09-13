ARG SIGNAL_CLI_VERSION=0.14.8
ARG SIGNAL_CALL_TUNNEL_COMMIT=69034124090dc6ee9591b0f208f105d034a172c2

FROM alpine:3.22 AS signal-cli-release

ARG SIGNAL_CLI_VERSION
ARG SIGNAL_CLI_SHA256=36569af20c709e0c5e6e677b74f50f147b21f3740620b8a7affde70f6027f82a

ADD https://github.com/AsamK/signal-cli/releases/download/v${SIGNAL_CLI_VERSION}/signal-cli-${SIGNAL_CLI_VERSION}-Linux-native.tar.gz /tmp/signal-cli.tar.gz
RUN echo "${SIGNAL_CLI_SHA256}  /tmp/signal-cli.tar.gz" | sha256sum -c - \
    && mkdir -p /out \
    && tar -xzf /tmp/signal-cli.tar.gz -C /out \
    && test -x /out/signal-cli

FROM rust:1.91-bookworm AS signal-call-tunnel-build

ARG SIGNAL_CALL_TUNNEL_COMMIT
RUN apt-get update \
    && apt-get install -y --no-install-recommends clang cmake git libpulse-dev pkg-config protobuf-compiler \
    && rm -rf /var/lib/apt/lists/* \
    && git clone --recurse-submodules https://github.com/AsamK/signal-call-tunnel.git /src \
    && git -C /src checkout "${SIGNAL_CALL_TUNNEL_COMMIT}" \
    && git -C /src submodule update --init --recursive \
    && cargo build --release --manifest-path /src/signal-call-tunnel/Cargo.toml

FROM bbernhard/signal-cli-rest-api:latest

ARG SIGNAL_CLI_VERSION

USER root
COPY --from=signal-cli-release /out/signal-cli /opt/signal-cli-${SIGNAL_CLI_VERSION}/bin/signal-cli-native
COPY --from=signal-call-tunnel-build /src/signal-call-tunnel/target/release/signal-call-tunnel /usr/local/bin/signal-call-tunnel
COPY scripts/signal-audiosocket-bridge.py /usr/local/bin/signal-audiosocket-bridge.py
COPY deploy/signal-call-tunnel/pulse-client.conf /etc/pulse/client.conf.d/99-spruik.conf
COPY deploy/signal-cli/spruik-pulse.conf /etc/supervisor/conf.d/spruik-pulse.conf
COPY deploy/signal-cli/signal-audiosocket-bridge.conf /etc/supervisor/conf.d/signal-audiosocket-bridge.conf
RUN apt-get update \
    && apt-get install -y --no-install-recommends pulseaudio pulseaudio-utils python3 \
    && rm -rf /var/lib/apt/lists/* \
    && chmod 0755 /usr/local/bin/signal-call-tunnel /usr/local/bin/signal-audiosocket-bridge.py \
    && ln -sfn /opt/signal-cli-${SIGNAL_CLI_VERSION}/bin/signal-cli-native /usr/bin/signal-cli-native

LABEL org.opencontainers.image.source="https://github.com/d-b-w-gain/spruik"
LABEL org.opencontainers.image.description="signal-cli REST API with a pinned upstream signal-cli native release"
