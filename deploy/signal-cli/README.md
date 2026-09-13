# Spruik signal-cli image

This image keeps the `signal-cli-rest-api` HTTP/JSON-RPC wrapper while replacing
its bundled signal-cli executable with a pinned official upstream native release.
It also packages the patched RingRTC call tunnel, PulseAudio, and Spruik's
Asterisk AudioSocket bridge. The signal-cli release archive is verified during
the build with its SHA-256 digest.

Build version 0.14.8:

```sh
docker build \
  -f deploy/signal-cli/Dockerfile \
  -t spruik/signal-cli-rest-api:0.14.8-call-bridge \
  .
```

Create the receiving Signal identity as a secret before applying the deployment
overlay:

```sh
kubectl -n cnc-controller create secret generic signal-call-bridge \
  --from-literal=account='+61000000000' \
  --from-literal=recipient='+61000000001'
```

For a cluster that already runs the community REST image, the deployment overlay
selects the Spruik image and preserves the single-writer rollout strategy:

```sh
kubectl -n cnc-controller patch deployment signal-cli \
  --type=strategic \
  --patch-file deploy/signal-cli/deployment-overlay.yaml
```

The Signal account data remains outside the image on the deployment's persistent
volume. Run this workload as a single replica with `maxSurge: 0`; simultaneous
daemons must not open the same signal-cli account database.
