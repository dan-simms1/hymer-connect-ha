# Attribution

This repository builds on earlier HYMER Connect reverse-engineering and Home
Assistant integration work by **Jan Tiedemann / BetaHydri**:

- `https://github.com/BetaHydri/hymer-connect-ha`

The current codebase has been substantially reworked around a metadata-driven,
vehicle-scoped discovery architecture, but parts of the implementation still
carry direct lineage from the upstream project.

## Permission And Provenance

Attribution is not the same thing as licensing or permission.

At the time this note was first written, the upstream repository did not
publish a separate open-source license file and public-release permission for
this derivative work had not yet been confirmed.

Jan Tiedemann / BetaHydri has since confirmed that he is happy for this
derivative repository to be published.

This file is intended to make provenance clear. It does not claim that
attribution alone replaces clear licensing terms for upstream-derived code.

## Upstream Project Credit

- Original project: `BetaHydri/hymer-connect-ha`
- Original author: `Jan Tiedemann / BetaHydri`
- Project focus: HYMER Connect Home Assistant integration and associated
  reverse-engineering work

## File Lineage

The split below is based on repository path continuity and current code
structure.

### Adapted From The Upstream Repository

These files existed upstream under the same or materially similar paths and
should be treated as derivative/adapted unless a later clean-room rewrite is
documented separately:

- `README.md`
- `CHANGELOG.md`
- `custom_components/hymer_connect_metadata/CHANGELOG.md`
- `custom_components/hymer_connect_metadata/README.md`
- `custom_components/hymer_connect_metadata/__init__.py`
- `custom_components/hymer_connect_metadata/api.py`
- `custom_components/hymer_connect_metadata/binary_sensor.py`
- `custom_components/hymer_connect_metadata/climate.py`
- `custom_components/hymer_connect_metadata/config_flow.py`
- `custom_components/hymer_connect_metadata/const.py`
- `custom_components/hymer_connect_metadata/coordinator.py`
- `custom_components/hymer_connect_metadata/device_tracker.py`
- `custom_components/hymer_connect_metadata/light.py`
- `custom_components/hymer_connect_metadata/manifest.json`
- `custom_components/hymer_connect_metadata/pia_decoder.py`
- `custom_components/hymer_connect_metadata/select.py`
- `custom_components/hymer_connect_metadata/sensor.py`
- `custom_components/hymer_connect_metadata/signalr_client.py`
- `custom_components/hymer_connect_metadata/strings.json`
- `custom_components/hymer_connect_metadata/switch.py`
- `custom_components/hymer_connect_metadata/translations/en.json`
- `dashboards/hymer_connect_metadata.yaml`

### New Primary Architecture Added In This Repository

These files are new at the repository-path level and implement the newer
metadata-driven discovery architecture, runtime cataloging, richer template
layer, and local metadata preparation flow:

- `custom_components/hymer_connect_metadata/button.py`
- `custom_components/hymer_connect_metadata/capability_resolver.py`
- `custom_components/hymer_connect_metadata/catalog.py`
- `custom_components/hymer_connect_metadata/cover.py`
- `custom_components/hymer_connect_metadata/diagnostics.py`
- `custom_components/hymer_connect_metadata/discovery.py`
- `custom_components/hymer_connect_metadata/entity_base.py`
- `custom_components/hymer_connect_metadata/fan.py`
- `custom_components/hymer_connect_metadata/number.py`
- `custom_components/hymer_connect_metadata/platform_setup.py`
- `custom_components/hymer_connect_metadata/repairs.py`
- `custom_components/hymer_connect_metadata/runtime_metadata.py`
- `custom_components/hymer_connect_metadata/scene.py`
- `custom_components/hymer_connect_metadata/slot_actions.py`
- `custom_components/hymer_connect_metadata/template_specs.py`
- `custom_components/hymer_connect_metadata/templates/`
- `custom_components/hymer_connect_metadata/text.py`
- `custom_components/hymer_connect_metadata/specs/`
- `scripts/generate_cleanroom_registry.py`
- `scripts/prepare_runtime_metadata.py`
- `docs/runtime-metadata.md`
