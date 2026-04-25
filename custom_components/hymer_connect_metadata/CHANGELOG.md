# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
