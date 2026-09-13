# Spruik signal-cli image

This image keeps the `signal-cli-rest-api` HTTP/JSON-RPC wrapper while replacing
its bundled signal-cli executable with a pinned official upstream native release.
The release archive is verified during the build with its SHA-256 digest.

Build version 0.14.8:

```sh
docker build -t spruik/signal-cli-rest-api:0.14.8 deploy/signal-cli
```

For a cluster that already runs the community REST image, the deployment overlay
provides the same pinned upgrade without requiring a container registry:

```sh
kubectl -n cnc-controller patch deployment signal-cli \
  --type=strategic \
  --patch-file deploy/signal-cli/deployment-overlay.yaml
```

The Signal account data remains outside the image on the deployment's persistent
volume. Run this workload as a single replica with `maxSurge: 0`; simultaneous
daemons must not open the same signal-cli account database.
