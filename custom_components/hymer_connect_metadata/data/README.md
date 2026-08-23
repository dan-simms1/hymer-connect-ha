# Local Runtime Metadata

This directory is intentionally local-only.

The JSON files here are generated on the user's machine from a lawfully
obtained HYMER Android app bundle.

The integration expects these generated files:

- `component_kinds.json`
- `sensor_labels.json`
- `control_catalog.json`
- `vehicle_catalog.json`
- `scenario_catalog.json`
- `coverage_audit.json`
- `support_matrix.json`
- `oauth_client.json`

`oauth_client.json` contains locally derived app OAuth client auth used for
cloud account sign-in. It is sensitive local material. Do not share it and do
not publish generated packs that include it.

Generate them from a full checkout of this repository on any machine with:

```bash
python3 scripts/prepare_runtime_metadata.py \
  --apk-url <apk-url> \
  --zip-out hymer_connect_metadata_runtime_metadata.zip
```

`--apk-url` must point directly at the APK file itself. If you already have the
file locally, use `--apk-path` instead:

```bash
python3 scripts/prepare_runtime_metadata.py \
  --apk-path ~/Downloads/com.ehg.hymerconnect.apk \
  --zip-out hymer_connect_metadata_runtime_metadata.zip
```

Then extract that zip into the Home Assistant config directory so the files
land in this folder.

The generated zip is local-only. Do not redistribute it.

The easiest path needs none of this: Home Assistant can build the pack itself
from an APK URL, via the Repair issue or the integration's options dialog (see
`docs/runtime-metadata.md`). A pure-Python Hermes reader reconstructs the
catalogs and OAuth client directly from the app's bytecode — no decompiler.

The offline script reconstructs the Hermes bundle the same way, so a
`hbc-decompiler` is no longer required. If you already have a pseudo-JS bundle
from some other tool you may still pass it with `--bundle-js`, but for a normal
APK just point the script at the file:

```bash
python3 scripts/prepare_runtime_metadata.py \
  --apk-path /path/to/com.ehg.hymerconnect.apk \
  --zip-out hymer_connect_metadata_runtime_metadata.zip
```

If you have a full checkout of this repository on the Home Assistant host
itself, you can write them directly into the HA config directory with:

```bash
python3 scripts/prepare_runtime_metadata.py \
  --apk-path /path/to/com.ehg.hymerconnect.apk \
  --hbc-decompiler /path/to/hbc-decompiler \
  --ha-config-dir /path/to/home-assistant-config
```

For advanced local workflows:

```bash
python3 scripts/generate_cleanroom_registry.py --source bundle --bundle <path/to/bundle.js>
```

Any local metadata-preparation workflow must write the same filenames and
top-level shapes:

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
- `oauth_client.json`
  object with `_comment` and `authorization_header`

The maintained resolver/template definitions live in `../specs/`.
