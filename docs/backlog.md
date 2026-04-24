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

- Revisit example Home Assistant dashboards after the entity model settles.
- Treat them as examples to adapt per vehicle, not as supported drop-in packs.
- Generate them against `hymer_connect_metadata` entity naming, not the old
  `hymer_connect` domain.
- Keep any future dashboard docs separate from the main install path so users
  do not assume dashboards are shipped or supported by default.
