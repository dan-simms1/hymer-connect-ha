# Changelog

The authoritative integration changelog lives in:

- `custom_components/hymer_connect_metadata/CHANGELOG.md`

Current repository state:

- `2.0.1` — makes in-Home-Assistant metadata provisioning work for a **fresh
  install**: the config flow now asks for a HYMER APK URL as its first step and
  builds the pack (incl `oauth_client.json`) before sign-in, instead of
  dead-ending because auth needs a pack that only an existing entry could
  provision. Adds SCU Bluetooth-address discovery (pick the SCU from a list
  instead of typing its MAC), and fixes the setup instructions accordingly
  (first-run flow, no-decompiler note, options list, and a leftover upstream
  signpost).
- `2.0.0` — **stable release of the 2.0.0 line** (no code change from `2.0.0b3`;
  promoted out of beta after live verification on a vehicle). Headlines: Home
  Assistant builds its own runtime metadata from a HYMER APK in-app (pure-Python
  Hermes reader, no decompiler) via a Repair flow / options action; a
  security-hardened, fully-bounded ingestion path for the untrusted APK (from a
  three-round external review); and the battery/scenario/dashboard fixes. Repo
  tidy-up: the desktop token tool marked archived / research-only in place, the
  "use the upstream repo" signposts removed (BetaHydri attribution kept), and the
  README metadata section updated to the in-HA flow.
- `2.0.0b3` (beta) — security and robustness hardening of the in-HA APK
  provisioning, from a full external review. The APK is untrusted input parsed
  inside Home Assistant, so every read and reconstruction is now bounded and
  fail-closed: uncompressed-bundle and compression-ratio caps (zip-bomb
  defence), header-offset validation, array/object/instruction budgets, an
  iterative depth/node-bounded graph clean (no more process-wide recursion-limit
  change), HTTPS-only downloads with catalog-invariant validation, and a
  transactional locked pack swap with rollback. Also fixes stale-register leakage
  (vehicle **group** now resolves to its real name, not `0`), binds the OAuth
  client from the same config object, recovers light display names on the APK
  path, disambiguates scenario/scene id collisions with a migration, and reloads
  every entry after an options-dialog rebuild. Reconstruction is pinned to
  Hermes v96. Backward-compatible; no change for a normal APK.
- `2.0.0b2` (beta) — **the integration can build its own runtime metadata from a
  HYMER APK, inside Home Assistant, with no external toolchain.** A pure-Python
  Hermes-bytecode reader reconstructs the catalog object literals (including
  nested ranges, enum options and scenario actions) and the OAuth client
  straight from the app's bytecode — no `hermes-dec`, no decompiler. Wired into a
  fixable repair flow (first-time setup) and an options action (rebuild). The
  offline `prepare_runtime_metadata.py` no longer needs a decompiler either.
  Additive and backward-compatible for existing installs.
- `1.1.1` — hardening pass over the 1.1.0 BLE transport (a full review-and-fix
  loop) plus a Hassfest CI fix (manifest key ordering). Fail-closed vehicle
  binding and a pre-pairing identity-collision check in the QR/BLE reconfigure,
  BLE session robustness (locking, teardown, monotonic shutdown), pairing/agent
  correctness, a clearer error taxonomy, a unit-test CI workflow, and docs
  reconciled to the verified BLE result. BLE still off by default; HA-integration
  BLE paths still pending live-vehicle re-verification.
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
