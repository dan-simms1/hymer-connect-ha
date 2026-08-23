# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.0.0] - 2026-08-23

Stable release of the 2.0.0 line. No code change from `2.0.0b3` — this promotes
the beta out of pre-release after live verification on a vehicle (cloud control
and the regenerated dashboard confirmed on a Grand Canyon S 700).

Headlines, consolidated from the beta entries below:

- **Home Assistant can build its own runtime metadata from a HYMER APK, in-app,
  with no external toolchain.** A pure-Python Hermes-bytecode reader reconstructs
  the catalogs (including nested ranges, enum options and scenario actions) and
  the OAuth client straight from the app's bytecode — no `hermes-dec`, no
  decompiler — wired into a fixable Repair flow and an options action. The
  offline `prepare_runtime_metadata.py` no longer needs a decompiler either.
- **Security-hardened APK ingestion.** The APK is untrusted input parsed inside
  the HA process, so the whole path is bounded and fail-closed: HTTPS-only
  (redirects included), streamed size-capped download, ZIP central-directory and
  bundle-size caps, a single shared reconstruction budget with a memoized graph
  clean, structural OAuth binding, strong catalog validation, and a transactional
  locked pack swap with rollback. (Full external review over three rounds.)
- **Battery, scenario and dashboard fixes** (generic across van types): correct
  leisure/vehicle battery labels and the BMS "time remaining" sentinel, built-in
  scenarios surfaced, and dashboard tabs aligned to the HYMER app.
- **Docs & repo tidy-up:** the desktop token tool at `tools/hymer_token_tool/`
  is now clearly marked archived / research-only (unmaintained); the
  "use/follow the upstream repository" signposts removed from the README
  while keeping the BetaHydri attribution and `ATTRIBUTION.md`; and the README
  metadata section rewritten around the in-HA provisioning flow (no decompiler).

See [2.0.0b1]–[2.0.0b3] for the detailed change list.

## [2.0.0b3] - 2026-08-23

Security and robustness hardening of the in-Home-Assistant APK provisioning,
from a full external review of the 2.0.0 beta. The APK bytes are untrusted
input parsed inside the HA process, so this pass makes every read and every
reconstruction bounded and fail-closed. No behaviour change for a normal,
trusted APK; existing installs are unaffected.

### Security

- Bound the download and archive: stream the APK to a size-capped spooled file
  (never held twice in memory), require **HTTPS** on the initial URL *and every
  redirect hop*, and reject a ZIP central-directory bomb — bounding both the
  declared entry count *and* the declared directory size (which is what actually
  drives how many records the parser reads) — before the archive is parsed.
- Bound the untrusted Hermes bundle end to end: cap the *uncompressed* bundle
  size and compression ratio (zip-bomb defence — the download cap only covered
  the compressed APK), validate every header-derived section offset and every
  string entry against the file, and bound each instruction's operand reads to
  its function body.
- Cap the reconstruction under one shared budget: charge every literal element,
  array fill, object and decoded instruction, and fail closed on an
  attacker-controlled array index — so a tiny bundle cannot balloon memory via
  repeated buffer literals or `PutOwnByIndex`.
- Make the object-graph cleanup iterative, depth- and node-bounded across all
  roots, with shared-subgraph memoization and no process-wide recursion-limit
  change — a crafted deep/cyclic/shared graph is truncated, not amplified or
  crashed. Caps are calibrated to ~15–40× the real app's output.
- OAuth credentials come only from the single reconstructed config object (v96);
  the unbounded value-buffer byte-scan fallback is gone, so no unrelated strings
  can be mistaken for the secret and parse/limit errors fail closed.
- Validate strong nested catalog invariants (and decode the Basic OAuth header
  to a real `user:password`) before publishing, so a malicious mirror cannot
  overwrite a working pack with an empty-but-plausible one.
- Publish the eight pack files **transactionally** under a lock, with rollback
  (including fresh-install removal), so an interrupted or concurrent provision
  can never leave a half-old/half-new pack; wrap filesystem errors as
  `ApkProvisionError`.

### Fixed

- Reconstruction no longer leaks stale register values into objects: unmodelled
  value-producing opcodes are invalidated, and property reads (`GetById`) are
  resolved against reconstructed enums. Vehicle **group** now recovers its real
  name (CamperVan, Integrated, …) instead of a stale `0`. Reconstruction is
  pinned to Hermes v96 (the version whose opcode table it uses).
- OAuth credentials are bound from the **same** config object (via the
  reconstruction) rather than by scanning the value buffer, so unrelated
  adjacent strings can't be mistaken for the client secret.
- Recover light-circuit/group/module display names from the reconstructed
  objects (previously dropped on the APK path, degrading ~38 names).
- A `scenario` and a `scene` that share a key (e.g. `GOOD_MORNING`) get distinct
  entity ids (keyed by kind + id + key) with a registry migration, instead of
  one silently shadowing the other.
- Rebuilding the pack from the options dialog now reloads **every** HYMER entry,
  not just the current one (the pack is global).
- Narrow the BMS "time remaining" sentinel filter to the battery-time slot and
  an exact integral `32767`/`65535`, so a genuine minute reading is never hidden.
- `executable_for_vehicle` now means "has ≥1 supported action" (matching what is
  actually exposed); the strict all-actions sense moves to
  `fully_executable_for_vehicle`.

## [2.0.0b2] - 2026-08-23

Battery, scenario and dashboard fixes on the 2.0.0 beta (all generic across van
types).

### Fixed

- **Built-in scenarios now appear.** The scenario catalog is a union across every
  provider family, so a given vehicle rarely has 100% of a scenario's actions —
  the old all-or-nothing rule meant *no* scenes were created. A scenario is now
  exposed when any of its actions are supported on the vehicle, executing that
  supported subset (e.g. "Good Night" runs the lights/actions your van has).
- **Battery runtime no longer shows a bogus value.** A BMS "time remaining" of
  `0x7FFF`/`0xFFFF` minutes (its "not applicable / not discharging" sentinel) now
  reads as *unavailable* instead of a nonsensical ~22-day figure.

### Changed

- **Clearer battery labels.** On the generated dashboard the leisure battery's
  charge is the headline "Leisure Battery" (its voltage reads "Leisure Battery
  Voltage"), with "Leisure Battery Health" and "Vehicle Battery" — so the leisure
  and vehicle batteries are unambiguous.
- **Dashboard tabs mirror the HYMER in-van app.** Tab order, titles and icons now
  follow the app's rail (Energy, Lights, Climate, Water, Components, Vehicle,
  Scenarios); section names already matched the app's sub-tabs.

## [2.0.0b1] - 2026-08-23

Beta / pre-release. Major feature: the integration can now build its own runtime
metadata from a HYMER APK, in Home Assistant, with no external toolchain — so
first-time setup no longer requires a repository checkout or a Hermes decompiler.
Existing installs are unaffected: the provisioning is opt-in and additive, and
packs already in `data/` keep working. Shipped as a beta so the in-app
APK → pack path can be exercised on real installs before a stable 2.0.0.

### Added

- **Self-provision the runtime metadata pack from a HYMER APK, inside Home
  Assistant — no external toolchain.** Point the integration at a HYMER Android
  APK URL and it downloads the APK and rebuilds the local `data/` pack
  (component/sensor/control/vehicle/scenario catalogs, coverage/support matrices,
  and the OAuth client) entirely in-process. Two entry points:
  - **Repair flow (first-time setup):** when the metadata pack is missing the
    "runtime metadata is missing" repair is now **fixable** — click *Fix*, paste
    an APK URL, and Home Assistant builds the pack and reloads the integration.
  - **Options (rebuild):** the integration's options gained an *APK URL* field
    and a *Rebuild metadata from the APK now* action, e.g. after a HYMER app
    update adds new components or sensors.

- **A pure-Python Hermes-bytecode reader (`apk_hermes.py`).** The HYMER app is a
  React-Native/Hermes build; its runtime metadata lives in JS object literals
  compiled to Hermes bytecode. This reconstructs those literals — **including the
  nested `range`/enum options and scenario action lists** — by parsing the Hermes
  function table and interpreting each function's object/array-construction
  opcodes with register tracking. No `hermes-dec`, no decompiler, no third-party
  dependencies. `apk_oauth.py` recovers the OAuth client from the same bytecode.
  Validated against Hermes bytecode version 96 (the version HYMER ships); it
  refuses unknown versions loudly rather than mis-parsing.

- **The clean-room catalog generator now ships inside the integration**
  (`metadata_overlay.py`), so the whole APK → pack pipeline runs under Home
  Assistant. The offline `scripts/prepare_runtime_metadata.py` still works and,
  given only an APK, no longer needs `--hbc-decompiler`; the `scripts/` modules
  are thin re-export shims over the shipped code.

### Changed

- The download is awaited on the event loop (with size and timeout caps); the
  heavy bytecode reconstruction and catalog generation run in the executor.
- The "runtime metadata missing" repair issue explains the in-app fix first, with
  the offline command retained as an advanced fallback.

## [1.1.1] - 2026-08-23

Hardening pass over the 1.1.0 BLE transport (a full review-and-fix loop), plus a
CI fix. BLE is still off by default; cloud-only behaviour is unchanged.

### Fixed

- **Hassfest CI.** `manifest.json` keys are now ordered `domain`, `name`, then
  alphabetical, as hassfest requires — `after_dependencies` had been placed out
  of order in 1.1.0, which failed the Hassfest workflow on `main`.
- **QR/BLE pairing binds fail-closed to the entry's own vehicle.** The owner QR
  token is validated against the entry's *stored* vehicle URN (never the
  discovery resolver's selection, whose single-vehicle fallback could be a
  different SCU), and a duplicate-entry identity collision is now detected
  *before* pairing — so a remote-access token is never minted and then
  discarded, and never bound to the wrong vehicle.
- **BLE session robustness.** Every write→response cycle (including pairing)
  holds a session-wide lock; a partial connection is torn down on any start
  failure or cancellation; a timeout/TLS/disconnect invalidates the session
  instead of being retained for a repeated 30s timeout; a routine SignalR
  teardown no longer stops a healthy BLE session; shutdown is monotonic.
- **Pairing correctness.** The confirmation is sent only after a request-id-
  correlated, success-status response; an error status is never treated as a
  minted token. Legacy PIN/passkey BlueZ agent callbacks are implemented so
  bonding does not fail when the SCU selects them. MTU acquisition probes the
  bleak backend so large frames ride a larger MTU.
- **Error taxonomy.** Cloud vs BLE vs bad-QR failures during pairing map to
  distinct, accurate messages; transient cloud failures (5xx, timeouts,
  408/425/429) are reported as retryable rather than as an invalid token.
- **Malformed frames.** The PIA frame length is capped at 64 KiB to prevent
  unbounded buffer growth on a bad header.

### Added

- Unit-test CI workflow (`.github/workflows/tests.yml`) running the full suite,
  and adversarial tests covering the above (concurrency, lifecycle, shutdown,
  vehicle binding, identity-collision, error classification).
- `options.error` translations for the Bluetooth options-flow field validation,
  in all nine shipped locales.

### Changed

- `HymerConnectApiError` now carries the HTTP status (when known) so callers can
  tell a client rejection from a transient upstream failure.
- Documentation (root README, token-tool README, BLE runbook) reconciled to the
  verified 2026-08-22 result: BLE pairing and token minting are proven end to
  end on a real vehicle (still a hands-on path needing a Linux/BlueZ host).

## [1.1.0] - 2026-08-22

### Added

- **Local BLE transport — control the vehicle over Bluetooth, and mint the
  cloud key from within Home Assistant.** Built clean-room from our own proven
  encoder (the token tool, verified on a real vehicle 2026-08-22), applying the
  Home-Assistant wiring pattern rather than any upstream code, and including the
  working BLE writes the prior art was missing.

  New modules in the integration:
  - `ble_pia.py` — PIA framing, TLS envelope, `setValues`, pairing and response
    decode; byte-identical to the proven tool encoder (a parity test guards it).
    Commands are wrapped as `BleProtocol.request` (field 1), the fix that makes
    the SCU accept them.
  - `ble_tls.py` — the legacy TLS 1.0/1.1 engine over `ssl.MemoryBIO`.
  - `ble_transport.py` — `HymerBleTransport`, wired into HA's Bluetooth stack
    (`async_ble_device_from_address` + `bleak-retry-connector`, raw-bleak
    fallback). Bonds via a device-locked `NoInputNoOutput` D-Bus pairing agent
    on the system bus (`dbus_fast`, which ships with HA), acquires a larger MTU
    like the app, and uses write-with-response. Implements the same
    `send_light_command` / `send_multi_sensor_command` surface as the cloud
    client, plus `pair_mobile` and an `async_pair_over_ble` helper.

- **Transport selection in the coordinator.** With BLE enabled, control
  commands try BLE and cloud in the configured order: `fallback` (cloud first,
  BLE when the cloud path fails — a home HA near the van) or `primary` (BLE
  first, cloud fallback — a van-local HA). Raw PIA requests, slot actions and
  restart stay on the cloud. BLE is **off by default**; existing cloud-only
  behaviour is unchanged.

- **Options**: `ble_enabled`, `ble_address`, `ble_mode`.

- **Reconfigure now mints the remote-access refresh token over BLE.** Provide
  the vehicle's QR activation token and the SCU's Bluetooth address, press the
  vehicle's CONNECTION button, and the flow bonds, does the TLS handshake and
  the `PairMobileRequest`, and stores the minted token — no proxy capture and
  no external tool.

### Changed

- `manifest.json` gains `after_dependencies: ["bluetooth"]` (soft — Bluetooth is
  set up first when present, but cloud-only installs without an adapter still
  load) and `get_confirmation_token_value()` on the cloud API.

### Notes

- The BLE bonding and the on-vehicle pairing/control paths are hardware-verified
  from a standalone Linux host but **not yet re-verified through the HA
  integration on a live vehicle**. Bonding needs a *local* BlueZ adapter and
  cannot work over a remote Bluetooth proxy.

## [1.0.31] - 2026-08-22

### Changed

- `APP_VERSION` bumped `2.10.14` -> `2.10.16` to match the current app. Verified
  against the decompiled 2.10.16 bundle that every endpoint the token tool uses
  (`/api/v2/oauth/token`, `/api/ehg/v1/accounts/*`, `.../vehicles/byToken`,
  `.../remoteAccessToken`, `/api/rv-twin/vehicles`), the OAuth client
  credentials, and the PIA version (`v0.32.0`) are unchanged - so the extractor
  is current. The only app-side changes are additive (sub-user invitation
  endpoints, new component types for other vehicles) and do not affect it.

## [1.0.30] - 2026-08-22

### Fixed

- **BLE pairing and control now work end to end against the vehicle.** The
  SCU's Nordic UART RX was being written *without* response; at a 23-byte MTU a
  `PairMobileRequest` (it carries two JWTs, ~63 GATT chunks) then overran the
  SCU receive buffer and was dropped with no reply - the "timed out waiting for
  SCU BLE/TLS data" seen on 2026-08-22. The SCU UART RX now defaults to
  write-with-response, matching the app's `WRITE_TYPE_DEFAULT`. Verified in the
  field the same day: from a Linux host inside the vehicle with no LTE, the
  app-level `PairMobileRequest` completed and minted a remote refresh token
  over BLE, and a `setValues` write returned `status=1` (SUCCESS) and actuated
  the load. `connect()` also now acquires a larger MTU like the app
  (`requestMtu(245)`) as a second line of defence, though write-with-response
  alone carried the field test at MTU 23. Diagnosis credited to the upstream
  `BetaHydri/hymer-connect-ha-ble` notes, which documented the same overflow
  and the write-with-response requirement.

### Added

- **The complete PIA response status enum** in `pia_decoder.py`, read from the
  decompiled app (bundle byte 19,151,699): values 0-19 including
  `ACCESS_DENIED` (5), `INVALID_INPUT` (2) and `INVALID_PROTOCOL_VERSION` (4).
  These distinguish "understood but not authorised" from "malformed", which
  matters for interpreting BLE write responses.
- **Field tooling for BLE control from a Linux host**, in `tools/hymer_token_tool`:
  `dbus_pair.py` (a BlueZ pairing agent locked to one device), `full_pair.sh`
  (remove -> rescan -> bond -> app pair -> one confirmed write), `bond_test.py`
  (TLS + one `setValues`), `setup_ble_host.sh` (rebuilds a LibreELEC Pi as a BLE
  host from a pinned, hash-verified wheel manifest), and `BLE_RUNBOOK.md`.
  Secrets are read from `0600` files (`--activation-token-file`, `--ini-file`),
  never from argv; nothing actuates without `CONFIRM=1` and explicit
  component/value ids; every script exits non-zero on failure.
- `scu-user-topic` CLI command and `build_user_topic_probe_frame()` /
  `ScuBleSession.probe_user_topic()`: a **single-shot, operator-gated** probe for
  UserRequestTopic device/account-management sub-fields (e.g. the paired-device
  list). It refuses to run without `--i-understand-may-be-destructive` and never
  sweeps a range, because some sub-fields (`deleteUser`, `deleteAllUsers`) are
  destructive and their field numbers are unknown.
- `ScuBleSession.set_write_with_response()` as a public override for the UART
  write mode, and `--activation-token-file` on `scu-pair-mobile` and
  `mint-remote-refresh`.

### Changed

- The token tool supports **bleak 3.x**, which removed
  `BleakClient.get_services()`. Service discovery now uses the `services`
  property with a guarded fallback to the old coroutine, in both `scu.py` and
  `ble.py`. `pyproject.toml` allows `bleak<4`.
- The token-tool README and RUNBOOK no longer claim the BLE path is unverified.
  On 2026-08-22 a Linux host inside the vehicle bonded, completed the TLS
  handshake (`TLSv1.1 AES128-SHA`) and had a `setValues` write parsed and
  answered by the SCU with `ACCESS_DENIED` - understood, not yet authorised.
  App-level `PairMobileRequest` is the remaining open step.

### Security

- Field-session secret filenames (`activation.txt`, `creds.ini`,
  `remote-refresh.txt`, `pair-session.json`) are gitignored at any depth, and
  the runbook carries no real vehicle, SCU, host or adapter identifiers.

## [1.0.29] - 2026-08-19

### Fixed

- **A single failing subscription no longer tears down a working SignalR
  connection.** On connect the integration sends seven `PiaRequest`
  subscriptions and raised on the first one that failed. Because commands
  require a healthy client, that made the vehicle uncontrollable whenever one
  subscription failed — even though the rest of the connection was fine.

  Observed on the vehicle on 2026-08-18: one subscription returned
  `status=15` (`SCU_IS_NOT_ONLINE`) about 200 ms after each connect, while the
  same connections delivered **118 sensor slots** successfully. The connection
  was then destroyed and rebuilt every few seconds — 21 consecutive failures
  in a few minutes — and every attempt to write a value failed with
  "SignalR is not connected".

  Subscriptions that fail with a transient upstream status
  (`SCU_IS_NOT_ONLINE`, `CALL_TO_SCU_FAILED`, `CLOUD_ERROR`,
  `CONNECTIVITY_ISSUE`) or that go unanswered are now logged and skipped, and
  the connection is kept. This matches the official app, which settles the
  failed request and keeps its socket. Auth statuses
  (`AUTH_TOKEN_EXPIRED`, `REMOTE_TOKEN_EXPIRED`) still propagate, because
  those mean the session really is invalid. If *no* subscription succeeds the
  connection is still treated as failed, since it would carry no data.

### Added

- `PiaRequestFailedError`, a `HymerConnectApiError` subclass carrying the PIA
  `status` and `request_id`, so callers can tell a transient vehicle-side
  condition from an invalid session. Existing handlers are unaffected.
- Named constants for the full PIA status enum, and
  `TRANSIENT_UPSTREAM_STATUSES` describing which of them say nothing about the
  health of our own connection.

## [1.0.28] - 2026-08-18

### Added

- **BLE control-write encoding, mirroring the official Android app.** The token
  tool could already reach the SCU over Bluetooth, complete the legacy TLS
  handshake and pair a mobile device, but it could not ask the vehicle to
  *change* anything. It now builds and sends `setValues` writes and SCU restart
  commands, in `tools/hymer_token_tool`:

  - `build_connected_component_value()` encodes one value the way the app's
    `toPiaValues` does: fields 1 and 2 always, exactly one typed field (3-6)
    chosen by datatype, and field 10 only when the capability carries an
    instance. Field 9 (`connectedComponentIndex`) is never emitted.
  - `instance_string_to_bytes()` converts a capability instance the way the app
    does, splitting on hyphens and parsing each part as base 16, so `01-0a-ff`
    becomes three raw bytes rather than a number or a string.
  - `build_set_values_ble_pia_frame()` and `build_restart_ble_pia_frame()`
    produce complete frames and return the request id used.
  - `ScuBleSession.set_values()` / `.restart()` send one request and wait for
    the response **whose request id matches**, with the app's 30 second
    timeout.
  - New CLI command `scu-set-value` for running a single write against a
    vehicle, with `--restart` for the command topic.

  This path is deliberately built from the decompiled app rather than from any
  existing open-source implementation. The prior art wrapped its command
  payload in protobuf field 2, which on BLE is `BleProtocol.response` rather
  than `request`, so the SCU parsed each command as a response and discarded it
  without an error — which is consistent with the "silently dropped" symptom
  that led that project to remove BLE writes entirely.

  Two traps are encoded in the design, both taken from the app: the GATT write
  completing is not an acknowledgement, and neither is the UI changing, because
  the app updates its own store optimistically before any response arrives.
  Only a matching request id counts.

  Not yet exercised against a vehicle. Value writes (`Request.connectedComponent`)
  and restart (`Request.command`) are different topics, so a working restart
  would not on its own prove that value writes are accepted.

### Changed

- `build_request_message_with_topic()` generalises the PIA Request envelope over
  the topic field number, so the same envelope serves pairing (field 8),
  value writes (field 4) and commands (field 9). `build_request_message()` keeps
  its previous pairing-specific behaviour and delegates.
- `ScuBleSession` gained `_next_pending_frame()`, and the existing
  send-and-wait helper now uses it, so frame reassembly lives in one place.

## [1.0.27] - 2026-08-16

### Fixed

- **Reconnect storm when the vehicle is unreachable.** The backoff ceiling was
  `_MAX_BACKOFF` (16s) and `_MAX_CONSECUTIVE_FAILURES` was counted but never
  acted on, because the coordinator's poll cycle called `start_signalr()` again
  regardless of how many times the backoff loop had given up.

  Observed in the field on 2026-08-15: the van lost its LTE connection
  overnight and the integration made **2,142 connection attempts in twelve
  hours**, roughly one every sixteen seconds, each failing at subscription with
  `status=15`. A vehicle out of signal is an ordinary event that can last hours,
  so once the failure cap is passed the retry interval now steps up to five
  minutes and the per-poll warning drops to debug, where before it filled the
  log overnight.

- **A dead session was retried indefinitely rather than replaced.** After the
  outage the van came back (the official app recovered) but the integration did
  not: reconnecting reuses the existing OAuth2 session, and that session could
  no longer subscribe. It took a Home Assistant restart to clear. After
  `_REAUTH_AFTER_FAILURES` consecutive failures the coordinator now forces the
  full re-authentication it already performs for extended standby, rather than
  resubscribing on a session that cannot work.

## [1.0.26] - 2026-08-15

### Removed

- **Proactive session-auth renewal**, added in 1.0.25 and never released.
  Measured against a live vehicle it never delivered, in two distinct ways:

  1. The first version renewed the EHG remote token via the
     `STATUS_REMOTE_TOKEN_EXPIRED` path. That is not the credential which
     lapses mid-session, so the observed expiry cadence was untouched by
     renewals landing between the expiries.
  2. The second targeted the right credential but used a fixed 24 minute
     interval, chosen from an observed ~31 minute expiry gap. That gap then
     moved to ~16 minutes within the same morning. Since each reactive refresh
     resets the freshness clock, any threshold longer than the expiry gap can
     never be reached — the timer would wake every 60s and do nothing,
     permanently.

  The reactive path handles expiry correctly and was observed doing so seven
  times in a morning with no errors and no lost commands: the SCU answers with
  `STATUS_AUTH_TOKEN_EXPIRED`, OAuth2 and `UpdateTokens` are refreshed, and
  queued requests are replayed. The keepalive poll added in 1.0.25 also draws
  that status while the session is otherwise idle, so a user's command is not
  what discovers the expiry — which was the original justification for renewing
  proactively.

  Rather than guess a third interval against a moving target, the timer, its
  three constants and the attempt/success timestamps are gone.

## [1.0.25] - 2026-08-15

### Added

- **SignalR keepalive poll.** The SCU stops publishing after a few minutes of
  silence, stranding every entity on its last value. `build_refresh_command()`
  already existed but was dead code — nothing called it. The coordinator now
  sends it from its update cycle.

  In practice that cycle fires roughly a minute after the *last push* rather
  than on a fixed interval, because every push calls `async_set_updated_data()`
  and resets the timer. That suits a keepalive well: it prods the SCU exactly
  when the stream goes quiet and stays silent while data flows. Measured in the
  field at 59-61s after the last push.

  The poll is fire-and-forget. `send_pia_request()` awaits a completion future,
  and a poll is not reliably answered, so routing it there would stall the
  coordinator for `PIA_REQUEST_TIMEOUT` every cycle. Its request ids come from a
  reserved band above the app's 1..10,000,000 space, so a late response cannot
  resolve a command's pending future and report an unacknowledged vehicle write
  as successful.

- **Proactive session-auth renewal**, on a timer independent of the
  coordinator. **Removed again in 1.0.26 before either version was released —
  see that entry for why it could not work.**

- **Post-reconnect subscription confirmation.** Commands sent while the SCU is
  still processing subscriptions are silently dropped, so a command waits
  briefly for a frame proving this connection is live. Keyed on a connection
  generation rather than on "is there any slot data", since slot data is never
  cleared and survives every reconnect.

### Fixed

- A failed keepalive write now marks the transport disconnected and hands over
  to the reconnect loop, as the command path already did. The keepalive is the
  session's liveness probe, so swallowing its failure defeated the purpose.
  (The renewal-timestamp and rate-limiting fixes that also landed here went
  with the renewal itself in 1.0.26.)

## [1.0.24] - 2026-08-14

### Fixed

- **Fuel level reported 0% for a full tank** — `fuel_level` carried an
  `invert100` transform on both `(1, 2)` and `(108, 2)`, so the runtime
  computed `100 - raw`. A full tank reports raw `100`, which came out as `0%`.

  The transform was never correct. The decompiled component registry names
  the slot `FuelTankLevel` — a fill level — with `LowFuelWarning` as a
  separate boolean on component 108, so there is no "how empty" semantic to
  invert. Upstream applies no transform to this slot while using
  `div1000`/`div10`/`div3600` on neighbouring slots of the same component,
  so its absence there is deliberate rather than an oversight.

  Verified on a live install: the sensor moved from `0.0` to `100.0` against
  a full tank once the transform was removed.

  > **Existing installs need action.** These hints are not read at runtime —
  > the registry generator extracts them into `data/sensor_labels.json`, which
  > is what the integration loads. Either regenerate the metadata pack, or
  > remove `"transform": "invert100"` from the `"1:2"` and `"108:2"` entries
  > of your installed `data/sensor_labels.json` and restart Home Assistant.

## [1.0.23] - 2026-08-08

### Added

- **MIT License** — The repository now carries a `LICENSE` file at its root,
  retaining the upstream copyright notice
  (`Copyright (c) 2024-2026 BetaHydri`) alongside this project's own. The
  upstream project publishes MIT, so this derivative does the same. This was
  the sole blocker on the HACS default-catalog submission
  ([hacs/default#7526](https://github.com/hacs/default/pull/7526)).

### Changed

- `ATTRIBUTION.md` now records that upstream publishes an MIT License and
  points at `LICENSE` for terms, rather than stating that no upstream license
  file exists. It also notes that the original repository was deprecated in
  May 2026 in favour of `BetaHydri/hymer-connect-ha-ble`.

## [1.0.22] - 2026-06-20

### Fixed

- **SignalR rapid-reconnect storms** — Ported from upstream
  (`BetaHydri/hymer-connect-ha-ble` v2.63.11). Azure SignalR silently drops a
  new connection that arrives before it has released a just-closed session
  server-side, which shows up as repeating rapid drops. When the previous
  session lasted less than 30s, the reconnect loop now waits a short cooldown
  (5s) before its first attempt so the service can clean up; long-lived
  sessions still reconnect immediately.
- **`device_tracker` forward compatibility** — Import `TrackerEntity` from
  `homeassistant.components.device_tracker` instead of the deprecated
  `.config_entry` submodule, which Home Assistant Core removes in 2027.6.

## [1.0.21] - 2026-06-07

### Fixed

- **Commands silently dropped after a stale OAuth2 session or extended 12V
  standby** — Ported from the upstream cloud reliability work
  (`BetaHydri/hymer-connect-ha` v2.55.4 / v2.56.1). When the OAuth2 session
  degrades over time, or the SCU has sat in 12V standby for more than 10
  minutes, the server-side hub→SCU command routing goes stale: a plain SignalR
  reconnect (negotiate only) reuses the stale session and produces a connection
  that looks healthy but cannot deliver commands. The coordinator now performs
  a full OAuth2 re-authentication (password grant, mirroring an integration
  reload) before reconnecting SignalR:
  - `force_reauth_and_reconnect()` re-authenticates, then rebuilds the SignalR
    connection with a clean session.
  - `async_ensure_signalr_healthy()` proactively detects extended standby
    (`scu_standby_seconds > 10 min`) and forces the full re-auth + reconnect
    *before* sending the command, instead of waiting for the 60s readback
    timeout.
  - The 12V main-switch readback path now escalates to a full re-auth on a
    mismatch and is capped at a single retry, so a truly dead command channel
    no longer loops endlessly — it clears optimistic state and logs a warning.

## [1.0.20] - 2026-05-09

### Changed

- **HACS submission readiness** — adds HACS and Hassfest validation workflows.
- **Dashboard dependency declaration** — declares Home Assistant `http` and
  `lovelace` dependencies in the manifest because the generated-dashboard
  service uses static paths and Lovelace storage APIs.

### Fixed

- **Hassfest config-schema warning** — declares the integration as config-entry
  only for YAML setup validation.

## [1.0.19] - 2026-05-09

### Added

- **European localisation preview** — adds initial German, Swiss German,
  French, Spanish, Italian, Dutch, Swedish, and Danish translation files for
  setup, options, repairs, and the highest-visibility vehicle entities.

### Changed

- **SCU signal metadata alignment** — treats bus 30 slot 3 as LTE connection
  quality and bus 30 as SCU signals in the synthetic fixtures, matching the
  current app-derived metadata model.
- **Heater safety naming** — renames the previous heater-window wording to
  "Heater Diesel Safety" to avoid implying that the value is a user-facing
  window contact.

### Fixed

- **12 V standby-entry routing** — when the SCU enters standby, SignalR now
  refreshes `UpdateTokens` without resubscribing, avoiding stale state echoes
  while keeping the command route fresh.
- **12 V main-switch readback recovery** — main-switch commands now get a
  delayed readback check; if the SCU still reports the old state, the
  coordinator forces a SignalR reconnect and retries the command once.

## [1.0.18] - 2026-05-05

### Changed

- **Brand asset refresh** — replaces the generated campervan icon/logo with the
  supplied campervan artwork rendered to the Home Assistant/HACS brand asset
  sizes.

## [1.0.17] - 2026-05-05

### Fixed

- **Windows metadata extraction** — reads expanded Hermes pseudo-JS bundles with
  explicit UTF-8 replacement decoding so `scripts/generate_cleanroom_registry.py`
  does not fail on Windows' default `cp1252` text encoding when the decompiled
  bundle contains non-ASCII bytes.

## [1.0.16] - 2026-05-04

### Fixed

- **Preserve local runtime metadata during HACS updates** — adds the HACS
  `persistent_directory` setting for `data/` so locally generated metadata and
  OAuth client material are backed up and restored when HACS replaces the
  integration folder.
- **Repairs helper module auto-load** — renames the internal Repair issue helper
  module so Home Assistant no longer treats it as an invalid Repairs platform at
  startup.
- **Campervan brand asset** — updates the integration icon/logo vehicle
  silhouette to read as a campervan rather than a car.

## [1.0.15] - 2026-05-04

### Added

- **Local brand assets** — adds integration `brand/icon.png` and `brand/logo.png`
  so Home Assistant and HACS can show a project-specific HYMER Connect Metadata
  icon/logo without depending on the upstream HYMER Connect branding.

### Documentation

- **HACS migration note** — documents that users migrating from Jan Tiedemann /
  BetaHydri's repository must remove the old HACS custom repository entry and
  add `dan-simms1/hymer-connect-ha`, otherwise HACS will continue checking the
  upstream repository for updates.

## [1.0.14] - 2026-05-04

### Added

- **Optional Hermes bytecode decompile step** — `scripts/prepare_runtime_metadata.py`
  now accepts `--hbc-decompiler /path/to/hbc-decompiler` so users can generate
  the local metadata pack directly from a Hermes-based APK without separately
  preparing `bundle.js`. The documented workflow was validated with
  `hermes-dec` 0.1.3 and remains local-only; no decompiled bundle or generated
  metadata is shipped in the repository.

### Fixed

- **Post-standby DataHub refresh** — when the SCU reports the 12 V main switch
  waking from standby, the SignalR client now waits for the SCU to settle,
  refreshes `UpdateTokens`, and resubscribes to the app-style PIA subscription
  burst. Main-switch command acknowledgements no longer fake the transport
  cache before the SCU readback arrives.
- **BLE token-tool transport compatibility** — the early-alpha token tool now
  enables the SCU's legacy TLS 1.0/1.1 cipher profile with OpenSSL security
  level lowered for that local session, prefers write-without-response for the
  UART RX data characteristic, and paces BLE chunks to avoid overrunning the
  SCU.
- **Dashboard distance display polish** — generated dashboards now request
  one-decimal display precision for kilometre-backed distance entities and use
  clearer chassis-card labels/icons for odometer, service distance, fuel,
  AdBlue range, outside temperature, and washer-fluid rows.

## [1.0.13] - 2026-05-01

### Added

- **Value-free slot debug export** — adds the
  `hymer_connect_metadata.export_slot_debug_report` service for opt-in
  capability investigations. When the per-entry debug diagnostics option is
  enabled, the service writes a local JSON report containing observed slot IDs,
  metadata coverage status, unknown/audit-missing slots, raw fallback slots,
  and stale slot IDs without exporting live slot values.

### Changed

- **Debug report documentation** — README now documents where the local slot
  debug report is written and clarifies that it intentionally excludes returned
  slot values.

## [1.0.12] - 2026-05-01

### Fixed

- **Blocking OAuth metadata file read during token refresh** — the locally
  generated OAuth client header is now preloaded and cached during integration
  setup via Home Assistant's executor path, avoiding synchronous
  `oauth_client.json` reads from the event loop when OAuth tokens refresh

## [1.0.11] - 2026-05-01

### Fixed

- **Miles display still rendered as kilometres** — distance slot sensors no
  longer advertise Home Assistant's distance device class while the
  integration-level miles option is enabled, preventing Home Assistant's
  global unit system from converting the integration-managed mile value back to
  kilometres
- **Existing distance unit overrides** — the registry policy now actively sets
  `mi` while the miles option is enabled and clears that override again when
  the option is disabled

## [1.0.10] - 2026-04-30

### Fixed

- **Existing distance entities pinned to kilometres** — clears stale Home
  Assistant entity-registry `km`/`mi` unit overrides for generated distance
  slot entities, allowing the integration's miles display option to take effect
  on odometer, distance-to-service, AdBlue remaining distance, and similar
  sensors after reload

## [1.0.9] - 2026-04-30

### Added

- **Metadata-aware deep PIA decoding** — known locally generated metadata slots
  are now accepted from deeper real-time cloud frames, while unknown depth-4
  wrapper-like entries remain filtered to avoid phantom slot values
- **Remote refresh token extraction helper** — the early-alpha desktop token
  tool can scan a local text capture for JWT-shaped tokens and write the first
  `ett=access-refresh` token to a local secret file without printing it by
  default

### Changed

- **Passive sensor documentation** — README now explains that some app-visible
  passive sensor changes may be BLE-only on some SCU firmware even when the
  cloud decoder accepts deeper known slots

### Fixed

- **Display-unit options on newer Home Assistant cores** — config-entry options
  exposed as read-only mappings are now honoured, so the generated dashboard
  and integration entities consistently show mile/temperature/admin/debug
  preferences after reload

## [1.0.8] - 2026-04-26

### Changed

- **Generated dashboard responsiveness** — Dashboard, Energy, and Climate views
  now use top-level Lovelace cards instead of fixed panel grids, so Home
  Assistant can wrap the main columns more naturally on phones, tablets, and
  wide desktop screens
- **Dashboard docs** — expands the README instructions for the local
  `hymer_connect_metadata.generate_dashboard` service, including multi-vehicle
  `entry_id` handling, generated URL paths, and regeneration after dashboard
  changes

## [1.0.7] - 2026-04-25

### Changed

- **Energy dashboard layout** — generated dashboards now split the Energy tab
  into three functional columns for controls/readings, battery graphs, and
  solar graphs/details
- **Battery voltage graphs** — replaces the large multi-entity voltage history
  graph with compact per-sensor voltage trend cards to avoid excessive blank
  space in Home Assistant's native history graph card

## [1.0.6] - 2026-04-25

### Fixed

- **Vehicle map marker** — the vehicle location tracker now exposes a local
  campervan SVG as its `entity_picture`, so generated dashboard map cards can
  render a vehicle marker instead of text initials

### Added

- **Integration static assets** — registers a small integration-local static
  asset path for dashboard UI assets

## [1.0.5] - 2026-04-25

### Added

- **Generated Lovelace dashboard service** — adds
  `hymer_connect_metadata.generate_dashboard`, which builds a local app-style
  dashboard from the entities resolved for the selected vehicle
- **Persisted dashboard output** — generated dashboards are written both as a
  readable local YAML audit copy under
  `/config/dashboards/hymer_connect_metadata/` and as a Lovelace storage
  dashboard that survives Home Assistant restarts
- **Location map card** — the generated dashboard now uses the live vehicle
  `device_tracker` entity to show the van location on the main dashboard and
  Info tab without storing coordinates in the repository or generated YAML

### Changed

- **App-style dashboard grouping** — generated views now group capabilities
  into Dashboard, Info, Water, Light, Energy, Climate, Components, and
  Scenarios tabs based on canonical capabilities, rich templates, and selected
  fallback entities
- **Light controls** — generated light sections now show an explicit
  `All on/off` aggregate row for each area group, followed by the individual
  light toggles for that section
- **Dashboard docs and backlog** — README and backlog notes now describe the
  generator model instead of a fixed shipped dashboard pack

## [1.0.4] - 2026-04-24

### Changed

- **Local-only OAuth client auth** — the repository no longer ships the app's
  embedded OAuth Basic auth material in tracked source. The metadata-prep
  script now derives `oauth_client.json` locally from the user's own app
  artefact and includes it in the local runtime pack used by the integration
  and token tool
- **Generated subscription burst** — the SignalR startup subscription requests
  are now built from structured protocol metadata instead of shipping captured
  base64 request blobs in source
- **Setup and tooling alignment** — config flow, docs, and token-tool guidance
  now treat the local runtime pack as a prerequisite for account sign-in
- **Release reset** — this repository is being republished as a clean public
  `1.0.4` snapshot without the earlier public release line

### Fixed

- **Shutdown-path reconnect noise** — Home Assistant stop/unload now schedules
  coordinator shutdown safely so SignalR reconnect attempts do not race against
  closing HTTP sessions and emit `Session is closed` warnings
- **Synthetic decoder fixtures only** — decoder transport tests no longer ship
  a real-vehicle captured telemetry frame; the response payloads are now built
  synthetically inside the test suite
- **Runtime-pack validation coverage** — tests now pin the locally generated
  OAuth client file, zip layout, missing-pack error path, and config-flow
  behavior when the local pack has not yet been prepared
