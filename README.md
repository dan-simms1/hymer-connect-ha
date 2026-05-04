# HYMER Connect Metadata Edition

Home Assistant integration for HYMER / Erwin Hymer Group campervans and
motorhomes with a Smart Control Unit.

If a campervan or motorhome is supported by the HYMER Connect app, this
integration is intended to make it available in Home Assistant as well.

In plain terms, this integration:

- signs into your HYMER / EHG account from Home Assistant
- lets you choose one campervan or motorhome to add
- creates Home Assistant entities for that campervan or motorhome's sensors and controls
- gives you live telemetry and control when a valid remote-access token is available

Depending on the campervan or motorhome, that can include things like battery
values, water levels, location, lights, the 12 V main switch, water pump,
heater, boiler, fridge, awning, charger, solar, and inverter data.

A key design choice in this integration is that it does not rely on one fixed
hardcoded vehicle map. Instead, it uses a locally generated runtime metadata
pack to interpret the low-level data points reported by the selected campervan
or motorhome and map them onto useful Home Assistant entities.

> [!WARNING]
> This is an unofficial project. Use it at your own risk.
>
> This repository was produced primarily for research and educational purposes.
> It is not endorsed by HYMER / EHG and should not be treated as a supported
> consumer product.
>
> It may rely on undocumented interfaces, may stop working without notice, and
> may lead HYMER / EHG to limit, suspend, or block access to connected
> services. If that happens, it could affect this integration and could also
> affect your ability to use the official HYMER Connect app or other
> cloud-backed vehicle features. Commands sent through Home Assistant can
> affect real vehicle systems. No warranty or support is provided.

## Start Here

This repository is a metadata-driven derivative branch of the earlier HYMER
Home Assistant work started by Jan Tiedemann (`BetaHydri`).

If you want the more established and broadly tested integration, use Jan's
repository:

- https://github.com/BetaHydri/hymer-connect-ha

This repository exists for people who specifically want to test the
metadata-driven approach.

Jan did the original reverse-engineering and Home Assistant integration work,
and this repository builds on that foundation. He has reviewed this branch and
is happy for it to be published.

More detailed provenance notes live in [ATTRIBUTION.md](ATTRIBUTION.md).

## What The Main Terms Mean

If you are new to this project, these are the main terms that matter most:

- **Smart Control Unit (SCU)**: the vehicle's onboard control/gateway hardware
  that talks to the cloud and exposes telemetry and command paths.
- **Slot**: a numbered data point reported by the vehicle, such as a battery
  value, a tank level, a switch state, or a temperature reading.
- **Runtime metadata pack**: a local set of JSON files that tells the
  integration what those low-level numbered data points mean on real
  campervans and motorhomes. This repository does not ship those generated
  JSON files in git.
- **Raw slot entity**: a generic Home Assistant entity created directly from a
  low-level slot when this branch does not yet have a richer, more user-friendly
  mapping for that capability.
- **Remote-access refresh token**: a long-lived key used by the HYMER / EHG
  cloud for live vehicle access. Without it, you can sign in and discover the
  vehicle, but live telemetry and live control will not work.

## Why This Branch Exists

Different vans expose different component and slot layouts. A single fixed map
works for some vehicles, but it does not scale cleanly across the wider EHG
surface.

This branch takes a different approach:

1. discover the selected vehicle
2. open the vehicle-scoped cloud session
3. load locally generated runtime metadata
4. build entities from the actual low-level data and controls exposed by that
   campervan or motorhome

The aim is to keep behaviour metadata-led rather than adding more and more
per-van branching to Python.

## Distinct Integration ID

This integration uses the Home Assistant domain `hymer_connect_metadata`.

That means it can coexist with Jan's `hymer_connect` integration on the same
Home Assistant instance for comparison or migration testing.

## Before You Install

You should be comfortable with all of the following:

- this is not an official HYMER / EHG product
- there is a real risk that HYMER / EHG could change, limit, suspend, or block
  connected-service access, which could affect this integration and the
  official HYMER Connect app
- some commands affect real systems in the vehicle
- live telemetry and control depend on a separate remote-access token
- you must prepare a local runtime metadata pack before the integration can
  finish setup

## Installation Overview

There are four steps:

1. install the integration
2. generate the runtime metadata zip
3. copy that zip into your Home Assistant config directory and unzip it
4. add the integration in Home Assistant and sign in

## 1. Install The Integration

### HACS Custom Repository

1. Open **HACS**.
2. Go to **Integrations**.
3. Add a **Custom repository**:
   `https://github.com/dan-simms1/hymer-connect-ha`
4. Choose category **Integration**.
5. Install **HYMER Connect Metadata**.
6. Restart Home Assistant.

### Manual Install

1. Copy `custom_components/hymer_connect_metadata` into your Home Assistant
   `custom_components/` directory.
2. Restart Home Assistant.

## 2. Generate The Runtime Metadata Zip

The integration expects a local metadata pack under:

- `/config/custom_components/hymer_connect_metadata/data/`

Generate that pack from a full checkout of this repository with:

```bash
python3 scripts/prepare_runtime_metadata.py \
  --apk-url <apk-url> \
  --zip-out hymer_connect_metadata_runtime_metadata.zip
```

The generated pack now includes:

- the runtime slot/component metadata
- a local-only `oauth_client.json` file derived from the same app artefact and
  used for cloud account sign-in

That `oauth_client.json` file is intentionally not tracked in git. Treat the
generated zip as local-only and do not share or publish it.

`--apk-url` must be a **direct APK download URL**, not a web page about the
APK.

If you already downloaded the APK file locally, `--apk-path` is usually
easier:

```bash
python3 scripts/prepare_runtime_metadata.py \
  --apk-path ~/Downloads/com.ehg.hymerconnect.apk \
  --zip-out hymer_connect_metadata_runtime_metadata.zip
```

Example direct-download shape:

```text
https://downloads.example.invalid/path/to/com.ehg.hymerconnect.apk
```

If the website only gives you a landing page or starts the download inside the
browser, save the APK locally first and then use `--apk-path`.

To identify the correct Android app, start from the official Google Play
listing for HYMER Connect and confirm the package name:

```text
https://play.google.com/store/apps/details?id=com.ehg.hymerconnect
```

If you need an APK file for the metadata-preparation workflow:

1. confirm the package name is `com.ehg.hymerconnect`
2. obtain the APK from a source you consider trustworthy
3. download it locally
4. pass the local file to `--apk-path`

Popular third-party APK mirrors exist, but this repository does not endorse a
specific one.

If you are working from a downloaded file, use `--apk-path`.

If the APK contains Hermes bytecode, rerun with an expanded `bundle.js`:

```bash
python3 scripts/prepare_runtime_metadata.py \
  --apk-path ~/Downloads/com.ehg.hymerconnect.apk \
  --bundle-js /path/to/bundle.js \
  --zip-out hymer_connect_metadata_runtime_metadata.zip
```

Further detail:

- [docs/runtime-metadata.md](docs/runtime-metadata.md)
- [custom_components/hymer_connect_metadata/data/README.md](custom_components/hymer_connect_metadata/data/README.md)

## 3. Copy The Zip Into Home Assistant And Unzip It

The generated zip already contains the correct folder layout. You do not need
to create the subdirectories manually.

> [!WARNING]
> The generated zip is no longer just neutral metadata. It now also contains
> locally derived app OAuth client auth in `oauth_client.json` so Home
> Assistant can sign in without this repository shipping that material in git.
> Keep the zip local and do not publish or redistribute it.

Typical **Home Assistant OS / Supervised** flow:

1. copy `hymer_connect_metadata_runtime_metadata.zip` into `/config`
   using Samba, the Studio Code Server add-on, SSH/SFTP, or another file
   transfer method
2. open the Terminal & SSH add-on or another shell on the HA host
3. run:

```bash
cd /config
unzip -o hymer_connect_metadata_runtime_metadata.zip
```

4. restart Home Assistant or reload the integration

Typical **Home Assistant Container / Core** flow:

1. copy the zip to the machine that holds your HA config directory
2. change into that config directory
3. run:

```bash
unzip -o /path/to/hymer_connect_metadata_runtime_metadata.zip
```

4. restart Home Assistant or reload the integration

## 4. Add The Integration In Home Assistant

1. Go to **Settings > Devices & Services**.
2. Add **HYMER Connect Metadata**.
3. Select your brand.
4. Enter your HYMER Connect username and password.
5. Select the campervan or motorhome to add.
6. Optionally paste the **EHG remote-access refresh token**.

If you skip the remote-access refresh token:

- the config entry can still be created
- REST-backed vehicle identity metadata can still load
- live telemetry and live control will not work

You can add or replace that token later through **Reconfigure**.

## Current Supported Token Workflow

The currently supported real-world method is still the manual proxy-based
capture flow using a patched Android APK and `mitmproxy`.

For the currently maintained step-by-step instructions, follow Jan's upstream
guide:

- https://github.com/BetaHydri/hymer-connect-ha#obtaining-the-ehg-refresh-token

At a high level:

1. patch the Android app for local proxy inspection
2. run `mitmproxy` or `mitmdump`
3. perform the relevant app flow
4. capture the `remoteAccessToken` exchange

> [!WARNING]
> Treat that token like a password. A third party who gets it may be able to
> access vehicle data and remote functions, including location and
> configuration details.

## Alternative Desktop Token Tool

This repository also includes `tools/hymer_token_tool/`.

The intention of that tool is to provide an **alternative laptop-based way** to
obtain the remote-access key/token from a **Windows, macOS, or Linux** machine
instead of relying on the patched-APK + proxy workflow above.

It is shipped as **early alpha** research code and should **not** be used yet
for real vehicle pairing or production token minting.

At the moment, it should be read as exploratory work toward a future desktop
token-extraction path, not as a supported user workflow.

The token tool now reads the same locally generated `oauth_client.json` file as
the integration, so you should run the metadata-preparation step at least once
from the same repository checkout before using the tool.

For the experimental BLE/TLS path, the tool handles the SCU's legacy TLS
profile internally. It enables TLS 1.0/1.1 with the older AES-SHA cipher suites
for that local session and does not require global OpenSSL or HAOS changes.

It also includes a local helper for extracting an EHG remote-access refresh token
from text you already captured yourself:

```bash
hymer-token-tool extract-remote-refresh --input-file capture.txt
```

That helper scans for JWT-shaped strings, decodes them locally without signature
verification, and keeps the first token whose payload identifies it as
`ett=access-refresh`. It writes the token to `remote-access-refresh-token.txt`
with restrictive file permissions and does not print the token unless you pass
`--print-token`.

## What You Can Expect To See In Home Assistant

Depending on the selected campervan or motorhome and the data it reports, the
integration may
surface entities for:

- vehicle identity and chassis state
- location
- water levels and tank capacities
- 12 V main switch
- water pump
- grouped and named lights
- heater and warm-water boiler
- fridge power, level, silent mode, and status
- living and vehicle battery values
- shoreline, solar, charger, and inverter values

Coverage depends on the actual vehicle, fitted hardware, and the locally
generated metadata pack.

Some passive state changes depend on how the SCU reports data to the cloud. The
cloud / SignalR path can lag behind the app for sensors the app reads directly
over local BLE, such as fridge-door state on some vehicles. This branch now
accepts deeper known metadata slots from real-time cloud frames, but if a sensor
is only exposed over BLE by a given SCU firmware it will remain stale in Home
Assistant until a future BLE path is implemented.

## Dashboards

This repository still does **not** ship a fixed ready-made Home Assistant
dashboard pack.

Different vans expose different controls, sensors, and component groupings, so
shipping one static YAML dashboard in git would either break on many vehicles
or lock the UI to one model.

Instead, the integration now provides a local dashboard generator service:

- `hymer_connect_metadata.generate_dashboard`

It generates a **local Lovelace dashboard** from:

- the canonical capabilities your vehicle actually resolved
- the rich template entities the integration created
- selected raw fallback entities where no richer abstraction exists yet
- the locally generated runtime metadata, including component names derived
  from your own HYMER app artefact

The generated dashboard groups capabilities into app-style tabs such as:

- `Dashboard`
- `Info`
- `Water`
- `Light`
- `Energy`
- `Climate`
- `Components`
- `Scenarios`

Typical flow:

1. install the integration and let it populate entities for your vehicle
2. go to **Developer Tools -> Services**
3. select `HYMER Connect Metadata: Generate Dashboard`
4. run the service once the vehicle's entities have been created
5. open the generated dashboard from the Home Assistant sidebar

For a single HYMER Connect Metadata config entry, the service can be called
with no data:

```yaml
{}
```

If you have more than one van configured, pass the config entry ID for the
vehicle you want to generate:

```yaml
entry_id: 01K...
```

Optional fields:

- `title` sets the Lovelace dashboard title; default is `<vehicle title> Dashboard`
- `filename` sets the local YAML audit filename stem
- `url_path` sets the Home Assistant dashboard URL path

Generated output:

1. the integration writes a local YAML audit copy under `/config/dashboards/hymer_connect_metadata/`
2. the integration persists a Lovelace dashboard and adds it to the sidebar
3. the dashboard is restored automatically after Home Assistant restarts

Regenerate the dashboard after:

- updating this integration to a version with dashboard changes
- adding or removing vehicle hardware/capabilities
- changing entity naming enough that you want the generated labels refreshed

When regenerated with the same `url_path`, the existing generated dashboard is
updated in place.

The generated dashboard is stored locally in your Home Assistant instance, so
it survives Home Assistant restarts. The YAML file is kept as a readable local
copy of what was generated; it is not shipped by this repository.

The output file still lives under:

- `/config/dashboards/hymer_connect_metadata/`

This keeps the repo free of a stale hard-coded dashboard while still letting
the integration generate a dashboard that follows the app's grouping model as
closely as the detected capabilities allow.

## Configuration Options

The options flow currently supports:

- admin actions visibility
- debug diagnostics visibility
- miles vs kilometres
- Fahrenheit vs Celsius

Admin actions are hidden by default. That includes the Smart Unit restart
button.

When debug diagnostics are enabled, the
`hymer_connect_metadata.export_slot_debug_report` service can write a local
JSON report under `/config/hymer_connect_metadata/debug_slots/`. The report is
for capability investigation only: it lists observed slot IDs, whether each
slot is known to the local metadata pack, and metadata labels/categories where
available. It intentionally does not include live returned slot values.

## Limits Of This Branch

- It is not as widely tested as Jan's upstream integration.
- Some campervans and motorhomes will still expose generic low-level entities
  where richer, friendlier entity handling is not yet defined.
- Some write paths are inferred from app/runtime metadata and should be tested
  carefully on each vehicle.
- Home Assistant's stock device UI cannot reproduce the app's tabbed layout.
- The metadata generator depends on the current app/bundle structure.

## Supported Brands

Any Erwin Hymer Group brand using the same HYMER Connect / EHG cloud stack and
a Smart Control Unit may be a candidate:

| Brand | Brand |
| --- | --- |
| HYMER | Carado |
| Bürstner | Laika |
| Dethleffs | Sunlight |
| Eriba | FreeOnTour |
| LMC | Niesmann+Bischoff |

## Credit

This project builds directly on Jan Tiedemann / BetaHydri's earlier HYMER
Connect reverse-engineering and Home Assistant integration work.

If you are choosing one repository to install and follow day-to-day, Jan's
upstream integration remains the default recommendation.

## Affiliation

This is an unofficial community project. It is not affiliated with, endorsed
by, or supported by HYMER, Erwin Hymer Group, or the authors of the official
mobile app.
