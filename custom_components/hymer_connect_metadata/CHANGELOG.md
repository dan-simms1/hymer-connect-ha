# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
