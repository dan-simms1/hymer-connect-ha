# Runtime Metadata Preparation

This guide covers the local metadata pack only. Install the HYMER Connect
Metadata integration itself first, then use the steps below to populate
`custom_components/hymer_connect_metadata/data/`.

## Why This Exists

This integration needs semantic metadata to turn low-level
`(component_id, sensor_id)` telemetry into useful Home Assistant entities.

In this project, a **slot** is just one numbered data point reported by the
vehicle, for example a battery reading, water level, switch state, or
temperature value.

Live SignalR/PIA traffic tells us:

- which numbered data points ("slots") exist on a vehicle
- the current raw value
- the transport datatype

Live traffic does **not** fully tell us:

- slot labels
- units and transforms
- enum option sets
- scenario definitions
- cross-vehicle component family layouts

For that reason, the integration uses a **local runtime metadata pack** under
`custom_components/hymer_connect_metadata/data/`.

That pack is not committed to git. Users generate it locally from their own
lawfully obtained app artefact.

The local pack now also carries `oauth_client.json`, which contains locally
derived app OAuth client auth needed for account sign-in. This repository does
not ship that material in git.

## Quick Start

### Option 1: Prepare A Transfer Zip

The integration reads local files from:

- `custom_components/hymer_connect_metadata/data/`

inside the Home Assistant config directory. There is no upload step in the
config-flow UI. If the metadata pack is missing, the integration raises a
Repair issue that points you at this preparation workflow.

The most reliable path is to run the prep command from a full checkout of this
repository on any machine and generate a transfer zip:

```bash
python3 scripts/prepare_runtime_metadata.py \
  --apk-url <apk-url> \
  --zip-out hymer_connect_metadata_runtime_metadata.zip
```

That transfer zip is local-only. It now contains both runtime metadata and the
locally derived `oauth_client.json` file. Do not share or publish it.

`--apk-url` means a **direct** URL to the APK file itself. It should download
an `.apk` immediately. It should not be a page that describes the APK.

Example shape:

```text
https://downloads.example.invalid/path/to/com.ehg.hymerconnect.apk
```

If you already have the file locally, or the website only gives you a browser
download rather than a copyable direct link, use `--apk-path` instead:

```bash
python3 scripts/prepare_runtime_metadata.py \
  --apk-path ~/Downloads/com.ehg.hymerconnect.apk \
  --zip-out hymer_connect_metadata_runtime_metadata.zip
```

To identify the correct Android app, start from the official Google Play
listing for HYMER Connect and confirm the package name:

```text
https://play.google.com/store/apps/details?id=com.ehg.hymerconnect
```

If you need an APK file for this workflow:

1. confirm the package name is `com.ehg.hymerconnect`
2. obtain the APK from a source you consider trustworthy
3. download it locally
4. pass the local file to `--apk-path`

Popular third-party APK mirrors exist, but this repository does not endorse a
specific one.

If you are working from a downloaded file, use `--apk-path`.

Then extract that zip into the Home Assistant config directory so the JSON files
land under:

- `custom_components/hymer_connect_metadata/data/`

### Installing The Zip Into Home Assistant

The generated zip already contains the correct `custom_components/...` folder
layout. You do not need to create the subdirectories manually.

> [!WARNING]
> The generated zip contains locally derived app auth material in
> `oauth_client.json`. Keep it local and do not redistribute it.

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

There is currently no upload flow in the integration UI. A future one-zip
import flow is tracked in [docs/backlog.md](backlog.md).

### Option 2: Install Directly Into Home Assistant

If you have a full checkout of this repository on the Home Assistant host, you
can write directly into the HA config directory instead:

```bash
python3 scripts/prepare_runtime_metadata.py \
  --apk-path /path/to/com.ehg.hymerconnect.apk \
  --ha-config-dir /path/to/home-assistant-config
```

If the APK contains a **Hermes bytecode** bundle, the script will extract the
bytecode asset and stop with instructions to provide an expanded `bundle.js`:

```bash
python3 scripts/prepare_runtime_metadata.py \
  --apk-path /path/to/com.ehg.hymerconnect.apk \
  --bundle-js /path/to/bundle.js \
  --zip-out hymer_connect_metadata_runtime_metadata.zip
```

or:

```bash
python3 scripts/prepare_runtime_metadata.py \
  --apk-url <direct-apk-download-url> \
  --bundle-js-url <url-to-expanded-bundle-js> \
  --zip-out hymer_connect_metadata_runtime_metadata.zip
```

### Option 3: Prepare From An Expanded Bundle

If you already have an expanded `bundle.js`:

```bash
python3 scripts/prepare_runtime_metadata.py \
  --bundle-js /path/to/bundle.js \
  --ha-config-dir /path/to/home-assistant-config
```

### Option 4: Advanced Local Pack Workflow

If you build your own local generator, place or generate the expected JSON
files in:

- `custom_components/hymer_connect_metadata/data/`

The expected filenames and shapes are documented in:

- `custom_components/hymer_connect_metadata/data/README.md`

## What The Prep Script Writes

The script generates:

- `component_kinds.json`
- `sensor_labels.json`
- `control_catalog.json`
- `vehicle_catalog.json`
- `scenario_catalog.json`
- `coverage_audit.json`
- `support_matrix.json`
- `oauth_client.json`

The maintained authored rules are kept separately in:

- `custom_components/hymer_connect_metadata/specs/provider_specs.json`
- `custom_components/hymer_connect_metadata/specs/template_specs.json`

## Extending The Metadata

There are two different extension paths:

1. local runtime-pack files under `custom_components/hymer_connect_metadata/data/`
2. tracked authored specs under `custom_components/hymer_connect_metadata/specs/`

They solve different problems.

### Local Runtime-Pack Files

These files describe the discovered slot/component surface and are loaded at
runtime from:

- `custom_components/hymer_connect_metadata/data/`

in the Home Assistant config directory.

The integration expects these filenames:

- `component_kinds.json`
- `sensor_labels.json`
- `control_catalog.json`
- `vehicle_catalog.json`
- `scenario_catalog.json`
- `coverage_audit.json`
- `support_matrix.json`
- `oauth_client.json`

If you are preparing your own local metadata pack, those filenames and
top-level JSON shapes must match exactly.

Expected top-level structure:

- `component_kinds.json`
  object with `_comment` and `components`
- `sensor_labels.json`
  object with `_comment` and `slots`
- `control_catalog.json`
  object with `_comment`, `labels`, and `slots`
- `vehicle_catalog.json`
  object with `_comment` and `models`
- `scenario_catalog.json`
  object with `_comment`, `brands`, and `entries`
- `coverage_audit.json`
  object with `_comment`, `summary`, `components`, `slots`, and `scenarios`
- `support_matrix.json`
  object with `_comment`, `summary`, `canonical_capabilities`,
  `rich_templates`, and `generic_component_kinds`

Where the files go:

- direct install on the HA host:
  `config/custom_components/hymer_connect_metadata/data/`
- or inside a transfer zip that extracts to:
  `custom_components/hymer_connect_metadata/data/`

The integration does not care where the files were generated. It only cares
that those filenames exist in that folder and follow the expected structure.

### Tracked Authored Specs

These files live in the repository and define our own behavior layer:

- `custom_components/hymer_connect_metadata/specs/provider_specs.json`
- `custom_components/hymer_connect_metadata/specs/template_specs.json`

Use them when you want to:

- add a new canonical capability provider
- teach the runtime that a component family maps to an existing capability
- add a richer template family for lights, climate, fans, covers, and similar
- adjust claim rules or supported slot layouts

Use the local runtime-pack files when you want to:

- add slot labels
- add units or transforms
- add enum option metadata
- add component kind mappings
- add vehicle catalog data
- add scenario catalog data

### Practical Extension Workflow

1. Generate or obtain a runtime metadata pack.
2. Put the generated JSON files into `custom_components/hymer_connect_metadata/data/`.
3. If the runtime still only exposes generic/raw entities, check whether the
   missing behavior belongs in:
   - the runtime pack
   - or the tracked spec files
4. For community sharing, distribute either:
   - a full metadata pack zip
   - or a spec change against `provider_specs.json` / `template_specs.json`

For the exact filename and shape contract used by the runtime, also see:

- `custom_components/hymer_connect_metadata/data/README.md`
- `custom_components/hymer_connect_metadata/specs/README.md`
