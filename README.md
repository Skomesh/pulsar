# Pulsar

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

MIT — same as the upstream pactl-gui project. See the original [LICENSE](https://github.com/Skrappjaw/pactl-gui/blob/main/LICENSE) for the canonical text.

## Credits

- Original project: [Skrappjaw/pactl-gui](https://github.com/Skrappjaw/pactl-gui)
- This fork: Skomesh/Pulsar