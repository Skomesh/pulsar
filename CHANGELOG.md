# Changelog

All notable changes to Pulsar are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Output Only Here** on the App Audio tab: makes the selected application
  play on the chosen output *exclusively*, so its audio stops on the previous
  device. Uses `pactl move-sink-input` rather than a loopback — a loopback can
  only duplicate audio, it cannot remove the original path.
  - `PactlRunner.list_sink_inputs` — currently-playing streams with the sink
    each is playing to
  - `PactlRunner.move_sink_input` — move one stream to one sink
  - `PactlRunner.sink_inputs_for_node` — match a discovery row to a sink input
- An application icon (`assets/icons/`), generated from an SVG source by
  `scripts/generate_icon.py` with optical sizing for small sizes.
- Device-name parsing and classification extracted into `pulsar/utils/device_names.py`,
  making it unit-testable without a display or an audio server.
- `tests/run_ci_equivalent.sh` for reproducing CI locally.

### Changed
- User presets and routing profiles now resolve under
  `$XDG_DATA_HOME/pulsar/presets` instead of a `presets/` directory relative
  to the working directory. The old behaviour crashed the app outright in any
  sandboxed launch, and scattered a data directory wherever the app happened
  to be started from.
- `PresetManager` and `ProfileManager` write separate files
  (`audio_presets.json`, `routing_profiles.json`). They previously shared
  `user_presets.json` despite holding different schemas, so saving one could
  discard the other.
- The application is now a `pulsar` package (`src/pulsar/`) rather than
  top-level `utils`, `ui` and `config` modules. This is required for distro
  packaging: the old layout installed those generic names into system
  `dist-packages`, where they collide with other packages.

### Fixed
- Routing can no longer create a feedback loop. A sink routed onto itself,
  or onto a device that already has a path back to it, is now refused before
  `pactl` is called, with the offending chain shown (`music → game → voice →
  music`). Previously both loaded silently — no error, no warning — and the
  result was a device at full volume.
  - `PactlRunner.loopback_cycle` — pure graph analysis, testable without a server
  - `PactlRunner.LoopbackCycleError` — carries the chain for the message
- The "Route to default output?" dialog that appeared after creating a sink or
  source is gone, along with the loopback it silently created. Routing is now
  an explicit action on the Manage tab.
- The App Audio tab's Route-To dropdown and stream tree are rebuilt when the tab
  is selected, instead of once at startup. Sinks created or connected after
  launch now appear without a restart, and the current Route-To selection
  survives the refresh.
- Bluetooth headsets were classified as `builtin`. The `bluez-` branch tested
  `connection_part.startswith('bluez-')` while nested inside the `alsa_` block,
  which a `bluez_` name never enters.
- The non-ALSA fallback tested `'sink' in str(type(device_data))`, which asks
  whether the string `"<class 'dict'>"` contains `"sink"` — always false — so
  every such device was classified `builtin`.
- A GPU card whose `device.description` names no vendor (`HDMI Audio Device`)
  was filed as `builtin` by the device-name path and `hdmi` by the module path.
  The two classifiers carried separate GPU hint lists and `classify_device_name`
  was missing `hdmi`.

### Removed
- The three built-in routing profiles (`Headphones (single output)`,
  `Per-application routing (4 channels)`, `Stereo split (apps + voice)`).
  The profile list is now only what the user saved, so a fresh install starts
  empty instead of showing three profiles the user never created.
  - `src/pulsar/utils/presets/builtin_profiles.json` — deleted; the package
    no longer ships any preset data
  - `ProfileManager._load_builtin`, `is_builtin_name`, `is_shadowed_by_user`
    and the deprecated `is_builtin` — removed, along with the shadowing
    machinery that existed only to let a user profile override a built-in one
  - The `presets/*.json` package-data declaration in `pyproject.toml` —
    removed, since there is no longer any data to ship

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