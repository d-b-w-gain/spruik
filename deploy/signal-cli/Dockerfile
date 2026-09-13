ARG SIGNAL_CLI_VERSION=0.14.8

FROM alpine:3.22 AS signal-cli-release

ARG SIGNAL_CLI_VERSION
ARG SIGNAL_CLI_SHA256=36569af20c709e0c5e6e677b74f50f147b21f3740620b8a7affde70f6027f82a

ADD https://github.com/AsamK/signal-cli/releases/download/v${SIGNAL_CLI_VERSION}/signal-cli-${SIGNAL_CLI_VERSION}-Linux-native.tar.gz /tmp/signal-cli.tar.gz
RUN echo "${SIGNAL_CLI_SHA256}  /tmp/signal-cli.tar.gz" | sha256sum -c - \
    && mkdir -p /out \
    && tar -xzf /tmp/signal-cli.tar.gz -C /out \
    && test -x /out/signal-cli

FROM bbernhard/signal-cli-rest-api:latest

ARG SIGNAL_CLI_VERSION

USER root
COPY --from=signal-cli-release /out/signal-cli /opt/signal-cli-${SIGNAL_CLI_VERSION}/bin/signal-cli-native
RUN ln -sfn /opt/signal-cli-${SIGNAL_CLI_VERSION}/bin/signal-cli-native /usr/bin/signal-cli-native

LABEL org.opencontainers.image.source="https://github.com/dbwgain/spruik"
LABEL org.opencontainers.image.description="signal-cli REST API with a pinned upstream signal-cli native release"

