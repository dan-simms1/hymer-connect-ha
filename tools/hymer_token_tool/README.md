# HYMER Token Tool

Standalone cross-platform Python utility for HYMER / EHG account login, vehicle
discovery, remote-access token validation, and BLE pairing preflight.

This lives outside the Home Assistant integration on purpose. It is intended to
run on Windows, macOS, or Linux as a separate operator tool.

The long-term goal is to provide an alternative desktop/laptop workflow for
obtaining the remote-access refresh token without relying on the patched-APK +
proxy method from the main repository README.

## Early Alpha Warning

This tool is **early alpha** and should **not be used yet** for real vehicle
pairing or production refresh-token minting.

It is included in the repository as research code and for future development.
The live BLE/TLS pairing path in this tool has not been verified end-to-end on
real vehicle hardware in this repository, so the supported path for real use
remains the manual proxy-capture method described in the main repository
README.

## Current scope

Implemented now:

- OAuth login against the HYMER / EHG cloud
- account and vehicle discovery
- confirmation-token retrieval
- activation-token lookup (`/api/ehg/v1/vehicles/byToken`)
- remote-access refresh-token validation
- BLE scan and BLE service/characteristic probe
- legacy TLS-over-MemoryBIO engine matching the app's observable SCU profile
- local TLS 1.0 / 1.1 loopback self-test for the pairing transport stack
- live SCU BLE/TLS probe command
- optional explicit BLE bonding before the SCU transport probe/pair attempt
- optional SCU bonding-state challenge probe
- experimental live SCU mobile-pair command
- session export for pairing work
- manual entry of pairing context fields such as VIN, model name, and SCU ID

Not implemented yet:

- hardware-verified end-to-end SCU pairing on a real vehicle
- a supported token-minting workflow that should be recommended to users

The native app clearly has a BLE client path plus a cloud pairing step. This tool
now mirrors the app's local transport more closely, including the BLE/TLS path and
an explicit bond-first option, but it still needs hardware verification against a
real vehicle before it can be treated as a reliable minting path for the long-lived
remote-access refresh token. Until that happens, treat it as research code only.

## Why BLE matters

The long-lived remote-access refresh token is not produced by ordinary OAuth login.
The app obtains it as part of the mobile-device pairing flow.

For a desktop/laptop implementation that means:

- the machine needs BLE hardware
- the tool needs to act as the BLE central/client role that the phone app normally uses
- no Android or iPhone emulator is required if the protocol is reimplemented directly

If a QR code is part of the operator workflow, it appears to be carrying vehicle
identity fields rather than doing the pairing by itself. This tool therefore treats
those fields as typed inputs as well: if you already know the VIN, model name, SCU
ID, vehicle URN, or SCU URN, you can supply them directly without a QR step.

## Installation

Before using the tool, from the repository root generate the local app auth
file once from the same checkout:

```bash
python3 scripts/prepare_runtime_metadata.py \
  --apk-path /path/to/com.ehg.hymerconnect.apk
```

If the APK contains Hermes bytecode, rerun with `--bundle-js /path/to/bundle.js`.
This writes the local-only `oauth_client.json` file used by the tool's cloud
login path. Do not share that generated file.

From this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

On Windows PowerShell:

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

## Usage

For the current research flow, see `RUNBOOK.md`.

For commands that need cloud auth, credential lookup order is:

1. `--username` / `--password`
2. `--ini-file`
3. interactive prompt

Cloud-auth commands also require the locally generated `oauth_client.json` file
described above.

Supported INI sections are `hymer_token_tool`, `hymer-token-tool`, `hymer`, and
`auth`.

Example:

```ini
[hymer_token_tool]
username = you@example.com
password = your-password
```

Interactive preflight:

```bash
hymer-token-tool wizard
```

Interactive preflight with typed pairing context:

```bash
hymer-token-tool wizard \
  --manual-vin WDB... \
  --manual-model-name "Grand Canyon S 700" \
  --manual-scu-id s123.45.67.890.123
```

List account vehicles without prompts:

```bash
hymer-token-tool login --brand hymer --username you@example.com --password '...'
```

Inspect an activation token:

```bash
hymer-token-tool inspect-activation \
  --brand hymer \
  --username you@example.com \
  --password '...' \
  --activation-token '...'
```

Validate a known remote-access refresh token:

```bash
hymer-token-tool validate-remote-refresh \
  --brand hymer \
  --username you@example.com \
  --password '...' \
  --vehicle-urn 'urn:ehg:vehicle:...' \
  --remote-refresh-token '...'
```

Extract a remote-access refresh token from a local text capture, JSON dump, or
copied proxy output:

```bash
hymer-token-tool extract-remote-refresh --input-file capture.txt
```

The extractor scans local text for JWT-shaped values and keeps the first token
whose decoded payload has `ett=access-refresh`. It writes the token to
`remote-access-refresh-token.txt` using restrictive file permissions. The token
is not printed to stdout unless you explicitly pass `--print-token`.

Scan nearby BLE devices:

```bash
hymer-token-tool ble-scan --timeout 8
```

Probe a BLE device and dump its services:

```bash
hymer-token-tool ble-probe --identifier <mac-or-platform-id>
```

Verify the local Python TLS stack can speak the app's legacy TLS profile:

```bash
hymer-token-tool tls-self-test
```

This uses a local loopback server and checks that Python can complete a
`MemoryBIO` handshake for both TLS 1.0 and TLS 1.1 with `AES128-SHA` /
`AES256-SHA`.

Probe a real SCU over BLE and attempt only the TLS handshake:

```bash
hymer-token-tool scu-tls-probe \
  --identifier <mac-or-platform-id> \
  --bond \
  --probe-bonding-state \
  --wake-up
```

Attempt the live SCU pairing request itself:

```bash
hymer-token-tool scu-pair-mobile \
  --identifier <mac-or-platform-id> \
  --activation-token '...' \
  --confirmation-token '...' \
  --bond \
  --wake-up
```

The live SCU commands remain early-alpha research commands because they have
not yet been verified against a real vehicle in this environment. Do not rely
on them yet for a live pairing attempt.

Research-only high-level operator flow in one command:

```bash
hymer-token-tool mint-remote-refresh \
  --ini-file hymer.ini \
  --activation-token '...' \
  --session-file pairing-session.json
```

This command currently documents the intended research flow. It is not yet a
supported operator path for real use.

When complete, it is intended to:

- logs into the cloud
- fetches the `confirmationToken`
- scans and lets you choose a BLE device if `--identifier` is not provided
- requests BLE bonding
- sends the SCU pairing request
- writes the returned refresh token to `remote-access-refresh-token.txt`

## Session files

`wizard` can save a session JSON file with the selected vehicle, auth tokens,
confirmation token, manual pairing fields, and optional activation-token lookup
result.

Example:

```bash
hymer-token-tool wizard --session-file pairing-session.json
```

Treat the session file like a secret because it may contain bearer and refresh
tokens.
