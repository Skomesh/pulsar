"""
Profile Manager for Pulsar.

A profile captures a complete audio routing topology: which virtual devices
exist, what their settings are, and which loopbacks connect them to real
outputs. Profiles are persisted as JSON and can be applied to recreate the
exact same topology later.

Schema v2 (current):

.. code-block:: json

    {
        "<profile_name>": {
            "schema_version": 2,
            "created": "<ISO 8601 timestamp>",
            "description": "<optional human-readable description>",
            "devices": [
                {
                    "name": "game_sink",
                    "type": "sink",            // sink | source | both
                    "channels": 2,
                    "description": "Game Audio",
                    "rate": 48000,             // optional
                    "format": "s16le",         // optional
                    "channel_map": "front-left,front-right",  // optional
                    "sink_properties": {       // optional, JSON object
                        "device.description": "Game Audio",
                        "device.icon_name": "audio-card"
                    }
                }
            ],
            "routing": [
                {
                    "from": "game_sink",      // sink name (we loopback its monitor)
                    "to": "alsa_output.pci-...analog-stereo",
                    "latency_msec": 1
                }
            ]
        }
    }

Migration from schema v1 (channel presets only):

A v1 preset looked like ``{"name": {"channels": "2", ...}}``. v2 keys
profile names directly with ``schema_version`` set. Loading a v1 file
auto-migrates each entry to an empty v2 profile (no devices, no routing).
This preserves backward compatibility with files saved by older pactl-gui.

For details on why this schema looks the way it does, see
``docs/PHASE3_INTROSPECTION_REPORT.md``.
"""

import json
import os
import re
import shlex
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from utils.pactl_runner import PactlRunner

CURRENT_SCHEMA_VERSION = 2

# Sink names must be shell-safe. pactl tolerates more, but our apply path
# uses shlex.split, so we restrict to a conservative subset.
_VALID_SINK_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


class ProfileError(Exception):
    """Raised when a profile is invalid, fails to apply, or can't be saved."""


class ProfileManager:
    """Persist and apply audio routing topologies (profiles).

    The on-disk format is a single JSON file (``presets/user_presets.json``
    by default for backward compatibility with pactl-gui). Profile names are
    top-level keys. v1 entries are auto-migrated to empty v2 profiles on load.
    """

    def __init__(self, presets_dir: str = "presets"):
        self.presets_dir = presets_dir
        self.presets_file = os.path.join(presets_dir, "user_presets.json")
        self._ensure_presets_dir()

    # ------------------------------------------------------------------
    # Filesystem helpers
    # ------------------------------------------------------------------

    def _ensure_presets_dir(self):
        os.makedirs(self.presets_dir, exist_ok=True)

    def _load_raw(self) -> Dict[str, Any]:
        if not os.path.exists(self.presets_file):
            return {}
        try:
            with open(self.presets_file) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_raw(self, data: Dict[str, Any]) -> bool:
        try:
            with open(self.presets_file, "w") as f:
                json.dump(data, f, indent=2)
            return True
        except OSError:
            return False

    # ------------------------------------------------------------------
    # Schema migration
    # ------------------------------------------------------------------

    @staticmethod
    def _looks_like_v1(entry: Dict[str, Any]) -> bool:
        """v1 entries have channel fields directly; v2 entries have 'devices'."""
        return "devices" not in entry and (
            "channels" in entry or "channel_map" in entry or "description" in entry
        )

    @staticmethod
    def migrate_entry(name: str, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a v1 entry to v2 in place. Returns the migrated dict.

        v1 entries describe a single channel configuration; we wrap them in an
        empty v2 profile so the name is preserved (with a "(migrated)" marker
        in the description) but no devices/routing are carried over.
        """
        if not ProfileManager._looks_like_v1(raw):
            # Already v2 — pass through, but make sure schema_version is set.
            out = dict(raw)
            out.setdefault("schema_version", CURRENT_SCHEMA_VERSION)
            return out

        # v1 -> v2 empty wrapper
        return {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "created": raw.get("created") or datetime.now(timezone.utc).isoformat(),
            "description": (
                f"Migrated from v1 (channels={raw.get('channels', '?')}). "
                "Empty — no devices or routing were captured in the old format. "
                "Re-save after recreating your setup."
            ),
            "devices": [],
            "routing": [],
        }

    def load_all_profiles(self) -> Dict[str, Dict[str, Any]]:
        """Load all profiles, migrating v1 entries to v2 transparently."""
        raw = self._load_raw()
        migrated: Dict[str, Dict[str, Any]] = {}
        for name, entry in raw.items():
            if not isinstance(entry, dict):
                continue
            migrated[name] = self.migrate_entry(name, entry)
        return migrated

    def get_profile(self, name: str) -> Optional[Dict[str, Any]]:
        profiles = self.load_all_profiles()
        return profiles.get(name)

    def get_profile_names(self) -> List[str]:
        return list(self.load_all_profiles().keys())

    def save_profile(self, name: str, profile: Dict[str, Any]) -> bool:
        """Persist a profile under the given name. Returns False on IO error."""
        if not name or not _VALID_SINK_NAME.match(name):
            # Profile name is also used in the UI as a file/section label; keep
            # it conservative even though JSON keys allow more.
            raise ProfileError(
                f"Invalid profile name: {name!r}. Allowed: letters, digits, "
                "underscore, dot, dash."
            )
        # Make sure we save with schema_version set
        to_save = dict(profile)
        to_save.setdefault("schema_version", CURRENT_SCHEMA_VERSION)
        to_save.setdefault("created", datetime.now(timezone.utc).isoformat())

        all_profiles = self.load_all_profiles()
        all_profiles[name] = to_save
        return self._save_raw(all_profiles)

    def delete_profile(self, name: str) -> bool:
        profiles = self.load_all_profiles()
        if name not in profiles:
            return False
        del profiles[name]
        return self._save_raw(profiles)

    # ------------------------------------------------------------------
    # Capture (save current state)
    # ------------------------------------------------------------------

    @staticmethod
    def _device_type_from_media_class(media_class: str) -> str:
        """Map pactl media.class to our schema's 'type' field."""
        mc = (media_class or "").strip().strip('"')
        if mc == "Audio/Sink":
            return "sink"
        if mc == "Audio/Source":
            return "source"
        return "both"  # Audio/Duplex or anything else

    @staticmethod
    def _parse_sink_properties_string(s: str) -> Dict[str, str]:
        """Parse pactl's sink_properties=... string into a JSON-safe dict.

        `sink_properties` accepts key=value pairs separated by spaces. Values
        may be single- or double-quoted. We accept the most common form and
        fall back to a best-effort split on whitespace + '='.
        """
        if not s:
            return {}
        result: Dict[str, str] = {}
        # First try shlex (handles quoting)
        try:
            tokens = shlex.split(s)
        except ValueError:
            tokens = s.split()
        for tok in tokens:
            if "=" not in tok:
                continue
            k, _, v = tok.partition("=")
            # Strip surrounding quotes if any
            v = v.strip().strip('"').strip("'")
            result[k] = v
        return result

    def capture_topology(self, profile_name: str, description: str = "") -> Dict[str, Any]:
        """Build a v2 profile dict from the current audio topology.

        Walks `pactl list modules short` and pulls out every null-sink (with
        its media.class, channels, rate, format, channel_map, sink_properties)
        and every module-loopback that points at a virtual sink's monitor.

        Does NOT save — returns the dict so the caller can review/edit before
        calling `save_profile()`.

        Args:
            profile_name: The name to give this profile (also the dict key).
            description: Optional human-readable description.

        Returns:
            A v2 profile dict ready for save_profile().
        """
        modules = PactlRunner.list_modules_short()

        # Index null-sinks by sink_name for routing reference
        null_sink_modules = {}
        devices: List[Dict[str, Any]] = []
        for m in modules:
            if m["name"] != "module-null-sink":
                continue
            args = m["args"]
            sink_name = args.get("sink_name")
            if not sink_name:
                continue
            null_sink_modules[sink_name] = m

            device: Dict[str, Any] = {
                "name": sink_name,
                "type": self._device_type_from_media_class(args.get("media.class", "")),
                "channels": int(args["channels"]) if "channels" in args else 2,
            }
            # Optional fields — only include if set
            if "description" in args.get("properties", {}):
                device["description"] = args["properties"]["description"]
            if "rate" in args:
                try:
                    device["rate"] = int(args["rate"])
                except ValueError:
                    pass
            if "format" in args:
                device["format"] = args["format"]
            if "channel_map" in args:
                device["channel_map"] = args["channel_map"]
            sink_props_str = args.get("sink_properties")
            if sink_props_str:
                device["sink_properties"] = self._parse_sink_properties_string(
                    sink_props_str
                )
            devices.append(device)

        # Capture loopbacks — only include ones where the source is a sink we
        # captured (so we don't try to restore loopbacks of hardware sinks).
        routing: List[Dict[str, Any]] = []
        for m in modules:
            if m["name"] != "module-loopback":
                continue
            args = m["args"]
            source = args.get("source", "")
            sink = args.get("sink", "")
            # source ends in '.monitor' — strip to get the sink name
            if not source.endswith(".monitor"):
                continue
            sink_name = source[: -len(".monitor")]
            if sink_name not in null_sink_modules:
                # Loopback from a non-Pulsar virtual sink — skip
                continue
            entry: Dict[str, Any] = {"from": sink_name, "to": sink}
            if "latency_msec" in args:
                try:
                    entry["latency_msec"] = int(args["latency_msec"])
                except ValueError:
                    pass
            routing.append(entry)

        return {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "created": datetime.now(timezone.utc).isoformat(),
            "description": description,
            "devices": devices,
            "routing": routing,
        }

    # ------------------------------------------------------------------
    # Apply (restore from profile)
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_profile(profile: Dict[str, Any]) -> Tuple[List[str], List[str]]:
        """Return (errors, warnings) found in a profile dict.

        Validates:
        - schema_version is 2
        - devices list exists and is a list of dicts
        - each device has a 'name' and 'type'
        - routing is a list of {from, to} entries
        """
        errors: List[str] = []
        warnings: List[str] = []

        if profile.get("schema_version") != CURRENT_SCHEMA_VERSION:
            errors.append(
                f"Unsupported schema_version: {profile.get('schema_version')!r}. "
                f"Expected {CURRENT_SCHEMA_VERSION}."
            )

        devices = profile.get("devices")
        if not isinstance(devices, list):
            errors.append("'devices' must be a list")
            devices = []

        for i, d in enumerate(devices):
            if not isinstance(d, dict):
                errors.append(f"devices[{i}] must be a dict")
                continue
            name = d.get("name")
            dtype = d.get("type")
            if not name or not _VALID_SINK_NAME.match(str(name)):
                errors.append(f"devices[{i}].name is invalid: {name!r}")
            if dtype not in ("sink", "source", "both"):
                errors.append(
                    f"devices[{i}].type must be one of sink/source/both, got {dtype!r}"
                )

        routing = profile.get("routing")
        if routing is None:
            warnings.append("No 'routing' key — profile has no loopbacks")
        elif not isinstance(routing, list):
            errors.append("'routing' must be a list")
        else:
            for i, r in enumerate(routing):
                if not isinstance(r, dict):
                    errors.append(f"routing[{i}] must be a dict")
                    continue
                if not r.get("from") or not r.get("to"):
                    errors.append(f"routing[{i}] needs both 'from' and 'to'")

        return errors, warnings

    @staticmethod
    def _serialize_sink_properties(props: Optional[Dict[str, str]]) -> Optional[str]:
        """Convert a JSON dict back into pactl's sink_properties=... string.

        Uses single quotes inside, double quotes outside — verified as the
        only quoting style that doesn't break pactl's parser. See
        docs/PHASE3_INTROSPECTION_REPORT.md Q6b.
        """
        if not props:
            return None
        parts = []
        for k, v in props.items():
            # Escape any single quotes in the value
            v_escaped = str(v).replace("'", "'\\''")
            parts.append(f"{k}='{v_escaped}'")
        return " ".join(parts)

    def apply_profile(
        self,
        profile: Dict[str, Any],
        logger=None,
        unload_existing: bool = True,
    ) -> Dict[str, Any]:
        """Apply a v2 profile to the running audio system.

        Steps:
        1. Validate the profile.
        2. Optionally unload all existing null-sinks and loopbacks (clean slate).
        3. Pre-validate that all routing targets exist as sinks. If not,
           refuse to start (rollback would lose existing setup too).
        4. Create each device in order. Track module IDs.
        5. Create each loopback in order. Track module IDs.
        6. If anything fails mid-apply, unload the modules we created so far
           and report which step failed.

        Args:
            profile: A v2 profile dict (from get_profile() or capture_topology()).
            logger: Optional callback for per-step messages (used by UI for log panel).
            unload_existing: If True (default), unload all existing null-sinks and
                             loopbacks first. Set False to layer on top of existing.

        Returns:
            Dict with keys:
              - success: bool
              - created_devices: list of {name, module_id}
              - created_loopbacks: list of {from, to, module_id}
              - errors: list of human-readable error strings
              - rolled_back: bool — whether we unloaded partial state on failure
        """
        result: Dict[str, Any] = {
            "success": False,
            "created_devices": [],
            "created_loopbacks": [],
            "errors": [],
            "rolled_back": False,
        }

        errors, warnings = self._validate_profile(profile)
        if errors:
            result["errors"] = errors
            return result
        if logger:
            for w in warnings:
                logger(f"Profile warning: {w}")

        # Step 1: pre-validate routing targets. Hardware sinks must exist
        # BEFORE we touch anything (otherwise rollback would lose the user's
        # existing setup).
        for i, r in enumerate(profile.get("routing", [])):
            target = r.get("to", "")
            if not PactlRunner.sink_exists(target, logger):
                result["errors"].append(
                    f"routing[{i}].to references non-existent sink: {target!r}. "
                    "Hardware device not connected or renamed since profile was saved."
                )
                return result

        # Step 2: clean slate if requested
        if unload_existing:
            if logger:
                logger("Unloading existing null-sinks and loopbacks...")
            for lb in PactlRunner.list_loopbacks(logger):
                PactlRunner.unload_loopback(lb["id"], logger)
            unloaded, errors_unload = PactlRunner.unload_all_null_sinks(logger)
            if logger:
                logger(f"  Unloaded {unloaded} null-sinks")

        # Step 3: create devices
        for d in profile.get("devices", []):
            name = d["name"]
            dtype = d["type"]
            channels = int(d.get("channels", 2))
            kwargs: Dict[str, Any] = {}
            if "rate" in d:
                kwargs["rate"] = int(d["rate"])
            if "format" in d:
                kwargs["format"] = d["format"]
            if "channel_map" in d:
                kwargs["channel_map"] = d["channel_map"]
            sp = self._serialize_sink_properties(d.get("sink_properties"))
            if sp:
                kwargs["sink_properties"] = sp
            description = d.get("description", f"{name} Virtual Device")

            method = {
                "sink": PactlRunner.create_sink_only,
                "source": PactlRunner.create_source_only,
                "both": PactlRunner.create_duplex_sink,
            }.get(dtype, PactlRunner.create_duplex_sink)

            if logger:
                logger(f"  Creating device: {name} ({dtype})")
            ok = method(name, description, channels, logger=logger, **kwargs)
            if not ok:
                result["errors"].append(
                    f"Failed to create device {name!r} (type={dtype})"
                )
                self._rollback(result, logger)
                return result
            # Capture the new module ID for rollback / result tracking
            new_mod = next(
                (
                    m
                    for m in PactlRunner.list_modules_short(logger)
                    if m["name"] == "module-null-sink"
                    and m["args"].get("sink_name") == name
                ),
                None,
            )
            mod_id = new_mod["id"] if new_mod else None
            result["created_devices"].append({"name": name, "module_id": mod_id})

        # Step 4: create loopbacks
        for r in profile.get("routing", []):
            src = r["from"]
            tgt = r["to"]
            latency = int(r.get("latency_msec", 1))
            monitor = PactlRunner.monitor_source_for(src)
            if monitor is None:
                result["errors"].append(f"Cannot derive monitor for {src!r}")
                self._rollback(result, logger)
                return result
            if logger:
                logger(f"  Routing: {src} -> {tgt}")
            lb_id = PactlRunner.create_loopback(monitor, tgt, latency, logger=logger)
            if lb_id is None:
                result["errors"].append(
                    f"Failed to create loopback from {src!r} to {tgt!r}"
                )
                self._rollback(result, logger)
                return result
            result["created_loopbacks"].append(
                {"from": src, "to": tgt, "module_id": lb_id}
            )

        result["success"] = True
        if logger:
            logger(
                f"Profile applied: {len(result['created_devices'])} devices, "
                f"{len(result['created_loopbacks'])} loopbacks"
            )
        return result

    def _rollback(self, result: Dict[str, Any], logger=None) -> None:
        """Unload everything we created in this apply attempt."""
        if logger:
            logger("Rolling back partial apply...")
        for lb in result["created_loopbacks"]:
            mod_id = lb.get("module_id")
            if mod_id is not None:
                PactlRunner.unload_loopback(str(mod_id), logger)
        for d in result["created_devices"]:
            mod_id = d.get("module_id")
            if mod_id is not None:
                PactlRunner.unload_module(str(mod_id), logger)
        result["created_devices"] = []
        result["created_loopbacks"] = []
        result["rolled_back"] = True
