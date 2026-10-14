# Changelog

All notable changes to Pulsar are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Device-name parsing and classification extracted into `src/utils/device_names.py`,
  making it unit-testable without a display or an audio server.
- `tests/run_ci_equivalent.sh` for reproducing CI locally.

### Fixed
- Bluetooth headsets were classified as `builtin`. The `bluez-` branch tested
  `connection_part.startswith('bluez-')` while nested inside the `alsa_` block,
  which a `bluez_` name never enters.
- The non-ALSA fallback tested `'sink' in str(type(device_data))`, which asks
  whether the string `"<class 'dict'>"` contains `"sink"` — always false — so
  every such device was classified `builtin`.

## [0.1.0] - 2026-06-17

First tagged snapshot of the fork. Early development; inherits pactl-gui's MVP
feature set.

### Added
- Graphical audio routing for PulseAudio and PipeWire, forked from
  [pactl-gui](https://github.com/Skrappjaw/pactl-gui).
- Sink / Source / Both device creation.
- Loopback routing and profile persistence.
- App Audio Route: forced hard reroute on configuration change.

[Unreleased]: https://github.com/Skomesh/pulsar/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Skomesh/pulsar/releases/tag/v0.1.0