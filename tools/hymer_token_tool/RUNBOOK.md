# HYMER Pairing Runbook

This runbook is for the standalone token tool only. Do not use it as guidance for
the Home Assistant integration.

## Early Alpha Warning

This runbook is retained as a research note for the standalone token tool.

The token tool is **early alpha** and should **not be used yet** for live
vehicle pairing or production remote-refresh-token minting. The BLE/TLS pairing
path described below has not been hardware-verified end-to-end in this
repository.

For actual current use, follow the manual proxy-capture method in the main
repository README instead.

## Goal

Document the intended long-lived EHG `remoteAccessRefreshToken` minting flow by
mirroring the Android app's pairing path:

1. cloud preflight for `confirmationToken`
2. local BLE bond to the SCU
3. local BLE/TLS probe
4. local `PairMobileRequest` to the SCU

## Prerequisites

- Python 3
- BLE hardware on the machine running the tool
- the vehicle QR code value used as `activationToken`
- EHG cloud credentials
- the SCU nearby and advertising over BLE

Treat BLE bonding as required for the SCU path. Use `--bond` on SCU commands.

## Setup

From the repository root, prepare the local app auth file first:

```bash
python3 scripts/prepare_runtime_metadata.py \
  --apk-path /path/to/com.ehg.hymerconnect.apk
```

If the APK contains Hermes bytecode, rerun with `--bundle-js /path/to/bundle.js`.
This produces the local-only `oauth_client.json` file used by the cloud-login
path. Do not share that generated file.

Then set up the tool:

```bash
cd tools/hymer_token_tool
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Optional INI file for credentials:

```ini
[hymer_token_tool]
username = you@example.com
password = your-password
```

Credential lookup order is command line, then INI file, then interactive prompt.

## Fastest Research Flow

If you are continuing the research later and want the tool to handle the cloud
preflight, BLE selection, bonding, and token write in one command, run:

```bash
python3 -m hymer_token_tool mint-remote-refresh \
  --ini-file hymer.ini \
  --activation-token '<QR_ACTIVATION_TOKEN>' \
  --session-file pairing-session.json
```

This writes the long-lived refresh token to `remote-access-refresh-token.txt`.

## Step 1: Cloud Preflight

Fetch the cloud-side data and save it locally:

```bash
python3 -m hymer_token_tool wizard \
  --brand hymer \
  --username 'you@example.com' \
  --password '...' \
  --activation-token '<QR_ACTIVATION_TOKEN>' \
  --session-file pairing-session.json \
  --json
```

This gives you:

- `confirmation_token`
- the activation-token lookup result from `/api/ehg/v1/vehicles/byToken`
- selected vehicle context

To extract the confirmation token with `jq`:

```bash
jq -r .confirmation_token pairing-session.json
```

Optional activation-token check:

```bash
python3 -m hymer_token_tool inspect-activation \
  --brand hymer \
  --username 'you@example.com' \
  --password '...' \
  --activation-token '<QR_ACTIVATION_TOKEN>' \
  --json
```

## Step 2: Find the SCU

Scan nearby BLE devices:

```bash
python3 -m hymer_token_tool ble-scan --timeout 8
```

Optional GATT probe:

```bash
python3 -m hymer_token_tool ble-probe --identifier '<BLE_IDENTIFIER>' --json
```

## Step 3: Probe the Local Transport

Run the BLE/TLS probe first. This confirms the local path before attempting the
pairing request.

```bash
python3 -m hymer_token_tool scu-tls-probe \
  --identifier '<BLE_IDENTIFIER>' \
  --bond \
  --probe-bonding-state \
  --wake-up \
  --json
```

Check for:

- `bond_status`
- a populated `bonding_state_probe`
- negotiated TLS version and cipher
- no timeout

## Step 4: Send the Pairing Request

Once the probe succeeds, attempt the SCU pairing request:

```bash
python3 -m hymer_token_tool scu-pair-mobile \
  --identifier '<BLE_IDENTIFIER>' \
  --activation-token '<QR_ACTIVATION_TOKEN>' \
  --confirmation-token '<CONFIRMATION_TOKEN>' \
  --bond \
  --probe-bonding-state \
  --wake-up \
  --json
```

Success should return:

- `pair_mobile_response.remote_access_token`
- `pair_mobile_response.remote_access_refresh_token`

The `remote_access_refresh_token` is the long-lived token you want.

## Minimal Sequence

```bash
cd tools/hymer_token_tool
source .venv/bin/activate

python3 -m hymer_token_tool wizard \
  --brand hymer \
  --username 'you@example.com' \
  --password '...' \
  --activation-token '<QR_ACTIVATION_TOKEN>' \
  --session-file pairing-session.json

python3 -m hymer_token_tool ble-scan --timeout 8

python3 -m hymer_token_tool scu-tls-probe \
  --identifier '<BLE_IDENTIFIER>' \
  --bond \
  --probe-bonding-state \
  --wake-up \
  --json

python3 -m hymer_token_tool scu-pair-mobile \
  --identifier '<BLE_IDENTIFIER>' \
  --activation-token '<QR_ACTIVATION_TOKEN>' \
  --confirmation-token "$(jq -r .confirmation_token pairing-session.json)" \
  --bond \
  --probe-bonding-state \
  --wake-up \
  --json
```

## Notes

- On macOS, explicit programmatic BLE bonding may not be available through the BLE
  backend. If the tool reports an unsupported bonding backend, complete OS pairing
  if prompted and retry.
- `--wake-up` is still optional in theory, but it is useful while testing.
- If `scu-tls-probe` times out, the next thing to inspect is the TLS handshake,
  especially the session-id gap between the Android app and Python stdlib TLS.
> [!WARNING]
> Treat `pairing-session.json` and any returned refresh token as secrets. A
> third party who gets that token may be able to access vehicle data and
> remote functions, including location and configuration details.
