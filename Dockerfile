FROM andrius/asterisk:22.10.1_debian-trixie

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gettext-base jq python3 \
    && rm -rf /var/lib/apt/lists/*

COPY config/ /opt/spruik/config/
COPY manager/ /opt/spruik/manager/
COPY scripts/entrypoint.sh scripts/generate-greeting.sh scripts/generate-static-prompts.sh scripts/send-voicemail-signal.sh scripts/log-spruik-event.py /usr/local/bin/

RUN chmod 0555 /usr/local/bin/entrypoint.sh /usr/local/bin/generate-greeting.sh /usr/local/bin/generate-static-prompts.sh /usr/local/bin/send-voicemail-signal.sh /usr/local/bin/log-spruik-event.py \
    && mkdir -p /etc/asterisk /var/lib/asterisk/agi-bin /var/lib/asterisk/sounds/custom /var/lib/asterisk/moh/spruik /var/lib/spruik /var/spool/asterisk/voicemail /var/run/asterisk \
    && ln -sf /usr/local/bin/generate-greeting.sh /var/lib/asterisk/agi-bin/generate-greeting.sh \
    && ln -sf /usr/local/bin/send-voicemail-signal.sh /var/lib/asterisk/agi-bin/send-voicemail-signal.sh \
    && ln -sf /usr/local/bin/log-spruik-event.py /var/lib/asterisk/agi-bin/log-spruik-event.py \
    && chown -R 1000:999 /etc/asterisk /var/lib/asterisk /var/lib/spruik /var/spool/asterisk /var/run/asterisk

USER 1000:999

EXPOSE 5060/udp
EXPOSE 10000-10199/udp
EXPOSE 8088/tcp

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD asterisk -rx "core show uptime" || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["/usr/sbin/asterisk", "-f", "-n"]
