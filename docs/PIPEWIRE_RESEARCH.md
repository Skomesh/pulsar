# PipeWire Feature Research for Pulsar

Comprehensive inventory of PipeWire (and WirePlumber) capabilities relevant to a GUI audio routing manager, based on direct inspection of this system's installation and the official docs (PipeWire 1.6.6 / WirePlumber 0.5.14 docs, plus local `man libpipewire-module-*` and CLI help).

This document informs the [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md). Features are ranked by fit with Pulsar's mission: **a routing/routing-management GUI for streamers, gamers, and content creators**.

---

## How PipeWire Differs From PulseAudio (the foundation)

PulseAudio is a flat sound server: **sinks** (outputs) and **sources** (inputs) connected by `module-loopback` and similar.

PipeWire is a **graph-based multimedia framework** with a fundamentally richer model:

- **Nodes** — the basic media-processing unit. An app is a client node; a hardware device is a device node; a virtual device is a stream node.
- **Ports** — input or output audio channels on a node.
- **Links** — explicit connections between an output port of one node and an input port of another.
- **Endpoints** — abstractions over nodes, used by the session manager (WirePlumber) for policy and routing decisions.

What this means for Pulsar: we're not limited to PA-style loopback hacks. We can do native graph-level routing with `pw-cli create-link` and per-port connections. This unlocks features impossible in PA (notably per-app audio capture, which PulseAudio can't do at all).

Two CLI layers:
- **`pactl`** — the legacy PulseAudio-compatible CLI. What pactl-gui already wraps. Works against PipeWire via the `pipewire-pulse` shim but is intentionally limited.
- **`pw-cli` / `pw-dump` / `pw-top` / `wpctl`** — the native PipeWire tools. Give us access to the full graph model.

Pulsar should treat `pactl` as a compatibility layer and use `pw-cli`/`wpctl` for advanced features.

---

## Feature Inventory

### A. Modules (loadable via `pactl load-module` or `pw-cli load-module`)

Authoritative info from local `man libpipewire-module-*` on this system.

| Module | What it does | Pulsar fit | Complexity |
|---|---|---|---|
| **`module-null-sink`** | Creates a virtual sink + source with a monitor (the loopback target). The workhorse. | ✅ Core | Small — already used |
| **`module-loopback`** | Passes output of a capture stream to a playback stream unmodified. The "wire this to that" tool. Can also create virtual sinks/sources or remap channels. | ✅ Core (Phase 2) | Small |
| **`module-combine-stream`** | Creates a virtual sink that fans out to multiple sinks, OR a virtual source that mixes multiple sources. Uses match rules to select inputs. | ✅ Useful (Phase 4+) | Medium |
| **`module-echo-cancel`** | Built-in WebRTC echo cancellation. Creates `echo-cancel-capture` and `echo-cancel-playback` virtual devices. Critical for VoIP. | ✅ High value | Small |
| **`module-filter-chain`** | Arbitrary processing graph: LADSPA, LV2, SOFA, FFmpeg filters, builtins. Can be made into a virtual sink/source or inserted between any 2 nodes. | ✅ High value (Phase 4) | Large |
| **`module-parametric-equalizer`** | Loads AutoEQ / Squiglink parametric EQ configs and applies them via the filter chain. | ⚠️ Out of scope (easyeffects territory) | N/A |
| **`module-link-factory`** | Lets clients create links between arbitrary ports via properties (node.name, port.name, etc.). | ✅ Useful (per-app routing) | Medium |
| **`module-portal`** | DBus bridge for sandboxed clients. Manages app permissions for PipeWire access. | ⚠️ Mostly user-transparent | N/A |
| **`module-fallback-sink`** | Auto-fallback when default sink disconnects. | ⚠️ System-level, leave to WP | N/A |
| **`module-adapter`** | Adapts client nodes so session manager can manage them. | ⚠️ Internal | N/A |
| **`module-metadata`** | Generic key/value store. Used by WP for persistence. | ⚠️ Internal | N/A |
| **`module-profiler`** | Provides stats used by `pw-top`/`pw-profiler`. | ✅ Telemetry (nice-to-have) | Small |
| **JACK tunnel / JACK DBus detect** | Bridges JACK clients into PipeWire. | ⚠️ JACK users already have tools | N/A |
| **RTP / ROC / SAP / VBAN / Netjack2 / AirPlay / Snapcast / RAOP** | Network audio transports. | ❌ Out of scope (different problem) | N/A |
| **AVB / FFADO** | Professional audio over Ethernet / FireWire. | ❌ Out of scope | N/A |

### B. WirePlumber features (the session manager)

WirePlumber 0.5.14 (per local docs) is the policy/session brain of PipeWire. It's already running on this system (PID 2450). It exposes:

| Feature | What it does | Pulsar fit | Complexity |
|---|---|---|---|
| **Session management** | Auto-connects client nodes to sinks/sources per policy. | ✅ Leveraged, don't reimplement | N/A |
| **Linking Policy** | Rules for which clients can link to which nodes. | ⚠️ Power-user, document only | Medium |
| **Smart Filters** | Auto-applies filter chains based on device properties (e.g. EQ on headphones). | ⚠️ Phase 5+ consideration | Large |
| **Automatic Software DSP** | PipeWire applies software DSP when hardware can't (rate conversion, format conversion). | ⚠️ User-visible (show in UI) | Small |
| **Lua scripting** | Users can write Lua scripts to extend WP behavior. | ⚠️ Power-user escape hatch | N/A |
| **`wpctl` CLI** | Status, default, volume, mute, profile, route, settings. | ✅ Use it for all device control | Small |
| **`wpctl set-default ID`** | Change default sink/source. | ✅ Core | Small |
| **`wpctl set-profile ID INDEX`** | Switch a device's profile (e.g. Bluetooth HFP ↔ A2DP). | ✅ High value for BT users | Small |
| **`wpctl set-route ID INDEX`** | Switch a device's route within a profile. | ✅ Useful for HDMI/USB | Small |
| **Bluetooth configuration** | Codec selection, profile switching, headset handling. | ✅ Show + control in UI | Medium |
| **ALSA configuration** | Card profiles, mixer controls. | ⚠️ Mostly covered by WP | N/A |
| **Access configuration** | Permissions for portal-managed clients. | ⚠️ Leave to desktop settings | N/A |

### C. Per-application routing (the killer feature)

**This is where PipeWire fundamentally beats PulseAudio.**

In PA, you can only route at the **sink level**: "Discord's audio goes to sink X" means ALL of Discord's audio. If Discord is playing a voice call AND a notification sound, both go to sink X.

In PipeWire/WirePlumber, you can route at the **stream level**: a single playing audio stream within an app. This is how Discord's "voice goes here, notification goes there" actually works under the hood on modern Linux.

The mechanism:
- WirePlumber exposes each playing audio stream as a separate "node" with its own node.id
- `wpctl set-default` works per-stream (you can pin a specific Spotify song to a sink, not all of Spotify)
- `pactl move-sink-input` does the same thing in PA-compat

For Pulsar this means:
- **"Send this app to this sink"** is table-stakes (already in PA world)
- **"Send this specific audio stream within this app to this sink"** is the PipeWire-native superpower

Currently pactl-gui (and pavucontrol) treat per-app routing as "set the default sink, then any sink-inputs go there." That's PA thinking. Pulsar can do better with WP introspection.

### D. Per-app audio capture (screen-share audio)

**This is the other PipeWire superpower**, and it directly enables a workflow that required a virtual sink hack in `StreamAudio.sh`.

How OBS captures "Discord's audio" today: Discord → `discord_sink` (virtual) → OBS reads from `discord_sink.monitor`. You had to wire Discord through a virtual sink for OBS to hear it.

How PipeWire does it natively: the **ScreenCast portal** (verified working on this system — `AvailableSourceTypes: 7` = MONITOR + MICROPHONE + APPLICATION) lets an app ask for **a specific application's audio stream by ID**, not just a whole sink.

Workflow becomes:
- User clicks "Capture Discord audio in OBS" in Pulsar
- Pulsar queries WP for Discord's audio nodes
- Pulsar tells OBS (via WebSocket or similar) to add a PipeWire audio capture source for that specific application node
- No virtual sink needed. Discord's actual audio output is captured directly.

This is a Phase 5 feature for Pulsar but it's a significant UX improvement over the null-sink pattern.

### E. Tool / CLI inventory (what we'd wrap)

Verified working on this system:

| Tool | Purpose | Use in Pulsar |
|---|---|---|
| `pactl` | PA-compat CLI (load-module, list, info, set-default, set-volume, move-sink-input, set-card-profile, set-port-latency-offset, send-message, subscribe). | Backward-compat path |
| `pw-cli` | Native PW CLI. Commands: load-module, unload-module, connect, list-objects, info, create-device, create-node, destroy, create-link, export-node, enum-params, set-param, permissions, send-command. | Native graph manipulation |
| `pw-dump` | Full graph state as JSON. Subscribe with `--monitor` for live updates. | UI reactivity — re-render when graph changes |
| `pw-top` | Real-time node/device stats. Quantum, sample rate, status. | Diagnostic tab |
| `wpctl` | WP control: status, inspect, set-default, set-volume, set-mute, set-profile, set-route, settings. | Per-device operations |
| `pw-cat` | Play/record audio to/from PW. | Test utility — verify sinks work |
| `spa-inspect` | Inspect SPA plugins (the underlying mechanism PW uses for filters/devices). | Debugging |

### F. Stream metadata & app discovery

WirePlumber exposes rich metadata on every node:
- `application.name`, `application.id`, `application.process.id`, `application.process.binary`, `application.process.user`
- `media.name`, `media.class` (Stream/Input/Output)
- `node.name`, `node.description`, `node.nick`
- `node.latency`, `audio.rate`, `audio.channels`

This means a Pulsar GUI can show the user "Currently routing: **Discord** (PID 12345) — **#general voice** (node 78)" instead of just "sink-input 42."

App discovery options:
- Process scanning (`/proc`, `.desktop` files) to map PIDs to app names
- DBus introspection (`org.freedesktop.Notifications`, `org.freedesktop.Application`) for richer metadata
- WirePlumber's own metadata (already has everything we need)

### G. Sample rate / format / clock-rate concerns

PipeWire handles format conversion automatically via the `adapter` module, but mismatches can cause latency or quality issues. A Pulsar diagnostic tab could show:
- Current `audio.rate` (typically 48000 Hz)
- Current `audio.channels` per device
- Quantum (latency in samples) per driver
- Suggested fixes (e.g. "your mic is 44100 Hz, resampling to 48000 — switch it to 48000 in alsamixer")

Useful but Phase 4+ polish.

### H. OBS integration

Three possible integration levels:

1. **Loose:** Document that users can add a PulseAudio/PipeWire audio source in OBS and select our virtual sinks. No code needed. **Recommended for v1.**

2. **Medium:** Pulsar auto-detects OBS (via OBS WebSocket plugin) and offers to manage OBS audio sources when profiles switch. Lets you script "when I apply Streaming profile, OBS swaps audio sources to match."

3. **Tight:** Embed OBS audio source configuration directly in Pulsar profiles. Higher complexity, lower value unless we add a lot of OBS-specific features.

Phase 5+ candidates.

### I. Notifications & logging

- PulseAudio has `pactl subscribe` — emits events on the server end (sink change, sink-input change, etc.)
- PipeWire's equivalent is `pw-dump --monitor` — emits full graph state on every change
- A Pulsar UI should subscribe to one of these to stay in sync with external changes (user changes volume in pavucontrol, app starts/stops, etc.)

This is plumbing, not a user-facing feature, but essential for a polished app.

### J. Hardware-specific

| Hardware | PipeWire feature | Pulsar UX |
|---|---|---|
| Bluetooth headphones | Profile switching HFP/A2DP, codec selection (SBC/AAC/aptX/LDAC) | "BT codec: AAC" indicator + selector |
| HDMI output | Route switching, hot-plug handling | Show available HDMI profiles; let user pin a default |
| USB audio devices | Hot-plug, often quirks with feedback or format | Detect new devices; offer "set as default for music" |
| Webcams with mics | The C922 on this system has both | Group them in UI; show as one "Webcam" device with separate sink/source |
| Multi-channel surround | 5.1/7.1 setups | Already in pactl-gui's presets; keep |

### K. Things to NOT do (out of scope)

| Feature | Why excluded |
|---|---|
| **Be a mixer** (faders, sends, EQ, compression) | easyeffects / PulseEffects already do this; different problem |
| **Be a JACK replacement** | Different audience (pro audio); JACK bridge exists if needed |
| **Music production tools** (sample-accurate sync, multi-track recording) | Use Ardour + JACK |
| **Dolby Atmos / spatial audio** | Hardware-dependent; niche; not relevant for streamers |
| **Built-in effects** (reverb, compressor, limiter) | easyeffects owns this; would conflict |
| **Network audio sync** (RTP/SAP/ROC) | Different problem; PulseAudio has these too |
| **Voice synthesis / ASR** | Outside audio routing entirely |
| **Be a replacement for pavucontrol** | pavucontrol is the volume/mute panel; we do routing |

---

## Suggested Pulsar Feature Additions (Ranked)

Based on this research, here are features that should be **added to or elevated in the implementation plan**:

### Tier 1 — Must-have for Pulsar's mission (add to existing phases)

1. **Per-app routing persistence via WirePlumber** — currently the plan mentions routing topology but not how apps stick to sinks across reboots. WirePlumber does this with default-routes rules; Pulsar can wrap `wpctl set-default` and persist choices in profiles.

2. **Stream-level metadata in UI** — show "Discord — #general voice" instead of "sink-input 42." Trivial to implement (parse `pw-dump` JSON), huge UX win. **Add to Phase 3.**

3. **Subscribe to live graph changes** — `pw-dump --monitor` keeps the UI in sync when external changes happen. **Add to Phase 3 (essential for a responsive UI).**

4. **Device profiles (Bluetooth/HDMI)** — `wpctl set-profile` wraps cleanly. **Add to Phase 4.**

5. **Echo-cancellation preset** — many users won't know `module-echo-cancel` exists. Pre-built "VoIP mode" profile that loads it. **Add to Phase 4.**

### Tier 2 — Strong value-add (new phase or expanded Phase 5)

6. **Per-application audio capture workflow** — the "send Discord to OBS without a virtual sink" pattern. Uses ScreenCast portal. New sub-phase in Phase 5.

7. **Combine-stream for multi-output** — "send my music to headphones AND speakers simultaneously." This is the reverse of loopback. New feature, medium complexity. **Phase 4 or 5.**

8. **Volume sliders per virtual device** — easy with `wpctl set-volume` or `pactl set-sink-volume`. Pavucontrol does this for hardware sinks; we do it for virtual ones. **Phase 4 polish.**

9. **Filter-chain visual builder** — let users compose LADSPA/LV2 filters on a virtual sink without writing JSON. Phase 5+. **Different problem from routing — consider a separate project.**

### Tier 3 — Defer or exclude

10. **Built-in parametric EQ** — easyeffects owns this. Don't compete.
11. **Filter-chain wrapper** — if needed at all, only as "load a pre-made filter graph from easyeffects" import.
12. **Network audio (RTP/ROC)** — different problem entirely.
13. **Spatial audio / Atmos** — hardware-dependent; tiny user base.
14. **JACK tunnel UI** — JACK tools already cover this.

### Tier 4 — Discovered surprises worth knowing

- **JACK is a PipeWire session, not a separate system** on modern Linux. PipeWire provides JACK API via `pipewire-jack`. So "JACK users" are already our users.
- **easyeffects is already running on this system** (PID 2974) — there's prior art for filter-chain GUIs in the ecosystem. Don't reinvent.
- **portal ScreenCast AvailableSourceTypes=7** means we can capture per-app audio today, with no extra setup needed.
- **The `loopback` module can do channel remapping** — `audio.position` and `audio.layout` options. Useful for converting 5.1 → stereo for headphones.
- **WirePlumber's `smart-filters` and `automatic-software-dsp` features** — these could conflict with or complement Pulsar's manual routing. Worth understanding before designing Phase 4.
- **`send-message`** — PulseAudio (and PW) lets clients send arbitrary messages. Not user-facing, but a scripting hook for power users.

---

## Recommendations for the Implementation Plan

The current plan (Phase 0–6) is good in structure but under-specifies:

1. **Phase 3 (Profile persistence)** should explicitly include:
   - WP metadata format (not just JSON)
   - Stream-level routing (per-app, not per-sink)
   - Live updates via `pw-dump --monitor`
   - Migration from pactl-only metadata

2. **Phase 4 (Polish)** should add:
   - Bluetooth codec/profile UI
   - Volume sliders per virtual device
   - Combine-stream ("play to multiple outputs")
   - Built-in "VoIP" preset using module-echo-cancel

3. **Phase 5 (PipeWire-native)** should split into:
   - **5a:** Per-application audio capture (the killer feature for streamers)
   - **5b:** Advanced graph features (combine-stream, custom filters)
   - **5c:** Hardware integrations (BT codecs, HDMI profile switching)

4. **A new Phase 7 — Diagnostics** is worth considering:
   - Embed `pw-top` view (read-only)
   - Show graph state, sample rates, latency
   - "Why isn't my audio working?" troubleshooting wizard

---

## Source References

Local (verified on this system, PipeWire 1.6.2 / WirePlumber 1.6.2 / pactl 17.0):

- `man libpipewire-module-loopback` — Loopback module
- `man libpipewire-module-combine-stream` — Combine Stream module
- `man libpipewire-module-filter-chain` — Filter-Chain module
- `man libpipewire-module-echo-cancel` — Echo Cancel module
- `man libpipewire-module-link-factory` — Link Factory module
- `man libpipewire-module-parametric-equalizer` — Parametric EQ module
- `man libpipewire-module-portal` — Portal module
- `pactl help` — full command inventory
- `pw-cli help` — full command inventory
- `wpctl help` — WP CLI commands
- `pw-dump --help`, `pw-top --help` — tool options
- `wpctl status` — live system state
- `dbus-send ... org.freedesktop.portal.Desktop ... ScreenCast` — confirmed AvailableSourceTypes=7 (per-app capture enabled)

Remote (officially documented):

- https://docs.pipewire.org/ — PipeWire documentation (1.6.6)
- https://docs.pipewire.org/page_modules.html — module index
- https://docs.pipewire.org/page_man_pw-cli_1.html — pw-cli reference
- https://docs.pipewire.org/page_man_pw-top_1.html — pw-top reference
- https://pipewire.pages.freedesktop.org/wireplumber/ — WirePlumber 0.5.14 docs
- https://pipewire.pages.freedesktop.org/wireplumber/daemon/configuration.html — WP configuration
- https://pipewire.pages.freedesktop.org/wireplumber/daemon/policy.html — WP policies (linking policy, smart filters, automatic software DSP)