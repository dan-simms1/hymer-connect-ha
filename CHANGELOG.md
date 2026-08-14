# Changelog

The authoritative integration changelog lives in:

- `custom_components/hymer_connect_metadata/CHANGELOG.md`

Current repository state:

- `1.0.24` — fixes fuel level reporting 0% for a full tank (a spurious
  `invert100` transform on the `FuelTankLevel` slots). Existing installs must
  regenerate their metadata pack, or hand-edit `data/sensor_labels.json`, to
  pick this up.
- `1.0.23` — adds an MIT `LICENSE` at the repository root (matching upstream,
  retaining BetaHydri's copyright notice) and updates `ATTRIBUTION.md`
  accordingly. Unblocks the HACS default-catalog submission.
- `1.0.22` — ports two further upstream cloud fixes: a SignalR rapid-reconnect
  cooldown (avoids Azure dropping reconnects after a short-lived session) and
  the `device_tracker` TrackerEntity import deprecation fix.
