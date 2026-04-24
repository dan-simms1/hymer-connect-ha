# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
