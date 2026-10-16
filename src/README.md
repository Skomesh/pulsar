# PulseAudio Control GUI - Source Code

This directory contains the source code for the PulseAudio Control GUI application.

## Directory Structure

```
src/
├── __init__.py                 # Package initialization
├── main.py                     # Application entry point
├── config/                     # Configuration management
│   └── __init__.py
├── ui/                         # UI components
│   ├── __init__.py
│   └── main_window.py          # Main application window implementation
└── utils/                      # Utility functions
    ├── __init__.py
    └── pactl_runner.py         # PulseAudio command execution and parsing
```

## Main Components

### main.py
Entry point for the application. Initializes the Tkinter root window and main application window.

### ui/main_window.py
Implements the main application window and all UI components:
- Tab-based interface (Create, Manage, Output)
- Menu system
- Status bar
- Event handling

### utils/pactl_runner.py
Handles interaction with PulseAudio through `pactl` commands:
- Running PulseAudio commands
- Parsing output from commands
- Managing audio devices and modules

## Running the Application

From the project root directory:
```bash
python3 -m pulsar
```

## Contributing

When adding new features:
1. Add utility functions to the appropriate modules
2. Update UI components in main_window.py
3. Document any changes or new features 

## Module boundaries

`utils/` holds logic that does not touch Tkinter and can therefore be unit
tested with no display and no audio server. `utils/device_names.py` in
particular holds the device-name parsing and classification behind
`MainWindow._extract_hardware_device_info_from_name`, which is a thin
delegate to its `classify_device_name()`.

`MainWindow._detect_device_type` is the *other* classifier, and as of this
commit it is still implemented in `ui/main_window.py` rather than `utils/`.
The two used to disagree: a `bluez_output.<MAC>.1` name was classified as
`builtin` by one and `bluetooth` by the other. Check which side you are
editing before assuming both delegate here.

Pure methods on a class that need no widget state can still be tested without
a display:

```python
import ui.main_window as mw
bare = object.__new__(mw.MainWindow)   # skips __init__, no root, no widgets
```

That snippet needs `src/` on the import path — either run under
`PYTHONPATH=src` or install the package first with `./install.sh` (which
installs it in editable mode, pointing at `src/`). Nothing in the module
imports Tk at call time, so a bare instance is enough to exercise the
methods.

Keep new parsing and classification logic in `utils/`, not in `ui/`. 