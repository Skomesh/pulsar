# Pulsar Implementation Plan

A long-term roadmap for evolving the Pulsar fork from pactl-gui's MVP into a streamer-oriented audio routing manager. Phases are ordered so each one delivers a usable improvement before moving on. Every phase ends in something runnable that you can actually test on your real PipeWire setup.

---

## Current State (baseline, post-fork)

**Inherited from pactl-gui (3,338 LOC of Python+Tkinter):**
- `src/utils/pactl_runner.py` (382 lines) — wraps `pactl` subprocess calls; can list sinks/sources/modules, create duplex sinks, unload modules
- `src/utils/preset_manager.py` (192 lines) — saves/loads presets as JSON; supports 5 built-in channel configurations (Stereo/Mono/5.1/7.1/Custom)
- `src/ui/main_window.py` (2,723 lines) — single Tkinter window with three tabs: Create / Manage / Output
- **Limitations:** creates only duplex (sink+source) devices, no Sink-vs-Source choice; no `module-loopback` support at all; preset loading is wired up but doesn't recreate routing; no concept of "profile" or "topology"

**Environment:**
- Target runtime: PipeWire 1.6.2 with `pipewire-pulse` shim (confirmed on this system)
- Python 3.14, Tkinter
- `pactl 17.0` available; legacy PA is masked/inactive

**Git state:**
- Upstream: `Skrappjaw/pactl-gui` (trackable for future improvements)
- Origin: `Skomesh/pulsar`
- Branching: use `feat/*`, `fix/*`, `refactor/*` conventions; PR each phase to `main`

---

## Guiding Principles

1. **Each phase ends in something you can actually use.** No 6-month gaps where the app is half-broken.
2. **Backend first, UI last within each phase.** Get the `pactl` calls right and tested from the CLI/repl before touching Tkinter.
3. **Preserve upstream compatibility where cheap.** Don't delete pactl-gui features — extend them. Makes future re-merging easier.
4. **Test on the real PipeWire system.** No mocking pactl. If a test needs audio, use a real null sink.
5. **One concern per PR.** Don't bundle Sink/Source refactor + loopback + presets into one mega-commit.

---

## Phase 0 — Housekeeping

**Goal:** Establish project infrastructure before writing features.

**Tasks:**
- [ ] Add a `LICENSE` file (MIT text — currently only referenced in README but missing from repo, which is a real problem for a public fork)
- [ ] Verify upstream actually IS MIT by inspecting their LICENSE (look at the `pactl-gui` repo's main branch)
- [ ] Add `CONTRIBUTING.md` (pactl-gui references one but doesn't ship it; we should)
- [ ] Add `pyproject.toml` or `requirements.txt` enforcement via a Makefile (`make run`, `make test`, `make lint`)
- [ ] Set up a basic CI workflow (GitHub Actions): Python 3 syntax check, `pylint` or `ruff`, JSON-validates `user_presets.json`
- [ ] Decide: rename the entry point `pactl-gui.sh` → `pulsar`? Rename the desktop file? Lower priority but tidy up before features pile up.

**Done when:** A fresh clone on a clean machine can `make run` and the CI passes.

---

## Phase 1 — Sink / Source / Both Choice at Creation

**Goal:** Replace the "always-duplex" creation with a user choice. Lowest-risk first feature because it only changes the Create tab UI and one `pactl_runner` method.

**Backend (`pactl_runner.py`):**
- Add `create_sink_only(name, description, ...)` — wraps `pactl load-module module-null-sink sink_name=X ...`
- Add `create_source_only(name, description, ...)` — wraps `pactl load-module module-null-sink sink_name=X ... source_name=Y ...` with `source_properties=...` to expose it as a source
- Keep `create_duplex_sink()` for backward compat (existing user_presets.json entries still work)
- Each returns the loaded module ID so the UI can reference it later

**UI (`main_window.py`, Create tab):**
- Replace the channel-count / channel-map controls with a tri-state radio: **Sink only / Source only / Both (duplex)**
- Sink-only and Source-only variants show a description hint: "Apps can play to this device" / "Apps can record from this device" / "Apps can do both"
- "Both" keeps the existing channel configuration options (Stereo/Mono/5.1/7.1/Custom)
- "Sink only" and "Source only" use sensible defaults (Stereo, no special channel map)

**Testing:**
- Manual: create each of the three types, verify in `pactl list short sinks` and `pactl list short sources` that they appear correctly
- Automated: optional `tests/test_pactl_runner.py` using `pytest` with a marker that skips if `pactl` isn't available

**Done when:** You can create a sink-only device, set Discord's output to it in pavucontrol, and audio goes there.

**Estimated complexity:** Small. ~150 lines of changes.

---

## Phase 2 — Loopback Routing

**Goal:** Add the missing piece that makes Pulsar actually useful — wire a virtual sink to a real output.

**Backend:**
- Add `create_loopback(source_name, sink_name, latency_msec=1)` to `pactl_runner.py` — wraps `pactl load-module module-loopback source=X.monitor sink=Y latency_msec=Z`
- Add `list_loopbacks()` — parses `pactl list modules short` filtering for `module-loopback`
- Add `unload_loopback(module_id)` — wraps `pactl unload-module`
- Returned module IDs stored alongside the device they route FROM (not the device they route TO)

**UI (Manage tab):**
- For each sink-only or duplex device, show a dropdown of real output sinks (`pactl list short sinks` minus the null-sinks)
- Selecting an option creates a loopback and shows "🔊 Routing to: <name>" with a stop button
- Selecting "(none)" unloads any existing loopback for that virtual sink
- Source-only devices don't show the routing control (you can't loopback a source to a sink meaningfully without `module-remap-source` first, which is out of scope for this phase)

**Edge cases:**
- Handle "real output disappears" (USB headphones unplugged) — show a warning, offer "pick new output"
- Handle "loopback already exists" idempotently — don't double-loopback
- `latency_msec=1` is fine for desktop; expose as a setting later

**Testing:**
- Create `game_sink`, route it to headphones, play audio in an app set to `game_sink`, verify it's audible
- Unplug headphones, verify graceful handling

**Done when:** You can recreate your `StreamAudio.sh` workflow via the GUI: three null sinks, two looped to your headphones, one isolated.

**Estimated complexity:** Medium. ~300 lines of changes, mostly UI.

---

## Phase 3 — Profile Persistence

**Goal:** Save and load complete routing topologies, not just device configurations.

**Data model evolution (`user_presets.json`):**
Current schema (per preset):
```json
{
  "name": {"channels": "2", "channel_map": "...", "description": "...", "builtin": false}
}
```
New schema (rename concept to "profile" or keep "preset" — decide in this phase):
```json
{
  "Game and Teamspeak": {
    "created": "...",
    "schema_version": 2,
    "devices": [
      {"name": "game_sink", "type": "sink", "channels": 2, "description": "Game Audio"},
      {"name": "teamspeak_sink", "type": "sink", "channels": 2, "description": "Voice"},
      {"name": "music_sink", "type": "source", "channels": 2, "description": "Music for stream"}
    ],
    "routing": [
      {"from": "game_sink.monitor", "to": "alsa_output.pci-0000_00_1f.3.analog-stereo"},
      {"from": "teamspeak_sink.monitor", "to": "alsa_output.pci-0000_00_1f.3.analog-stereo"}
    ],
    "app_routing": [
      {"match_application": "discord", "sink": "teamspeak_sink"},
      {"match_application": "steam_app_440", "sink": "game_sink"}
    ]
  }
}
```

**Backend:**
- New schema version field for migration safety
- `load_profile(name)` — creates all devices, then creates all routing, then applies app routing
- `delete_profile(name)` — unloads all modules in the profile
- `apply_profile(name)` — atomic-ish: if anything fails mid-way, offer to roll back
- `get_active_profile()` — diff the running config against stored profiles to detect "modified by user externally"
- **Stream metadata enrichment:** parse `pw-dump` JSON to show "Discord — #general voice (PID 12345)" instead of "sink-input 42"
- **Live updates:** subscribe to `pw-dump --monitor` (or `pactl subscribe`) so the UI refreshes when external changes happen (user changes volume in pavucontrol, app starts/stops)
- **`wpctl set-default` for per-app persistence** — pin a specific app's audio to a specific sink via WirePlumber's default-routes mechanism, so the choice survives app restarts

**UI:**
- New tab: **Profiles** (or merge into Manage tab — decide based on tab count)
- List of saved profiles with Apply / Edit / Delete buttons
- "Save current state as profile" button on Create and Manage tabs
- On startup: detect if saved profile matches current config, show indicator
- App-routing editor: drag-drop apps to sinks, or "Discord → always goes to teamspeak_sink" toggle

**Migration:**
- Existing `user_presets.json` files keep working — wrap old format in a new envelope with empty `devices`, `routing`, and `app_routing` arrays

**Done when:** You can click "Apply: Game and Teamspeak" and the entire `StreamAudio.sh` setup happens via GUI, AND Discord's audio is automatically pinned to `teamspeak_sink` even after restarting Discord.

**Estimated complexity:** Medium-large. ~600 lines, including migration code.

**Reference:** See `docs/PIPEWIRE_RESEARCH.md` for the underlying WP/pw-dump/pw-cli APIs that make this work.

---

## Phase 4 — Streamer-Focused Polish

**Goal:** Make Pulsar feel like a tool, not a CLI wrapper.

**Features:**
- **Bundled starter profiles:** ship 2-3 built-in profiles (Gaming, Streaming, Voice Chat Only) that appear in the Profiles list on first run
- **System tray integration:** minimize to tray, right-click menu for "Apply Game profile" / "Apply Stream profile"
- **Profile auto-switching:** detect when a specific app launches (e.g. Discord, OBS, Steam Big Picture) and offer to apply a profile
- **Volume sliders per channel:** not just mute/unmute — give each routed device its own volume (this is what pavucontrol does for inputs, we add it for our virtual sinks). Use `wpctl set-volume` or `pactl set-sink-volume`.
- **Better empty states:** when no virtual devices exist, show "Create your first virtual audio device" with a one-click "Gaming setup" button that creates all three sinks in one shot
- **Bluetooth device profiles** — show available profiles (HFP/A2DP) and let user switch with `wpctl set-profile`. Also show codec selection if available.
- **HDMI/USB device routing** — show profiles/routes for hardware with multiple outputs; let user pin a default
- **Combine-stream preset** — "music plays on speakers AND headphones simultaneously" using `module-combine-stream`. UI: "send this sink to multiple outputs" toggle.
- **Echo-cancellation preset** — one-click "VoIP mode" that loads `module-echo-cancel` with WebRTC AEC. Many users don't know this exists.

**Done when:** A new user can install Pulsar and have a working streaming setup in under 60 seconds without reading docs.

**Estimated complexity:** Large. ~1000 lines + design decisions.

**Reference:** See `docs/PIPEWIRE_RESEARCH.md` Tier 1 features 4–5 and Tier 2 features 7–9 for the underlying APIs.

---

## Phase 5 — PipeWire-Native Features

**Goal:** Once the core works on the PA shim, unlock PipeWire-specific power.

**Why:** Some advanced routing is impossible or awkward in PA-compat mode. These features only work when running against PipeWire directly.

**Phase 5a — Per-application audio capture (the killer feature):**
- Use the xdg-desktop-portal ScreenCast API (`AvailableSourceTypes=7` confirmed on this system — APPLICATION capture is available)
- Workflow: user clicks "Capture Discord audio in OBS" → Pulsar queries WP for Discord's audio nodes → tells OBS (via WebSocket) to add a PipeWire audio capture source for that specific app node → no virtual sink needed
- See `docs/PIPEWIRE_RESEARCH.md` Section D for the full rationale

**Phase 5b — Advanced graph features:**
- **Custom filter chains:** wrap `module-filter-chain` for routing audio through LADSPA/LV2 filters. Note: this is borderline with easyeffects territory; consider it a power-user escape hatch only.
- **Combine-stream editor:** expose the `module-combine-stream` config UI for arbitrary mixing (e.g. "mic + system audio both go to OBS").
- **pw-cli link management:** for power users who want per-port routing (e.g. only the front-left/right of a 5.1 stream goes to headphones, not the LFE channel).

**Phase 5c — Hardware integrations:**
- **Bluetooth codec selection** (SBC/AAC/aptX/LDAC) — show current codec, let user switch. Requires WirePlumber settings API.
- **Headset profile switching** — HFP for voice chat, A2DP for quality. `wpctl set-profile`.
- **HDMI hot-plug handling** — when a TV connects/disconnects, offer to apply the appropriate profile.
- **USB device quirks** — detect feedback-capable devices and warn users.

**Detection:**
- Check `pactl info` for "Server Name: PulseAudio (on PipeWire X.Y.Z)" — if PipeWire, enable these features in the UI; if pure PA, hide them
- Verify portal ScreenCast version ≥ 4 and AvailableSourceTypes includes APPLICATION (bit 3 = 4)

**Done when:**
- 5a: A user can route Discord's audio to OBS without ever creating a virtual device
- 5b: Power users can build custom filter graphs via the GUI
- 5c: Bluetooth headphones show their codec and let the user switch

**Estimated complexity:** Large. Requires PipeWire-specific knowledge; consider targeting one sub-phase at a time.

---

## Phase 6 — Distribution & Community

**Goal:** Make Pulsar easy to install and welcoming to contributors.

**Tasks:**
- [ ] Flatpak packaging (pactl-gui has a flatpak.yml but it's stale — update it)
- [ ] AUR package (Arch users will eat this up; Python+system deps + desktop file)
- [ ] Snap or .deb for Ubuntu users
- [ ] `pulsar` command-line variant for headless / SSH setups
- [ ] Screenshots in README that show the actual UI
- [ ] Asciinema recording of the "60-second setup" workflow
- [ ] Decide on Discord/Matrix/issue-tracker for community

**Done when:** `flatpak install pulsar` works and lands you on a runnable app on any modern distro.

**Estimated complexity:** Mostly mechanical, but high coordination cost (testing on multiple distros).

---

## Non-Goals (Explicit)

To stay focused, Pulsar is NOT trying to be:

- **A general audio mixer** (that's PulseEffects/easyeffects territory, different problem)
- **A JACK replacement** (JACK is for ultra-low-latency pro audio; different audience)
- **An OBS plugin** (OBS already handles its own audio routing well)
- **A Windows/macOS app** (the gap is specifically on Linux; Voicemeeter and Wave Link already own the desktop OS side)
- **A pro audio tool with sample-accurate sync** (use JACK + Ardour for that)

---

## Open Questions to Resolve During Implementation

1. **"Preset" vs "Profile" naming.** The pactl-gui code calls them presets, but they really represent whole routing topologies. Renaming is a UX call. Decide in Phase 3.
2. **Tab structure.** Three tabs is fine now, but Phase 3+ will push us toward four or five. Worth rethinking before Phase 3.
3. **PipeWire-as-only target?** The PA-compat shim works, but maintaining dual support is friction. Consider: detect at startup, warn if running on legacy PA, support both but optimize for PipeWire.
4. **Fork vs. vendoring pactl-gui.** Currently we treat it as a fork. As our changes diverge (Phase 3+), merging from upstream becomes harder. Decide whether to keep the upstream remote or absorb the project entirely.

---

## Suggested Execution Order

1. Phase 0 — Housekeeping (small, sets up CI)
2. Phase 1 — Sink/Source/Both choice (small, immediate value)
3. Phase 2 — Loopback routing (medium, the headline Pulsar feature)
4. **Stop and validate.** Run this for a week. If you're actually using it daily, Phase 3+ is justified. If not, you've learned the problem space and can rethink scope.
5. Phase 3 — Profile persistence (only if Phase 2 is solid)
6. Phase 4 — Polish (only if you have users besides yourself)
7. Phase 5 — PipeWire-native (only if the project has momentum)
8. Phase 6 — Distribution (always last; distributions before features are done = embarrassment)

---

## Phase 7 — Diagnostics

**Goal:** Help users figure out why their audio isn't working.

**Features:**
- **Embedded `pw-top` view** — read-only real-time node/device graph, scrollable, sortable by latency
- **Sample-rate mismatch detector** — warn when a device is running at a different rate than the system clock (causes resampling overhead and subtle quality loss)
- **Quantum/latency inspector** — show per-driver quantum, suggest tuning for low-latency use cases
- **Module dependency viewer** — show which loopbacks depend on which sinks; help debug "why did my music_sink disappear when I unloaded game_sink?"
- **"Why isn't my audio working?" wizard** — checks: is pactl/pw-cli reachable? Is WirePlumber running? Is the user in the audio group? Are required modules loaded?
- **Live log viewer** — tail `pw-top --batch-mode` output for debugging

**API basis:** `pw-top` for live stats, `pw-dump --monitor` for graph state, `wpctl status` for device summary.

**Done when:** A non-expert user can open the Diagnostics tab and immediately see why their microphone isn't reaching Discord.

**Estimated complexity:** Medium. ~400 lines, mostly UI work wrapping existing CLI tools.

---

## Tracking

- One GitHub issue per phase, with a checklist matching the tasks above
- One PR per logical unit of work (not per phase — phases are too big for single PRs)
- Tag releases as `v0.1.0` after Phase 1, `v0.2.0` after Phase 2, etc.

---

## Further Reading

- `docs/PIPEWIRE_RESEARCH.md` — comprehensive PipeWire/WirePlumber feature inventory and module reference. Read this before starting Phase 3+.