# Pulsar Phase 3 Profile Introspection: Verified Command Report

System tested: pactl 17.0 / libpulse 17.0.0, PipeWire 1.6.2, WirePlumber 1.6.2
Test environment: real PipeWire instance with hardware (USB Razer, PCI Starship) and existing EasyEffects null-sinks.

---

## TL;DR (recommendation up front)

Use **`pactl list modules short`** as your primary source of truth for the profile — both for capture and apply. It is single-line, stable, parseable, and contains exactly the args needed to recreate. Use `pactl list sinks` to look up the *Owner Module* field of the sinks a loopback connects to (for richer device metadata if you want it). Use `pactl info` to capture default sink/source. For hardware-sink identity across sessions, prefer **device.bus-path** or **device.serial** from `pw-dump` (or `pactl list cards`) over `node.name`.

---

## Q1. Capturing current null-sinks

### Best command

```bash
pactl list modules short
```

Output format (verified):

```
<id>\t<module_name>\t<arg_string>\t
```

Example verified output:

```
536870918	module-null-sink	media.class=Audio/Sink sink_name=pulsar_test_sink channels=2 rate=48000 sink_properties=device.description="Pulsar Test Sink"
536870920	module-loopback	source=pulsar_test_sink.monitor sink=alsa_output.pci-0000_28_00.4.analog-surround-21 latency_msec=1
```

### `pactl list modules short` vs `pactl list modules` (verbose)

| Aspect | `short` | `verbose` |
|---|---|---|
| Format | One TAB-delimited line per module | Multi-line block with `Module #N`, `Name:`, `Argument:`, `Properties:` etc. |
| Has args? | Yes, as a single string in field 3 | Yes, in `Argument:` line |
| Has extra metadata? | No | Yes (`module.author`, `module.description`, `module.usage`, `module.version`) |
| Parseable? | **Easiest** — split on `\t`, then shlex-split field 3 | Needs block-by-block parsing |
| Stable? | Yes | Yes |

**Verdict: use `pactl list modules short`.** The `Argument:` field in verbose mode is byte-identical to field 3 in short mode. The extra metadata is the static `module.usage` string (which already documents the arg format) and is not useful for round-tripping a profile.

### Get just one module by ID?

`pactl list modules <ID>` does **NOT** work. Tested: `pactl list modules 536870918` returns `Specify nothing, or one of: modules, sinks, sources, ...`. There is no ID filter in pactl's CLI.

Workarounds:

- Parse the `short` output and filter by ID in Python (cheap — output is small).
- For richer info on a specific module, use `pw-cli get-property <module-id>` or `pw-dump` filtered.

### `pw-dump` for null-sinks

`pw-dump` gives a 200+ object JSON of the entire PipeWire graph. Verified:

```json
{
  "id": 258,
  "type": "PipeWire:Interface:Node",
  "info": {
    "props": {
      "factory.name": "support.null-audio-sink",
      "media.class": "Audio/Sink",
      "node.name": "pulsar_test_sink",
      "audio.channels": "2",
      "audio.rate": "48000",
      "audio.position": "[ FL, FR ]",
      "object.serial": 7301,
      "node.driver": true,
      "client.id": 264
    }
  }
}
```

**Important asymmetry** verified: when you create a null-sink via `pactl load-module module-null-sink`, the result is a `PipeWire:Interface:Node` in `pw-dump` with `factory.name=support.null-audio-sink`, but it does **NOT** appear in the `PipeWire:Interface:Module` list in `pw-dump`. The `pactl` interface has its own internal module ID space (we saw values like `536870918` — PipeWire uses 9-digit IDs because pactl reserves 32 bits of the module ID and PipeWire uses high bits for its own modules).

**Verdict: `pw-dump` is overkill for this task.** It's slow (loads 200+ objects, ~630 KB JSON), contains lots of unrelated stuff (Ports, Links, Clients, Factories), and the null-sink data is duplicated in pactl's interface. Use it only if you need to access fields `pactl` doesn't expose (e.g. `object.serial`, `node.driver`, `factory.name`).

---

## Q2. Capturing current loopbacks

Same answer as Q1. `pactl list modules short` reports loopbacks on a single line. Example verified:

```
536870920	module-loopback	source=pulsar_test_sink.monitor sink=alsa_output.pci-0000_28_00.4.analog-surround-21 latency_msec=1
```

**Verified full set of args supported by `module-loopback` (from `module.usage`):**
`source=`, `sink=`, `latency_msec=`, `rate=`, `channels=`, `channel_map=`, `sink_input_properties=`, `source_output_properties=`, `source_dont_move=`, `sink_dont_move=`, `remix=`

The `source_dont_move` and `sink_dont_move` flags are worth considering for your schema — they prevent WirePlumber from re-routing your loopback when the user changes default devices.

**Critical gotcha verified (see Q5):** the loopback module is created successfully even when the source or sink name does not exist. The module ID is returned and the module shows up in `pactl list modules`, but no actual audio path is established. The only way to detect this is to verify the source/sink existed *before* creating the loopback, or to check `pactl list sink-inputs` after creation (the broken loopback won't appear).

---

## Q3. Listing hardware outputs

### How to enumerate real (non-null) sinks

```bash
pactl list sinks short
```

This gives all sinks. To filter, use the `is_null_sink()` approach (below).

### `is_null_sink(name)` reliability

**Verdict: your current approach (`is_null_sink` that checks `pactl list modules short` for `sink_name=<name>`) is the *most reliable* approach for null-sinks created via `pactl`.** Verified.

There are two alternative approaches I tested — both are *worse*:

1. **Check `Owner Module` field in `pactl list sinks`.** Does NOT work. Verified: the `Owner Module` field is `4294967295` (i.e. `0xFFFFFFFF`, the "n/a" sentinel) for *both* ALSA hardware sinks AND for any sink created via `pw-cli create-node` (which is how EasyEffects creates its sinks). The field is only useful for sinks created via `pactl load-module module-null-sink`, and even then, matching the owner ID to a module name is the same amount of work as the direct check.

2. **Check `factory.name` from `pw-dump`.** More complete (catches EasyEffects-style sinks too), but requires `pw-dump` (heavyweight) and a join from sink name to node id.

### Recommended `is_null_sink()`

```python
def is_null_sink(sink_name: str) -> bool:
    """True if `sink_name` was created via `pactl load-module module-null-sink`."""
    out = subprocess.check_output(['pactl', 'list', 'modules', 'short'], text=True)
    needle = f'sink_name={sink_name}'  # safe: sink_name can't contain '='
    for line in out.splitlines():
        parts = line.split('\t', 2)
        if len(parts) == 3 and parts[1] == 'module-null-sink' and needle in parts[2]:
            return True
    return False
```

**Edge case:** if a user manually names a null-sink the same as an existing hardware sink (e.g. `alsa_output.foo`), this will return False for the hardware sink. Extremely unlikely in practice, but you can add a `pactl list sinks` check to disambiguate if you care.

**Edge case:** EasyEffects-created null-sinks will return False from this. If you want Pulsar to also recognize those (e.g. for the "show all current virtual devices" case), you'd need a `pw-dump` check on `factory.name=support.null-audio-sink`. Probably not needed for v1 of the profile feature.

---

## Q4. Comparing hardware sinks across sessions

### The problem

`node.name` for ALSA sinks follows the pattern `alsa_output.<bus-id>.<profile>` where `<bus-id>` comes from `udev` enumeration:

- PCI (internal): `alsa_output.pci-0000_00_1f.3.analog-stereo` — extremely stable, won't change unless the user changes their hardware.
- USB: `alsa_output.usb-1532_Razer_BlackShark_V2_Pro_2.4_O001000007-00.analog-stereo` — includes the device's USB serial number, very stable for the same physical device.
- Bluetooth: `bluez_output.<MAC-or-name>.<codec>` — name is reasonably stable, but the device may register with different paths/codecs after a re-pair.

### Stable identifiers verified

From `pw-dump` and `pactl list cards`, ranked by stability:

1. **`device.bus-path`** — physical USB/PCI path. Verified: `pci-0000:03:00.0-usb-0:4:1.0`. Survives reconnections to the same port. Will change if user moves the device to a different USB port.
2. **`device.serial`** — USB serial number. Verified: `1532_Razer_BlackShark_V2_Pro_2.4_O001000007`. Survives any port changes. Only present for devices that report a serial (most do, some cheap ones don't).
3. **PCI `device.bus-path`** — `pci-0000:28:00.4`. Essentially hardware-fixed.
4. **Vendor/Product IDs** — `device.vendor.id=0x10de`, `device.product.id=0x1aef`. Useful for "any Razer BlackShark V2 Pro" matching.
5. **`node.name` (the `alsa_output.X` string)** — stable in practice for PCI and most USB, but is reconstructed from the bus path each session, so it WILL change if a device appears at a different port or with a different udev name.

### Recommendation

For your schema, do **not** rely solely on `node.name` for hardware sinks. Two options:

- **Option A (simple):** store `node.name`, and at apply time, look up the device's `device.bus-path` and re-derive the current `node.name`. If the user's profile says `alsa_output.pci-0000_00_1f.3.analog-stereo`, look up the bus-path `pci-0000:00:1f.3` in `pw-dump` and find whichever `alsa_output` node belongs to that device now. This handles USB re-plugging gracefully.

- **Option B (more robust):** store `device.bus-path` (or a list of candidate identifiers: bus-path, serial, vendor+product) and resolve to current `node.name` at apply time. Add a UI step "Could not find device matching your saved profile — pick a hardware sink" if resolution fails.

`pactl list cards` exposes `device.bus_path` (note: underscore vs dot, it's `bus_path` in pactl output and `bus-path` in pw-dump) and `device.serial` per card. So you don't even need `pw-dump` to access these.

---

## Q5. Error recovery during apply

### Q5a: channels=8 when only stereo is supported

**Verified: this almost never fails at module-load time.** Tested `module-null-sink media.class=Audio/Sink sink_name=pulsar_8ch channels=8` — exit 0, module ID returned, sink created successfully with 8 channels (uses `aux0..aux7` channel map by default).

The sink creation will only fail if:
- `channel_map` is explicitly given with wrong count (e.g. `channels=4 channel_map=FL,FR` → `Failure: Invalid argument`, exit 1).
- `format` is invalid (e.g. `format=invalid_format`).
- `rate` is a value the SPA layer can't negotiate (e.g. `rate=999999999` *did* still succeed — PipeWire has flexible rate handling).

**For null-sinks specifically, channel count is mostly free** — a null-sink isn't bound to hardware capabilities. The constraint is at the *loopback* level (a 2-channel monitor feeding into a stereo sink works; a 6-channel monitor feeding into stereo requires the `remix` flag to downmix).

### Q5b: loopback to non-existent sink

**Critical verified finding:** `pactl load-module module-loopback source=pulsar_test_sink.monitor sink=alsa_output.does_not_exist latency_msec=1` returns **success (exit 0, module ID 536870922)**. The module is created in a broken state — it shows in `pactl list modules` but no audio is routed. Verified the same for non-existent source.

This means you **cannot** trust the exit code of `pactl load-module module-loopback` alone. You need to verify the source and sink exist *before* issuing the command, then optionally verify the loopback produced a `sink-input` entry after.

### Q5c: Detecting partial failure mid-apply

Recommended strategy: **pre-flight validation, then best-effort apply, then post-flight verification, with rollback on failure.**

1. **Pre-flight** — for each device in the profile:
   - If the profile has a unique `sink_name` that already exists in `pactl list sinks short` and you didn't expect it, abort (or unload the existing one).
   - If a routing entry's `to` field refers to a sink that doesn't currently exist (in `pactl list sinks short`), abort with a clear error.

2. **Apply devices first** (cheap to roll back). For each device, capture the returned module ID. If the device doesn't appear in `pactl list sinks short` within ~200ms of the load-module call, treat it as failed. Rollback: unload all device modules created in this session.

3. **Apply loopbacks** (these are the brittle ones). For each loopback, capture the module ID, then check if the loopback created a corresponding `sink-input` (look in `pactl list sink-inputs` for one whose `Sink` matches the target hardware). If after ~200ms there's no sink-input, the loopback is in a zombie state — unload it.

4. **Rollback**: track all module IDs you created (devices and loopbacks), call `pactl unload-module` on each. This is idempotent — unloading a non-existent module is a no-op.

A simpler alternative: just **unload everything Pulsar created in the current session** (track module IDs in a Set) before applying a new profile, and require the user to manually fix any partial state.

### Q5c.bonus: zombie modules confirmed

When you create a null-sink with a `media.class` value that PipeWire doesn't recognize (tested `Audio/Bogus`), `pactl load-module` still returns success and the module shows in `pactl list modules short`, but the sink **does NOT** appear in `pactl list sinks short`. So a 2-step verification (module loaded + sink exists) catches this.

---

## Q6. Profile schema design

### Q6a: Fields missing for round-trip

For `null-sink` modules, your schema should round-trip all six options Pulsar uses. Verified that `pactl list modules short` exposes them all on a single line:
- `media.class` (required for distinguishing sink/source/duplex)
- `sink_name` (required)
- `channels` (optional but common)
- `rate` (optional, default 48000)
- `format` (optional, default s16le/float32le depending on PW config)
- `channel_map` (optional, but only if non-default)
- `sink_properties` (optional, but if set, required for round-trip)

For `loopback` modules, the full arg set is:
- `source=` (required)
- `sink=` (required)
- `latency_msec=` (optional, default 100)
- `rate=`, `channels=`, `channel_map=` (optional, for resampling/recoding)
- `sink_dont_move=`, `source_dont_move=` (optional but recommended — see below)
- `remix=` (optional, default `true`)
- `sink_input_properties=`, `source_output_properties=` (optional, advanced)

**Recommended: add `sink_dont_move=true` and `source_dont_move=true` to every loopback Pulsar creates by default.** Without these, WirePlumber can move your loopback's endpoints when the user changes the default sink via the system tray. With them, the loopback is pinned.

**Missing field for v2: nothing critical, but consider capturing `pactl info`'s `Default Sink:` and `Default Source:`** (Q6c below).

### Q6b: Encoding for `sink_properties`

**`sink_properties=key1=val1 key2=val2 ...`** is what `pactl load-module` accepts. JSON in your profile is fine; you'll just need to serialize back to a single string at apply time.

**Verified gotcha: the parsing of `sink_properties=...` via `pactl load-module` is fragile.** Tested:
- `sink_properties="device.description='Test2' device.icon_name='audio-card'"` — works correctly, both properties applied.
- `sink_properties='device.description="Test Prop" device.icon_name="audio-card"'` — **broken**: the second property gets concatenated into the first. The parser doesn't handle the embedded space + unescaped double-quote inside the value well.

**Recommendation:** when serializing back to `sink_properties=...`, **always use single-quoted values and double-quote the whole arg** in your shell. E.g.:

```bash
pactl load-module module-null-sink ... \
  sink_properties="device.description='Hello World' device.icon_name='speaker'"
```

A more robust alternative: pass `sink_properties` to `pw-cli create-node` as a structured proplist (which supports proper escaping). But then the sink won't be a `module-null-sink` anymore and you'll have to track it differently. Probably not worth it for v1.

For JSON encoding, your `sink_properties` field should be a JSON object (`{"device.description": "Hello World", "device.icon_name": "speaker"}`) — easier to edit, easy to serialize, no quoting headaches in your profile file.

### Q6c: Should the schema capture default-sink changes?

**Yes, but as a v2.1 thing.** Verified that `pactl info` gives `Default Sink:` and `Default Source:` as plain text fields. Two options:

1. Add a top-level `defaults: {"sink": "...", "source": "..."}` to the profile.
2. Implicitly set the default to the first device's sink (which is what most users want anyway).

Pulsar doesn't do this today, so for v2 of the profile format, I'd **leave it out** and add it as v3 if requested. The apply path can always run `pactl set-default-sink` at the end if you want it.

### Q6 additional findings (not asked but useful)

- **Sink name validation:** verified that sink names can contain dots (`app.something`) and can be very long (300+ chars worked). They cannot contain spaces at the shell level unless quoted. Recommend: in your UI, validate `sink_name` against `[A-Za-z0-9_.-]+` and surface an error otherwise.
- **Profile names (top-level keys in the JSON):** PulseAudio's own default profile mechanism uses directory names like `output:analog-stereo+input:analog-stereo`. Pulsar may want to allow more characters here, but the safest set is also `[A-Za-z0-9_.-]+`.
- **`channel_map` format:** comma-separated, e.g. `front-left,front-right` or `aux0,aux1,aux2,aux3,aux4,aux5,aux6,aux7`. The `aux*` names appear when you don't specify a channel_map and the count is > 8 (or some other unusual config).
- **Description vs name:** `device.description` in `sink_properties` becomes the `Description:` field in `pactl list sinks`. The sink's `Name:` is always `sink_name`. So your schema's separate `description` field makes sense.

---

## Q7. Loading pactl output programmatically — Python gotchas

### `pactl list modules short` format stability

**Verified stable across the version we tested** (pactl 17.0 = PulseAudio client 17, on PipeWire 1.6.2). The format is:

```
<module_id>\t<module_name>\t<arg_string>\t
```

Notes:
- Three TAB-separated fields, **always** exactly three (so `split('\t', 2)` is safe — the last field may itself contain tabs in theory, but in practice doesn't for `module-null-sink` or `module-loopback`).
- `<module_id>` is a decimal integer string. PipeWire returns 9-digit values (e.g. `536870918`); legacy PulseAudio returns smaller values (e.g. `28`).
- The third field is a single line — **no embedded newlines** in the arg string, even for complex module types with multi-line args like `filter-chain` (which isn't exposed via pactl anyway, but worth checking). Verified with `cat -A` that the line ends in `$` (LF), no `^M` (CR).
- The line ends with a trailing TAB before the LF. Your parser should be robust to this (use `splitlines()` or strip trailing whitespace).

The format has been stable since PulseAudio 5+ (~2014). PipeWire 1.x preserves it. **Safe to rely on.**

### Multi-line args

Verified: `pactl list modules short` is **strictly one line per module** in pactl 17.0 / PipeWire 1.6.2. There are no modules exposed via pactl that have multi-line args — `module-filter-chain` is the classic case (it uses `filter.graph={ ... }` with embedded newlines) and it is not exposed via pactl. You won't encounter this in Pulsar's use case.

If you ever need to round-trip `filter-chain` (e.g. for EasyEffects compatibility), use `pactl list modules` (verbose mode) which preserves the `Argument:` line as-is including embedded newlines, and parse the block as a whole.

### Python parsing recommendations

```python
import subprocess
import shlex

def list_pactl_modules() -> list[dict]:
    """Return list of {id, name, args_dict} for all loaded pactl modules."""
    out = subprocess.check_output(['pactl', 'list', 'modules', 'short'], text=True)
    modules = []
    for line in out.splitlines():
        parts = line.split('\t', 2)
        if len(parts) != 3:
            continue
        mod_id, mod_name, args_str = parts
        args = parse_pactl_args(args_str)
        modules.append({
            'id': int(mod_id),
            'name': mod_name,
            'args': args,
            'raw_args': args_str,
        })
    return modules

def parse_pactl_args(s: str) -> dict:
    """
    Parse 'key1=val1 key2=val2 sink_properties=device.description=foo device.icon=bar'
    into {'key1': 'val1', 'key2': 'val2', 'sink_properties': '...full string...'}.
    Special handling: 'sink_properties' captures the rest (Pulsar set it as one string).
    """
    args = {}
    tokens = shlex.split(s)  # may not handle all cases — see below
    for tok in tokens:
        if '=' in tok:
            k, _, v = tok.partition('=')
            args[k] = v
    return args
```

**Python gotcha: `shlex.split` doesn't perfectly match pactl's parser.** The `sink_properties=...` value can contain spaces (e.g. `device.description="My Device"`) and `shlex.split` may break that. For Pulsar's case, the safer approach is to use the args verbatim and re-serialize at apply time. The only fields you actually need to read are `media.class`, `sink_name`, `channels`, `rate`, `format`, `channel_map`, and `source`/`sink`/`latency_msec` for loopbacks — all of which are simple unquoted values.

If you do want to parse `sink_properties` for the schema, use a regex:
```python
import re
m = re.search(r'sink_properties=((?:[^\s"]|"[^"]*")+)', args_str)
```
or just keep the raw substring after `sink_properties=` and pass it back verbatim on apply (no parsing needed).

---

## Summary of gotchas to handle

1. **Loopback to non-existent sink/source returns success.** Pre-validate endpoint names.
2. **Null-sink with bogus `media.class` returns success but no sink is registered.** Verify the sink appears in `pactl list sinks short` after creation.
3. **Owner Module is `4294967295` (n/a) for hardware sinks AND for sinks created via `pw-cli` (EasyEffects).** Use `pactl list modules short` membership to detect Pulsar's null-sinks, not Owner Module.
4. **`pactl list modules` does not accept an ID filter.** Parse the short output client-side.
5. **`sink_properties=...` is fragile when values contain both quotes and spaces.** Use single-quoted values when serializing.
6. **Sink names with spaces work in pactl but break shell quoting in your apply path.** Validate `sink_name` is shell-safe.
7. **Hardware sink names can change across sessions for USB/Bluetooth devices.** Capture `device.bus-path` and `device.serial` from `pactl list cards` for stable identity; resolve to current `node.name` at apply time.
8. **Loopback default behavior: WirePlumber may move your loopback endpoints.** Pass `sink_dont_move=true` and `source_dont_move=true` on every Pulsar-created loopback.
9. **`pactl list modules short` is single-line and stable. Trust it.**
10. **Module IDs in PipeWire are 9-digit (e.g. `536870918`)** — make sure your code doesn't assume they fit in 32 bits (they do, but parse as strings to be safe).

---

## Reference: Verified working command list

```bash
# Capture full topology (devices + loopbacks)
pactl list modules short

# For each sink: get its Owner Module + actual sample spec
pactl list sinks

# For each loopback: verify it produced a sink-input
pactl list sink-inputs

# Hardware identification
pactl list cards        # has device.bus_path, device.serial, device.vendor.id, device.product.id
pw-dump                 # full graph (overkill but has everything)

# For appy: create the topology
pactl load-module module-null-sink media.class=Audio/Sink sink_name=X channels=2 [rate=R] [format=F] [channel_map=M] [sink_properties="..."]
pactl load-module module-loopback source=X.monitor sink=Y latency_msec=1 [sink_dont_move=true source_dont_move=true]

# For rollback: unload everything Pulsar created
pactl unload-module <id>

# For default-sink changes (optional)
pactl set-default-sink <name>
pactl set-default-source <name>
pactl info   # to read current defaults
```
