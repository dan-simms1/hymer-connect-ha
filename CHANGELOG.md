# Changelog

The authoritative integration changelog lives in:

- `custom_components/hymer_connect_metadata/CHANGELOG.md`

Current repository state:

- `1.0.22` — ports two further upstream cloud fixes: a SignalR rapid-reconnect
  cooldown (avoids Azure dropping reconnects after a short-lived session) and
  the `device_tracker` TrackerEntity import deprecation fix.
