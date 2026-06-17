"""
PulseAudio command execution and parsing utilities.
"""

import re
import subprocess
from typing import Any, Dict, List, Optional, Tuple


class PactlRunner:
    """
    A class to execute PulseAudio commands and parse their output.
    """

    @staticmethod
    def run_command(command: List[str], logger=None) -> Tuple[str, int]:
        """
        Run a pactl command and return its output.

        Args:
            command: A list of command components (e.g., ['list', 'sinks'])
            logger: Optional callback function to log command execution

        Returns:
            A tuple containing (output_string, return_code)
        """
        full_command = ['pactl'] + command
        command_str = ' '.join(full_command)

        # Log the command being executed
        if logger:
            logger(f"$ {command_str}")

        try:
            result = subprocess.run(
                full_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False
            )

            # Log the result
            if logger:
                if result.returncode == 0:
                    if result.stdout.strip():
                        # Only log output for commands that produce meaningful output
                        if any(cmd in command_str for cmd in ['list', 'info']):
                            logger("Command completed successfully (output truncated for readability)")
                        else:
                            logger("Command completed successfully")
                            if result.stdout.strip():
                                logger(f"Output: {result.stdout.strip()}")
                    else:
                        logger("Command completed successfully")
                else:
                    logger(f"Command failed (exit code {result.returncode})")
                    if result.stdout.strip():
                        logger(f"Error: {result.stdout.strip()}")

            return result.stdout, result.returncode
        except Exception as e:
            error_msg = str(e)
            if logger:
                logger(f"Command execution failed: {error_msg}")
            return error_msg, 1

    @staticmethod
    def list_sinks(logger=None) -> List[Dict[str, Any]]:
        """
        Get a comprehensive list of all audio sinks (outputs) with full specifications.

        Args:
            logger: Optional callback function to log command execution

        Returns:
            A list of dictionaries containing complete sink information
        """
        output, return_code = PactlRunner.run_command(['list', 'sinks'], logger)
        if return_code != 0:
            return []

        sinks = []
        current_sink = None
        current_section = None

        for line in output.splitlines():
            line_stripped = line.strip()

            if line.startswith('Sink #'):
                # Save previous sink and start new one
                if current_sink:
                    sinks.append(current_sink)
                sink_id = line.split('#')[1].strip()
                current_sink = {'id': sink_id, 'properties': {}}
                current_section = None

            elif current_sink:
                if line_stripped.startswith('Properties:'):
                    current_section = 'properties'
                elif line_stripped.startswith('Formats:'):
                    current_section = 'formats'
                    current_sink['formats'] = []
                elif current_section == 'properties' and '=' in line_stripped:
                    # Parse property line: key = "value"
                    parts = line_stripped.split(' = ', 1)
                    if len(parts) == 2:
                        key = parts[0].strip()
                        value = parts[1].strip().strip('"')
                        current_sink['properties'][key] = value
                elif current_section == 'formats' and line_stripped:
                    current_sink['formats'].append(line_stripped)
                elif ':' in line and current_section not in ['properties', 'formats']:
                    # Parse regular field: Key: Value
                    key, value = line.split(':', 1)
                    key = key.strip()
                    value = value.strip()

                    # Map to standardized field names
                    field_map = {
                        'State': 'state',
                        'Name': 'name',
                        'Description': 'description',
                        'Driver': 'driver',
                        'Sample Specification': 'sample_spec',
                        'Channel Map': 'channel_map',
                        'Owner Module': 'owner_module',
                        'Mute': 'mute',
                        'Volume': 'volume',
                        'Base Volume': 'base_volume',
                        'Monitor Source': 'monitor_source',
                        'Latency': 'latency',
                        'Flags': 'flags'
                    }

                    field_name = field_map.get(key, key.lower().replace(' ', '_'))
                    current_sink[field_name] = value

        if current_sink:
            sinks.append(current_sink)

        return sinks

    @staticmethod
    def list_sources(logger=None) -> List[Dict[str, Any]]:
        """
        Get a comprehensive list of all audio sources (inputs) with full specifications.

        Args:
            logger: Optional callback function to log command execution

        Returns:
            A list of dictionaries containing complete source information
        """
        output, return_code = PactlRunner.run_command(['list', 'sources'], logger)
        if return_code != 0:
            return []

        sources = []
        current_source = None
        current_section = None

        for line in output.splitlines():
            line_stripped = line.strip()

            if line.startswith('Source #'):
                # Save previous source and start new one
                if current_source:
                    sources.append(current_source)
                source_id = line.split('#')[1].strip()
                current_source = {'id': source_id, 'properties': {}}
                current_section = None

            elif current_source:
                if line_stripped.startswith('Properties:'):
                    current_section = 'properties'
                elif line_stripped.startswith('Formats:'):
                    current_section = 'formats'
                    current_source['formats'] = []
                elif current_section == 'properties' and '=' in line_stripped:
                    # Parse property line: key = "value"
                    parts = line_stripped.split(' = ', 1)
                    if len(parts) == 2:
                        key = parts[0].strip()
                        value = parts[1].strip().strip('"')
                        current_source['properties'][key] = value
                elif current_section == 'formats' and line_stripped:
                    current_source['formats'].append(line_stripped)
                elif ':' in line and current_section not in ['properties', 'formats']:
                    # Parse regular field: Key: Value
                    key, value = line.split(':', 1)
                    key = key.strip()
                    value = value.strip()

                    # Map to standardized field names
                    field_map = {
                        'State': 'state',
                        'Name': 'name',
                        'Description': 'description',
                        'Driver': 'driver',
                        'Sample Specification': 'sample_spec',
                        'Channel Map': 'channel_map',
                        'Owner Module': 'owner_module',
                        'Mute': 'mute',
                        'Volume': 'volume',
                        'Base Volume': 'base_volume',
                        'Monitor of Sink': 'monitor_of_sink',
                        'Latency': 'latency',
                        'Flags': 'flags'
                    }

                    field_name = field_map.get(key, key.lower().replace(' ', '_'))
                    current_source[field_name] = value

        if current_source:
            sources.append(current_source)

        return sources

    @staticmethod
    def list_modules(logger=None) -> List[Dict[str, Any]]:
        """
        Get a comprehensive list of all loaded PulseAudio modules with full specifications.

        Args:
            logger: Optional callback function to log command execution

        Returns:
            A list of dictionaries containing complete module information
        """
        output, return_code = PactlRunner.run_command(['list', 'modules'], logger)
        if return_code != 0:
            return []

        modules = []
        current_module = None
        current_section = None

        for line in output.splitlines():
            line_stripped = line.strip()

            if line.startswith('Module #'):
                # Save previous module and start new one
                if current_module:
                    modules.append(current_module)
                module_id = line.split('#')[1].strip()
                current_module = {'id': module_id, 'properties': {}}
                current_section = None

            elif current_module:
                if line_stripped.startswith('Properties:'):
                    current_section = 'properties'
                elif current_section == 'properties' and '=' in line_stripped:
                    # Parse property line: key = "value"
                    parts = line_stripped.split(' = ', 1)
                    if len(parts) == 2:
                        key = parts[0].strip()
                        value = parts[1].strip().strip('"')
                        current_module['properties'][key] = value
                elif line_stripped.startswith('Name: '):
                    current_module['name'] = line_stripped[6:].strip()
                elif line_stripped.startswith('Argument: '):
                    # Handle multi-line arguments
                    arg_content = line_stripped[10:].strip()
                    if arg_content.startswith('{'):
                        # Multi-line argument block
                        current_section = 'argument'
                        current_module['argument'] = arg_content
                    else:
                        # Single line argument
                        current_module['argument'] = arg_content
                elif current_section == 'argument':
                    # Continue collecting multi-line argument
                    if 'argument' not in current_module:
                        current_module['argument'] = ''
                    current_module['argument'] += '\n' + line
                    if line_stripped.endswith('}'):
                        current_section = None
                elif line_stripped.startswith('Usage counter: '):
                    current_module['usage_counter'] = line_stripped[15:].strip()
                    current_section = None  # Reset section after usage counter

        if current_module:
            modules.append(current_module)

        return modules

    @staticmethod
    def unload_module(module_id: str, logger=None) -> bool:
        """
        Unload a PulseAudio module by ID.

        Args:
            module_id: The numeric ID of the module to unload
            logger: Optional callback function to log command execution

        Returns:
            True if successful, False otherwise
        """
        output, return_code = PactlRunner.run_command(['unload-module', module_id], logger)
        return return_code == 0

    @staticmethod
    def create_duplex_sink(
        name: str,
        description: str,
        channels: int = 2,
        rate: Optional[int] = None,
        format: Optional[str] = None,
        channel_map: Optional[str] = None,
        sink_properties: Optional[str] = None,
        logger=None
    ) -> bool:
        """
        Create a duplex null sink with the given parameters.

        Args:
            name: The name for the sink (no spaces, used as identifier)
            description: Human-readable description (not used in the command directly)
            channels: Number of channels (1=mono, 2=stereo, etc.)
            rate: Sample rate in Hz (optional, defaults to system default)
            format: Sample format (optional, defaults to system default)
            channel_map: Channel mapping (optional, defaults to system default)
            sink_properties: Additional sink properties (optional)
            logger: Optional callback function to log command execution

        Returns:
            True if successful, False otherwise
        """
        return PactlRunner._create_null_sink(
            media_class='Audio/Duplex',
            name=name,
            description=description,
            channels=channels,
            rate=rate,
            format=format,
            channel_map=channel_map,
            sink_properties=sink_properties,
            logger=logger,
        )

    @staticmethod
    def create_sink_only(
        name: str,
        description: str,
        channels: int = 2,
        rate: Optional[int] = None,
        format: Optional[str] = None,
        channel_map: Optional[str] = None,
        sink_properties: Optional[str] = None,
        logger=None
    ) -> bool:
        """
        Create a sink-only null sink (no source role).

        Apps can play audio to this device but cannot record from it
        (the .monitor is not exposed as a recordable source for normal apps).

        Use this when you want a routing target that goes OUT to speakers
        or another sink — e.g. a virtual "game_sink" you wire to your
        headphones via module-loopback.

        Args:
            Same as create_duplex_sink.
        """
        return PactlRunner._create_null_sink(
            media_class='Audio/Sink',
            name=name,
            description=description,
            channels=channels,
            rate=rate,
            format=format,
            channel_map=channel_map,
            sink_properties=sink_properties,
            logger=logger,
        )

    @staticmethod
    def create_source_only(
        name: str,
        description: str,
        channels: int = 2,
        rate: Optional[int] = None,
        format: Optional[str] = None,
        channel_map: Optional[str] = None,
        sink_properties: Optional[str] = None,
        logger=None
    ) -> bool:
        """
        Create a source-only null sink (no sink role).

        Apps can record from this device but cannot play audio to it.
        Useful as a capture target for streaming/recording pipelines
        (e.g. a virtual "mic_source" that apps can pick as their mic
        while the actual audio is being fed in from elsewhere).

        Args:
            Same as create_duplex_sink.
        """
        return PactlRunner._create_null_sink(
            media_class='Audio/Source',
            name=name,
            description=description,
            channels=channels,
            rate=rate,
            format=format,
            channel_map=channel_map,
            sink_properties=sink_properties,
            logger=logger,
        )

    @staticmethod
    def _create_null_sink(
        media_class: str,
        name: str,
        description: str,
        channels: int = 2,
        rate: Optional[int] = None,
        format: Optional[str] = None,
        channel_map: Optional[str] = None,
        sink_properties: Optional[str] = None,
        logger=None
    ) -> bool:
        """
        Internal helper: build and run a module-null-sink load-module command.

        Args:
            media_class: One of 'Audio/Sink', 'Audio/Source', or 'Audio/Duplex'.
            name: Sink identifier.
            description: Human-readable description (unused in command, kept for API compat).
            channels: Number of channels.
            rate: Sample rate in Hz (optional).
            format: Sample format (optional).
            channel_map: Channel mapping (optional).
            sink_properties: Additional sink properties (optional).
            logger: Optional logger callback.

        Returns:
            True if the module loaded successfully (return code 0).
        """
        # Build the command arguments
        cmd_args = [
            'load-module',
            'module-null-sink',
            f'media.class={media_class}',
            f'sink_name={name}',
            f'channels={channels}'
        ]

        # Add advanced options if specified
        if rate is not None:
            cmd_args.append(f'rate={rate}')

        if format is not None:
            cmd_args.append(f'format={format}')

        if channel_map is not None:
            cmd_args.append(f'channel_map={channel_map}')

        if sink_properties is not None:
            cmd_args.append(f'sink_properties={sink_properties}')

        output, return_code = PactlRunner.run_command(cmd_args, logger)

        return return_code == 0

    @staticmethod
    def unload_all_null_sinks(logger=None) -> Tuple[int, List[str]]:
        """
        Unload all null sink modules.

        Args:
            logger: Optional callback function to log command execution

        Returns:
            A tuple containing (number_of_modules_unloaded, list_of_errors)
        """
        modules = PactlRunner.list_modules(logger)
        null_sink_modules = [m for m in modules if m.get('name') == 'module-null-sink']

        successful = 0
        errors = []

        for module in null_sink_modules:
            module_id = module.get('id', '')
            if module_id:
                success = PactlRunner.unload_module(module_id, logger)
                if success:
                    successful += 1
                else:
                    errors.append(f"Failed to unload module #{module_id}")

        return successful, errors

    @staticmethod
    def create_loopback(
        source_monitor: str,
        sink: str,
        latency_msec: int = 1,
        logger=None,
    ) -> Optional[str]:
        """
        Create a module-loopback that forwards audio from a source's monitor to a sink.

        In PulseAudio/PipeWire, you "loopback a virtual sink to a real output" by
        creating a module-loopback whose source is the virtual sink's monitor
        (e.g. `game_sink.monitor`) and whose sink is the real output (e.g.
        `alsa_output.pci-...analog-stereo`).

        The loopback is pinned to its endpoints via `sink_dont_move=true` and
        `source_dont_move=true`. Without these, WirePlumber may re-route the
        loopback when the user changes the default sink in the system tray.
        See docs/PHASE3_INTROSPECTION_REPORT.md for details.

        Args:
            source_monitor: Full monitor source name (sink_name + '.monitor').
                             Example: 'game_sink.monitor'
            sink: Target sink name (real output device).
                  Example: 'alsa_output.pci-0000_00_1f.3.analog-stereo'
            latency_msec: Loopback latency in milliseconds. 1ms is fine for desktop
                          use; raise it for heavier DSP loads.
            logger: Optional callback function to log command execution.

        Returns:
            The loaded module ID as a string if successful, None otherwise.
        """
        cmd_args = [
            'load-module',
            'module-loopback',
            f'source={source_monitor}',
            f'sink={sink}',
            f'latency_msec={latency_msec}',
            'sink_dont_move=true',
            'source_dont_move=true',
        ]
        output, return_code = PactlRunner.run_command(cmd_args, logger)
        if return_code != 0:
            return None
        # pactl returns the module ID as a plain integer on stdout
        return output.strip() or None

    @staticmethod
    def list_loopbacks(logger=None) -> List[Dict[str, str]]:
        """
        Return all currently loaded module-loopback instances.

        Each entry is a dict with keys:
            - 'id': module ID as string
            - 'source': source name (typically ends in '.monitor')
            - 'sink': target sink name

        Args:
            logger: Optional callback function to log command execution.

        Returns:
            List of loopback module dicts. Empty list if none.
        """
        modules = PactlRunner.list_modules(logger)
        loopbacks = []
        for m in modules:
            if m.get('name') != 'module-loopback':
                continue
            arg = m.get('argument', '')
            # Argument looks like: source=X.monitor sink=Y latency_msec=1
            entry: Dict[str, str] = {'id': m.get('id', '')}
            for token in arg.split():
                if '=' in token:
                    key, _, value = token.partition('=')
                    if key in ('source', 'sink'):
                        entry[key] = value
            loopbacks.append(entry)
        return loopbacks

    @staticmethod
    def unload_loopback(module_id: str, logger=None) -> bool:
        """
        Unload a module-loopback by ID.

        Args:
            module_id: The numeric module ID returned by create_loopback().
            logger: Optional callback function to log command execution.

        Returns:
            True if successful, False otherwise.
        """
        return PactlRunner.unload_module(module_id, logger)

    @staticmethod
    def is_null_sink(sink_name: str, logger=None) -> bool:
        """
        Return True if the given sink name was created by module-null-sink
        (i.e. is a virtual device, not real hardware).

        Heuristic: query all loaded modules and check whether any
        module-null-sink has an argument containing `sink_name=<this_name>`.

        Args:
            sink_name: The sink's `Name` field from `pactl list sinks`.
            logger: Optional callback function to log command execution.

        Returns:
            True if this is a null-sink (virtual), False if it's a real device
            or unknown.
        """
        modules = PactlRunner.list_modules(logger)
        marker = f'sink_name={sink_name}'
        for m in modules:
            if m.get('name') == 'module-null-sink' and marker in m.get('argument', ''):
                return True
        return False

    @staticmethod
    def monitor_source_for(sink_name: str, logger=None) -> Optional[str]:
        """
        Return the monitor source name for a given sink.

        In PulseAudio/PipeWire, every sink automatically has a `<sink_name>.monitor`
        source that captures the audio being played to it. This is the source you
        pass to `create_loopback` to wire the sink to a real output.

        Args:
            sink_name: The sink's `Name` field from `pactl list sinks`.
            logger: Optional callback function to log command execution (unused,
                    kept for API symmetry).

        Returns:
            `f"{sink_name}.monitor"` always, since this is a PulseAudio/PipeWire
            convention. Returns None if sink_name is empty.
        """
        if not sink_name:
            return None
        return f"{sink_name}.monitor"

    @staticmethod
    def list_hardware_outputs(logger=None) -> List[str]:
        """
        Return the names of real (non-null-sink) output sinks suitable for routing.

        These are the sinks a user would pick from a "Route to" dropdown.
        A sink is considered "available hardware" if:
          - It was NOT created by module-null-sink (so it's a real device,
            not a virtual sink we built for routing)
          - It is in RUNNING or SUSPENDED state (SUSPENDED = idle but still
            routable, the device is just sleeping; UNAVAILABLE would mean
            physically disconnected, but in practice PA/PW only emit
            RUNNING and SUSPENDED for connected sinks)
          - It has a monitor_source (some PW filters don't, and can't
            be routed to via a loopback)

        Args:
            logger: Optional callback function to log command execution.

        Returns:
            List of sink names suitable as routing targets.
        """
        sinks = PactlRunner.list_sinks(logger)
        hardware = []
        for s in sinks:
            name = s.get('name', '')
            if not name:
                continue
            # Exclude null-sinks (virtual devices we created for routing)
            if PactlRunner.is_null_sink(name, logger):
                continue
            # Exclude sinks without a monitor_source (can't be loopback targets)
            if not s.get('monitor_source'):
                continue
            # SUSPENDED is fine — it just means the sink is idle. The user
            # can still route audio to it and it will wake up.
            hardware.append(name)
        return hardware

    @staticmethod
    def parse_pactl_module_args(args_str: str) -> Dict[str, str]:
        """
        Parse a pactl module argument string into a dict.

        Handles the common case where `sink_properties=key1=val1 key2=val2 ...`
        captures the rest of the string as one value. For all other args, splits
        on `=` and takes the first `=` as the separator.

        Args:
            args_str: The argument string from `pactl list modules short` field 3.

        Returns:
            Dict mapping arg name to arg value. `sink_properties` (if present)
            captures the rest of the string verbatim — callers should not try
            to parse it further without single-quote-aware tooling.
        """
        result: Dict[str, str] = {}
        tokens = args_str.split()
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if '=' not in tok:
                i += 1
                continue
            key, _, value = tok.partition('=')
            if key == 'sink_properties':
                # Capture everything after sink_properties= as one value
                # (the rest of the tokens are the proplist).
                # First token's value is whatever came after the first '='.
                # We need to reconstruct: drop the 'sink_properties=' prefix
                # and re-join everything from here.
                value = args_str.split('sink_properties=', 1)[1]
                result[key] = value
                return result  # sink_properties always ends the args list
            else:
                result[key] = value
            i += 1
        return result

    @staticmethod
    def list_modules_short(logger=None) -> List[Dict[str, Any]]:
        """
        Return all loaded pactl modules with parsed args.

        Unlike list_modules() (which returns the verbose block format),
        this returns a flat list of {id, name, args, raw_args} from
        `pactl list modules short` — the format used for round-tripping
        profiles.

        Args:
            logger: Optional callback function to log command execution.

        Returns:
            List of {id: int, name: str, args: dict, raw_args: str}.
        """
        output, return_code = PactlRunner.run_command(
            ['list', 'modules', 'short'], logger
        )
        if return_code != 0:
            return []
        modules = []
        for line in output.splitlines():
            parts = line.split('\t', 2)
            if len(parts) != 3:
                continue
            mod_id_str, mod_name, args_str = parts
            try:
                mod_id = int(mod_id_str)
            except ValueError:
                continue
            modules.append({
                'id': mod_id,
                'name': mod_name,
                'args': PactlRunner.parse_pactl_module_args(args_str),
                'raw_args': args_str,
            })
        return modules

    @staticmethod
    def sink_exists(sink_name: str, logger=None) -> bool:
        """Return True if a sink with this exact name is currently registered."""
        if not sink_name:
            return False
        sinks = PactlRunner.list_sinks(logger)
        return any(s.get('name') == sink_name for s in sinks)

    @staticmethod
    def get_default_sink(logger=None) -> Optional[str]:
        """Return the name of the system's current default sink.

        Reads ``pactl info`` and parses the ``Default Sink: <name>`` line.
        Used by ProfileManager.apply_profile to resolve the
        ``<AUTO_DEFAULT>`` sentinel in starter profiles — hardware output
        names are environment-specific (USB headsets, ALSA HDA, etc.), so
        shippable profiles can't hard-code them.

        Args:
            logger: Optional logger callback for command tracing.

        Returns:
            The default sink name as a string, or None if it can't be
            determined (e.g. pactl not running, no default set, or
            ``pactl info`` returned no matching line).
        """
        output, return_code = PactlRunner.run_command(['info'], logger)
        if return_code != 0:
            return None
        for line in output.splitlines():
            # Both PulseAudio and PipeWire's compat shim emit the same format.
            line_stripped = line.strip()
            if line_stripped.startswith("Default Sink:"):
                _, _, value = line_stripped.partition(":")
                value = value.strip()
                return value or None
        return None

    @staticmethod
    def get_default_source(logger=None) -> Optional[str]:
        """Return the name of the system's current default source.

        Mirror of get_default_sink for input devices.
        """
        output, return_code = PactlRunner.run_command(['info'], logger)
        if return_code != 0:
            return None
        for line in output.splitlines():
            line_stripped = line.strip()
            if line_stripped.startswith("Default Source:"):
                _, _, value = line_stripped.partition(":")
                value = value.strip()
                return value or None
        return None

    @staticmethod
    def set_default_sink(sink_name: str, logger=None) -> bool:
        """Set the system default sink by name. Returns True on success."""
        _, return_code = PactlRunner.run_command(
            ['set-default-sink', sink_name], logger
        )
        return return_code == 0

    @staticmethod
    def set_default_source(source_name: str, logger=None) -> bool:
        """Set the system default source by name. Returns True on success."""
        _, return_code = PactlRunner.run_command(
            ['set-default-source', source_name], logger
        )
        return return_code == 0

    @staticmethod
    def _parse_volume_percent(output: str) -> Optional[int]:
        """Parse a `pactl get-sink-volume` / `get-source-volume` line.

        Format: ``Volume: front-left: 65536 / 100% / 0.00 dB,   front-right: ...``
        We take the first ``\\d+%`` match.

        Returns:
            Integer percent 0-100+ (values over 100% are possible), or None
            if the output doesn't match the expected format.
        """
        if not output:
            return None
        match = re.search(r"(\d+)\s*%", output)
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    @staticmethod
    def get_sink_volume(sink_name: str, logger=None) -> Optional[int]:
        """Return the volume of a sink as a percent (0-100+, can be over 100).

        Returns None if pactl fails or the sink doesn't exist.
        """
        output, return_code = PactlRunner.run_command(
            ['get-sink-volume', sink_name], logger
        )
        if return_code != 0:
            return None
        return PactlRunner._parse_volume_percent(output)

    @staticmethod
    def get_source_volume(source_name: str, logger=None) -> Optional[int]:
        """Return the volume of a source as a percent (0-100+)."""
        output, return_code = PactlRunner.run_command(
            ['get-source-volume', source_name], logger
        )
        if return_code != 0:
            return None
        return PactlRunner._parse_volume_percent(output)

    @staticmethod
    def set_sink_volume(sink_name: str, percent: int, logger=None) -> bool:
        """Set the volume of a sink by percent (0-150 typical, can be higher).

        Returns True on success. pactl accepts values like '50%', '0.5', or
        linear '32768'. We always send the percent form for readability.
        """
        clamped = max(0, int(percent))
        _, return_code = PactlRunner.run_command(
            ['set-sink-volume', sink_name, f"{clamped}%"], logger
        )
        return return_code == 0

    @staticmethod
    def set_source_volume(source_name: str, percent: int, logger=None) -> bool:
        """Set the volume of a source by percent. Returns True on success."""
        clamped = max(0, int(percent))
        _, return_code = PactlRunner.run_command(
            ['set-source-volume', source_name, f"{clamped}%"], logger
        )
        return return_code == 0

    @staticmethod
    def set_sink_mute(sink_name: str, muted: bool, logger=None) -> bool:
        """Mute or unmute a sink. Returns True on success."""
        toggle = "1" if muted else "0"
        _, return_code = PactlRunner.run_command(
            ['set-sink-mute', sink_name, toggle], logger
        )
        return return_code == 0

    @staticmethod
    def get_sink_mute(sink_name: str, logger=None) -> Optional[bool]:
        """Return True/False if the sink is muted, or None on error."""
        output, return_code = PactlRunner.run_command(
            ['get-sink-mute', sink_name], logger
        )
        if return_code != 0:
            return None
        # Format: "Mute: yes" or "Mute: no"
        line = output.strip().lower()
        if line.startswith("mute: yes"):
            return True
        if line.startswith("mute: no"):
            return False
        return None
