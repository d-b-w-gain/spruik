# Experimental Signal voice calls

Spruik can exercise signal-cli's experimental one-to-one calling API with the
separate `signal-call-tunnel` RingRTC process.

Build the tunnel on a Docker host:

```sh
scripts/build-signal-call-tunnel.sh dist
```

The Linux tunnel uses PulseAudio virtual devices. `pulse-client.conf` points the
tunnel at a private PulseAudio socket; production deployment still needs a
supervised PulseAudio process and an Asterisk-to-PulseAudio media bridge.

`signal-self-call.py` is a diagnostic client. It subscribes to call events,
places a short call to the signal-cli account, reports only redacted call state,
and hangs up after the observation window.

## Known limitation

A same-account call can ring another linked Signal device through Signal's push
and native call UI, but the current experimental implementation does not form a
stable media session. Testing exposed three linked-device problems:

1. a returned offer can replace the outgoing call state;
2. a returned busy response can remove the outgoing call before another linked
   device answers;
3. signal-cli hardcodes RingRTC's local device ID to `1` rather than using the
   account's actual linked-device ID.

The local patch documents all three experiments. The generally applicable
device-ID correction is maintained separately in the signal-cli upstream fork
so it can be reviewed without Spruik-specific code.

The reliable production design remains two distinct Signal identities: one for
Spruik and one for the receiving phone. That gives RingRTC two real peers and
avoids same-account coordination semantics.
