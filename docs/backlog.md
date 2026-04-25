# Backlog

## Runtime Metadata Import UX

- Add a single-zip runtime metadata import flow to the integration UI.
- Prefer one validated zip upload over per-file JSON uploads.
- Make the Repair issue the likely entry point when runtime metadata is
  missing.
- Validate the expected filenames before extraction.
- Extract the zip into `custom_components/hymer_connect_metadata/data/`.
- Reload or prompt for reload once extraction succeeds.

## Dashboards

- Improve the generated dashboard service rather than shipping a fixed pack.
- Keep the generated layout tied to canonical capabilities and rich templates,
  not raw slot names.
- Use locally generated runtime metadata to refine app-like grouping and naming
  without tracking extracted dashboard definitions in git.
- Treat any future shipped examples as examples to adapt per vehicle, not as
  supported drop-in packs.
