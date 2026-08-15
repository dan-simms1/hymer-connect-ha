# Changelog

The authoritative integration changelog lives in:

- `custom_components/hymer_connect_metadata/CHANGELOG.md`

Current repository state:

- `1.0.26` — removes the proactive session-auth renewal added in `1.0.25`
  (never released). Two field measurements showed it could not work: it first
  renewed the wrong credential, then used an interval longer than the expiry
  gap it was meant to pre-empt, which a reactive refresh resets. The reactive
  path handles expiry correctly and the keepalive poll surfaces it while idle.
- `1.0.25` — keeps the SignalR session alive between pushes: a keepalive poll
  so the SCU does not fall silent, and a post-reconnect wait so commands are
  not sent into a window where the SCU drops them.
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
