# HYMER Connect Metadata Edition

<p align="center">
  <img src="custom_components/hymer_connect_metadata/brand/logo.png" alt="HYMER Connect Metadata" width="720">
</p>

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

This repository exists for people who specifically want the metadata-driven
approach.

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

1. **Install the integration** (HACS or manual).
2. **Provide the runtime metadata pack** — Home Assistant builds it for you from
   your HYMER APK URL (no external tools, no zip to copy).
3. **Add the integration and sign in** with your HYMER / EHG account.
4. **Pair over Bluetooth to mint the remote-access token** — Bluetooth is
   required for initial pairing, and that token is what enables live telemetry
   and control. This needs a local Bluetooth adapter near the van.

## 1. Install The Integration

### HACS Custom Repository

1. Open **HACS**.
2. Go to **Integrations**.
3. Add a **Custom repository**:
   `https://github.com/dan-simms1/hymer-connect-ha`
4. Choose category **Integration**.
5. Install **HYMER Connect Metadata**.
6. Restart Home Assistant.

If you previously installed Jan Tiedemann / BetaHydri's `hymer-connect-ha`
repository through HACS, remove that old custom repository entry from HACS and
add this repository URL instead. The two integrations use different Home
Assistant domains, so HACS will otherwise keep checking Jan's repository for
updates and will not offer this project's `v1.x` releases.

### Manual Install

1. Copy `custom_components/hymer_connect_metadata` into your Home Assistant
   `custom_components/` directory.
2. Restart Home Assistant.

## 2. Provide The Runtime Metadata Pack

The integration needs a local metadata pack under
`/config/custom_components/hymer_connect_metadata/data/` to interpret your
vehicle. **Home Assistant builds it for you — no external toolchain, no zip.**

When the pack is missing, the integration raises a fixable **Repair** issue.
Open it and paste a **direct `https://` URL to your HYMER Android APK**; Home
Assistant downloads it and reconstructs the catalogs and the local OAuth client
straight from the app's Hermes bytecode, in-process (pure Python — no
decompiler). You can rebuild the pack any time from the integration's
**Configure → options** dialog.

- The URL must return the **`.apk` file itself**, not a web page. To identify
  the official app, check its Google Play listing and confirm the package name
  `com.ehg.hymerconnect`:

  ```text
  https://play.google.com/store/apps/details?id=com.ehg.hymerconnect
  ```

  Play does not serve the `.apk` directly, so download it from a source you
  trust. This repository does not endorse a specific mirror.

- The pack includes a local-only `oauth_client.json`, derived from your own app
  artefact and used for cloud sign-in. It stays on your Home Assistant instance
  and is never shipped in git — keep it local.

> [!NOTE]
> **Archived (advanced): offline generation.** Building the pack outside Home
> Assistant with `scripts/prepare_runtime_metadata.py` and copying a zip in is an
> older, archived path kept only for air-gapped or advanced setups — it is no
> longer the recommended flow. See
> [docs/runtime-metadata.md](docs/runtime-metadata.md) if you specifically need it.

## 3. Add The Integration And Sign In

1. Go to **Settings > Devices & Services**.
2. Add **HYMER Connect Metadata**.
3. Select your brand.
4. Enter your HYMER Connect username and password.
5. Select the campervan or motorhome to add.

At this point the config entry exists and REST-backed vehicle identity metadata
can load, but **live telemetry and control need the remote-access token** — mint
it in the next step by pairing over Bluetooth. (If you already have a token from
another source, you can paste it during setup or later via **Reconfigure**.)

## 4. Mint The Remote-Access Token Over Bluetooth

Bluetooth pairing is how you mint the vehicle's remote-access token from Home
Assistant — it is a required part of first-time setup for live telemetry and
control.

1. Open the integration's **Reconfigure** flow.
2. Enter the vehicle's **QR activation-code** text and the SCU's **Bluetooth
   address**.
3. Press the **CONNECTION** button on the vehicle, then submit.

Home Assistant bonds with the SCU, performs the TLS handshake and the pairing
exchange, and stores the minted refresh token — no proxy capture and no external
tool.

Requirements:

- BLE bonding needs a **local** BlueZ adapter (Home Assistant OS, or a Linux host
  near the van). It **cannot** work over a remote Bluetooth proxy, and macOS
  cannot bond on demand.
- The SCU's legacy TLS profile is used only for that local pairing session; no
  global OpenSSL / HAOS changes are needed.

> [!NOTE]
> The BLE encoder and pairing exchange were proven end to end against a real
> vehicle from a Linux/BlueZ host (see
> [`tools/hymer_token_tool/BLE_RUNBOOK.md`](tools/hymer_token_tool/BLE_RUNBOOK.md)).
> The in-integration Reconfigure flow uses that same proven exchange; if you hit
> trouble, the archived desktop tool below is the hands-on-verified fallback.

## Bluetooth Control (optional)

Beyond pairing, Bluetooth can also carry **control commands** as a local
transport. This is **off by default**; a cloud-only install without a Bluetooth
adapter is unaffected.

Enable it in **Options** (`ble_enabled`, the SCU's `ble_address`, and a
`ble_mode`: `fallback` tries the cloud first and BLE when the cloud fails — a
home HA near the van; `primary` tries BLE first with cloud as backup — a
van-local HA). Only value writes (lights, switches, fridge, heater) use BLE;
other calls stay on the cloud.

## Getting The Token Without Bluetooth (archived)

Bluetooth pairing (above) is the supported way to mint the token. Two older
paths are kept for reference only:

- **Proxy capture (archived).** Patching the Android app and capturing the
  `remoteAccessToken` exchange with `mitmproxy` still works as a manual fallback
  when Bluetooth is not an option, but it is no longer the recommended flow.
- **Desktop token tool (archived).** A laptop-based BLE/TLS pairing tool that
  mints the token from a Linux/BlueZ host near the van. It is unmaintained and
  research-only; its documentation lives in its own folder:
  [`tools/hymer_token_tool/`](tools/hymer_token_tool/). It also includes a local
  helper to extract a token from text you captured yourself
  (`hymer-token-tool extract-remote-refresh --input-file capture.txt`).

> [!WARNING]
> Treat the remote-access token like a password. A third party who obtains it
> may be able to access vehicle data and remote functions, including location
> and configuration details.

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

The integration also includes recovery logic for 12 V standby transitions. When
the Smart Unit enters standby after the 12 V main switch is turned off, it
refreshes the cloud command route without replaying the full subscription burst
so stale cached state is less likely to overwrite the Home Assistant view. Main
switch commands also get a delayed readback check; if the Smart Unit still
reports the old state, the integration reconnects SignalR and retries once.

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
- Bluetooth (BLE) control: `ble_enabled`, the SCU `ble_address`, and `ble_mode`
  (`fallback` = cloud first, BLE on cloud failure; `primary` = BLE first). Off
  by default. See the Bluetooth section above.

Admin actions are hidden by default. That includes the Smart Unit restart
button.

## Localisation

Home Assistant uses the language selected in the user's profile. English is the
primary maintained language for this integration. Initial European translations
are included for German, Swiss German, French, Spanish, Italian, Dutch, Swedish,
and Danish. These cover setup, options, Repair text, and the most visible
vehicle entities; metadata-generated entity names may still fall back to
English.
Technical terms such as SCU, SignalR, runtime metadata, and remote-access token
are intentionally kept close to their original wording where translating them
would make support or diagnostics less clear.

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

## License

Released under the [MIT License](LICENSE), matching the upstream project this
work derives from. See [ATTRIBUTION.md](ATTRIBUTION.md) for the file-level
provenance breakdown.
