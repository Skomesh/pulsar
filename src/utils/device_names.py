"""Pure parsing helpers for PipeWire audio device names.

Split out of ``ui.main_window.MainWindow`` so the logic can be unit-tested
without a display, an audio server, or a constructed Tk object. Nothing in
this module may import tkinter or touch ``self``.

PipeWire names look like::

    alsa_output.usb-BOSS_GCS-8-01.pro-output-0
    alsa_input.pci-0000_00_1f.3.analog-stereo
    alsa_output.pci-0000_01_00.1.hdmi-stereo
    bluez_output.AC_12_2F_9B_00_11.1
    bluez_input.AC_12_2F_9B_00_11.1
"""

from typing import Any, Dict, Optional

#: Substrings in ``device.description`` that indicate the PCI audio device is
#: actually a GPU's HDMI output rather than onboard analog audio.
GPU_HINTS = ("nvidia", "amd", "radeon", "intel hd")


def classify_device_name(
    device_name: str, device_data: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Classify a PipeWire device name into a device_info dict.

    Returns ``None`` for an empty name. The returned dict always has the keys
    ``device_key``, ``device_type``, ``device_name``, ``device_identifier``,
    ``connection_part`` and ``properties``.

    ``device_type`` is one of ``usb``, ``pci``-derived (``hdmi`` or
    ``builtin``), ``bluetooth``, or ``builtin`` as the fallback.
    """
    if not device_name:
        return None

    properties = device_data.get("properties", {}) or {}

    # Bluetooth: PipeWire names these bluez_output.* / bluez_input.*. The MAC
    # address is the second dot-separated field. This must be checked BEFORE
    # the alsa_ branch -- bluez names never match alsa_, and the old code had
    # the bluez branch nested inside the alsa_ block where it was dead.
    if device_name.startswith("bluez_"):
        identifier = device_name.split(".")[1] if "." in device_name else device_name
        return _build(
            device_type="bluetooth",
            device_identifier=identifier,
            connection_part=identifier,
            device_name=properties.get("device.description", "Bluetooth Audio"),
            properties=properties,
        )

    if device_name.startswith("alsa_"):
        parts = device_name.split(".")
        if len(parts) >= 2:
            connection_part = parts[1]

            if connection_part.startswith("usb-"):
                usb_part = connection_part[4:]
                identifier = usb_part.rsplit("-", 1)[0]
                clean = identifier.replace("_", " ").replace("-", " ")
                if " " in clean:
                    brand, model = clean.split(" ", 1)
                    display_name = f"{brand} {model}".strip()
                else:
                    display_name = clean
                return _build(
                    device_type="usb",
                    device_identifier=identifier,
                    connection_part=connection_part,
                    device_name=display_name,
                    properties=properties,
                )

            if connection_part.startswith("pci-"):
                description = properties.get("device.description", "")
                is_gpu = any(hint in description.lower() for hint in GPU_HINTS)
                return _build(
                    device_type="hdmi" if is_gpu else "builtin",
                    device_identifier=connection_part,
                    connection_part=connection_part,
                    device_name=description or ("GPU Audio" if is_gpu else "Built-in Audio"),
                    properties=properties,
                )

            # Unknown ALSA connection type.
            return _build(
                device_type="builtin",
                device_identifier=connection_part,
                connection_part=connection_part,
                device_name=device_data.get("description", device_name),
                properties=properties,
            )

    # Non-ALSA, non-bluez: no name pattern to go on.
    return _build(
        device_type="builtin",
        device_identifier=device_name,
        connection_part=device_name,
        device_name=device_data.get("description", device_name),
        properties=properties,
    )


def _build(
    device_type: str,
    device_identifier: str,
    connection_part: str,
    device_name: str,
    properties: Dict[str, Any],
) -> Dict[str, Any]:
    """Assemble the device_info dict shared by every branch."""
    return {
        "device_key": f"{device_type}_{device_identifier}",
        "device_type": device_type,
        "device_name": device_name,
        "device_identifier": device_identifier,
        "connection_part": connection_part,
        "properties": properties,
    }
