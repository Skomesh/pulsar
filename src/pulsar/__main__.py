#!/usr/bin/env python3
"""
Pulsar - graphical audio routing manager for PulseAudio/PipeWire.
Main application entry point.
"""

import shutil
import sys
import tkinter as tk

from pulsar.ui.main_window import MainWindow


def main():
    """Main application entry point."""
    # Check if pactl is available. Use shutil.which rather than os.system:
    # a shell spawn per launch is slow and the "which" output was discarded.
    if shutil.which("pactl") is None:
        print("Error: PulseAudio command-line utility (pactl) not found!")
        print("Please install PulseAudio utilities package:")
        print("  Debian/Ubuntu: sudo apt-get install pulseaudio-utils")
        print("  Fedora: sudo dnf install pulseaudio-utils")
        print("  openSUSE: sudo zypper install pulseaudio-utils")
        sys.exit(1)

    # Create the main window
    root = tk.Tk()
    MainWindow(root)

    # Start the main application loop
    root.mainloop()


if __name__ == "__main__":
    main()
