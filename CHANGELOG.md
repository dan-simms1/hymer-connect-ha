# Changelog

The authoritative integration changelog lives in:

- `custom_components/hymer_connect_metadata/CHANGELOG.md`

Current repository state:

- `1.0.12` — preloads the local OAuth client metadata off the event loop so
  token refresh no longer performs blocking file I/O
