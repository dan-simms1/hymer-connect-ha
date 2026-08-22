# Changelog

The authoritative integration changelog lives in:

- `custom_components/hymer_connect_metadata/CHANGELOG.md`

Current repository state:

- `1.1.0` — adds a local BLE transport to the integration: control the vehicle
  over Bluetooth (with cloud/BLE transport selection) and mint the remote-access
  token by pairing over BLE from the reconfigure flow. Built from our own proven
  encoder with the working writes the prior art lacked. BLE is off by default;
  cloud-only behaviour is unchanged. HA-integration BLE paths not yet re-verified
  on a live vehicle.
- `1.0.31` — bumps `APP_VERSION` to 2.10.16 after verifying against the
  decompiled current app that all extractor endpoints, OAuth creds and the PIA
  version are unchanged; app-side changes are additive and don't affect us.
- `1.0.30` — completes the PIA status enum, adds bleak 3.x support and the
  Linux-host BLE field tooling; secrets via `0600` files only, actuation gated
  on explicit confirmation and target ids. **BLE pairing and control now work
  end to end against the vehicle with no LTE**: from a Linux host in the van a
  `PairMobileRequest` minted a remote refresh token over BLE and a `setValues`
  write returned SUCCESS and actuated the load. The fix was writing the SCU
  UART RX with-response (the app's `WRITE_TYPE_DEFAULT`); without it a pairing
  request overran the SCU buffer at low MTU and was dropped.
- `1.0.29` — keeps a working SignalR connection when a single subscription
  fails with a transient status such as `SCU_IS_NOT_ONLINE`, instead of tearing
  it down and leaving the vehicle uncontrollable while LTE is marginal.
- `1.0.28` — adds the BLE control-write encoding to the token tool, built from
  the decompiled Android app: `setValues` value writes and SCU restart, with
  acknowledgement judged on a matching request id. Untested against a vehicle.
- `1.0.27` — stops a reconnect storm when the vehicle is unreachable (2,142
  attempts in twelve hours when the van lost LTE) and forces a full re-auth
  once a session has failed repeatedly, rather than retrying a dead one.
- `1.0.26` — removes the proactive session-auth renewal added in `1.0.25`
  (never released). Two field measurements showed it could not work: it first
  renewed the wrong credential, then used an interval longer than the expiry
  gap it was meant to pre-empt, which a reactive refresh resets. The reactive
  path handles expiry correctly and the keepalive poll surfaces it while idle.
- `1.0.25` — keeps the SignalR session alive between pushes: a keepalive poll
  so the SCU does not fall silent, and a post-reconnect wait so commands are
  not sent into a window where the SCU drops them.
- `1.0.24` — fixes fuel level reporting 0% for a full tank (a spurious
  `invert100` transform on the `FuelTankLevel` slots). Existing installs must
  regenerate their metadata pack, or hand-edit `data/sensor_labels.json`, to
  pick this up.
- `1.0.23` — adds an MIT `LICENSE` at the repository root (matching upstream,
  retaining BetaHydri's copyright notice) and updates `ATTRIBUTION.md`
  accordingly. Unblocks the HACS default-catalog submission.
- `1.0.22` — ports two further upstream cloud fixes: a SignalR rapid-reconnect
  cooldown (avoids Azure dropping reconnects after a short-lived session) and
  the `device_tracker` TrackerEntity import deprecation fix.
