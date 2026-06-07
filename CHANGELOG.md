# Changelog

The authoritative integration changelog lives in:

- `custom_components/hymer_connect_metadata/CHANGELOG.md`

Current repository state:

- `1.0.21` — ports the upstream cloud command-reliability fixes: full OAuth2
  re-auth on a dead command channel, proactive re-auth before commands during
  extended 12V standby, and a capped switch retry loop.
