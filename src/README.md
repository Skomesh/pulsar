# Pulsar — source code

The application source. Layout:

```
src/pulsar/
├── __init__.py
├── __main__.py                # GUI entry point (`pulsar-gui`)
├── cli.py                     # command-line interface (`pulsar`)
├── config/
│   └── __init__.py
├── ui/
│   ├── __init__.py
│   └── main_window.py         # the Tkinter application
└── utils/                     # no Tkinter in here, ever
    ├── device_names.py        # device-name parsing and classification
    ├── pactl_runner.py        # the PulseAudio/PipeWire command layer
    ├── paths.py               # XDG paths
    ├── preset_manager.py      # saved presets
    ├── profile_manager.py     # saved routing profiles
    ├── pw_app_capture.py      # app-audio stream discovery (pw-dump)
    ├── pw_app_route.py        # per-app routing (native PipeWire links)
    └── pw_diagnostics.py      # Diagnostics tab graph snapshot
```

## The rule that shapes the layout

**`utils/` must not import Tkinter.** That is what makes it testable
with no display and no audio server, which is where nearly all the
logic lives. `ui/main_window.py` holds the widgets and the event
handlers and delegates.

So: new parsing and classification goes in `utils/`, not in `ui/`.

The two device classifiers are a known trap:

- `utils/device_names.py` holds the name parsing behind
  `MainWindow._extract_hardware_device_info_from_name`, which is a
  thin delegate to `classify_device_name()`.
- `MainWindow._detect_device_type` is the *other* classifier, and it
  is still implemented in `ui/main_window.py` rather than `utils/`.

The two used to disagree: a `bluez_output.<MAC>.1` name was
classified as `builtin` by one and `bluetooth` by the other. Check
which side you are editing before assuming both delegate to
`utils/`.

## Testing a UI method without a display

Pure methods on a class that need no widget state can still be tested
without a display:

```python
import ui.main_window as mw
bare = object.__new__(mw.MainWindow)   # skips __init__, no root, no widgets
```

That snippet needs `src/` on the import path — either run under
`PYTHONPATH=src` or install the package first with `./install.sh`
(which installs it in editable mode, pointing at `src/`). Nothing in
the module imports Tk at call time, so a bare instance is enough to
exercise the methods.

Two things that bite when testing with real Tk widgets instead:

- Tk does not deliver `<<ListboxSelect>>` to an **unmapped** widget,
  and a programmatic `selection_set()` does not fire it at all.
- A modal `messagebox` **blocks forever** under Xvfb.

Both are covered in [AGENTS.md](../AGENTS.md), along with the coding
policy — validation at the boundary, error handling, and the traps
specific to driving an audio server.

## Running the application

From the project root directory:

```bash
python3 -m pulsar        # the GUI
pulsar                   # the CLI, once installed
```
