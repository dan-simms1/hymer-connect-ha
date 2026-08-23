# BLE control over Bluetooth: what works, what doesn't, and how to get back here

Field session 2026-08-22. Written so none of the environment archaeology has to
be repeated. Identifiers below are placeholders; the real ones for a given
vehicle live in your own notes, not in this repository.

## Inputs you need

| Thing | Where it comes from |
|---|---|
| `SCU` | the SCU's BLE MAC, e.g. `AA:BB:CC:DD:EE:FF`; on Linux `ble-scan` shows it next to a name like `HYMER 000NNNNN` which matches the SCU ID on the vehicle document |
| activation token | decode the QR on the vehicle document (OpenCV decodes it; it is a ~560-char JWT with `ett: owner`). **A credential: keep it in a `0600` file and nowhere else** |
| account credentials | an ini file, `0600`: `[hymer]\nusername = ...\npassword = ...` |
| `oauth_client.json` | gitignored; copy from a working HA install at `/config/custom_components/hymer_connect_metadata/data/` |
| `COMPONENT_ID`, `VALUE_ID` | the write target. In this integration `bus_id` is the component id and `sensor_id` the value id; the entity's `provider_component_id`/`provider_sensor_id` attributes expose them |

## Confirmed working

- **TLS over BLE against the real SCU**: `TLSv1.1 AES128-SHA`. Previously only
  proven on loopback. This was the last unknown on the critical path.
- **BLE PIA framing and the write encoding.** The SCU parsed a `setValues`
  frame and answered with a **matching request id**. That only happens if the
  `A0 CB` header, big-endian length, CRC32, the `BleProtocol` **field 1**
  (`request`) wrapper and the `ConnectedComponent`/`setValues` topic are all
  correct.
- **Link-layer bonding** from Linux/BlueZ.
- **Cloud half of `mint-remote-refresh`**: authenticates, fetches the
  confirmation token, looks up the vehicle by activation token.

## The result that matters (2026-08-22): it works end to end

From a Linux/BlueZ host inside the vehicle, with no LTE:

1. link-layer bond,
2. TLS over BLE (`TLSv1.1 AES128-SHA`),
3. **app-level `PairMobileRequest` completed and minted a remote refresh
   token** (660-char refresh + 661-char access), then
4. a `setValues` write returned **`status=1` (SUCCESS)** and actuated the load.

This refutes the upstream conclusion that BLE writes are silently dropped, and
proves the whole pairing ceremony and control path work over Bluetooth alone.

### What actually fixed it

The SCU Nordic UART RX must be written **with response**. It was being written
without response; at MTU 23 a `PairMobileRequest` is ~63 GATT chunks and a
no-response burst that long overran the SCU receive buffer, so the request was
dropped with no reply while the tiny `setValues` (a couple of chunks) survived
and got a real `ACCESS_DENIED`. Switching UART RX to write-with-response (the
app's `WRITE_TYPE_DEFAULT`) carried the field test even though the MTU stayed at
23. Raising the MTU (`connect()` now asks, as the app does) is a second line of
defence, but was not what unblocked it. This matches the upstream
`BetaHydri/hymer-connect-ha-ble` notes.

Earlier, before app-level pairing, a `setValues` returned `status=5`
(`ACCESS_DENIED`): understood but not yet authorised. After pairing it returns
`status=1`.

## Full PIA status enum

Read from `bundle.js` byte 19,151,699 on 2026-08-22. Mirrored in
`custom_components/hymer_connect_metadata/pia_decoder.py`.

| # | Name | # | Name |
|---|---|---|---|
| 0 | NO_STATUS | 10 | MAIN_USER_ALREADY_PAIRED |
| 1 | SUCCESS | 11 | MAIN_USER_CANNOT_ACCEPT_INVITATION |
| 2 | INVALID_INPUT | 12 | AUTH_TOKEN_EXPIRED |
| 3 | INTERNAL_ERROR | 13 | REMOTE_TOKEN_EXPIRED |
| 4 | INVALID_PROTOCOL_VERSION | 14 | VEHICLE_NOT_FOUND |
| 5 | ACCESS_DENIED | 15 | SCU_IS_NOT_ONLINE |
| 6 | TOKEN_EXPIRED | 16 | CALL_TO_SCU_FAILED |
| 7 | NOT_FOUND | 17 | CLOUD_ERROR |
| 8 | UNAVAILABLE | 18 | CONNECTIVITY_ISSUE |
| 9 | INVALID_SIZE | 19 | BACKEND_SERVICE_ERROR |

## Host requirements, learned the hard way

**macOS cannot do this.** CoreBluetooth will not bond on demand
(`bond=unsupported-backend`) and exposes no real MAC. It can scan and connect
and nothing more.

**A host outside the vehicle cannot hear it.** A metal body kills BLE at 3 m.
The host must be *in or beside* the van.

You need a Linux host with BlueZ, physically near the vehicle. `setup_ble_host.sh`
rebuilds one on LibreELEC. What it handles, and why:

1. **Bluetooth is gated behind a flag file.** `bluetooth.service` has
   `ConditionPathExists=/storage/.cache/services/bluez.conf`, created by the
   Kodi settings addon. Without it the unit is silently skipped and `hci0`
   never gets an address.
2. **Check the clock.** A wrong clock fails every HTTPS download with a
   confusing certificate error rather than a clock error.
3. **No pip, and `get-pip.py` cannot work** - LibreELEC ships Python stripped
   of `.py` sources, so it dies on an `AssertionError`. Wheels are zips: the
   script fetches a **pinned, SHA-256-verified** set and unpacks them.
4. **Directory layout matters.** `api.py` computes `REPO_ROOT` as
   `parents[3]`, so the package must sit at
   `<root>/tools/hymer_token_tool/hymer_token_tool/` and `oauth_client.json`
   at `<root>/custom_components/hymer_connect_metadata/data/`. The setup
   script creates exactly that layout under `/storage/hymer`.
5. **Beware a shadowing copy.** An older `hymer_token_tool/` in the CWD wins
   over `PYTHONPATH` and `REPO_ROOT` silently reverts to `/`.

## The sequence that works

The van's pairing button opens a short window. Everything must happen inside it.

```sh
# on the BLE host, all files 0600
export SCU=AA:BB:CC:DD:EE:FF COMPONENT_ID=<n> VALUE_ID=<n> CONFIRM=1
sh /storage/hymer/full_pair.sh      # then press the pairing button
```

`full_pair.sh` does: remove stale bond -> **rescan** -> bond -> app pair -> write.
It refuses to run without `SCU`, the target ids and `CONFIRM=1`, refuses
secret files that are not `0600`, stops before the write if pairing fails, and
exits non-zero on every failure (codes documented in the script).

**The rescan is not optional.** Removing a bond deletes BlueZ's device object,
so `Pair()` has nothing to call and fails instantly with an empty error.

**Always clear the stale bond first.** BlueZ will report `Bonded: yes` after
the SCU has discarded its half. Connecting then fails during service discovery
with `failed to discover services, device disconnected`, which reads like a
connection fault but is a key mismatch.

## Failure signatures

| Symptom | Cause |
|---|---|
| `failed to discover services, device disconnected` | stale bond, keys mismatched - remove, rescan, re-bond |
| `org.bluez.Error.AuthenticationFailed` | pairing window shut, or no agent registered |
| `bond=unsupported-backend` | you are on macOS; use Linux |
| `Timed out waiting for SCU BLE/TLS data` | connected and encrypted, but the SCU never answered |
| Empty adapter address, service inactive | LibreELEC flag file missing |
| `Local OAuth client auth is missing` | wrong `REPO_ROOT`, or a shadowing package copy |
| `must not be group/other readable` | chmod 600 the token/ini file |

## Tooling notes

- `bluetoothctl` **exits the moment stdin closes**, so piping `pair ...` into
  it never actually pairs. `dbus_pair.py` registers a `NoInputNoOutput` agent
  **for one device only** and calls `Pair()` in the same process, so the agent
  cannot outlive the attempt and cannot accept a stranger.
- Never `pkill -f <pattern>` over SSH when the pattern appears in your own
  command line. It kills the session. Kill by PID.
- `timeout` does not exist on macOS or on LibreELEC.

## Status and next steps

Pairing is solved. On 2026-08-22 `PairMobileRequest` completed against a real
SCU, a remote-access refresh token was minted, and a `setValues` control write
then returned `status=1`. The unblock was write-with-response (large frames
overflowed the SCU RX buffer under write-without-response), not the MTU.

Remaining, still unverified:

1. Portable bond: BlueZ keys live under `/var/lib/bluetooth/<adapter>/<peer>/`
   and can move between Linux hosts if the destination adapter's address is set
   with `btmgmt public-addr`. Not yet verified.
