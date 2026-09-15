# Spruik

A small Asterisk PABX that registers once with a SIP carrier, uses Signal as its
primary lock-screen endpoint, keeps SIP endpoints as backup, generates speech
with an OpenAI-compatible Kokoro endpoint, and delivers unanswered-call
recordings through Signal.

The example is deliberately sanitized. It contains documentation-only IP
addresses and fictional telephone numbers, and it never commits passwords.

## What it demonstrates

- One carrier registration shared by multiple SIP handsets.
- Signal voice first, then SIP extensions `101` and `102` as fallback.
- Caller-ID normalization for local and international number formats.
- Ringback while a personalized greeting is generated.
- Answering only after TTS is ready, then playing 24 kHz signed-linear audio.
- A configurable spoken promotion while endpoints ring, which stops on answer.
- Voicemail after decline, busy, timeout, or endpoint unavailability.
- Persistent recordings with optional Signal delivery and safe failure retention.
- Docker Compose and Kubernetes deployments using the same container image.
- Regression coverage for routing and Signal delivery behaviour.

## Docker Compose

Start a Kokoro server first. The companion `tertius-kokoro` repository provides
CPU and NVIDIA Compose examples. Spruik generates its standard prompts during
startup, so it deliberately remains unready if the configured Kokoro endpoint
cannot produce them.

```sh
cp .env.example .env
# Edit .env with your carrier, LAN, extension, and greeting settings.
docker compose up -d --build
docker compose logs -f pabx
```

Configure SIP clients with the Docker host as their server, usernames `101` and
`102`, and the corresponding passwords from `.env`.

The default Compose file publishes UDP `5060` and RTP ports `10000-10199`.
`PBX_ADVERTISED_ADDRESS` must be an address that the handsets can reach. Docker
Desktop users must enable host networking support where required by their
platform; SIP/RTP is simplest on a Linux Docker host.

The first handset to answer an incoming call wins. If both decline, report busy,
become unavailable, or reach the configured timeout, the caller is asked to
leave a message.

Internal test extensions are:

- `600`: standard welcome message.
- `601`: promotional message heard while endpoints ring.
- `602`: complete voicemail recording and delivery path.

Signal delivery is optional. Set `SIGNAL_API_URL`, `SIGNAL_NUMBER`, and
`SIGNAL_RECIPIENT` for a compatible `signal-cli-rest-api` service. When these
are unset or delivery fails, Spruik retains the WAV file on the persistent
`pabx-voicemail` volume.

Experimental Signal voice-call tooling and the pinned signal-cli server upgrade
are documented in `deploy/signal-call-tunnel/README.md` and
`deploy/signal-cli/README.md`.

## Management UI

Set `SPRUIK_ADMIN_TOKEN` to a long random value to enable the built-in manager.
With Compose it listens at `http://127.0.0.1:8088` by default. Enter the token
when the page opens; it is kept only in page memory and is not written to local
or session storage.

The first management release provides:

- Sanitised trunk, Signal voice/message, endpoint, and active-channel status.
- Caller-anonymous recent call outcomes.
- Kokoro prompt editing and in-browser audio preview.
- Live installation of standard, promotional, voicemail, and confirmation prompts.
- Internal welcome, hold, and voicemail test calls to extension `101`.
- Playback, Signal retry, and deletion of retained voicemail recordings.

Set `SPRUIK_MANAGER_HOST=0.0.0.0` only when another device needs access. The
manager has no TLS terminator of its own, so remote access should go through a
private VPN or authenticated HTTPS reverse proxy. Kubernetes uses host networking
and exposes port `8088` on the node whenever the manager is enabled.

Set `SPRUIK_HEALTH_ALERTS_ENABLED=true` to send a Signal message when the SIP
trunk, Signal message service, or Signal audio bridge changes state. The monitor
records bounded service state only; it does not include caller identities.

Set `SPRUIK_AUTO_RETRY_ENABLED=true` to retry retained voicemail after 1, 5, 15,
and 60 minutes. The final delay repeats until the configured eight-attempt limit.
Every attempt remains visible in the manager, and an exhausted recording stays
available for playback or manual retry.

## Signal commands

Spruik can expose a private, read-only command console through its Signal
identity. Set `SPRUIK_SIGNAL_COMMANDS_ENABLED=true`, provide the one allowed
sender in `SPRUIK_SIGNAL_COMMAND_ALLOWED_SENDER`, and generate a separate random
`SPRUIK_SIGNAL_COMMAND_WEBHOOK_TOKEN`. Configure signal-cli-rest-api's
`RECEIVE_WEBHOOK_URL` as:

```text
http://spruik-manager.telephony.svc.cluster.local:8088/api/signal/events/<token>
```

The webhook returns before sending the reply, avoiding a JSON-RPC webhook/send
deadlock. The token is suppressed from Spruik's access log. Only direct command
messages from the allowed sender are accepted; ordinary chat, group messages,
typing events, receipts, and sent-message sync events are ignored.

Available commands are `/help`, `/status`, `/calls`, `/voicemail`, and `/ping`.
They expose bounded operational summaries without caller identities or message
contents.

## Encrypted backup

`scripts/backup-spruik.sh` exports the PBX manifests, retained voicemail,
manager state, and Signal identity state directly into an `age`-encrypted
archive. It requires an `age` public recipient and refuses to overwrite an
existing backup:

```sh
scripts/backup-spruik.sh ./spruik-$(date +%Y%m%d).tar.gz.age age1replace_me
```

Treat the encrypted archive as sensitive and keep its private key separate from
the Kubernetes server. No plaintext archive is written.

## Kubernetes

Build and publish the image, replace the example image in
`deploy/kubernetes/deployment.yaml`, and edit the non-secret settings in
`deploy/kubernetes/configmap.yaml`.

Create the Secret without committing it:

```sh
kubectl apply -f deploy/kubernetes/namespace.yaml
kubectl -n telephony create secret generic spruik-secrets \
  --from-literal=SIP_TRUNK_PASSWORD='replace-me' \
  --from-literal=EXTENSION_101_PASSWORD='replace-me' \
  --from-literal=EXTENSION_102_PASSWORD='replace-me' \
  --from-literal=SIGNAL_NUMBER='' \
  --from-literal=SIGNAL_RECIPIENT=''
kubectl apply -k deploy/kubernetes
```

The Kubernetes example uses the node network because SIP and RTP are awkward
behind an extra service-NAT layer. Change the advertised address and network
before deployment.

Set `SIGNAL_API_URL` in `deploy/kubernetes/configmap.yaml` only when a compatible
Signal service is reachable. The sender and recipient identities belong in the
Secret, never in the ConfigMap.

## Kokoro contract

The AGI script sends JSON to `POST /v1/audio/speech` with a voice, message, and
`response_format: pcm`. Kokoro returns raw mono 16-bit PCM at 24 kHz. Saving it
with the `.sln24` extension lets Asterisk play it without transcoding.

Set `KOKORO_URL` to the complete speech endpoint. In Compose it defaults to the
Docker host; in Kubernetes it defaults to the companion service in namespace
`tts`.

## Security

- Never commit `.env`, Kubernetes Secret manifests, SIP passwords, real caller
  lists, recordings, or generated audio.
- Keep SIP private or behind a VPN. Do not expose UDP `5060` directly to the
  internet.
- Replace every example password and documentation address before use.
- Review local recording and privacy laws before adding call recording.

## Project status

The portable PBX core, Signal lock-screen endpoint, operational status, recent
call outcomes, and retained-message retry are implemented. Caller-profile
management and release signing remain active work. See
[ROADMAP.md](ROADMAP.md) for the bounded v1 checklist.

## License

MIT
