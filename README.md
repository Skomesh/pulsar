# Pulsar

[![CI](https://github.com/Skomesh/pulsar/actions/workflows/ci.yml/badge.svg)](https://github.com/Skomesh/pulsar/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/Skomesh/pulsar/blob/main/LICENSE)
[![Linux](https://img.shields.io/badge/platform-Linux-blue.svg)](https://www.kernel.org/)
[![Forked from pactl-gui](https://img.shields.io/badge/forked%20from-Skrappjaw%2Fpactl--gui-informational)](https://github.com/Skrappjaw/pactl-gui)

A graphical audio routing manager for Linux, built on PulseAudio and PipeWire.

Pulsar is a fork of [pactl-gui](https://github.com/Skrappjaw/pactl-gui) by Skrappjaw, licensed under MIT. We're extending it with routing features aimed at streamers, gamers, and content creators who need to split audio between apps (game, voice chat, music, microphone) into independently controllable channels.

## What Pulsar adds over pactl-gui

- **Sink / Source / Both (duplex)** choice at device creation time
- **Loopback routing** — wire virtual devices to your real output with one click
- **Profile persistence** — save and reload complete routing topologies
- **Streamer-oriented defaults** — opinionated presets for game + voice + music + mic

## Status

Early development. Currently inheriting pactl-gui's MVP feature set; Pulsar-specific features are landing incrementally.

## Requirements

- Linux with PulseAudio **or** PipeWire (most modern distros include one pre-configured)
- Python 3.6+
- Tkinter
- The `pactl` command-line utility

```bash
# Debian/Ubuntu
sudo apt-get install python3-tk pulseaudio-utils

# Fedora
sudo dnf install python3-tkinter pulseaudio-utils

# Arch
sudo pacman -S tk pulseaudio
```

## Installation

```bash
git clone https://github.com/Skomesh/pulsar.git
cd pulsar
./install.sh
pulsar        # or run from application menu
```

To run without installing:

```bash
python3 src/main.py
```

## Syncing with upstream

Pulsar tracks pactl-gui as upstream. To pull in future improvements from the original project:

```bash
git fetch upstream
git merge upstream/main
```

## License

MIT — see [LICENSE](LICENSE) in this repository for the full text.

This is a fork of [pactl-gui](https://github.com/Skrappjaw/pactl-gui), whose README states
the project is MIT-licensed. Upstream does not currently publish a `LICENSE` file, so this
repository ships its own MIT text; portions of the code are Copyright (c) Skrappjaw and are
used under the terms of that license.

## Reporting bugs

Issues are tracked in the [issue tracker](https://github.com/Skomesh/pulsar/issues) — please
use the templates so the report includes the details needed to reproduce it. Security
problems should go through [SECURITY.md](SECURITY.md) rather than a public issue.

## Credits

- Original project: [Skrappjaw/pactl-gui](https://github.com/Skrappjaw/pactl-gui)
- This fork: Skomesh/Pulsar