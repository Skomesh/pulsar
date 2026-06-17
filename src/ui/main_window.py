"""
Main application window for the pactl-gui application.
"""

import json
import os
import re

# Importing our utility modules
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.pactl_runner import PactlRunner
from utils.preset_manager import PresetManager
from utils.profile_manager import ProfileManager
from utils.pw_app_capture import (
    discover_app_audio_nodes,
)
from utils.pw_diagnostics import (
    get_graph_snapshot,
    health_summary,
    pw_dump_available,
    sample_rate_audit,
)


class MainWindow:
    """Main application window for pactl-gui."""

    def __init__(self, root: tk.Tk):
        """
        Initialize the main window.

        Args:
            root: The root Tkinter window
        """
        self.root = root
        self.root.title("PulseAudio Control GUI")
        self.root.geometry("800x600")
        self.root.minsize(700, 500)

        # Initialize preset manager
        self.preset_manager = PresetManager()

        # Initialize profile manager (Phase 3: persist full routing topologies)
        self.profile_manager = ProfileManager()

        # Output text for command results (will be initialized in setup_output_tab)
        self.output_text = None

        # Status bar variables
        self.status_var = tk.StringVar()
        self.status_var.set("Ready")

        # Set up the menu
        self.setup_menu()

        # Create tab control
        self.tab_control = ttk.Notebook(root)

        # Create tabs
        self.create_tab = ttk.Frame(self.tab_control)
        self.manage_tab = ttk.Frame(self.tab_control)
        self.profiles_tab = ttk.Frame(self.tab_control)
        self.capture_tab = ttk.Frame(self.tab_control)
        self.diagnostics_tab = ttk.Frame(self.tab_control)
        self.output_tab = ttk.Frame(self.tab_control)

        # Add tabs to notebook
        self.tab_control.add(self.create_tab, text="Create")
        self.tab_control.add(self.manage_tab, text="Manage")
        self.tab_control.add(self.profiles_tab, text="Profiles")
        self.tab_control.add(self.capture_tab, text="App Audio")
        self.tab_control.add(self.diagnostics_tab, text="Diagnostics")
        self.tab_control.add(self.output_tab, text="Output")

        # Bind tab change event to reset form state
        self.tab_control.bind("<<NotebookTabChanged>>", self.on_tab_changed)

        self.tab_control.pack(expand=1, fill="both")

        # Set up tab contents
        self.setup_create_tab()
        self.setup_manage_tab()
        self.setup_profiles_tab()
        self.setup_capture_tab()
        self.setup_diagnostics_tab()
        self.setup_output_tab()

        # Status bar at the bottom
        self.status_bar = ttk.Label(
            root,
            textvariable=self.status_var,
            relief=tk.SUNKEN,
            anchor=tk.W
        )
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    def setup_menu(self):
        """Set up the application menu."""
        menubar = tk.Menu(self.root)

        # File menu
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Save Preset...", command=self.save_preset)
        file_menu.add_command(label="Load Preset...", command=self.load_preset)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        menubar.add_cascade(label="File", menu=file_menu)

        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menubar)

    def _setup_scrollable_tab(self, tab, padding=""):
        """Wrap a notebook tab in a Canvas+Scrollbar so it scrolls vertically
        when the window is shorter than the content.

        Returns the inner Frame (placed inside the canvas) where the caller
        should add its widgets. The scroll region auto-updates when the
        inner frame's contents change size, and mouse-wheel scrolling is
        bound while the cursor is over the tab.

        Args:
            tab: The ttk.Frame (notebook tab) to make scrollable.
            padding: Tkinter padding string for the inner frame (e.g. "10"
                     to match a direct pack'd frame with padding=10).

        Returns:
            The inner ttk.Frame inside the canvas.
        """
        canvas = tk.Canvas(tab, highlightthickness=0)
        scrollbar = ttk.Scrollbar(tab, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        inner = ttk.Frame(canvas, padding=padding)
        inner_window = canvas.create_window((0, 0), window=inner, anchor=tk.NW)

        def _on_inner_configure(_event):
            canvas.configure(scrollregion=canvas.bbox("all"))
            # Re-bind to catch any new children
            _bind_wheel_to_children(inner)

        def _on_canvas_configure(event):
            canvas.itemconfigure(inner_window, width=event.width)

        inner.bind("<Configure>", _on_inner_configure)
        canvas.bind("<Configure>", _on_canvas_configure)

        def _on_mousewheel(event):
            # macOS/Windows: event.delta. X11: num 4 (up) / 5 (down).
            if event.delta:
                delta = -1 * (event.delta // 120)
            elif event.num == 4:
                delta = -1
            elif event.num == 5:
                delta = 1
            else:
                delta = 0
            if delta:
                canvas.yview_scroll(delta, "units")

        # Bind mousewheel ONLY to the inner frame and its descendants
        # (not bind_all). The bug we fixed: bind_all catches every
        # <MouseWheel> event anywhere in the app, including over
        # Treeviews and Listboxes inside the tab, scrolling the page
        # instead of the inner widget. Tkinter's event propagation
        # already routes child events to parent handlers, so binding
        # to the inner frame catches wheel events over any widget
        # inside it while letting widgets OUTSIDE the tab scroll
        # naturally (e.g. other tabs, popup menus, the Output tab
        # text widget).
        def _bind_wheel_to_children(widget):
            widget.bind("<MouseWheel>", _on_mousewheel)
            widget.bind("<Button-4>", _on_mousewheel)
            widget.bind("<Button-5>", _on_mousewheel)
            for child in widget.winfo_children():
                _bind_wheel_to_children(child)

        # Bind to all descendants of the inner frame. Also rebind
        # every time the inner frame's contents change (because new
        # widgets created after this returns wouldn't otherwise get
        # the binding).
        _bind_wheel_to_children(inner)

        def _on_inner_configure(_event):
            canvas.configure(scrollregion=canvas.bbox("all"))
            # Re-bind to catch any new children
            _bind_wheel_to_children(inner)

        return inner

    def setup_create_tab(self):
        """Set up the Create tab content."""
        frame = self._setup_scrollable_tab(self.create_tab, padding="10")

        # Add descriptive label
        ttk.Label(
            frame,
            text="Create a new virtual audio device",
            font=("", 12, "bold")
        ).grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 10))

        # Device type radio (Sink / Source / Both)
        # Default to "both" (duplex) for backward compatibility with existing users.
        type_frame = ttk.LabelFrame(frame, text="Device Type", padding="10")
        type_frame.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))

        self.device_type_var = tk.StringVar(value="both")
        ttk.Radiobutton(
            type_frame,
            text="Sink only — apps play to this device (e.g. game_sink, music_sink)",
            variable=self.device_type_var,
            value="sink",
            command=self.on_device_type_changed,
        ).grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=2)
        ttk.Radiobutton(
            type_frame,
            text="Source only — apps record from this device (e.g. capture target)",
            variable=self.device_type_var,
            value="source",
            command=self.on_device_type_changed,
        ).grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=2)
        ttk.Radiobutton(
            type_frame,
            text="Both (duplex) — apps play AND record from this device",
            variable=self.device_type_var,
            value="both",
            command=self.on_device_type_changed,
        ).grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=2)

        # Add format example label
        self.command_format_label = ttk.Label(
            frame,
            text="Format: pactl load-module module-null-sink media.class=Audio/Duplex sink_name=<name> channels=<channels>",
            font=("", 9, "italic"),
            foreground="gray"
        )
        self.command_format_label.grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=(0, 10))

        # Basic Options Section
        basic_frame = ttk.LabelFrame(frame, text="Basic Options", padding="10")
        basic_frame.grid(row=3, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))

        # Sink name
        ttk.Label(basic_frame, text="Sink Name (optional):").grid(
            row=0, column=0, sticky=tk.W, pady=5
        )
        self.sink_name_var = tk.StringVar()
        self.sink_name_entry = ttk.Entry(basic_frame, textvariable=self.sink_name_var)
        self.sink_name_entry.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=5, pady=5)

        # Bind events for placeholder behavior
        self.sink_name_entry.bind("<FocusIn>", self.on_name_focus_in)
        self.sink_name_entry.bind("<FocusOut>", self.on_name_focus_out)
        self.sink_name_entry.bind("<KeyPress>", self.on_name_key_press)

        # Track if user has manually entered a name
        self.user_has_custom_name = False

        # Sink description - kept for backward compatibility but less emphasized
        ttk.Label(basic_frame, text="Description (optional):").grid(
            row=1, column=0, sticky=tk.W, pady=5
        )
        self.sink_desc_var = tk.StringVar()
        self.sink_desc_entry = ttk.Entry(basic_frame, textvariable=self.sink_desc_var)
        self.sink_desc_entry.grid(row=1, column=1, sticky=(tk.W, tk.E), padx=5, pady=5)

        # Track if user has manually entered a description
        self.user_has_custom_desc = False
        self.sink_desc_entry.bind("<KeyPress>", self.on_desc_key_press)

        # Audio Preset (moved from advanced to basic)
        ttk.Label(basic_frame, text="Audio Preset:").grid(
            row=2, column=0, sticky=tk.W, pady=5
        )

        # Create frame for preset controls
        preset_frame = ttk.Frame(basic_frame)
        preset_frame.grid(row=2, column=1, sticky=(tk.W, tk.E), padx=5, pady=5)

        # Editable preset combobox
        self.audio_preset_var = tk.StringVar(value="Stereo")
        self.audio_preset_combo = ttk.Combobox(
            preset_frame,
            textvariable=self.audio_preset_var,
            width=20,
            state="normal"  # Make it editable
        )
        self.audio_preset_combo.grid(row=0, column=0, sticky=(tk.W, tk.E))

        # Load preset names into combobox
        self.refresh_preset_list()

        # Save preset button
        self.save_preset_btn = ttk.Button(
            preset_frame,
            text="💾",
            width=3,
            command=self.save_current_preset
        )
        self.save_preset_btn.grid(row=0, column=1, padx=(5, 0))

        # Delete preset button
        self.delete_preset_btn = ttk.Button(
            preset_frame,
            text="🗑️",
            width=3,
            command=self.delete_current_preset
        )
        self.delete_preset_btn.grid(row=0, column=2, padx=(2, 0))

        # Configure preset frame grid
        preset_frame.columnconfigure(0, weight=1)

        # Bind preset selection to update advanced fields
        self.audio_preset_combo.bind("<<ComboboxSelected>>", self.on_audio_preset_selected)
        self.audio_preset_combo.bind("<KeyRelease>", self.on_preset_name_changed)

        # Advanced Options Section (Collapsible)
        self.show_advanced_var = tk.BooleanVar(value=False)
        self.advanced_toggle = ttk.Checkbutton(
            frame,
            text="Show Advanced Options",
            variable=self.show_advanced_var,
            command=self.toggle_advanced_options,
        )
        self.advanced_toggle.grid(row=4, column=0, columnspan=2, sticky=tk.W, pady=(10, 5))

        # Advanced options frame — placed BELOW the toggle (row 5) so
        # the toggle stays visible when the frame is shown. Initially
        # hidden via grid_remove().
        self.advanced_frame = ttk.LabelFrame(frame, text="Advanced Options", padding="10")
        self._advanced_frame_row = 5

        # Sample Rate
        ttk.Label(self.advanced_frame, text="Sample Rate (Hz):").grid(
            row=0, column=0, sticky=tk.W, pady=5
        )
        self.rate_var = tk.StringVar(value="44100")
        rate_combo = ttk.Combobox(
            self.advanced_frame,
            textvariable=self.rate_var,
            values=("8000", "16000", "22050", "44100", "48000", "88200", "96000", "192000"),
            width=15
        )
        rate_combo.grid(row=0, column=1, sticky=tk.W, padx=5, pady=5)
        rate_combo.current(3)  # Default to 44100

        # Sample Format (with descriptive labels)
        ttk.Label(self.advanced_frame, text="Sample Format:").grid(
            row=1, column=0, sticky=tk.W, pady=5
        )
        self.format_var = tk.StringVar(value="s16le")
        format_options = [
            ("s16le", "16-bit Little Endian (Default)"),
            ("s16be", "16-bit Big Endian"),
            ("s24le", "24-bit Little Endian"),
            ("s24be", "24-bit Big Endian"),
            ("s32le", "32-bit Little Endian"),
            ("s32be", "32-bit Big Endian"),
            ("float32le", "32-bit Float Little Endian"),
            ("float32be", "32-bit Float Big Endian"),
            ("u8", "8-bit Unsigned")
        ]

        format_combo = ttk.Combobox(
            self.advanced_frame,
            textvariable=self.format_var,
            values=[option[1] for option in format_options],
            width=25
        )
        format_combo.grid(row=1, column=1, sticky=tk.W, padx=5, pady=5)
        format_combo.current(0)  # Default to 16-bit Little Endian

        # Store format mappings for easy access
        self.format_mappings = {desc: code for code, desc in format_options}
        self.format_reverse_mappings = {code: desc for code, desc in format_options}

        # Bind format selection to update the underlying value
        format_combo.bind("<<ComboboxSelected>>", self.on_format_selected)

        # Channels (moved from basic to advanced)
        ttk.Label(self.advanced_frame, text="Channels:").grid(
            row=2, column=0, sticky=tk.W, pady=5
        )
        self.channels_var = tk.StringVar(value="2")
        channels_combo = ttk.Combobox(
            self.advanced_frame,
            textvariable=self.channels_var,
            values=("1", "2", "4", "6", "8"),
            width=10
        )
        channels_combo.grid(row=2, column=1, sticky=tk.W, padx=5, pady=5)
        channels_combo.current(1)  # Default to stereo (2 channels)

        # Channel Map
        # Channel Map — preset dropdown + custom entry. The map tells
        # PulseAudio which speaker each audio channel corresponds to
        # (front-left, front-right, etc). Without it, PA picks a default
        # based on channel count, but for surround setups the user often
        # wants explicit control.
        ttk.Label(self.advanced_frame, text="Channel Map:").grid(
            row=3, column=0, sticky=tk.W, pady=5
        )
        # The map labels are "preset name — channel_layout". We split on
        # ' — ' to get back the layout string. Custom is a sentinel
        # that shows the entry below.
        self.channel_map_presets = [
            ("Default (PA picks based on channel count)", ""),
            ("Mono (1 ch)", "mono"),
            ("Stereo (2 ch, FL+FR)", "front-left,front-right"),
            ("2.1 Stereo (FL+FR+LFE)", "front-left,front-right,lfe"),
            ("Quad (4 ch, FL+FR+RL+RR)", "front-left,front-right,rear-left,rear-right"),
            ("Surround 4.0 (FL+FR+FC+RC)",
             "front-left,front-right,front-center,rear-center"),
            ("Surround 5.0 (FL+FR+FC+RL+RR)",
             "front-left,front-right,front-center,rear-left,rear-right"),
            ("Surround 5.1 (FL+FR+FC+LFE+RL+RR)",
             "front-left,front-right,front-center,lfe,rear-left,rear-right"),
            ("Surround 6.0 (FL+FR+FC+RC+SL+SR)",
             "front-left,front-right,front-center,rear-center,side-left,side-right"),
            ("Surround 6.1 (FL+FR+FC+LFE+RC+SL+SR)",
             "front-left,front-right,front-center,lfe,rear-center,side-left,side-right"),
            ("Surround 7.1 (FL+FR+FC+LFE+RL+RR+SL+SR)",
             "front-left,front-right,front-center,lfe,rear-left,rear-right,side-left,side-right"),
            ("Custom (type below)", "__custom__"),
        ]
        self.channel_map_preset_var = tk.StringVar(
            value=self.channel_map_presets[1][0]  # default to Stereo
        )
        self.channel_map_combo = ttk.Combobox(
            self.advanced_frame,
            textvariable=self.channel_map_preset_var,
            values=[p[0] for p in self.channel_map_presets],
            state="readonly",
            width=30,
        )
        self.channel_map_combo.grid(row=3, column=1, sticky=tk.W, padx=5, pady=5)
        self.channel_map_combo.bind(
            "<<ComboboxSelected>>", self._on_channel_map_preset_change
        )
        # The underlying channel_map text (what gets sent to pactl)
        self.channel_map_var = tk.StringVar(value="front-left,front-right")
        # Custom entry — hidden unless "Custom" is selected
        self.channel_map_custom_entry = ttk.Entry(
            self.advanced_frame, textvariable=self.channel_map_var, width=30
        )
        # Initially hidden
        self.channel_map_custom_var = tk.StringVar()  # for trace

        # Update default map when channels preset changes (Stereo is
        # the natural default for 2 channels, etc.)
        def _on_channels_change_default_map(*_):
            try:
                ch = int(self.channels_var.get())
            except (ValueError, tk.TclError):
                return
            # Don't override user custom choice
            if self.channel_map_preset_var.get().startswith("Custom"):
                return
            # Match by channel count to a sensible default
            defaults = {
                1: "mono",
                2: "front-left,front-right",
                3: "front-left,front-right,front-center",
                4: "front-left,front-right,rear-left,rear-right",
                5: "front-left,front-right,front-center,rear-left,rear-right",
                6: "front-left,front-right,front-center,lfe,rear-left,rear-right",
                8: "front-left,front-right,front-center,lfe,rear-left,rear-right,side-left,side-right",
            }
            default_map = defaults.get(ch)
            if not default_map:
                return
            # Find a matching preset by map string
            for label, layout in self.channel_map_presets:
                if layout == default_map:
                    self.channel_map_preset_var.set(label)
                    break
            self.channel_map_var.set(default_map)

        self.channels_var.trace_add("write", _on_channels_change_default_map)

        # Additional Properties (moved to row 5 to make room for
        # the custom channel-map entry that appears below the
        # channel-map combo when "Custom" is selected).
        ttk.Label(self.advanced_frame, text="Additional Properties:").grid(
            row=5, column=0, sticky=tk.W, pady=5
        )
        self.properties_var = tk.StringVar()
        properties_entry = ttk.Entry(self.advanced_frame, textvariable=self.properties_var, width=30)
        properties_entry.grid(row=5, column=1, sticky=(tk.W, tk.E), padx=5, pady=5)

        # Properties help label
        ttk.Label(
            self.advanced_frame,
            text="e.g., device.description='My Custom Sink'",
            font=("", 8, "italic"),
            foreground="gray"
        ).grid(row=6, column=1, sticky=tk.W, padx=5, pady=(0, 5))

        # Loopback Latency — affects any future loopback created from
        # this sink. Too low = robotic/crackly audio (buffer underrun).
        # Too high = noticeable audio delay. The recommendation scales
        # with sample rate because higher rates have tighter per-frame
        # deadlines (21μs at 48kHz, 5μs at 192kHz) while per-frame
        # DSP cost is roughly constant.
        ttk.Label(self.advanced_frame, text="Loopback Latency:").grid(
            row=7, column=0, sticky=tk.W, pady=5
        )
        # Build the preset list. The "Auto" option picks the
        # recommended value based on the current sample rate, and
        # its label updates when the rate changes. Custom shows a
        # small entry for power users.
        self._latency_presets = [
            ("5 (lowest, light DSP only)", 5),
            ("10", 10),
            ("20 (snappy)", 20),
            ("Auto (recommended for sample rate)", None),  # None = compute from rate
            ("50 (safe default)", 50),
            ("100 (heavy DSP / USB)", 100),
            ("200 (safest)", 200),
            ("Custom (type below)", "__custom__"),
        ]
        # Initial recommendation
        try:
            init_rate = int(self.rate_var.get())
        except (ValueError, tk.TclError):
            init_rate = 48000
        init_rec = PactlRunner.recommended_loopback_latency_ms(init_rate)
        self._latency_recommended_ms = init_rec
        # The latency preset var holds the LABEL (string). The actual
        # millisecond value is computed by _get_loopback_latency_ms.
        self.latency_preset_var = tk.StringVar()
        self.latency_combo = ttk.Combobox(
            self.advanced_frame,
            textvariable=self.latency_preset_var,
            values=self._latency_preset_labels(),
            state="readonly",
            width=30,
        )
        self.latency_combo.grid(row=7, column=1, sticky=tk.W, padx=5, pady=5)
        self.latency_combo.bind(
            "<<ComboboxSelected>>", self._on_latency_preset_change
        )
        # Set initial selection to Auto (recommended) — the label says
        # "<N> ms (recommended for sample rate)" so the user can see
        # the actual value.
        self.latency_preset_var.set(self._latency_auto_label())
        # Custom entry — hidden unless "Custom" is selected
        self.latency_var = tk.StringVar(value=str(init_rec))
        self.latency_custom_entry = ttk.Entry(
            self.advanced_frame, textvariable=self.latency_var, width=10
        )
        # Update recommended when sample rate changes
        def _on_rate_change_recommend(*_):
            try:
                rate = int(self.rate_var.get())
            except (ValueError, tk.TclError):
                return
            self._latency_recommended_ms = (
                PactlRunner.recommended_loopback_latency_ms(rate)
            )
            # Update the "Auto" label to reflect the new rate
            auto_label = self._latency_auto_label()
            # If the user is currently on Auto, refresh the displayed label
            if self.latency_preset_var.get().startswith("Auto"):
                self.latency_preset_var.set(auto_label)
            else:
                # Otherwise just update the dropdown's Auto option label
                # (Tkinter doesn't easily let us change a single value in
                # a combobox, so we just rebuild the values list)
                try:
                    self.latency_combo.config(values=self._latency_preset_labels())
                except tk.TclError:
                    pass

        self.rate_var.trace_add("write", _on_rate_change_recommend)

        # Configure advanced frame grid
        self.advanced_frame.columnconfigure(1, weight=1)

        # Create button
        self.create_button = ttk.Button(
            frame,
            text="Create Both (Duplex) Sink",
            command=self.create_device
        )
        self.create_button.grid(row=7, column=0, columnspan=2, pady=20)

        # Output preview
        ttk.Label(frame, text="Command Preview:").grid(
            row=8, column=0, sticky=tk.W, pady=(10, 5)
        )
        self.command_preview_var = tk.StringVar()
        self.command_preview_var.set("pactl load-module module-null-sink media.class=Audio/Duplex sink_name=example channels=2")

        command_preview = ttk.Label(
            frame,
            textvariable=self.command_preview_var,
            font=("Courier", 9),
            background="#f0f0f0",
            relief=tk.GROOVE,
            padding=10,
            wraplength=500
        )
        command_preview.grid(row=8, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)

        # Update preview when values change
        self.sink_name_var.trace_add("write", self.update_command_preview)
        self.audio_preset_var.trace_add("write", self.update_command_preview)
        self.channels_var.trace_add("write", self.update_command_preview)
        self.rate_var.trace_add("write", self.update_command_preview)
        self.format_var.trace_add("write", self.update_command_preview)
        self.channel_map_var.trace_add("write", self.update_command_preview)
        self.properties_var.trace_add("write", self.update_command_preview)
        self.device_type_var.trace_add("write", self.update_command_preview)

        # Configure grid
        frame.columnconfigure(1, weight=1)

        # Initialize preset values AFTER all UI elements are created
        self.on_audio_preset_selected(None)

    def setup_manage_tab(self):
        """Set up the Manage tab content with simplified category-based view."""
        # Wrap the tab in a scrollable canvas so the whole tab scrolls
        # vertically when the window is shorter than the content. The inner
        # `frame` hosts the stacked panels (tree, buttons, routing, details);
        # the inner-frame height grows with its contents and the canvas
        # provides the scrollbar.
        frame = self._setup_scrollable_tab(self.manage_tab, padding="10")

        # --- Quick Routing Panel (always visible) ---
        # Prominent, prominent panel at the TOP so users immediately
        # see how to route a selected virtual sink to hardware outputs.
        # The tree below is where they pick what to route.
        quick_route_frame = ttk.LabelFrame(
            frame, text="Route Selected Sink", padding="10"
        )
        quick_route_frame.pack(fill=tk.X, padx=0, pady=(0, 10))
        # Status line
        self.quick_route_status_var = tk.StringVar(
            value="Select a virtual sink below, then check the outputs you "
                  "want its audio to play through, then click 'Apply Routing'."
        )
        ttk.Label(
            quick_route_frame,
            textvariable=self.quick_route_status_var,
            wraplength=600, font=("", 9),
        ).pack(anchor=tk.W, pady=(0, 5))
        # Checkbox list of hardware outputs
        self.quick_route_listbox_frame = ttk.Frame(quick_route_frame)
        self.quick_route_listbox_frame.pack(fill=tk.X, pady=(0, 5))
        self.quick_route_listbox = tk.Listbox(
            self.quick_route_listbox_frame,
            height=4,
            selectmode=tk.EXTENDED,
            exportselection=False,
            font=("", 9),
        )
        self.quick_route_listbox.pack(
            side=tk.LEFT, fill=tk.BOTH, expand=True
        )
        qr_scroll = ttk.Scrollbar(
            self.quick_route_listbox_frame, orient=tk.VERTICAL,
            command=self.quick_route_listbox.yview,
        )
        qr_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.quick_route_listbox.configure(yscrollcommand=qr_scroll.set)
        # Buttons
        qr_buttons = ttk.Frame(quick_route_frame)
        qr_buttons.pack(fill=tk.X)
        ttk.Button(
            qr_buttons, text="Apply Routing",
            command=self._on_quick_route_apply, width=15,
        ).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(
            qr_buttons, text="Stop All Routing",
            command=self._on_quick_route_stop_all, width=15,
        ).pack(side=tk.LEFT, padx=5)
        ttk.Button(
            qr_buttons, text="Refresh Outputs",
            command=self._refresh_quick_route_outputs, width=15,
        ).pack(side=tk.LEFT, padx=5)
        # State for the quick route panel
        self._quick_route_outputs: List[str] = []
        self._quick_route_sink_name: Optional[str] = None
        # Initial population of the output list
        self._refresh_quick_route_outputs()

        # Add Show System Modules checkbox
        self.show_system_var = tk.BooleanVar(value=False)
        show_system_cb = ttk.Checkbutton(
            frame,
            text="Show System Modules",
            variable=self.show_system_var,
            command=self.toggle_system_modules
        )
        show_system_cb.pack(anchor=tk.W, padx=0, pady=(0, 5))

        # Add Show Monitor Sources checkbox
        self.show_monitors_var = tk.BooleanVar(value=False)
        show_monitors_cb = ttk.Checkbutton(
            frame,
            text="Show Monitor Sources",
            variable=self.show_monitors_var,
            command=self.toggle_monitor_sources
        )
        show_monitors_cb.pack(anchor=tk.W, padx=0, pady=(0, 5))

        # Create tree frame for the unified tree and scrollbars
        tree_frame = ttk.Frame(frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        # Create unified tree view with columns for ID, type, and name only
        columns = ("id", "type", "name")
        self.unified_tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="tree headings",  # Show both tree and column headings
            selectmode="browse"
        )

        # Define column headings
        self.unified_tree.heading("id", text="ID")
        self.unified_tree.heading("type", text="Type")
        self.unified_tree.heading("name", text="Name")

        # Define column widths - optimized for better visibility
        self.unified_tree.column("id", width=60, anchor=tk.CENTER)
        self.unified_tree.column("type", width=100, anchor=tk.CENTER)
        self.unified_tree.column("name", width=300)

        # Configure tag styles for different entity types
        self.unified_tree.tag_configure("module", background="#E0E0FF")
        self.unified_tree.tag_configure("sink", background="#E0FFE0")
        self.unified_tree.tag_configure("source", background="#FFE0E0")
        self.unified_tree.tag_configure("category", background="#F0F0F0", font=("", 9, "bold"))

        # Scrollbars
        y_scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.unified_tree.yview)
        self.unified_tree.configure(yscrollcommand=y_scrollbar.set)

        x_scrollbar = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.unified_tree.xview)
        self.unified_tree.configure(xscrollcommand=x_scrollbar.set)

        # Pack everything
        self.unified_tree.grid(row=0, column=0, sticky="nsew")
        y_scrollbar.grid(row=0, column=1, sticky="ns")
        x_scrollbar.grid(row=1, column=0, sticky="ew")

        # Configure grid
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        # Selection handling
        self.unified_tree.bind("<<TreeviewSelect>>", self.on_unified_tree_select)

        # Button frame
        button_frame = ttk.Frame(frame, padding="5")
        button_frame.pack(fill=tk.X, pady=5)

        # Action buttons
        self.refresh_button = ttk.Button(
            button_frame, text="Refresh All", command=self.refresh_all_views
        )
        self.refresh_button.pack(side=tk.LEFT, padx=5)

        self.unload_button = ttk.Button(
            button_frame, text="Unload Selected Module", command=self.unload_selected_from_tree,
            state="disabled"  # Disabled by default until a module is selected
        )
        self.unload_button.pack(side=tk.LEFT, padx=5)

        self.remove_null_sinks_button = ttk.Button(
            button_frame, text="Remove All Virtual Devices", command=self.unload_all_null_sinks
        )
        self.remove_null_sinks_button.pack(side=tk.LEFT, padx=5)

        # Routing frame — lets the user wire a selected virtual sink to a real output
        self.routing_frame = ttk.LabelFrame(frame, text="Routing")
        self.routing_frame.columnconfigure(1, weight=1)

        # --- Single-output routing (legacy dropdown) -------------------
        # Status line
        self.routing_status_var = tk.StringVar(value="Select a virtual sink to route")
        ttk.Label(
            self.routing_frame,
            textvariable=self.routing_status_var,
            font=("", 9),
        ).grid(row=0, column=0, columnspan=3, sticky=tk.W, padx=5, pady=(5, 2))

        # Output dropdown
        ttk.Label(self.routing_frame, text="Route to:").grid(
            row=1, column=0, sticky=tk.W, padx=5, pady=2
        )
        self.routing_output_var = tk.StringVar()
        self.routing_output_combo = ttk.Combobox(
            self.routing_frame,
            textvariable=self.routing_output_var,
            values=(),
            state="disabled",
            width=60,
        )
        self.routing_output_combo.grid(row=1, column=1, sticky=(tk.W, tk.E), padx=5, pady=2)

        # Buttons
        self.route_button = ttk.Button(
            self.routing_frame,
            text="Apply Routing",
            command=self.apply_routing,
            state="disabled",
        )
        self.route_button.grid(row=1, column=2, padx=5, pady=2)

        self.stop_route_button = ttk.Button(
            self.routing_frame,
            text="Stop Routing",
            command=self.stop_routing,
            state="disabled",
        )
        self.stop_route_button.grid(row=1, column=3, padx=5, pady=2)

        self.routing_frame.columnconfigure(1, weight=1)

        # --- Multi-output routing (Phase 5b) ----------------------------
        # Checkbox list of all hardware outputs. Each checked item gets a
        # loopback. Useful for the "music plays on speakers AND
        # headphones simultaneously" use case (combine-stream preset
        # from the plan, implemented via multiple loopbacks since
        # module-combine-stream does the opposite: mix multiple sources
        # INTO one sink).
        self.multi_routing_frame = ttk.LabelFrame(
            self.routing_frame, text="Multi-output Routing"
        )
        self.multi_routing_frame.grid(
            row=2, column=0, columnspan=4, sticky=(tk.W, tk.E), padx=5, pady=(10, 5)
        )

        ttk.Label(
            self.multi_routing_frame,
            text=(
                "Check one or more hardware outputs to send this sink's audio "
                "to all of them simultaneously. Pre-checked items already "
                "have an active loopback."
            ),
            wraplength=600,
            font=("", 9),
        ).pack(anchor=tk.W, padx=5, pady=(5, 2))

        # Listbox with multi-select via EXTENDED (Shift/Ctrl+click)
        listbox_frame = ttk.Frame(self.multi_routing_frame)
        listbox_frame.pack(fill=tk.X, padx=5, pady=2)
        self.multi_routing_listbox = tk.Listbox(
            listbox_frame,
            selectmode=tk.EXTENDED,
            height=4,
            exportselection=False,
        )
        self.multi_routing_listbox.pack(
            side=tk.LEFT, fill=tk.BOTH, expand=True
        )
        listbox_scroll = ttk.Scrollbar(
            listbox_frame, orient=tk.VERTICAL,
            command=self.multi_routing_listbox.yview,
        )
        listbox_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.multi_routing_listbox.configure(yscrollcommand=listbox_scroll.set)

        # Track which targets are "currently routed" (for pre-checking).
        # Keyed by sink name (rebuilt when the user selects a sink).
        self._multi_routed_targets: List[str] = []

        multi_buttons = ttk.Frame(self.multi_routing_frame)
        multi_buttons.pack(fill=tk.X, padx=5, pady=2)
        self.multi_apply_button = ttk.Button(
            multi_buttons,
            text="Apply to Selected",
            command=self.apply_multi_routing,
            state="disabled",
        )
        self.multi_apply_button.pack(side=tk.LEFT, padx=(0, 5))
        self.multi_stop_all_button = ttk.Button(
            multi_buttons,
            text="Stop All Routing",
            command=self.stop_all_routing_for_selected,
            state="disabled",
        )
        self.multi_stop_all_button.pack(side=tk.LEFT, padx=5)

        # --- Device controls — per-device volume slider, mute, set-as-default.
        # Lets the user adjust the selected virtual sink's volume without
        # opening pavucontrol. This is the Phase 4 polish item that makes
        # Pulsar feel like a tool: one place to control your routing
        # topology AND its playback levels.
        self.device_controls_frame = ttk.LabelFrame(
            frame, text="Device Controls"
        )
        self.device_controls_frame.pack(fill=tk.X, padx=10, pady=5)
        self._build_device_controls(self.device_controls_frame)

        # Details frame with scrollable text widget and toggle
        details_frame = ttk.LabelFrame(frame, text="Details")
        details_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # Details toggle frame
        toggle_frame = ttk.Frame(details_frame)
        toggle_frame.pack(fill=tk.X, padx=5, pady=5)

        self.show_all_details_var = tk.BooleanVar(value=False)
        details_toggle = ttk.Checkbutton(
            toggle_frame,
            text="Show All Technical Details",
            variable=self.show_all_details_var,
            command=self.toggle_details_view
        )
        details_toggle.pack(side=tk.LEFT)

        # Text frame for details display
        text_frame = ttk.Frame(details_frame)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Scrollable text widget for details
        self.details_text = tk.Text(
            text_frame,
            wrap=tk.WORD,
            height=8,
            state=tk.DISABLED,
            font=("Consolas", 9),
            background="#f8f8f8"
        )
        self.details_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Scrollbar for details
        details_scrollbar = ttk.Scrollbar(
            text_frame,
            orient=tk.VERTICAL,
            command=self.details_text.yview
        )
        self.details_text.configure(yscrollcommand=details_scrollbar.set)
        details_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Initialize details display
        self.update_details_display("Select an item to see details")

        # Initial load
        self.refresh_all_views()

    def setup_profiles_tab(self):
        """Set up the Profiles tab content.

        A profile captures a complete audio routing topology: which virtual
        devices exist, what their settings are, and which loopbacks connect
        them to real outputs. See src/utils/profile_manager.py for the
        schema and src/main.py's docs/ for the design rationale.
        """
        outer = ttk.Frame(self.profiles_tab, padding="10")
        outer.pack(fill=tk.BOTH, expand=True)

        # --- Top: profile list (left) + buttons (right) --------------------
        list_frame = ttk.LabelFrame(outer, text="Saved Profiles", padding="10")
        list_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))

        self.profile_listbox = tk.Listbox(list_frame, height=12, exportselection=False)
        self.profile_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.profile_listbox.bind("<<ListboxSelect>>", self.on_profile_select)

        list_scroll = ttk.Scrollbar(
            list_frame, orient=tk.VERTICAL, command=self.profile_listbox.yview
        )
        list_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.profile_listbox.configure(yscrollcommand=list_scroll.set)

        # --- Right side: action buttons + selected profile preview ---------
        actions_frame = ttk.LabelFrame(
            outer, text="Actions", padding="10"
        )
        actions_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(5, 0))

        ttk.Button(
            actions_frame, text="Apply Profile",
            command=self.apply_selected_profile, width=20
        ).pack(pady=3, fill=tk.X)

        ttk.Button(
            actions_frame, text="Save Current State",
            command=self.save_current_state_as_profile, width=20
        ).pack(pady=3, fill=tk.X)

        ttk.Button(
            actions_frame, text="Delete Profile",
            command=self.delete_selected_profile, width=20
        ).pack(pady=3, fill=tk.X)

        ttk.Separator(actions_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)

        ttk.Button(
            actions_frame, text="Refresh List",
            command=self.refresh_profile_list, width=20
        ).pack(pady=3, fill=tk.X)

        # --- Bottom: preview / details panel --------------------------------
        details_frame = ttk.LabelFrame(outer, text="Selected Profile", padding="10")
        details_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        self.profile_details = tk.Text(
            details_frame, height=8, wrap=tk.WORD, state=tk.DISABLED
        )
        self.profile_details.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        details_scroll = ttk.Scrollbar(
            details_frame, orient=tk.VERTICAL, command=self.profile_details.yview
        )
        details_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.profile_details.configure(yscrollcommand=details_scroll.set)

        # Initial population
        self.refresh_profile_list()

    def refresh_profile_list(self):
        """Reload the list of saved profiles into the Listbox.

        Built-in profiles show a "(built-in)" suffix. Built-ins that are
        currently shadowed by a user profile of the same name show
        "(built-in, your edits)" so the user knows their version is in
        effect and the original is still recoverable.
        """
        self.profile_listbox.delete(0, tk.END)
        try:
            names = self.profile_manager.get_profile_names()
        except Exception as e:
            messagebox.showerror("Profile error", f"Failed to read profiles: {e}")
            names = []
        # Insert with a visible label so built-ins vs user profiles are
        # distinguishable. The actual name is preserved for selection
        # logic — we just store the display label in a parallel list.
        self._profile_display_names = []  # index -> label
        self._profile_actual_names = []   # index -> real name
        for n in sorted(names):
            if self.profile_manager.is_builtin_name(n):
                shadowed = self.profile_manager.is_shadowed_by_user(n)
                label = f"{n}  (built-in, your edits)" if shadowed else f"{n}  (built-in)"
            else:
                label = n
            self.profile_listbox.insert(tk.END, label)
            self._profile_display_names.append(label)
            self._profile_actual_names.append(n)
        self._set_profile_details_text(
            "Select a profile to see its details.\n\n"
            "Built-in profiles (e.g. Gaming, Streaming, Voice Chat Only) are "
            "shipped with Pulsar. Use 'Save Current State' to capture your "
            "own setup, which will appear here without the (built-in) tag."
        )

    def on_profile_select(self, _event=None):
        """Show the selected profile's JSON in the details panel."""
        name = self._get_selected_profile_name()
        if not name:
            return
        profile = self.profile_manager.get_profile(name)
        if not profile:
            return
        import json as _json
        body = _json.dumps(profile, indent=2)
        self._set_profile_details_text(body)

    def _set_profile_details_text(self, text: str):
        """Replace the contents of the profile details Text widget."""
        self.profile_details.configure(state=tk.NORMAL)
        self.profile_details.delete("1.0", tk.END)
        self.profile_details.insert("1.0", text)
        self.profile_details.configure(state=tk.DISABLED)

    def _get_selected_profile_name(self) -> str:
        sel = self.profile_listbox.curselection()
        if not sel:
            return ""
        idx = sel[0]
        actual = getattr(self, "_profile_actual_names", [])
        if idx < len(actual):
            return actual[idx]
        # Fallback for an unexpected state — return the displayed text
        return self.profile_listbox.get(idx)

    def apply_selected_profile(self):
        """Apply the currently selected profile to the running audio system."""
        name = self._get_selected_profile_name()
        if not name:
            messagebox.showinfo("Apply Profile", "Please select a profile first.")
            return

        profile = self.profile_manager.get_profile(name)
        if not profile:
            messagebox.showerror("Apply Profile", f"Profile '{name}' not found.")
            return

        # Confirm — applying unloads existing null-sinks and loopbacks.
        n_dev = len(profile.get("devices", []))
        n_route = len(profile.get("routing", []))
        if not messagebox.askyesno(
            "Apply Profile",
            f"Apply profile '{name}'?\n\n"
            f"  {n_dev} device(s) will be created\n"
            f"  {n_route} loopback(s) will be created\n"
            f"  All existing null-sinks and loopbacks will be unloaded.\n\n"
            "Proceed?",
        ):
            return

        self.add_output(f"$ Applying profile: {name}")
        result = self.profile_manager.apply_profile(
            profile, logger=self.add_output, unload_existing=True
        )

        if result["success"]:
            self.status_var.set(
                f"Applied profile '{name}': "
                f"{len(result['created_devices'])} devices, "
                f"{len(result['created_loopbacks'])} loopbacks"
            )
            self.refresh_all_views()
            self.add_output(
                f"Profile '{name}' applied successfully."
            )
        else:
            err_text = "\n".join(f"  - {e}" for e in result["errors"])
            self.add_output(f"Failed to apply profile '{name}':\n{err_text}")
            if result.get("rolled_back"):
                self.add_output("Rolled back partial changes.")
            messagebox.showerror(
                "Apply Profile",
                f"Failed to apply profile '{name}':\n\n{err_text}\n\n"
                + ("Rolled back partial changes." if result.get("rolled_back") else ""),
            )

    def save_current_state_as_profile(self):
        """Capture the current null-sinks and loopbacks as a new profile."""
        # Ask for a name via a simple dialog
        dialog = tk.Toplevel(self.root)
        dialog.title("Save Profile")
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(
            dialog, text="Profile name:", padding=(10, 10, 0, 0)
        ).grid(row=0, column=0, sticky=tk.W)
        name_var = tk.StringVar()
        name_entry = ttk.Entry(dialog, textvariable=name_var, width=40)
        name_entry.grid(row=1, column=0, padx=10, sticky=tk.EW)

        ttk.Label(
            dialog, text="Description (optional):", padding=(10, 5, 0, 0)
        ).grid(row=2, column=0, sticky=tk.W)
        desc_var = tk.StringVar()
        ttk.Entry(dialog, textvariable=desc_var, width=40).grid(
            row=3, column=0, padx=10, sticky=tk.EW
        )

        ttk.Label(
            dialog,
            text="Allowed: letters, digits, underscore, dot, dash",
            font=("", 9, "italic"),
            padding=(10, 5, 0, 0),
        ).grid(row=4, column=0, sticky=tk.W)

        result = {"ok": False}

        def on_ok():
            result["ok"] = True
            dialog.destroy()

        def on_cancel():
            dialog.destroy()

        btn_frame = ttk.Frame(dialog, padding=10)
        btn_frame.grid(row=5, column=0, sticky=tk.EW)
        ttk.Button(btn_frame, text="Save", command=on_ok).pack(side=tk.RIGHT, padx=2)
        ttk.Button(btn_frame, text="Cancel", command=on_cancel).pack(side=tk.RIGHT, padx=2)

        name_entry.focus_set()
        dialog.bind("<Return>", lambda _e: on_ok())
        dialog.bind("<Escape>", lambda _e: on_cancel())

        self.root.wait_window(dialog)

        if not result["ok"]:
            return

        name = name_var.get().strip()
        if not name:
            messagebox.showwarning("Save Profile", "Profile name cannot be empty.")
            return

        try:
            profile = self.profile_manager.capture_topology(
                name, description=desc_var.get().strip()
            )
        except Exception as e:
            messagebox.showerror("Save Profile", f"Failed to capture state: {e}")
            return

        if not self.profile_manager.save_profile(name, profile):
            messagebox.showerror("Save Profile", f"Failed to save profile '{name}'.")
            return

        self.add_output(
            f"Saved profile '{name}': {len(profile['devices'])} devices, "
            f"{len(profile['routing'])} loopbacks"
        )
        self.status_var.set(f"Saved profile '{name}'")
        self.refresh_profile_list()

    def delete_selected_profile(self):
        """Delete the currently selected profile after confirmation.

        Built-in profiles cannot be deleted — they're shipped with Pulsar.
        If the user has shadowed a built-in with their own version, only
        the user version is deleted (the built-in becomes visible again).
        """
        name = self._get_selected_profile_name()
        if not name:
            messagebox.showinfo("Delete Profile", "Please select a profile first.")
            return
        # If this name is a built-in AND the user hasn't shadowed it,
        # there is nothing to delete (the data lives in builtin_profiles.json).
        if self.profile_manager.is_builtin_name(name) and \
                not self.profile_manager.is_shadowed_by_user(name):
            messagebox.showinfo(
                "Delete Profile",
                f"'{name}' is a built-in profile and cannot be deleted.\n\n"
                "If you want to remove it from your list, you can shadow it "
                "with your own version using 'Save Current State' (using the "
                "same name), then delete the shadow.",
            )
            return
        if not messagebox.askyesno(
            "Delete Profile",
            f"Delete profile '{name}'?\n\n"
            "This removes it from disk. Your current audio topology is unaffected.",
        ):
            return
        if self.profile_manager.delete_profile(name):
            self.add_output(f"Deleted profile '{name}'")
            self.status_var.set(f"Deleted profile '{name}'")
            self.refresh_profile_list()
        else:
            messagebox.showerror("Delete Profile", f"Failed to delete '{name}'.")

    def setup_capture_tab(self):
        """Set up the App Audio Discovery tab (formerly Phase 5a).

        PURPOSE: Find the PipeWire node ID for any application
        currently producing audio, so you can paste it into OBS,
        Discord, EasyEffects, or any other tool that consumes PW
        audio sources. This is the headline use case — once you
        have a node ID, you can route app audio anywhere you want
        in any PipeWire-aware app.

        What it does:
        - Scans pw-dump for active Stream/Input/Audio and
          Stream/Output/Audio nodes (apps currently producing audio)
        - Shows them in a table with PW node ID, app name, stream
          class, and node name
        - Lets you copy the node ID or node name to clipboard with
          one click

        What it does NOT do (intentionally):
        - Open an app picker dialog (the portal flow was broken on
          most systems and never returned a usable node ID)
        - Record to a file (capture-to-file with no follow-up
          destination is just clutter; if you want to record app
          audio, use OBS, EasyEffects, or pw-record directly)

        The portal flow and capture-to-file were removed because
        they didn't do anything the user couldn't do more easily
        with a dedicated tool. This tab now has one job: tell you
        the node ID for each running app's audio.
        """
        outer = self._setup_scrollable_tab(self.capture_tab, padding="10")

        # Header explanation
        ttk.Label(
            outer,
            text=(
                "App Audio Discovery — find the PipeWire node ID for each "
                "app currently producing audio. Copy the node ID and paste "
                "it into OBS (Application Audio Capture source), Discord, "
                "EasyEffects, or any other PipeWire-aware tool to route "
                "that app's audio anywhere you want."
            ),
            wraplength=700,
        ).pack(anchor=tk.W, pady=(0, 10))

        # Treeview of active streams
        discover_frame = ttk.LabelFrame(
            outer, text="Currently Active App Audio Streams", padding="10"
        )
        discover_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        cols = ("id", "app", "class", "node_name")
        self.capture_tree = ttk.Treeview(
            discover_frame, columns=cols, show="headings", height=10
        )
        self.capture_tree.heading("id", text="PW Node ID")
        self.capture_tree.heading("app", text="Application")
        self.capture_tree.heading("class", text="Stream Class")
        self.capture_tree.heading("node_name", text="Node Name")
        self.capture_tree.column("id", width=100, anchor=tk.W)
        self.capture_tree.column("app", width=200, anchor=tk.W)
        self.capture_tree.column("class", width=180, anchor=tk.W)
        self.capture_tree.column("node_name", width=300, anchor=tk.W)
        self.capture_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tree_scroll = ttk.Scrollbar(
            discover_frame, orient=tk.VERTICAL,
            command=self.capture_tree.yview,
        )
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.capture_tree.configure(yscrollcommand=tree_scroll.set)
        self.capture_tree.bind(
            "<<TreeviewSelect>>", self._on_capture_tree_select
        )

        # Action buttons
        disc_buttons = ttk.Frame(outer)
        disc_buttons.pack(fill=tk.X, pady=(0, 10))
        ttk.Button(
            disc_buttons, text="Refresh",
            command=self._refresh_capture_tree, width=12,
        ).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(
            disc_buttons, text="Copy Node ID",
            command=self._copy_selected_capture_node_id, width=15,
        ).pack(side=tk.LEFT, padx=5)
        ttk.Button(
            disc_buttons, text="Copy Node Name",
            command=self._copy_selected_capture_node_name, width=15,
        ).pack(side=tk.LEFT, padx=5)

        # --- Route to output ---
        # Lets the user pick a hardware output for the selected app's
        # audio. Creates a module-loopback from the app's monitor source
        # to the chosen sink, so the audio is heard on that output.
        # Simpler than going to Manage tab to set up routing manually.
        route_frame = ttk.LabelFrame(
            outer, text="Route App Audio To Output", padding="10"
        )
        route_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(
            route_frame,
            text=(
                "Pick a hardware output below and click 'Route Here' — the "
                "selected app's audio will be heard on that output. Use the "
                "Manage tab for more complex routing (multiple outputs, etc)."
            ),
            wraplength=700,
        ).pack(anchor=tk.W, pady=(0, 5))
        # Filter checkbox — when checked, only hardware outputs are
        # shown. Unchecked (default) also includes virtual sinks for
        # OBS-style capture setups.
        self._capture_route_hardware_only_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            route_frame,
            text="Show hardware outputs only",
            variable=self._capture_route_hardware_only_var,
            command=self._populate_capture_route_options,
        ).pack(anchor=tk.W)
        route_row = ttk.Frame(route_frame)
        route_row.pack(fill=tk.X, pady=(5, 0))
        self.capture_route_var = tk.StringVar(value="")
        self.capture_route_combo = ttk.Combobox(
            route_row, textvariable=self.capture_route_var,
            state="readonly", values=(), width=50,
        )
        self.capture_route_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.capture_route_combo.bind(
            "<<ComboboxSelected>>", self._on_capture_route_combo_select
        )
        self.capture_route_button = ttk.Button(
            route_row, text="Route Here", width=12,
            command=self._on_capture_route_clicked, state="disabled",
        )
        self.capture_route_button.pack(side=tk.LEFT, padx=5)
        # Populate output list now (doesn't depend on selection)
        self._populate_capture_route_options()

        # Selected node display
        selected_frame = ttk.LabelFrame(
            outer, text="Selected Stream", padding="10"
        )
        selected_frame.pack(fill=tk.X, pady=(0, 10))
        self.capture_selected_var = tk.StringVar(value="(no stream selected)")
        ttk.Label(
            selected_frame,
            textvariable=self.capture_selected_var,
            font=("Courier", 10),
        ).pack(anchor=tk.W)

        # Initial discovery
        self._refresh_capture_tree()

    def _populate_capture_route_options(self):
        """Refresh the Route To combo with available output sinks.

        By default this includes both hardware outputs AND virtual
        sinks — users often want to route an app's audio to a virtual
        sink for OBS-style capture or to monitor an app without
        routing it through the speakers. The user can use the
        "Show hardware only" checkbox to filter the list if needed.
        """
        hardware = []
        try:
            hardware = PactlRunner.list_hardware_outputs()
        except Exception as e:
            self.add_output(f"Capture route: failed to list outputs: {e}")
        # Also list virtual sinks — useful for routing an app's audio
        # to a virtual sink as a monitoring target or as an OBS source
        virtual = []
        try:
            sinks = PactlRunner.list_sinks(logger=self.add_output)
            for s in sinks:
                name = s.get("name", "")
                if not name:
                    continue
                # Skip the easyeffects internal sinks (already virtual
                # but they're better grouped with hardware for this
                # purpose — they ARE the user's actual audio output
                # path on systems with EasyEffects running)
                if name in hardware:
                    continue
                # Filter by user setting
                if self._capture_route_hardware_only_var.get():
                    continue
                # Mark virtual sinks with [virtual] suffix for clarity
                virtual.append(f"{name}  [virtual]")
        except Exception as e:
            self.add_output(f"Capture route: failed to list virtual sinks: {e}")
        # Hardware first (the user's actual speakers/headphones),
        # then virtual (for monitoring/OBS capture)
        all_outputs = hardware + virtual
        self.capture_route_combo.config(values=all_outputs)
        if all_outputs:
            self.capture_route_var.set(all_outputs[0])
        else:
            self.capture_route_var.set("")
        self._capture_route_all_outputs = all_outputs  # cache for sanitization

    def _on_capture_route_combo_select(self, _event=None):
        """Sanitize combo selection — strip [virtual] marker when reading."""
        sel = self.capture_route_var.get()
        if sel.endswith("  [virtual]"):
            self.capture_route_var.set(sel.replace("  [virtual]", ""))

    def _on_capture_route_refresh_outputs(self):
        """Refresh the output dropdown (called when checkbox toggles)."""
        self._populate_capture_route_options()

    def _on_capture_route_clicked(self):
        """Route the selected app's audio to the chosen hardware output.

        We need the app's monitor source to create a loopback. The
        Stream/Output/Audio node itself isn't routable directly — we
        need to look up the .monitor source for the sink or use the
        node's monitor source. Since the discovery gives us Stream
        nodes (apps producing audio), the audio is going to a real
        sink already; we need to either:
        1. Use the app's current sink's monitor source and create a
           loopback to the chosen output (this REDIRECTS the audio).
        2. Use pw-link to manipulate the graph (more invasive).

        The cleanest UX is approach #1: find what sink the app's
        Stream/Output/Audio node is connected to, then loopback
        that sink's monitor to the chosen output. This duplicates
        the audio rather than replacing it (no audio loss).
        """
        sel = self.capture_tree.selection()
        if not sel:
            messagebox.showinfo("Route App Audio", "Select a stream first.")
            return
        target = self.capture_route_var.get()
        # Strip the [virtual] marker if present
        if target.endswith("  [virtual]"):
            target = target.replace("  [virtual]", "")
        if not target:
            messagebox.showinfo(
                "Route App Audio",
                "No output selected. Pick one from the dropdown.",
            )
            return
        # Find the sink that the selected Stream/Output/Audio node is
        # connected to. discover_app_audio_nodes already populated
        # sink_name from pw-dump Links, so we just read it from the
        # selected row's stored metadata.
        values = self.capture_tree.item(sel[0])["values"]
        if not values:
            return
        pw_id, app_str, cls, name = values
        # Look up the sink_name from our parallel data structure.
        # self._capture_nodes is updated whenever _refresh_capture_tree
        # rebuilds the treeview, so the indices match.
        sink_name = None
        if hasattr(self, "_capture_nodes") and self._capture_nodes:
            try:
                idx = int(self.capture_tree.index(sel[0]))
                if 0 <= idx < len(self._capture_nodes):
                    sink_name = self._capture_nodes[idx].get("sink_name")
            except (ValueError, tk.TclError):
                pass
        if not sink_name:
            messagebox.showerror(
                "Route failed",
                f"Could not find the sink that app '{app_str}' is using. "
                "Try refreshing the list, or use the Manage tab to route "
                "manually.",
            )
            return
        monitor = PactlRunner.monitor_source_for(sink_name)
        if not monitor:
            messagebox.showerror(
                "Route failed",
                f"Could not find monitor source for sink '{sink_name}'.",
            )
            return
        # Check for existing loopback from this monitor to target
        existing = PactlRunner.list_loopbacks()
        for lb in existing:
            if lb.get("source") == monitor and lb.get("sink") == target:
                messagebox.showinfo(
                    "Already routed",
                    f"'{app_str}' is already routed to '{target}'.",
                )
                return
        lb_id = PactlRunner.create_loopback(
            sink_name, target, latency_msec=self.get_loopback_latency_ms(),
            logger=self.add_output,
        )
        if lb_id:
            self.add_output(
                f"Routed '{app_str}' (via {sink_name}) → '{target}' "
                f"(loopback #{lb_id})"
            )
            self.refresh_all_views()
        else:
            messagebox.showerror(
                "Route failed", f"Could not create loopback to '{target}'."
            )

    def _refresh_capture_tree(self):
        """Re-scan for current app audio streams and rebuild the tree."""
        for item in self.capture_tree.get_children():
            self.capture_tree.delete(item)
        try:
            nodes = discover_app_audio_nodes(logger=self.add_output)
        except Exception as e:
            messagebox.showerror(
                "Discovery error", f"Failed to enumerate nodes: {e}"
            )
            return
        # Cache the discovery results so the route button can look
        # up the sink_name for the currently selected row.
        self._capture_nodes = nodes
        for n in nodes:
            self.capture_tree.insert(
                "", tk.END,
                values=(
                    n["id"],
                    f"{n['application_name']} (pid {n['pid'] or '?'})",
                    n["media_class"],
                    n["node_name"],
                ),
            )
        if not nodes:
            self.capture_selected_var.set(
                "(no app audio streams running — start playing audio in "
                "an app, then hit Refresh)"
            )
        self.add_output(
            f"App Audio Discovery: {len(nodes)} active stream(s)"
        )

    def _on_capture_tree_select(self, _event=None):
        sel = self.capture_tree.selection()
        if not sel:
            self.capture_selected_var.set("(no stream selected)")
            self.capture_route_button.config(state="disabled")
            return
        values = self.capture_tree.item(sel[0])["values"]
        if not values:
            self.capture_route_button.config(state="disabled")
            return
        pw_id, app, cls, name = values
        self.capture_selected_var.set(
            f"PW ID: {pw_id}    Class: {cls}    Name: {name}"
        )
        # Only enable Route Here for output streams (apps producing audio)
        if cls == "Stream/Output/Audio":
            self.capture_route_button.config(state="normal")
        else:
            self.capture_route_button.config(state="disabled")

    def _copy_selected_capture_node_id(self):
        sel = self.capture_tree.selection()
        if not sel:
            messagebox.showinfo("Copy Node ID", "Select a stream first.")
            return
        pw_id = self.capture_tree.item(sel[0])["values"][0]
        self._copy_to_clipboard(str(pw_id), label=f"Node ID {pw_id}")

    def _copy_selected_capture_node_name(self):
        sel = self.capture_tree.selection()
        if not sel:
            messagebox.showinfo("Copy Node Name", "Select a stream first.")
            return
        name = self.capture_tree.item(sel[0])["values"][3]
        self._copy_to_clipboard(str(name), label=f"Node name {name}")


    def setup_diagnostics_tab(self):
        """Set up the Diagnostics tab (Phase 7).

        Three sub-sections:
        1. Health Summary — top-level status, warnings, errors
        2. Sample Rate Audit — are all audio nodes using the same rate?
        3. Graph Snapshot — pw-top-style live table of nodes/links/clients

        Refresh button takes a fresh snapshot. No auto-refresh (would
        be a separate feature; current code is on-demand to keep things
        simple and not interfere with the user's interaction).
        """
        outer = self._setup_scrollable_tab(self.diagnostics_tab, padding="10")

        # Top: refresh + status
        top = ttk.Frame(outer)
        top.pack(fill=tk.X, pady=(0, 10))
        ttk.Button(
            top, text="Refresh Snapshot",
            command=self._refresh_diagnostics, width=18,
        ).pack(side=tk.LEFT)
        if not pw_dump_available():
            ttk.Label(
                top,
                text="pw-dump not found — install pipewire-tools to enable diagnostics",
                foreground="red",
            ).pack(side=tk.LEFT, padx=(10, 0))

        # 1. Health Summary
        health_frame = ttk.LabelFrame(
            outer, text="Health Summary", padding="10"
        )
        health_frame.pack(fill=tk.X, pady=(0, 10))
        self.diag_health_var = tk.StringVar(value="(refresh to load)")
        self.diag_health_label = ttk.Label(
            health_frame, textvariable=self.diag_health_var,
            font=("", 11, "bold"),
        )
        self.diag_health_label.pack(anchor=tk.W)
        self.diag_warnings_var = tk.StringVar(value="")
        ttk.Label(
            health_frame, textvariable=self.diag_warnings_var,
            foreground="#996600", wraplength=800,
        ).pack(anchor=tk.W, pady=(5, 0))
        self.diag_errors_var = tk.StringVar(value="")
        ttk.Label(
            health_frame, textvariable=self.diag_errors_var,
            foreground="#cc0000", wraplength=800,
        ).pack(anchor=tk.W)

        # 2. Sample Rate Audit
        rate_frame = ttk.LabelFrame(
            outer, text="Sample Rate Audit", padding="10"
        )
        rate_frame.pack(fill=tk.X, pady=(0, 10))
        self.diag_rate_var = tk.StringVar(value="(refresh to load)")
        ttk.Label(
            rate_frame, textvariable=self.diag_rate_var,
            font=("", 10),
        ).pack(anchor=tk.W)

        # 3. Graph Snapshot — pw-top-style table
        snapshot_frame = ttk.LabelFrame(
            outer, text="Graph Snapshot", padding="10"
        )
        snapshot_frame.pack(fill=tk.BOTH, expand=True)

        # Counts row
        self.diag_counts_var = tk.StringVar(value="")
        ttk.Label(
            snapshot_frame, textvariable=self.diag_counts_var,
            font=("", 10),
        ).pack(anchor=tk.W, pady=(0, 5))

        # The node table (pw-top-style)
        ttk.Label(
            snapshot_frame, text="Audio Nodes:",
            font=("", 10, "bold"),
        ).pack(anchor=tk.W, pady=(5, 2))

        cols = ("id", "name", "class", "rate", "state", "app")
        self.diag_nodes_tree = ttk.Treeview(
            snapshot_frame, columns=cols, show="headings", height=8
        )
        for col, heading, width in [
            ("id", "PW ID", 70),
            ("name", "Node Name", 280),
            ("class", "Media Class", 180),
            ("rate", "Rate", 70),
            ("state", "State", 90),
            ("app", "App", 180),
        ]:
            self.diag_nodes_tree.heading(col, text=heading)
            self.diag_nodes_tree.column(col, width=width, anchor=tk.W)
        tree_scroll = ttk.Scrollbar(
            snapshot_frame, orient=tk.VERTICAL,
            command=self.diag_nodes_tree.yview,
        )
        self.diag_nodes_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.diag_nodes_tree.configure(yscrollcommand=tree_scroll.set)

        # Auto-refresh on tab open (only once per visit)
        self._diag_refreshed = False
        self.tab_control.bind(
            "<<NotebookTabChanged>>", self._on_diag_tab_changed, add="+"
        )

    def _on_diag_tab_changed(self, event=None):
        """When user clicks the Diagnostics tab, take an initial snapshot
        if we haven't already. After that, refresh is manual."""
        current = self.tab_control.select()
        for tab_id in self.tab_control.tabs():
            if tab_id == current and tab_id == str(self.diagnostics_tab):
                if not self._diag_refreshed:
                    self._refresh_diagnostics()
                    self._diag_refreshed = True
                break

    def _refresh_diagnostics(self):
        """Take a fresh snapshot of the PipeWire graph and update all
        the diagnostic panels."""
        if not pw_dump_available():
            self.diag_health_var.set("pw-dump not installed")
            return
        try:
            snap = get_graph_snapshot()
        except Exception as e:
            self.diag_health_var.set(f"Snapshot failed: {e}")
            self.add_output(f"Diagnostics: snapshot failed: {e}")
            return
        # Health summary
        health = health_summary(snap)
        status = health["status"]
        if status == "healthy":
            self.diag_health_var.set(
                f"✓ HEALTHY — {health['counts']['nodes']} nodes, "
                f"{health['counts']['links']} links, "
                f"{health['counts']['clients']} clients"
            )
            self.diag_health_label.config(foreground="#006600")
        elif status == "warnings":
            warn_count = len(health["warnings"])
            self.diag_health_var.set(
                f"⚠ {warn_count} WARNING{'S' if warn_count != 1 else ''} — "
                f"see details below"
            )
            self.diag_health_label.config(foreground="#996600")
        else:
            err_count = len(health["errors"])
            self.diag_health_var.set(
                f"✗ {err_count} ERROR{'S' if err_count != 1 else ''} — "
                f"see details below"
            )
            self.diag_health_label.config(foreground="#cc0000")
        # Warnings / errors
        self.diag_warnings_var.set(
            "\n".join("⚠ " + w for w in health["warnings"]) if health["warnings"] else ""
        )
        self.diag_errors_var.set(
            "\n".join("✗ " + e for e in health["errors"]) if health["errors"] else ""
        )
        # Sample rate audit
        audit = sample_rate_audit(snap)
        if audit["audio_node_count"] == 0:
            self.diag_rate_var.set("No audio nodes detected.")
        elif audit["is_consistent"]:
            self.diag_rate_var.set(
                f"✓ All {audit['audio_node_count']} audio nodes run at "
                f"{audit['recommended_rate']} Hz. "
                f"Rates seen: {audit['rates_seen']}"
            )
        else:
            self.diag_rate_var.set(
                f"⚠ Rate mismatch — {audit['audio_node_count']} audio nodes, "
                f"recommended: {audit['recommended_rate']} Hz. "
                f"Rates seen: {audit['rates_seen']}. "
                f"Mismatched: {len(audit['mismatched_nodes'])}"
            )
        # Counts summary
        c = health["counts"]
        self.diag_counts_var.set(
            f"Clients: {c['clients']}    Modules: {c['modules']}    "
            f"Devices: {c['devices']}    Ports: {c['ports']}    "
            f"Links: {c['links']}    Nodes: {c['nodes']}"
        )
        # Populate node tree (audio nodes only)
        for item in self.diag_nodes_tree.get_children():
            self.diag_nodes_tree.delete(item)
        for node in snap["nodes"]:
            if not node.get("media_class", "").startswith("Audio/"):
                continue
            self.diag_nodes_tree.insert(
                "", tk.END,
                values=(
                    node["id"],
                    node["name"],
                    node["media_class"],
                    f"{node.get('rate', '?')} Hz" if node.get("rate") else "?",
                    node["state"],
                    node.get("app_name", ""),
                ),
            )
        self.add_output(
            f"Diagnostics: snapshot complete "
            f"({health['counts']['nodes']} nodes, {health['counts']['links']} links)"
        )

    def setup_output_tab(self):
        """Set up the Output tab content."""
        frame = ttk.Frame(self.output_tab, padding="10")
        frame.pack(fill=tk.BOTH, expand=True)


        # Output text
        self.output_text = tk.Text(frame, wrap=tk.WORD, height=20)
        self.output_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Scrollbar
        scrollbar = ttk.Scrollbar(
            frame,
            orient=tk.VERTICAL,
            command=self.output_text.yview
        )
        self.output_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Clear button
        clear_button = ttk.Button(
            self.output_tab,
            text="Clear Output",
            command=self.clear_output
        )
        clear_button.pack(pady=10)

        # Add initial message
        self.add_output("PulseAudio Control GUI started. Ready for commands.")

    def add_output(self, text: str):
        """
        Add text to the output window with timestamp and formatting.

        Args:
            text: The text to add
        """
        if self.output_text:
            import datetime

            # Add timestamp for command execution
            if text.startswith("$ "):
                # Command execution - add separator and timestamp
                timestamp = datetime.datetime.now().strftime("%H:%M:%S")
                self.output_text.insert(tk.END, f"\n[{timestamp}] {text}\n")
            elif text.startswith("Command ") or text.startswith("Error:") or text.startswith("Output:"):
                # Command result - indent slightly
                self.output_text.insert(tk.END, f"  → {text}\n")
            else:
                # Regular application message
                self.output_text.insert(tk.END, text + "\n")

            self.output_text.see(tk.END)  # Scroll to the end

    def clear_output(self):
        """Clear the output window."""
        if self.output_text:
            self.output_text.delete(1.0, tk.END)
            self.add_output("Output cleared.")

    def on_device_type_changed(self):
        """Update button label, command preview format, and status when device type radio changes."""
        device_type = self.device_type_var.get()
        if device_type == "sink":
            button_text = "Create Sink-Only Device"
            media_class = "Audio/Sink"
        elif device_type == "source":
            button_text = "Create Source-Only Device"
            media_class = "Audio/Source"
        else:  # "both"
            button_text = "Create Both (Duplex) Sink"
            media_class = "Audio/Duplex"

        self.create_button.config(text=button_text)
        self.command_format_label.config(
            text=f"Format: pactl load-module module-null-sink "
            f"media.class={media_class} sink_name=<name> channels=<channels>"
        )
        self.update_command_preview()

    def create_device(self):
        """Create a new virtual audio device of the type selected in the radio."""
        device_type = self.device_type_var.get()
        if device_type == "sink":
            return self._create_device_of_type("sink")
        elif device_type == "source":
            return self._create_device_of_type("source")
        else:  # "both"
            return self._create_device_of_type("both")

    def _create_device_of_type(self, device_type):
        """Shared logic: gather inputs from the form, call the right PactlRunner method."""
        raw_name = self.sink_name_var.get().strip()
        description = self.sink_desc_var.get().strip()

        # Handle auto-naming
        if not raw_name or raw_name.endswith(" (auto)"):
            selected_preset = self.audio_preset_var.get()
            preset_configs = {
                "Stereo": "stereo",
                "Mono": "mono",
                "5.1 Surround": "surround51",
                "7.1 Surround": "surround71",
                "Custom": "custom",
            }
            base_name = preset_configs.get(selected_preset, selected_preset.lower())
            name = self._get_available_name(base_name)
        else:
            is_valid, cleaned_name, error_msg = self._validate_sink_name(raw_name)
            if not is_valid:
                response = messagebox.askyesno(
                    "Invalid Sink Name",
                    f"{error_msg}\n\nWould you like to use the suggested name instead?",
                    icon="warning",
                )
                if response and cleaned_name:
                    name = cleaned_name
                    self.sink_name_var.set(name)
                    self.user_has_custom_name = True
                    self.sink_name_entry.config(foreground="black")
                else:
                    return
            else:
                name = cleaned_name

        try:
            channels = int(self.channels_var.get())
        except ValueError:
            channels = 2

        if not description:
            selected_preset = self.audio_preset_var.get()
            preset_descriptions = {
                "Stereo": "Stereo Virtual Device",
                "Mono": "Mono Virtual Device",
                "5.1 Surround": "5.1 Surround Virtual Device",
                "7.1 Surround": "7.1 Surround Virtual Device",
                "Custom": "Custom Virtual Device",
            }
            type_label = {"sink": "Sink", "source": "Source", "both": "Duplex"}[device_type]
            description = preset_descriptions.get(
                selected_preset, f"{name} Virtual {type_label} Device"
            )

        advanced_options = {}
        if hasattr(self, "show_advanced_var") and self.show_advanced_var.get():
            rate = self.rate_var.get().strip()
            if rate and rate != "44100":
                try:
                    advanced_options["rate"] = int(rate)
                except ValueError:
                    messagebox.showerror("Error", f"Invalid sample rate: {rate}")
                    return
            format_desc = self.format_var.get().strip()
            if format_desc and hasattr(self, "format_mappings"):
                actual_format = self.format_mappings.get(format_desc, format_desc)
                if actual_format and actual_format != "s16le":
                    advanced_options["format"] = actual_format
            channel_map = self.channel_map_var.get().strip()
            if channel_map:
                advanced_options["channel_map"] = channel_map
            properties = self.properties_var.get().strip()
            if properties:
                advanced_options["sink_properties"] = properties

        type_label = {"sink": "sink-only", "source": "source-only", "both": "duplex"}[device_type]
        self.status_var.set(f"Creating {type_label} device '{name}'...")
        self.root.update()

        if device_type == "sink":
            success = PactlRunner.create_sink_only(
                name, description, channels, logger=self.add_output, **advanced_options
            )
        elif device_type == "source":
            success = PactlRunner.create_source_only(
                name, description, channels, logger=self.add_output, **advanced_options
            )
        else:
            success = PactlRunner.create_duplex_sink(
                name, description, channels, logger=self.add_output, **advanced_options
            )

        if success:
            self.add_output(f"Created {type_label} device: {name} ({description})")
            self.status_var.set(f"Created {type_label} device: {name}")
            self.refresh_all_views()
            # For sinks: offer to route to the default output. This is
            # the common case ("I just created a virtual game sink,
            # now I want to hear it on my headphones") and removes the
            # friction of having to manually create a loopback on the
            # Manage tab.
            if device_type in ("sink", "both"):
                self._offer_route_to_default(name)
        else:
            self.add_output(f"Failed to create {type_label} device: {name}")

    def _offer_route_to_default(self, sink_name: str):
        """After creating a sink, ask the user if they want to route
        it to the current default output. This is the most common
        next step ("I want to hear this on my speakers/headphones")
        and exposing it as a one-click confirmation is much better
        than expecting the user to find the routing panel."""
        default = PactlRunner.get_default_sink()
        if not default:
            return  # No default sink? Nothing useful to offer.
        # Don't offer if there's already a loopback from this sink
        monitor = PactlRunner.monitor_source_for(sink_name)
        existing = PactlRunner.list_loopbacks()
        if any(lb.get("source") == monitor for lb in existing):
            return
        if messagebox.askyesno(
            "Route to default output?",
            f"'{sink_name}' is created. Route it to your default "
            f"output ('{default}') so you can hear it now?\n\n"
            f"(You can change routing at any time on the Manage tab.)",
        ):
            PactlRunner.create_loopback(
                sink_name, default,
                latency_msec=self.get_loopback_latency_ms(),
                logger=self.add_output,
            )
            self.refresh_all_views()
            self.add_output(
                f"Routed '{sink_name}' → '{default}'"
            )

    def create_duplex_sink(self):
        """Backward-compatible alias. New code should call create_device()."""
        self.device_type_var.set("both")
        return self._create_device_of_type("both")

    def refresh_all_views(self):
        """Refresh all views with hierarchical relationships."""
        self.status_var.set("Refreshing all components...")
        self.root.update()

        # Get all data
        modules = PactlRunner.list_modules(logger=self.add_output)
        sinks = PactlRunner.list_sinks(logger=self.add_output)
        sources = PactlRunner.list_sources(logger=self.add_output)

        # Clear existing items
        for item in self.unified_tree.get_children():
            self.unified_tree.delete(item)

        # Build relationship mapping
        device_map = self._map_modules_to_devices(modules, sinks, sources)

        # Populate unified tree view with categorized device grouping
        self._populate_unified_tree(device_map, modules, sinks, sources)

        # Reset the details label
        self.update_details_display("Select an item to see details")

        # Update status
        self.status_var.set(f"Found {len(modules)} modules, {len(sinks)} sinks, {len(sources)} sources")
        self.add_output(f"Refreshed all components: {len(modules)} modules, {len(sinks)} sinks, {len(sources)} sources")

    def _map_modules_to_devices(self, modules, sinks, sources):
        """
        Create a mapping of which modules created which sinks and sources.
        Groups by device name (sink_name) rather than module ID for better organization.

        Returns: Dictionary mapping device names to lists of related modules, sinks and sources
        """
        device_map = {}

        # First, identify all device names from null-sink modules
        for module in modules:
            module_name = module.get('name', '')
            module_args = module.get('argument', '')

            if 'null-sink' in module_name:
                # Extract the sink_name from module arguments
                # Handle various formats and special characters
                import re
                sink_match = re.search(r'sink_name=([a-zA-Z0-9_.-]+)', module_args)
                if sink_match:
                    device_name = sink_match.group(1)
                    if device_name not in device_map:
                        device_map[device_name] = {'modules': [], 'sinks': [], 'sources': []}
                    device_map[device_name]['modules'].append(module)

        # Map sinks to device names
        for sink in sinks:
            sink_name = sink.get('name', '')
            # Check if this sink belongs to any of our tracked devices
            if sink_name in device_map:
                device_map[sink_name]['sinks'].append(sink)

        # Map sources to device names
        for source in sources:
            source_name = source.get('name', '')

            # Check for monitor sources (these are created automatically for sinks)
            if ".monitor" in source_name:
                # This is likely a sink's monitor source
                base_name = source_name.replace(".monitor", "")
                if base_name in device_map:
                    device_map[base_name]['sources'].append(source)
            else:
                # Direct source match
                if source_name in device_map:
                    device_map[source_name]['sources'].append(source)

        return device_map

    def _populate_unified_tree(self, device_map, modules, sinks, sources):
        """Populate the unified tree view with categorized device grouping."""
        # Create main category groups
        virtual_group = self.unified_tree.insert(
            "", "end",
            text="Virtual Devices",
            values=("", "category", ""),
            tags=("category",)
        )

        # Create system modules group if needed
        system_group = None
        if self.show_system_var.get():
            system_group = self.unified_tree.insert(
                "", "end",
                text="System Modules",
                values=("", "category", ""),
                tags=("category",)
            )

        # Track what we've added to avoid duplicates
        added_modules = set()
        added_sinks = set()
        added_sources = set()

        # Process virtual devices (unchanged - working well)
        for device_name in sorted(device_map.keys()):
            device_data = device_map[device_name]

            # Create device group
            device_item = self.unified_tree.insert(
                virtual_group, "end",
                text=f"Virtual Device: {device_name}",
                values=("", "device_group", device_name),
                tags=("category",)
            )

            # Add modules for this device
            for module in device_data['modules']:
                module_id = module.get('id', '')
                module_name = module.get('name', '')

                self.unified_tree.insert(
                    device_item, "end",
                    text=f"Module: {module_name}",
                    values=(module_id, "module", module_name),
                    tags=("module",)
                )
                added_modules.add(module_id)

            # Add sinks for this device
            for sink in device_data['sinks']:
                sink_id = sink.get('id', '')
                sink_name = sink.get('name', '')
                sink_desc = sink.get('description', sink_name)

                self.unified_tree.insert(
                    device_item, "end",
                    text=f"Output: {sink_desc}",
                    values=(sink_id, "sink", sink_name),
                    tags=("sink",)
                )
                added_sinks.add(sink_id)

            # Add sources for this device
            for source in device_data['sources']:
                source_id = source.get('id', '')
                source_name = source.get('name', '')
                source_desc = source.get('description', source_name)

                self.unified_tree.insert(
                    device_item, "end",
                    text=f"Input: {source_desc}",
                    values=(source_id, "source", source_name),
                    tags=("source",)
                )
                added_sources.add(source_id)

        # Process hardware devices with new categorization
        remaining_modules = [m for m in modules if m.get('id', '') not in added_modules]
        remaining_sinks = [s for s in sinks if s.get('id', '') not in added_sinks]
        remaining_sources = [s for s in sources if s.get('id', '') not in added_sources]

        # Categorize hardware devices
        hardware_categories = self._categorize_hardware_devices(
            remaining_modules, remaining_sinks, remaining_sources
        )

        # Create hardware category groups directly at top level
        category_names = {
            'builtin': '🔌 Built-in Audio',
            'usb': '🎧 USB Audio',
            'bluetooth': '📡 Bluetooth Audio',
            'hdmi': '📺 HDMI/DisplayPort'
        }

        for category, display_name in category_names.items():
            devices = hardware_categories.get(category, [])

            # Only create category if it has devices
            if devices:
                category_item = self.unified_tree.insert(
                    "", "end",  # Insert directly at root level
                    text=display_name,
                    values=("", "hardware_category", category),
                    tags=("category",)
                )

                # Add devices to this category
                for device in devices:
                    self._add_hardware_device_to_tree(device, category_item, added_modules, added_sinks, added_sources)

        # Add system modules if showing them
        if system_group:
            system_modules = [m for m in remaining_modules if m.get('id', '') not in added_modules]
            for module in system_modules:
                self._add_standalone_module_to_tree(module, system_group, added_modules)

        # Auto-expand the virtual devices by default
        self.unified_tree.item(virtual_group, open=True)

    def _add_hardware_device_to_tree(self, device_entry, parent_item, added_modules, added_sinks, added_sources):
        """Add a hardware device entry to the tree."""
        device_type = device_entry.get('type')

        if device_type == 'hardware_device_group':
            # New grouped hardware device with proper device grouping
            device_info = device_entry.get('device_info')
            modules = device_entry.get('modules', [])
            sinks = device_entry.get('sinks', [])
            sources = device_entry.get('sources', [])

            device_name = device_info['device_name']

            # Create device group
            device_item = self.unified_tree.insert(
                parent_item, "end",
                text=device_name,
                values=("", "hardware_device_group", device_name),
                tags=("category",)
            )

            # Add modules
            for module in modules:
                module_id = module.get('id', '')
                module_name = module.get('name', '')
                self.unified_tree.insert(
                    device_item, "end",
                    text=f"Module: {module_name}",
                    values=(module_id, "module", module_name),
                    tags=("module",)
                )
                added_modules.add(module_id)

            # Add sinks
            for sink in sinks:
                sink_id = sink.get('id', '')
                sink_name = sink.get('name', '')
                sink_desc = sink.get('description', sink_name)
                self.unified_tree.insert(
                    device_item, "end",
                    text=f"Output: {sink_desc}",
                    values=(sink_id, "sink", sink_name),
                    tags=("sink",)
                )
                added_sinks.add(sink_id)

            # Add sources (including monitors if checkbox enabled)
            for source in sources:
                source_id = source.get('id', '')
                source_name = source.get('name', '')
                source_desc = source.get('description', source_name)

                # Check if it's a monitor source
                if '.monitor' in source_name:
                    if self.show_monitors_var.get():
                        self.unified_tree.insert(
                            device_item, "end",
                            text=f"Monitor: {source_desc}",
                            values=(source_id, "source", source_name),
                            tags=("source",)
                        )
                else:
                    self.unified_tree.insert(
                        device_item, "end",
                        text=f"Input: {source_desc}",
                        values=(source_id, "source", source_name),
                        tags=("source",)
                    )
                added_sources.add(source_id)

        elif device_type == 'hardware_device':
            # Legacy hardware device handling (for backward compatibility)
            module = device_entry.get('module')
            sinks = device_entry.get('sinks', [])
            sources = device_entry.get('sources', [])

            if module:
                # Create device name from module or first sink/source
                device_name = self._extract_device_name(
                    module.get('name', ''),
                    module.get('argument', '')
                )

                # If we have sinks/sources, use their description for better naming
                if sinks:
                    device_name = sinks[0].get('description', device_name)
                elif sources:
                    device_name = sources[0].get('description', device_name)

                # Create device group
                device_item = self.unified_tree.insert(
                    parent_item, "end",
                    text=device_name,
                    values=("", "hardware_device_group", device_name),
                    tags=("category",)
                )

                # Add module
                module_id = module.get('id', '')
                module_name = module.get('name', '')
                self.unified_tree.insert(
                    device_item, "end",
                    text=f"Module: {module_name}",
                    values=(module_id, "module", module_name),
                    tags=("module",)
                )
                added_modules.add(module_id)

                # Add sinks
                for sink in sinks:
                    sink_id = sink.get('id', '')
                    sink_name = sink.get('name', '')
                    sink_desc = sink.get('description', sink_name)
                    self.unified_tree.insert(
                        device_item, "end",
                        text=f"Output: {sink_desc}",
                        values=(sink_id, "sink", sink_name),
                        tags=("sink",)
                    )
                    added_sinks.add(sink_id)

                # Add sources (including monitors if checkbox enabled)
                for source in sources:
                    source_id = source.get('id', '')
                    source_name = source.get('name', '')
                    source_desc = source.get('description', source_name)

                    # Check if it's a monitor source
                    if '.monitor' in source_name:
                        if self.show_monitors_var.get():
                            self.unified_tree.insert(
                                device_item, "end",
                                text=f"Monitor: {source_desc}",
                                values=(source_id, "source", source_name),
                                tags=("source",)
                            )
                    else:
                        self.unified_tree.insert(
                            device_item, "end",
                            text=f"Input: {source_desc}",
                            values=(source_id, "source", source_name),
                            tags=("source",)
                        )
                    added_sources.add(source_id)

        elif device_type == 'orphaned_sink':
            # Standalone sink
            sink = device_entry.get('sink')
            if sink:
                sink_id = sink.get('id', '')
                sink_name = sink.get('name', '')
                sink_desc = sink.get('description', sink_name)
                self.unified_tree.insert(
                    parent_item, "end",
                    text=f"Output: {sink_desc}",
                    values=(sink_id, "sink", sink_name),
                    tags=("sink",)
                )
                added_sinks.add(sink_id)

        elif device_type == 'orphaned_source':
            # Standalone source
            source = device_entry.get('source')
            if source:
                source_id = source.get('id', '')
                source_name = source.get('name', '')
                source_desc = source.get('description', source_name)

                if '.monitor' in source_name:
                    self.unified_tree.insert(
                        parent_item, "end",
                        text=f"Monitor: {source_desc}",
                        values=(source_id, "source", source_name),
                        tags=("source",)
                    )
                else:
                    self.unified_tree.insert(
                        parent_item, "end",
                        text=f"Input: {source_desc}",
                        values=(source_id, "source", source_name),
                        tags=("source",)
                    )
                added_sources.add(source_id)

    def _add_standalone_module_to_tree(self, module, parent_group, added_modules):
        """Helper method to add a standalone module to the tree."""
        module_id = module.get('id', '')
        module_name = module.get('name', '')

        # Extract device name from module
        device_name = self._extract_device_name(module_name, module.get('argument', ''))

        # Create module node
        self.unified_tree.insert(
            parent_group, "end",
            text=device_name,
            values=(module_id, "module", module_name),
            tags=("module",)
        )
        added_modules.add(module_id)

    def _add_orphaned_devices(self, sinks, sources, added_sinks, added_sources, virtual_group, hardware_group):
        """Add sinks and sources that aren't associated with any visible module."""
        # Process orphaned sinks
        orphaned_sinks = [s for s in sinks if s.get('id', '') not in added_sinks]
        for sink in orphaned_sinks:
            sink_id = sink.get('id', '')
            sink_name = sink.get('name', '')
            sink_desc = sink.get('description', sink_name)

            # Determine appropriate category
            parent_group = hardware_group  # Default
            if 'null' in sink_name or 'virtual' in sink_name.lower():
                parent_group = virtual_group

            self.unified_tree.insert(
                parent_group, "end",
                text=f"Output: {sink_desc}",
                values=(sink_id, "sink", sink_name),
                tags=("sink",)
            )

        # Process orphaned sources
        orphaned_sources = [s for s in sources if s.get('id', '') not in added_sources]
        for source in orphaned_sources:
            source_id = source.get('id', '')
            source_name = source.get('name', '')
            source_desc = source.get('description', source_name)

            # Determine appropriate category
            parent_group = hardware_group  # Default to hardware
            if 'null' in source_name or 'virtual' in source_name.lower() or '.monitor' in source_name:
                parent_group = virtual_group

            self.unified_tree.insert(
                parent_group, "end",
                text=f"Input: {source_desc}",
                values=(source_id, "source", source_name),
                tags=("source",)
            )

    def on_unified_tree_select(self, event):
        """Handle selection event from the unified tree view."""
        selected = self.unified_tree.selection()
        if not selected:
            self.update_details_display("Select an item to see details")
            self.unload_button.config(state="disabled")
            self.update_routing_panel(None)
            self.update_device_controls_panel(None)
            return

        # Get the selected item
        item = self.unified_tree.item(selected[0])
        values = item['values']

        if not values or len(values) < 2:
            self.update_details_display("Select an item to see details")
            self.unload_button.config(state="disabled")
            self.update_routing_panel(None)
            self.update_device_controls_panel(None)
            return

        entity_id, entity_type, entity_name = values

        # Generate technical specifications for the selected item
        details = self._generate_detailed_info(entity_id, entity_type, entity_name, selected[0])

        # Enable/disable unload button based on selection type
        if entity_type == "module" and entity_id:
            self.unload_button.config(state="normal")
        else:
            self.unload_button.config(state="disabled")

        # Update routing panel — only meaningful for virtual sinks
        if entity_type == "sink":
            self.update_routing_panel(entity_name)
            # Also update the prominent Quick Route panel at the top
            # of the tab so users see what to do without scrolling.
            self._quick_route_sink_name = entity_name
            self._refresh_quick_route_panel()
        else:
            self.update_routing_panel(None)
            self._quick_route_sink_name = None
            self._refresh_quick_route_panel()

        # Update device controls panel — works for both sinks and sources
        # so the user can adjust a null-sink's playback volume OR a
        # null-source's capture volume from one place.
        if entity_type in ("sink", "source"):
            self.update_device_controls_panel(
                entity_name, is_source=(entity_type == "source")
            )
        else:
            self.update_device_controls_panel(None)

        self.update_details_display(details)

    # ------------------------------------------------------------------
    # Quick Route panel (prominent panel at the top of the Manage tab)
    # ------------------------------------------------------------------

    def _refresh_quick_route_outputs(self):
        """Re-list hardware outputs in the Quick Route checkbox listbox."""
        try:
            outputs = PactlRunner.list_hardware_outputs()
        except Exception as e:
            outputs = []
            self.add_output(f"Quick route: failed to list outputs: {e}")
        self._quick_route_outputs = outputs
        # Preserve any active selection across refresh
        prior = set(self.quick_route_listbox.curselection())
        self.quick_route_listbox.delete(0, tk.END)
        for i, name in enumerate(outputs):
            # Pre-check outputs that already have an active loopback
            # for the currently selected sink.
            checked = i in prior
            self.quick_route_listbox.insert(tk.END, name)
            if checked:
                self.quick_route_listbox.selection_set(i)
        self._refresh_quick_route_panel()

    def _refresh_quick_route_panel(self):
        """Update the Quick Route panel's status line based on selection."""
        sink = self._quick_route_sink_name
        if not sink:
            self.quick_route_status_var.set(
                "Select a virtual sink in the tree below, then check the "
                "outputs you want its audio to play through, then click "
                "'Apply Routing'."
            )
            return
        if not PactlRunner.is_null_sink(sink):
            self.quick_route_status_var.set(
                f"Selected: {sink} (hardware sink — routing not applicable)"
            )
            return
        # Virtual sink — show current loopbacks
        try:
            monitor = PactlRunner.monitor_source_for(sink)
            loopbacks = PactlRunner.list_loopbacks()
            current_sinks = sorted({
                lb["sink"] for lb in loopbacks
                if lb.get("source") == monitor
            })
        except Exception:
            current_sinks = []
        if current_sinks:
            self.quick_route_status_var.set(
                f"Routing '{sink}' to: {', '.join(current_sinks)}. "
                f"Check/uncheck outputs and click 'Apply Routing' to change."
            )
        else:
            self.quick_route_status_var.set(
                f"'{sink}' is not routed to any output. "
                f"Check one or more outputs and click 'Apply Routing'."
            )

    def _on_quick_route_apply(self):
        """Apply the routing for the currently selected sink to the
        checked outputs in the Quick Route listbox.

        Idempotent: routes that already exist are left alone; missing
        routes are added; checked-but-already-routed boxes are kept;
        unchecked loopbacks (when explicitly removed by the user) are
        left intact — use 'Stop All Routing' to clear them.
        """
        sink = self._quick_route_sink_name
        if not sink:
            messagebox.showinfo(
                "Apply Routing",
                "Select a virtual sink in the tree first.",
            )
            return
        if not PactlRunner.is_null_sink(sink):
            messagebox.showinfo(
                "Apply Routing",
                f"'{sink}' is a hardware sink — nothing to route.",
            )
            return
        sel = self.quick_route_listbox.curselection()
        if not sel:
            messagebox.showinfo(
                "Apply Routing",
                "Check at least one output in the list above.",
            )
            return
        target_outputs = [self._quick_route_outputs[i] for i in sel]
        monitor = PactlRunner.monitor_source_for(sink)
        if not monitor:
            messagebox.showerror(
                "Apply Routing", f"Could not find monitor for '{sink}'."
            )
            return
        # Pre-validate: all targets must exist as sinks
        sinks = {s["name"] for s in PactlRunner.list_sinks()}
        for t in target_outputs:
            if t not in sinks:
                messagebox.showerror(
                    "Apply Routing",
                    f"Output '{t}' does not exist anymore. Hit Refresh "
                    "and try again.",
                )
                return
        # Idempotent apply — only add missing loopbacks
        existing = {
            lb["sink"] for lb in PactlRunner.list_loopbacks()
            if lb.get("source") == monitor
        }
        added = []
        for t in target_outputs:
            if t in existing:
                continue
            mod = PactlRunner.create_loopback(
                monitor, t,
                latency_msec=self.get_loopback_latency_ms(),
                logger=self.add_output,
            )
            if mod:
                added.append(t)
        if added:
            self.add_output(
                f"Quick route: routed '{sink}' to {', '.join(added)}"
            )
        else:
            self.add_output(
                f"Quick route: '{sink}' already routes to all selected "
                f"outputs (no changes)"
            )
        self._refresh_quick_route_panel()
        self.refresh_all_views()

    def _on_quick_route_stop_all(self):
        """Stop all loopbacks for the currently selected sink."""
        sink = self._quick_route_sink_name
        if not sink:
            messagebox.showinfo(
                "Stop All Routing",
                "Select a virtual sink in the tree first.",
            )
            return
        if not PactlRunner.is_null_sink(sink):
            messagebox.showinfo(
                "Stop All Routing",
                f"'{sink}' is a hardware sink — nothing to stop.",
            )
            return
        monitor = PactlRunner.monitor_source_for(sink)
        if not monitor:
            return
        stopped = 0
        for lb in PactlRunner.list_loopbacks():
            if lb.get("source") == monitor:
                if PactlRunner.unload_loopback(lb["id"], logger=self.add_output):
                    stopped += 1
        self.add_output(
            f"Quick route: stopped {stopped} loopback(s) for '{sink}'"
        )
        self._refresh_quick_route_panel()
        self.refresh_all_views()

    # ------------------------------------------------------------------
    # Routing panel methods (Phase 2: loopback support)
    # ------------------------------------------------------------------

    def update_routing_panel(self, sink_name):
        """Populate the Routing panel for the selected sink (or reset it).

        Called from on_unified_tree_select. Behavior:
        - sink_name is None: reset the panel to placeholder state.
        - sink_name is a hardware sink: disable the panel (hardware sinks
          don't need loopback routing; they ARE the real outputs).
        - sink_name is a null-sink (virtual): show current routing status
          (if any), populate the output dropdown, enable Apply/Stop as
          appropriate.
        """
        # Default: reset everything
        self.routing_status_var.set("Select a virtual sink to route")
        self.routing_output_combo.config(values=(), state="disabled")
        self.route_button.config(state="disabled")
        self.stop_route_button.config(state="disabled")
        self._routing_sink_name = None

        if sink_name is None:
            return

        # Hardware sinks: panel isn't useful (you can't "route to" a sink)
        if not PactlRunner.is_null_sink(sink_name):
            self.routing_status_var.set(
                f"'{sink_name}' is a hardware sink — nothing to route"
            )
            return

        # It's a virtual sink — populate the panel
        self._routing_sink_name = sink_name
        monitor = PactlRunner.monitor_source_for(sink_name)
        loopbacks = PactlRunner.list_loopbacks()
        current = next(
            (lb for lb in loopbacks if lb.get("source") == monitor), None
        )

        # Refresh output dropdown
        outputs = PactlRunner.list_hardware_outputs()
        if not outputs:
            self.routing_status_var.set(
                f"No hardware output sinks available to route '{sink_name}' to"
            )
            return

        self.routing_output_combo.config(values=outputs, state="readonly")
        # Pre-select current target if there's an active loopback
        if current and current.get("sink") in outputs:
            self.routing_output_var.set(current["sink"])
            self.routing_status_var.set(
                f"Currently routing '{sink_name}' → '{current['sink']}' "
                f"(loopback module #{current['id']})"
            )
            self.route_button.config(state="normal")
            self.stop_route_button.config(state="normal")
        else:
            # No active loopback. Default-select the first output.
            self.routing_output_var.set(outputs[0])
            self.routing_status_var.set(
                f"'{sink_name}' is not currently routed"
            )
            self.route_button.config(state="normal")
            self.stop_route_button.config(state="disabled")

        # Populate the multi-output listbox and pre-check items that
        # already have an active loopback from this sink's monitor.
        # (There can be multiple loopbacks with the same source — the
        # user can already have created them by other means.)
        self.multi_routing_listbox.delete(0, tk.END)
        for out in outputs:
            self.multi_routing_listbox.insert(tk.END, out)
        # Find all loopbacks from this sink's monitor
        active_targets: set[str] = set()
        for lb in loopbacks:
            if lb.get("source") != monitor:
                continue
            sink = lb.get("sink")
            if sink:
                active_targets.add(sink)
        self._multi_routed_targets = sorted(active_targets)
        # Pre-check active targets in the listbox
        for idx, out in enumerate(outputs):
            if out in active_targets:
                self.multi_routing_listbox.selection_set(idx)
        # Enable/disable multi-routing buttons
        if outputs:
            self.multi_apply_button.config(state="normal")
            self.multi_stop_all_button.config(
                state="normal" if active_targets else "disabled"
            )

    # ------------------------------------------------------------------
    # Device controls (Phase 4: volume + mute + set-as-default)
    # ------------------------------------------------------------------

    def _build_device_controls(self, parent):
        """Build the per-device volume / mute / set-as-default row.

        This row is greyed out until a virtual sink is selected in the
        tree. It is independent of the Routing panel: you can adjust
        volume without re-routing, and re-route without touching volume.
        """
        self.device_controls_target_var = tk.StringVar(
            value="(no device selected)"
        )
        ttk.Label(
            parent,
            textvariable=self.device_controls_target_var,
            font=("", 9, "italic"),
        ).grid(row=0, column=0, columnspan=4, sticky=tk.W, padx=5, pady=(5, 2))

        # Volume slider + percent label
        ttk.Label(parent, text="Volume:").grid(
            row=1, column=0, sticky=tk.W, padx=5, pady=2
        )
        # We use an IntVar for easy .get(); sliders in ttk are ttk.Scale.
        # Range 0-150 (pactl allows amplification above 100%).
        self.device_volume_var = tk.IntVar(value=100)
        self.device_volume_scale = ttk.Scale(
            parent,
            from_=0,
            to=150,
            orient=tk.HORIZONTAL,
            variable=self.device_volume_var,
            command=self._on_volume_scale_change,
            state="disabled",
        )
        self.device_volume_scale.grid(
            row=1, column=1, sticky=tk.EW, padx=5, pady=2
        )
        self.device_volume_label = ttk.Label(
            parent, text="—", width=6
        )
        self.device_volume_label.grid(row=1, column=2, padx=5, pady=2)

        # Mute checkbox
        self.device_mute_var = tk.BooleanVar(value=False)
        self.device_mute_check = ttk.Checkbutton(
            parent,
            text="Mute",
            variable=self.device_mute_var,
            command=self._on_mute_check_change,
            state="disabled",
        )
        self.device_mute_check.grid(row=1, column=3, padx=5, pady=2)

        # Set as default + Reset
        self.set_default_button = ttk.Button(
            parent,
            text="Set as Default",
            command=self._on_set_default_clicked,
            state="disabled",
        )
        self.set_default_button.grid(row=2, column=0, padx=5, pady=(2, 5), sticky=tk.W)

        ttk.Button(
            parent,
            text="Refresh",
            command=self._on_device_controls_refresh,
            state="disabled",
            width=10,
        ).grid(row=2, column=1, padx=5, pady=(2, 5), sticky=tk.W)

        # --- Card profile (shown for hardware sinks only) ---
        # Lets the user switch the active profile of the underlying card
        # (BT codec change, HDMI 2.0 <-> 5.1, USB pro-audio mode).
        # Replaces the old Hardware tab — the profile is most useful
        # right next to the device controls it affects.
        ttk.Label(parent, text="Card Profile:").grid(
            row=3, column=0, sticky=tk.W, padx=5, pady=2
        )
        self.card_profile_var = tk.StringVar(value="")
        self.card_profile_combo = ttk.Combobox(
            parent,
            textvariable=self.card_profile_var,
            state="disabled",
            values=(),
            width=50,
        )
        self.card_profile_combo.grid(
            row=3, column=1, columnspan=2, sticky=tk.EW, padx=5, pady=2
        )
        self.card_profile_combo.bind(
            "<<ComboboxSelected>>", self._on_card_profile_change
        )
        # Internal: map combobox display label -> profile name
        self._card_profile_map: Dict[str, str] = {}

        # Stretch the slider column
        parent.columnconfigure(1, weight=1)

        # State: which sink are we controlling? Set in update_device_controls_panel
        self._device_controls_sink_name: Optional[str] = None  # type: ignore[name-defined]
        self._device_controls_is_source = False

    def update_device_controls_panel(
        self, entity_name: Optional[str] = None, is_source: bool = False
    ):
        """Populate the device controls panel for a selected sink or source.

        Pass entity_name=None to reset the panel to its disabled state.
        """
        if not entity_name:
            self._device_controls_sink_name = None
            self.device_controls_target_var.set("(no device selected)")
            self.device_volume_scale.config(state="disabled")
            self.device_mute_check.config(state="disabled")
            self.set_default_button.config(state="disabled")
            self.device_volume_label.config(text="—")
            self._set_card_profile_combo(None)
            return

        self._device_controls_sink_name = entity_name
        self._device_controls_is_source = is_source
        self.device_controls_target_var.set(
            f"{entity_name}  ({'source' if is_source else 'sink'})"
        )

        # Read current volume from pactl and snap the slider to it.
        # The Scale's command fires on programmatic updates, so we
        # temporarily set a flag to ignore the change (otherwise the
        # user sees a flicker as pactl re-sets the value).
        self._device_controls_loading = True
        try:
            if is_source:
                vol = PactlRunner.get_source_volume(entity_name)
            else:
                vol = PactlRunner.get_sink_volume(entity_name)
            if vol is not None:
                self.device_volume_var.set(min(vol, 150))
                self.device_volume_label.config(text=f"{vol}%")
            else:
                self.device_volume_var.set(100)
                self.device_volume_label.config(text="?")

            muted = PactlRunner.get_sink_mute(entity_name)
            self.device_mute_var.set(bool(muted) if muted is not None else False)
        finally:
            self._device_controls_loading = False

        self.device_volume_scale.config(state="normal")
        self.device_mute_check.config(state="normal")
        # "Set as Default" only makes sense for sinks
        if is_source:
            self.set_default_button.config(state="disabled")
        else:
            self.set_default_button.config(state="normal")
        # Card profile: hardware sinks (alsa_output.* / alsa_input.*)
        # have an underlying alsa_card.* with selectable profiles.
        # Virtual sinks (PulseAudio_NULL_*) don't, so the combo
        # stays disabled.
        self._set_card_profile_combo(entity_name)

    def _set_card_profile_combo(self, entity_name):
        """Populate the Card Profile combobox for the selected entity.

        If the entity is a hardware sink (alsa_output.* or alsa_input.*),
        find the matching alsa_card.* and list its profiles. Otherwise
        (virtual sink, source, none) clear and disable the combo.

        The combo display format is '<name> (<description>)' and the
        selected display label is mapped back to the profile name
        via self._card_profile_map.
        """
        self._card_profile_map = {}
        if not entity_name or not entity_name.startswith("alsa_"):
            self.card_profile_combo.config(values=(), state="disabled")
            self.card_profile_var.set("")
            return
        # Find the matching card. We use a heuristic: match by the
        # description in the device list. This is more robust than
        # parsing the alsa naming scheme (which differs between
        # output/input formats).
        try:
            sinks = PactlRunner.list_sinks()
            sink_desc = next(
                (s.get("description", "") for s in sinks if s.get("name") == entity_name),
                "",
            )
            if not sink_desc:
                self.card_profile_combo.config(values=(), state="disabled")
                self.card_profile_var.set("")
                return
            cards = PactlRunner.list_cards()
            card = next(
                (c for c in cards if c.get("description") == sink_desc),
                None,
            )
            if not card or not card.get("profiles"):
                self.card_profile_combo.config(values=(), state="disabled")
                self.card_profile_var.set("")
                return
            # Build display labels: "profile_name — description"
            labels = []
            active_label = ""
            for p in card["profiles"]:
                avail_tag = "" if p["available"] else " (unavailable)"
                label = f"{p['name']}{avail_tag} — {p['description']}"
                labels.append(label)
                self._card_profile_map[label] = p["name"]
                if p["name"] == card.get("active_profile"):
                    active_label = label
            self.card_profile_combo.config(values=labels, state="readonly")
            self.card_profile_var.set(active_label)
        except Exception as e:
            self.add_output(f"Card profile lookup failed: {e}")
            self.card_profile_combo.config(values=(), state="disabled")
            self.card_profile_var.set("")

    def _on_card_profile_change(self, _event=None):
        """Apply the selected card profile when the user picks one."""
        name = self._device_controls_sink_name
        if not name:
            return
        selected_label = self.card_profile_var.get()
        profile_name = self._card_profile_map.get(selected_label)
        if not profile_name:
            return  # User typed something invalid
        # Don't prompt if it's already active
        sink_desc = ""
        card = None
        try:
            cards = PactlRunner.list_cards()
            sinks = PactlRunner.list_sinks()
            sink_desc = next(
                (s.get("description", "") for s in sinks if s.get("name") == name),
                "",
            )
            card = next(
                (c for c in cards if c.get("description") == sink_desc),
                None,
            )
            if card and card.get("active_profile") == profile_name:
                return  # Already active, nothing to do
        except Exception:
            pass
        if not messagebox.askyesno(
            "Switch card profile",
            f"Switch '{sink_desc}' card to profile '{profile_name}'?\n\n"
            "Note: this may briefly disconnect running audio streams.",
        ):
            # Reset combo to active profile
            self._set_card_profile_combo(name)
            return
        ok = PactlRunner.set_card_profile(
            card["name"], profile_name, logger=self.add_output
        ) if card else False
        if ok:
            self.add_output(
                f"Card profile switched: {sink_desc} → {profile_name}"
            )
            self.refresh_all_views()
        else:
            messagebox.showerror(
                "Profile switch failed",
                f"pactl refused to switch to '{profile_name}'.",
            )
            self._set_card_profile_combo(name)

    def _on_volume_scale_change(self, _value):
        """Slider drag — set the sink/source volume live."""
        if getattr(self, "_device_controls_loading", False):
            return
        name = self._device_controls_sink_name
        if not name:
            return
        try:
            pct = int(self.device_volume_var.get())
        except (tk.TclError, ValueError):
            return
        # Update the percent label
        self.device_volume_label.config(text=f"{pct}%")
        if self._device_controls_is_source:
            ok = PactlRunner.set_source_volume(name, pct)
        else:
            ok = PactlRunner.set_sink_volume(name, pct)
        if not ok:
            self.add_output(f"Failed to set volume for {name}")

    def _on_mute_check_change(self):
        """Mute toggle."""
        if getattr(self, "_device_controls_loading", False):
            return
        name = self._device_controls_sink_name
        if not name:
            return
        muted = self.device_mute_var.get()
        # Only sinks support get/set-sink-mute; sources use a similar API
        # (set-source-mute) but we keep the current implementation simple.
        if self._device_controls_is_source:
            self.add_output("Mute toggle for sources is not yet supported")
            return
        if not PactlRunner.set_sink_mute(name, muted):
            self.add_output(f"Failed to set mute for {name}")

    def _on_set_default_clicked(self):
        """Make the selected sink the system default sink."""
        name = self._device_controls_sink_name
        if not name or self._device_controls_is_source:
            return
        if not PactlRunner.set_default_sink(name):
            messagebox.showerror("Set Default", f"Failed to set {name} as default sink")
            return
        self.add_output(f"Set '{name}' as system default sink")
        self.status_var.set(f"Default sink: {name}")
        self.refresh_all_views()

    def _on_device_controls_refresh(self):
        """Re-read the current device's volume from pactl."""
        if not self._device_controls_sink_name:
            return
        self.update_device_controls_panel(
            self._device_controls_sink_name,
            is_source=self._device_controls_is_source,
        )

    def apply_routing(self):
        """Create (or replace) a loopback from the selected sink to the chosen output."""
        sink_name = getattr(self, "_routing_sink_name", None)
        if not sink_name:
            return

        target = self.routing_output_var.get().strip()
        if not target:
            messagebox.showerror("Routing error", "Pick a hardware output to route to")
            return

        monitor = PactlRunner.monitor_source_for(sink_name)
        if monitor is None:
            messagebox.showerror("Routing error", f"Cannot derive monitor source for '{sink_name}'")
            return
        # If there's an existing loopback from this sink's monitor, remove it first
        # so we don't end up with two loopbacks stacking audio.
        for lb in PactlRunner.list_loopbacks():
            if lb.get("source") == monitor:
                PactlRunner.unload_loopback(lb["id"])

        lb_id = PactlRunner.create_loopback(
            monitor, target, latency_msec=self.get_loopback_latency_ms(),
            logger=self.add_output,
        )
        if lb_id is None:
            self.add_output(f"Failed to create loopback from {monitor} to {target}")
            messagebox.showerror("Routing error", "pactl refused the loopback command")
            return

        self.add_output(
            f"Routing '{sink_name}' → '{target}' (loopback module #{lb_id})"
        )
        self.status_var.set(f"Routed '{sink_name}' → '{target}'")
        self.refresh_all_views()
        # Re-select the same sink to refresh the routing panel state
        self._reselect_after_refresh(sink_name)

    def stop_routing(self):
        """Unload the active loopback for the selected sink."""
        sink_name = getattr(self, "_routing_sink_name", None)
        if not sink_name:
            return

        monitor = PactlRunner.monitor_source_for(sink_name)
        if monitor is None:
            return
        loopbacks = PactlRunner.list_loopbacks()
        ours = [lb for lb in loopbacks if lb.get("source") == monitor]
        if not ours:
            messagebox.showinfo("Routing", f"'{sink_name}' is not currently routed")
            return

        for lb in ours:
            ok = PactlRunner.unload_loopback(lb["id"])
            if ok:
                self.add_output(f"Stopped routing '{sink_name}' (unloaded #{lb['id']})")
            else:
                self.add_output(f"Failed to unload loopback #{lb['id']}")

        self.status_var.set(f"Stopped routing '{sink_name}'")
        self.refresh_all_views()
        self._reselect_after_refresh(sink_name)

    def apply_multi_routing(self):
        """Route the selected virtual sink to every checked hardware output.

        Creates one loopback per checked target. Loopbacks to targets
        that the user has just unchecked are removed. The end state
        matches exactly what the user has selected in the listbox —
        idempotent.
        """
        sink_name = getattr(self, "_routing_sink_name", None)
        if not sink_name:
            return

        # Collect the selected targets from the listbox
        selected_indices = self.multi_routing_listbox.curselection()
        if not selected_indices:
            messagebox.showinfo(
                "Multi-output Routing",
                "Check one or more hardware outputs first.",
            )
            return
        selected_targets = {
            self.multi_routing_listbox.get(i) for i in selected_indices
        }

        monitor = PactlRunner.monitor_source_for(sink_name)
        if monitor is None:
            messagebox.showerror(
                "Routing error",
                f"Cannot derive monitor source for '{sink_name}'",
            )
            return

        # Find existing loopbacks from this monitor
        existing_loopbacks = [
            lb for lb in PactlRunner.list_loopbacks()
            if lb.get("source") == monitor
        ]
        existing_targets: dict[str, Any] = {}
        for lb in existing_loopbacks:
            sink = lb.get("sink")
            if sink:
                existing_targets[sink] = lb

        created: List[str] = []
        removed: List[str] = []
        failed: List[str] = []

        # Remove loopbacks whose target is no longer selected
        for target, lb in existing_targets.items():
            if target not in selected_targets:
                if PactlRunner.unload_loopback(lb["id"]):
                    removed.append(target)
                else:
                    failed.append(f"unload {target}")

        # Create loopbacks for newly selected targets
        for target in selected_targets:
            if target in existing_targets:
                continue  # Already routed to this target
            lb_id = PactlRunner.create_loopback(
                monitor, target, latency_msec=self.get_loopback_latency_ms(),
                logger=self.add_output,
            )
            if lb_id is not None:
                created.append(target)
            else:
                failed.append(f"create {target}")

        # Report
        if created:
            self.add_output(
                f"Multi-routing '{sink_name}' → {', '.join(sorted(created))}"
            )
        if removed:
            self.add_output(
                f"Stopped routing '{sink_name}' → {', '.join(sorted(removed))}"
            )
        if failed:
            self.add_output(
                f"Multi-routing failures: {', '.join(failed)}"
            )

        if created or removed:
            self.status_var.set(
                f"'{sink_name}' now routed to "
                f"{len(selected_targets)} output(s)"
            )
        else:
            self.status_var.set(
                f"'{sink_name}' routing unchanged"
            )

        self.refresh_all_views()
        self._reselect_after_refresh(sink_name)

    def stop_all_routing_for_selected(self):
        """Unload every loopback whose source is the selected sink's monitor."""
        sink_name = getattr(self, "_routing_sink_name", None)
        if not sink_name:
            return
        monitor = PactlRunner.monitor_source_for(sink_name)
        if monitor is None:
            return
        ours = [
            lb for lb in PactlRunner.list_loopbacks()
            if lb.get("source") == monitor
        ]
        if not ours:
            messagebox.showinfo(
                "Routing", f"'{sink_name}' is not currently routed"
            )
            return
        unloaded = 0
        for lb in ours:
            if PactlRunner.unload_loopback(lb["id"]):
                unloaded += 1
                self.add_output(
                    f"Stopped routing '{sink_name}' → '{lb.get('sink')}' "
                    f"(unloaded #{lb['id']})"
                )
        self.status_var.set(
            f"Stopped all {unloaded} routing(s) for '{sink_name}'"
        )
        self.refresh_all_views()
        self._reselect_after_refresh(sink_name)

    def _reselect_after_refresh(self, sink_name):
        """After refresh_all_views(), re-find and re-select the sink in the tree."""
        for tree_item in self.unified_tree.get_children():
            for child in self.unified_tree.get_children(tree_item):
                item = self.unified_tree.item(child)
                vals = item.get("values", ())
                if len(vals) >= 3 and vals[1] == "sink" and vals[2] == sink_name:
                    self.unified_tree.selection_set(child)
                    self.unified_tree.focus(child)
                    return

    def _generate_detailed_info(self, entity_id, entity_type, entity_name, tree_item_id):
        """Generate tiered technical specifications for the selected item."""

        if entity_type == "category":
            return f"{entity_name} - {len(self.unified_tree.get_children(tree_item_id))} items"
        elif entity_type == "hardware_category":
            # Hardware category summary
            children_count = len(self.unified_tree.get_children(tree_item_id))
            category_descriptions = {
                'builtin': 'Built-in audio devices (onboard, PCI sound cards)',
                'usb': 'USB connected audio devices',
                'bluetooth': 'Bluetooth wireless audio devices',
                'hdmi': 'HDMI/DisplayPort audio from graphics cards'
            }
            description = category_descriptions.get(entity_name, 'Hardware audio devices')
            return f"{description}\n\nDevices: {children_count}"
        elif entity_type == "device_group":
            return self._generate_device_group_summary(entity_name, tree_item_id)
        elif entity_type == "hardware_device_group":
            # Use the same device group summary but with hardware context
            return self._generate_hardware_device_group_summary(entity_name, tree_item_id)
        elif entity_type == "module":
            return self._generate_module_summary(entity_id, entity_name)
        elif entity_type == "sink":
            return self._generate_sink_summary(entity_id, entity_name, tree_item_id)
        elif entity_type == "source":
            return self._generate_source_summary(entity_id, entity_name, tree_item_id)
        else:
            return "Select an item to see details"

    def _generate_device_group_summary(self, device_name, tree_item_id):
        """Generate comprehensive summary for virtual device groups."""
        children = self.unified_tree.get_children(tree_item_id)

        # Collect information from child components
        module_info = None
        sink_info = None
        source_info = None

        for child in children:
            child_item = self.unified_tree.item(child)
            child_values = child_item.get('values', [])
            if len(child_values) >= 3:
                child_id, child_type, child_name = child_values

                if child_type == "module":
                    # Get module details
                    modules = PactlRunner.list_modules()
                    for mod in modules:
                        if str(mod.get('id', '')) == str(child_id):
                            module_info = mod
                            break

                elif child_type == "sink":
                    # Get sink details
                    sinks = PactlRunner.list_sinks()
                    for sink in sinks:
                        if str(sink.get('id', '')) == str(child_id):
                            sink_info = sink
                            break

                elif child_type == "source":
                    # Get source details
                    sources = PactlRunner.list_sources()
                    for source in sources:
                        if str(source.get('id', '')) == str(child_id):
                            source_info = source
                            break

        if self.show_all_details_var.get():
            # Full details view - show all component information
            info = f"Virtual Device: {device_name}\n"
            info += f"{'='*50}\n\n"

            if module_info:
                info += "MODULE DETAILS:\n"
                info += f"ID: {module_info.get('id', 'Unknown')}\n"
                info += f"Name: {module_info.get('name', 'Unknown')}\n"
                info += f"Arguments: {module_info.get('argument', 'None')}\n\n"

            if sink_info:
                info += "SINK (OUTPUT) DETAILS:\n"
                for key, value in sink_info.items():
                    if key not in ['id', 'name']:
                        info += f"{key}: {value}\n"
                info += "\n"

            if source_info:
                info += "SOURCE (INPUT) DETAILS:\n"
                for key, value in source_info.items():
                    if key not in ['id', 'name']:
                        info += f"{key}: {value}\n"

            return info
        else:
            # Summary view - aggregate key specifications
            info = f"Virtual Device: {device_name}\n"

            # Device overview
            component_count = len(children)
            info += f"Components: {component_count} (module, sink, source)\n"

            # Get primary audio specs from sink (most comprehensive)
            if sink_info:
                state = sink_info.get('state', 'Unknown')
                info += f"State: {state}\n"

                driver = sink_info.get('driver', 'Unknown')
                info += f"Driver: {driver}\n"

                # Audio Engineering Essentials
                sample_spec = sink_info.get('sample_spec', 'Unknown')
                info += f"\nAudio Specification: {sample_spec}\n"

                channel_map = sink_info.get('channel_map', 'Unknown')
                info += f"Channel Layout: {channel_map}\n"

                latency = sink_info.get('latency', 'Unknown')
                info += f"Latency: {latency}\n"

                # Buffer settings from properties
                properties = sink_info.get('properties', {})
                quantum_limit = properties.get('clock.quantum-limit', 'N/A')
                info += f"Buffer Quantum Limit: {quantum_limit}\n"

                # Volume and mute
                mute = sink_info.get('mute', 'Unknown')
                info += f"\nMute: {mute}\n"

                volume = sink_info.get('volume', 'Unknown')
                if volume != 'Unknown' and len(str(volume)) < 100:
                    info += f"Volume: {volume}\n"

            # Module configuration summary
            if module_info:
                info += "\nModule Configuration:\n"
                args = module_info.get('argument', '')

                # Parse key module parameters
                if 'channels=' in args:
                    import re
                    channels_match = re.search(r'channels=(\d+)', args)
                    if channels_match:
                        channels = int(channels_match.group(1))
                        channel_names = {1: "Mono", 2: "Stereo", 6: "5.1 Surround", 8: "7.1 Surround"}
                        channel_desc = channel_names.get(channels, f"{channels}-channel")
                        info += f"Created as: {channel_desc}\n"

                if 'rate=' in args:
                    import re
                    rate_match = re.search(r'rate=(\d+)', args)
                    if rate_match:
                        rate = rate_match.group(1)
                        info += f"Sample Rate Override: {rate} Hz\n"

            # Source monitoring info
            if source_info:
                monitor_of = source_info.get('monitor_of_sink', 'N/A')
                if monitor_of != 'N/A':
                    info += "\nMonitor Source: Available for recording output\n"

            # Usage instructions
            info += "\nUsage:\n"
            info += f"• Applications can output audio to '{device_name}'\n"
            info += "• Input monitor available for recording/routing\n"
            info += "• Select module component to remove entire device\n"

            return info

    def _generate_module_summary(self, module_id, module_name):
        """Generate tiered summary for module items."""
        # Get full module data
        modules = PactlRunner.list_modules()
        module_data = None

        for mod in modules:
            mod_id = mod.get('id', '')
            if str(mod_id) == str(module_id) or mod_id == module_id:
                module_data = mod
                break

        if not module_data:
            return f"Module #{module_id}: {module_name}\nModule data not found."

        if self.show_all_details_var.get():
            # Full technical details
            info = f"Module #{module_id}: {module_name}\n\n"
            for key, value in module_data.items():
                if key == 'properties':
                    info += "Properties:\n"
                    for prop_key, prop_value in value.items():
                        info += f"  {prop_key} = {prop_value}\n"
                elif key not in ['id', 'name']:
                    info += f"{key}: {value}\n"
            return info
        else:
            # Summary view
            info = f"Module #{module_id}: {module_name}\n"
            info += f"Type: {module_name}\n"

            # Parse key info from arguments
            args = module_data.get('argument', '')
            if 'sink_name=' in args:
                import re
                sink_match = re.search(r'sink_name=([a-zA-Z0-9_.-]+)', args)
                if sink_match:
                    info += f"Device Name: {sink_match.group(1)}\n"

            if 'channels=' in args:
                import re
                channels_match = re.search(r'channels=(\d+)', args)
                if channels_match:
                    channels = int(channels_match.group(1))
                    channel_names = {1: "Mono", 2: "Stereo", 6: "5.1 Surround", 8: "7.1 Surround"}
                    channel_desc = channel_names.get(channels, f"{channels}-channel")
                    info += f"Audio Format: {channel_desc}\n"

            return info

    def _generate_sink_summary(self, sink_id, sink_name, tree_item_id):
        """Generate tiered summary for sink (output) items."""
        # Get full sink data
        sinks = PactlRunner.list_sinks()
        sink_data = None

        for sink in sinks:
            s_id = sink.get('id', '')
            if str(s_id) == str(sink_id) or s_id == sink_id:
                sink_data = sink
                break

        if not sink_data:
            return f"Sink #{sink_id}: {sink_name}\nSink data not found."

        if self.show_all_details_var.get():
            # Full technical details
            info = f"Sink #{sink_id}: {sink_name}\n\n"
            for key, value in sink_data.items():
                if key == 'properties':
                    info += "Properties:\n"
                    for prop_key, prop_value in value.items():
                        info += f"  {prop_key} = {prop_value}\n"
                elif key == 'formats':
                    info += "Formats:\n"
                    for fmt in value:
                        info += f"  {fmt}\n"
                elif key not in ['id', 'name']:
                    info += f"{key}: {value}\n"
            return info
        else:
            # Summary view - Identity/Status first, then Audio Engineering essentials
            info = f"Audio Output #{sink_id}\n"

            # Identity/Status
            info += f"Name: {sink_name}\n"
            description = sink_data.get('description', sink_name)
            info += f"Description: {description}\n"
            state = sink_data.get('state', 'Unknown')
            info += f"State: {state}\n"
            driver = sink_data.get('driver', 'Unknown')
            info += f"Driver: {driver}\n"

            # Audio Engineering Essentials
            sample_spec = sink_data.get('sample_spec', 'Unknown')
            info += f"\nSample Specification: {sample_spec}\n"

            channel_map = sink_data.get('channel_map', 'Unknown')
            info += f"Channel Map: {channel_map}\n"

            latency = sink_data.get('latency', 'Unknown')
            info += f"Latency: {latency}\n"

            # Get buffer/quantum info from properties
            properties = sink_data.get('properties', {})
            quantum_limit = properties.get('clock.quantum-limit', 'N/A')
            info += f"Buffer Quantum Limit: {quantum_limit}\n"

            # Status info
            mute = sink_data.get('mute', 'Unknown')
            info += f"\nMute: {mute}\n"
            volume = sink_data.get('volume', 'Unknown')
            if volume != 'Unknown' and len(volume) < 100:  # Don't show if too long
                info += f"Volume: {volume}\n"

            return info

    def _generate_source_summary(self, source_id, source_name, tree_item_id):
        """Generate tiered summary for source (input) items."""
        # Get full source data
        sources = PactlRunner.list_sources()
        source_data = None

        for source in sources:
            s_id = source.get('id', '')
            if str(s_id) == str(source_id) or s_id == source_id:
                source_data = source
                break

        if not source_data:
            return f"Source #{source_id}: {source_name}\nSource data not found."

        if self.show_all_details_var.get():
            # Full technical details
            info = f"Source #{source_id}: {source_name}\n\n"
            for key, value in source_data.items():
                if key == 'properties':
                    info += "Properties:\n"
                    for prop_key, prop_value in value.items():
                        info += f"  {prop_key} = {prop_value}\n"
                elif key == 'formats':
                    info += "Formats:\n"
                    for fmt in value:
                        info += f"  {fmt}\n"
                elif key not in ['id', 'name']:
                    info += f"{key}: {value}\n"
            return info
        else:
            # Summary view - Identity/Status first, then Audio Engineering essentials
            info = f"Audio Input #{source_id}\n"

            # Identity/Status
            info += f"Name: {source_name}\n"
            description = source_data.get('description', source_name)
            info += f"Description: {description}\n"
            state = source_data.get('state', 'Unknown')
            info += f"State: {state}\n"
            driver = source_data.get('driver', 'Unknown')
            info += f"Driver: {driver}\n"

            # Audio Engineering Essentials
            sample_spec = source_data.get('sample_spec', 'Unknown')
            info += f"\nSample Specification: {sample_spec}\n"

            channel_map = source_data.get('channel_map', 'Unknown')
            info += f"Channel Map: {channel_map}\n"

            latency = source_data.get('latency', 'Unknown')
            info += f"Latency: {latency}\n"

            # Get buffer/quantum info from properties
            properties = source_data.get('properties', {})
            quantum_limit = properties.get('clock.quantum-limit', 'N/A')
            info += f"Buffer Quantum Limit: {quantum_limit}\n"

            # Status info
            mute = source_data.get('mute', 'Unknown')
            info += f"\nMute: {mute}\n"
            volume = source_data.get('volume', 'Unknown')
            if volume != 'Unknown' and len(volume) < 100:  # Don't show if too long
                info += f"Volume: {volume}\n"

            # Monitor info for sources
            monitor_of = source_data.get('monitor_of_sink', 'N/A')
            if monitor_of != 'N/A':
                info += f"Monitor of Sink: {monitor_of}\n"

            return info

    def unload_selected_from_tree(self):
        """Unload the selected module from the unified tree view."""
        selected = self.unified_tree.selection()
        if not selected:
            messagebox.showinfo("Info", "No module selected")
            return

        # Get the selected item
        item = self.unified_tree.item(selected[0])
        values = item['values']

        if not values or len(values) < 2 or values[1] != "module":
            messagebox.showinfo("Info", "Please select a module to unload")
            return

        module_id = values[0]

        # Confirm
        if not messagebox.askyesno(
            "Confirm",
            f"Are you sure you want to unload module #{module_id}?\n\n"
            "This will also remove any sinks and sources created by this module."
        ):
            return

        self.status_var.set(f"Unloading module #{module_id}...")
        self.root.update()

        # Unload the module
        success = PactlRunner.unload_module(str(module_id), logger=self.add_output)

        if success:
            self.add_output(f"Unloaded module #{module_id}")
            self.status_var.set(f"Unloaded module #{module_id}")
            # Refresh the views
            self.refresh_all_views()
        else:
            self.add_output(f"Failed to unload module #{module_id}")
            self.status_var.set("Error unloading module")
            messagebox.showerror("Error", f"Failed to unload module #{module_id}")

    def unload_all_null_sinks(self):
        """Unload all null sink modules."""
        # Confirm with the user
        if not messagebox.askyesno(
            "Confirm",
            "Are you sure you want to remove ALL null sinks?\n\n"
            "This will unload all module-null-sink modules, which may disrupt audio routing."
        ):
            return

        self.status_var.set("Removing all null sinks...")
        self.root.update()

        # Unload all null sinks
        count, errors = PactlRunner.unload_all_null_sinks(logger=self.add_output)

        # Update UI with results
        if count > 0:
            self.add_output(f"Successfully removed {count} null sink module(s)")

        if errors:
            error_msg = "\n".join(errors)
            self.add_output(f"Errors occurred:\n{error_msg}")
            messagebox.showerror("Error", f"Some errors occurred:\n{error_msg}")

        # Update the status bar
        if count > 0 and not errors:
            self.status_var.set(f"Successfully removed {count} null sink module(s)")
        elif count > 0 and errors:
            self.status_var.set(f"Removed {count} null sink(s) with some errors")
        else:
            self.status_var.set("No null sinks were removed")

        # Refresh all views with the unified approach
        self.refresh_all_views()

    def save_preset(self):
        """Save the current configuration as a preset."""
        # Create presets directory if it doesn't exist
        os.makedirs("presets", exist_ok=True)

        # Ask for preset name
        filename = filedialog.asksaveasfilename(
            initialdir="presets",
            title="Save Preset",
            filetypes=(("JSON files", "*.json"), ("All files", "*.*")),
            defaultextension=".json"
        )

        if not filename:
            return  # User canceled

        self.status_var.set("Saving preset...")
        self.root.update()

        # Get current configuration
        sinks = PactlRunner.list_sinks(logger=self.add_output)
        sources = PactlRunner.list_sources(logger=self.add_output)
        modules = PactlRunner.list_modules(logger=self.add_output)

        # Create preset data
        preset_data = {
            "sinks": sinks,
            "sources": sources,
            "modules": modules,
            "name": os.path.basename(filename).replace(".json", ""),
            "created": "TODO: Add timestamp"  # Would add datetime.now().isoformat() in a real implementation
        }

        try:
            with open(filename, 'w') as f:
                json.dump(preset_data, f, indent=2)

            self.add_output(f"Saved preset to {filename}")
            self.status_var.set(f"Saved preset to {filename}")
        except Exception as e:
            self.add_output(f"Error saving preset: {str(e)}")
            self.status_var.set("Error saving preset")
            messagebox.showerror("Error", f"Failed to save preset: {str(e)}")

    def load_preset(self):
        """Load a saved preset."""
        messagebox.showinfo(
            "Info",
            "Preset loading is not yet implemented in this version."
        )

    def show_about(self):
        """Show the about dialog."""
        messagebox.showinfo(
            "About PulseAudio Control GUI",
            "PulseAudio Control GUI (pactl-gui)\n\n"
            "A lightweight graphical user interface for managing "
            "PulseAudio modules and configurations.\n\n"
            "Version: 0.1 (Alpha)\n"
            "License: MIT\n\n"
            "Created with Python and Tkinter"
        )

    def update_command_preview(self, *args):
        """Update the command preview based on current input values."""
        raw_name = self.sink_name_var.get().strip()

        # Determine the actual name that will be used
        if not raw_name or raw_name.endswith(" (auto)"):
            # Use auto-naming preview
            selected_preset = self.audio_preset_var.get()
            preset_configs = {
                "Stereo": "stereo",
                "Mono": "mono",
                "5.1 Surround": "surround51",
                "7.1 Surround": "surround71",
                "Custom": "custom",
            }
            base_name = preset_configs.get(selected_preset, selected_preset.lower())
            name = f"{base_name}*"  # Use * to indicate auto-naming in preview
        else:
            name = raw_name.replace(" (auto)", "")

        channels = self.channels_var.get() or "2"

        # Pick media.class based on the selected device type
        device_type = (
            self.device_type_var.get() if hasattr(self, "device_type_var") else "both"
        )
        media_class = {
            "sink": "Audio/Sink",
            "source": "Audio/Source",
            "both": "Audio/Duplex",
        }[device_type]

        # Build the command with basic parameters
        cmd_parts = [
            "pactl load-module module-null-sink",
            f"media.class={media_class}",
            f"sink_name={name}",
            f"channels={channels}",
        ]

        # Add advanced options if they are set and advanced options are shown
        if hasattr(self, 'show_advanced_var') and self.show_advanced_var.get():
            # Sample rate
            rate = self.rate_var.get().strip()
            if rate and rate != "44100":  # Only add if different from default
                cmd_parts.append(f"rate={rate}")

            # Sample format - get the actual format code from the description
            format_desc = self.format_var.get().strip()
            if format_desc and hasattr(self, 'format_mappings'):
                actual_format = self.format_mappings.get(format_desc, format_desc)
                if actual_format and actual_format != "s16le":  # Only add if different from default
                    cmd_parts.append(f"format={actual_format}")

            # Channel map
            channel_map = self.channel_map_var.get().strip()
            if channel_map:
                cmd_parts.append(f"channel_map={channel_map}")

            # Additional properties
            properties = self.properties_var.get().strip()
            if properties:
                cmd_parts.append(f"sink_properties={properties}")

        command = " ".join(cmd_parts)
        self.command_preview_var.set(command)

    def toggle_system_modules(self):
        """Toggle visibility of system modules."""
        # We'll refresh all views which will respect the current state of show_system_var
        self.refresh_all_views()

        # Update status
        if self.show_system_var.get():
            self.status_var.set("Showing all modules including system modules")
        else:
            self.status_var.set("Showing only virtual and hardware devices")

    def _on_channel_map_preset_change(self, _event=None):
        """When the user picks a channel-map preset, update the underlying
        channel_map_var and show/hide the custom entry field."""
        label = self.channel_map_preset_var.get()
        for preset_label, layout in self.channel_map_presets:
            if preset_label == label:
                if layout == "__custom__":
                    # Show the custom entry
                    self.channel_map_custom_entry.grid(
                        row=4, column=1, sticky=(tk.W, tk.E), padx=5, pady=5
                    )
                else:
                    # Hide the custom entry (if visible)
                    self.channel_map_custom_entry.grid_remove()
                    self.channel_map_var.set(layout)
                break

    def _latency_auto_label(self) -> str:
        """Return the Auto preset's label, with the recommended value filled in.

        E.g. "Auto - 50 ms (recommended for 48kHz)" or
             "Auto - 100 ms (recommended for 96kHz)".
        """
        try:
            rate = int(self.rate_var.get())
        except (ValueError, tk.TclError):
            rate = 48000
        rec = self._latency_recommended_ms
        return f"Auto - {rec} ms (recommended for {rate // 1000}kHz)"

    def _latency_preset_labels(self) -> list:
        """Return the list of labels for the latency combobox, with
        the Auto option's label filled in dynamically."""
        out = []
        for label, value in self._latency_presets:
            if value is None:
                out.append(self._latency_auto_label())
            else:
                out.append(label)
        return out

    def _on_latency_preset_change(self, _event=None):
        """When the user picks a latency preset, show/hide the custom entry
        and update the underlying latency_var."""
        label = self.latency_preset_var.get()
        for preset_label, value in self._latency_presets:
            if value is None:
                # Auto — use the dynamic auto label as the match key
                if label == self._latency_auto_label() or label.startswith("Auto"):
                    self.latency_custom_entry.grid_remove()
                    return
                continue
            if preset_label == label or (
                value == "__custom__" and label == "Custom (type below)"
            ):
                if value == "__custom__":
                    # Show the custom entry, seeded with the current
                    # recommended value
                    try:
                        self.latency_var.set(str(self._latency_recommended_ms))
                    except AttributeError:
                        self.latency_var.set("50")
                    self.latency_custom_entry.grid(
                        row=8, column=1, sticky=tk.W, padx=5, pady=5
                    )
                else:
                    self.latency_custom_entry.grid_remove()
                    self.latency_var.set(str(value))
                return

    def get_loopback_latency_ms(self) -> int:
        """Return the currently selected loopback latency in milliseconds.

        Resolves the combobox selection to a concrete int, with
        bounds-checking (clamped to 1-1000 ms) and a sane fallback.
        """
        label = self.latency_preset_var.get()
        # 1. Match against a preset
        for preset_label, value in self._latency_presets:
            if value is None:
                if label == self._latency_auto_label() or label.startswith("Auto"):
                    return self._latency_recommended_ms
            elif preset_label == label:
                if value == "__custom__":
                    break  # fall through to the custom entry
                return value
        # 2. Custom entry — read and validate
        try:
            v = int(self.latency_var.get())
        except (ValueError, tk.TclError):
            return 50
        return max(1, min(1000, v))

    def toggle_advanced_options(self):
        """Toggle the visibility of advanced options.

        Bug fix: the toggle and the frame were both placed at row=4,
        so when the frame was shown it OVERLAPPED the toggle, making
        the checkbox invisible. Now the toggle lives ABOVE the frame
        (toggle at one row, frame at the next) so they don't fight.
        """
        if self.show_advanced_var.get():
            # Show advanced options — the frame goes below the toggle.
            # The toggle itself stays at its original row.
            self.advanced_frame.grid(
                row=self._advanced_frame_row,
                column=0, columnspan=2,
                sticky=(tk.W, tk.E), pady=(0, 10),
            )
            self.advanced_toggle.config(text="Hide Advanced Options")
        else:
            self.advanced_frame.grid_remove()
            self.advanced_toggle.config(text="Show Advanced Options")

        # Update command preview
        self.update_command_preview()

    def _extract_device_name(self, module_name, module_args):
        """
        Extract a human-readable name from module information.

        Args:
            module_name: The name of the module (e.g., 'module-null-sink')
            module_args: The module's arguments string

        Returns:
            A human-readable description of the device
        """
        # For null sinks, get the sink name
        if 'null-sink' in module_name:
            import re
            sink_match = re.search(r'sink_name=([a-zA-Z0-9_.-]+)', module_args)
            if sink_match:
                sink_name = sink_match.group(1)
                return f"Virtual Device: {sink_name}"
            return "Virtual Audio Device"

        # For hardware devices
        if 'alsa-card' in module_name:
            import re
            card_match = re.search(r'card_name=([^=\s]+)', module_args)
            if card_match:
                card_name = card_match.group(1).strip('"\'')
                return f"Hardware: {card_name}"

        # For HDMI, USB, or other recognizable hardware
        if any(hw_term in module_name for hw_term in ['hdmi', 'usb', 'bluetooth']):
            # Make the module name more readable
            for prefix in ['module-', 'alsa-']:
                if module_name.startswith(prefix):
                    module_name = module_name[len(prefix):]
            return f"Hardware: {module_name.replace('-', ' ').title()}"

        # For bluez devices
        if 'bluez' in module_name:
            import re
            device_match = re.search(r'device=([^=\s]+)', module_args)
            if device_match:
                device_name = device_match.group(1).strip('"\'')
                return f"Bluetooth: {device_name}"

        # For other modules, just make the name more readable
        display_name = module_name.replace('module-', '')
        display_name = display_name.replace('-', ' ').title()

        return display_name

    def on_audio_preset_selected(self, event):
        """Handle selection from the audio preset dropdown."""
        selected_preset = self.audio_preset_var.get()

        # Get preset data from preset manager
        preset_data = self.preset_manager.get_preset(selected_preset)

        if preset_data:
            # Load preset configuration
            self.channels_var.set(preset_data.get("channels", "2"))
            self.channel_map_var.set(preset_data.get("channel_map", ""))

            # Update placeholder text for sink name if user hasn't customized it
            current_name = self.sink_name_var.get()

            # Check if current name is auto-generated or empty
            is_auto_name = (not current_name or
                           current_name.endswith(" (auto)") or
                           current_name in ["stereo", "mono", "surround51", "surround71", "custom"] or
                           any(current_name.startswith(base + "2") or current_name.startswith(base + "3")
                               for base in ["stereo", "mono", "surround51", "surround71", "custom"]))

            if not self.user_has_custom_name or is_auto_name:
                # Reset to auto-naming mode
                self.user_has_custom_name = False
                # Use preset name as base for auto-naming
                base_name = selected_preset.lower().replace(" ", "").replace(".", "")
                # For builtin presets, use traditional names
                if self.preset_manager.is_builtin_preset(selected_preset):
                    builtin_names = {
                        "Stereo": "stereo",
                        "Mono": "mono",
                        "5.1 Surround": "surround51",
                        "7.1 Surround": "surround71",
                        "Custom": "custom"
                    }
                    base_name = builtin_names.get(selected_preset, base_name)

                auto_name = self._get_available_name(base_name)
                self.sink_name_var.set(f"{auto_name} (auto)")
                # Ensure placeholder styling
                self.sink_name_entry.config(foreground="gray")

            # Update description if user hasn't customized it
            current_desc = self.sink_desc_var.get()
            preset_desc = preset_data.get("description", f"{selected_preset} Virtual Device")
            is_auto_desc = (not current_desc or
                           current_desc.endswith("Virtual Device") or
                           current_desc in ["Stereo Virtual Device", "Mono Virtual Device",
                                          "5.1 Surround Virtual Device", "7.1 Surround Virtual Device",
                                          "Custom Virtual Device"])

            if not self.user_has_custom_desc or is_auto_desc:
                self.user_has_custom_desc = False
                self.sink_desc_var.set(preset_desc)

            # Load advanced options if present
            if hasattr(self, 'show_advanced_var'):
                # Sample rate
                if "rate" in preset_data:
                    self.rate_var.set(str(preset_data["rate"]))
                else:
                    self.rate_var.set("44100")

                # Sample format
                if "format" in preset_data:
                    format_code = preset_data["format"]
                    # Convert format code to description
                    if hasattr(self, 'format_reverse_mappings'):
                        format_desc = self.format_reverse_mappings.get(format_code, format_code)
                        self.format_var.set(format_desc)
                else:
                    self.format_var.set("16-bit Little Endian (Default)")

                # Additional properties
                if "properties" in preset_data:
                    self.properties_var.set(preset_data["properties"])
                else:
                    self.properties_var.set("")

        # Update delete button state
        if self.preset_manager.is_builtin_preset(selected_preset):
            self.delete_preset_btn.config(state="disabled")
        else:
            self.delete_preset_btn.config(state="normal")

        # Update command preview
        self.update_command_preview()

    def _get_available_name(self, base_name):
        """Get an available sink name by checking existing sinks and adding incremental numbers if needed."""
        # Clean the base name to be safe for PulseAudio
        # Allow alphanumeric, hyphens, and underscores only
        clean_base = re.sub(r'[^a-zA-Z0-9_-]', '', base_name.lower())

        if not clean_base:
            clean_base = "custom"

        # Get current sinks to check for conflicts
        existing_sinks = PactlRunner.list_sinks()
        existing_names = {sink.get('name', '') for sink in existing_sinks}

        # Check if base name is available
        if clean_base not in existing_names:
            return clean_base

        # Find the next available incremental name
        counter = 2
        while f"{clean_base}{counter}" in existing_names:
            counter += 1

        return f"{clean_base}{counter}"

    def _validate_sink_name(self, name):
        """
        Validate sink name and show user feedback for issues.

        Returns:
            tuple: (is_valid, cleaned_name, error_message)
        """
        if not name:
            return False, "", "Sink name cannot be empty"

        # Remove (auto) suffix if present
        clean_name = name.replace(" (auto)", "").strip()

        if not clean_name:
            return False, "", "Sink name cannot be empty"

        # Check for spaces
        if " " in clean_name:
            return False, "", "Sink name cannot contain spaces"

        # Check for invalid characters
        if not re.match(r'^[a-zA-Z0-9_-]+$', clean_name):
            # Extract valid characters
            valid_chars = re.sub(r'[^a-zA-Z0-9_-]', '', clean_name)
            return False, valid_chars, f"Sink name can only contain letters, numbers, hyphens, and underscores.\nSuggested name: {valid_chars}"

        # Check for conflicts
        existing_sinks = PactlRunner.list_sinks()
        existing_names = {sink.get('name', '') for sink in existing_sinks}

        if clean_name in existing_names:
            # Suggest an available name
            suggested_name = self._get_available_name(clean_name)
            return False, suggested_name, f"Name '{clean_name}' already exists.\nSuggested name: {suggested_name}"

        return True, clean_name, ""

    def on_name_focus_in(self, event):
        """Handle focus in event for the sink name entry."""
        current_value = self.sink_name_var.get()
        if current_value.endswith(" (auto)"):
            # Remove the (auto) suffix and change color to normal
            actual_name = current_value.replace(" (auto)", "")
            self.sink_name_var.set(actual_name)
            self.sink_name_entry.config(foreground="black")
            # Select all text for easy replacement
            self.sink_name_entry.select_range(0, tk.END)

    def on_name_focus_out(self, event):
        """Handle focus out event for the sink name entry."""
        current_value = self.sink_name_var.get().strip()
        if not current_value:
            # User cleared the field, restore auto-naming
            self.user_has_custom_name = False
            # Trigger preset selection to restore placeholder
            self.on_audio_preset_selected(None)
        elif not current_value.endswith(" (auto)"):
            # Check if this looks like an auto-generated name
            auto_names = ["stereo", "mono", "surround51", "surround71", "custom"]
            is_likely_auto = any(current_value.startswith(name) for name in auto_names)

            if not is_likely_auto:
                # User has entered truly custom content
                self.user_has_custom_name = True
                self.sink_name_entry.config(foreground="black")
            else:
                # Might be auto-generated, keep auto mode but ensure proper styling
                self.sink_name_entry.config(foreground="black")

    def on_name_key_press(self, event):
        """Handle key press event for the sink name entry."""
        current_value = self.sink_name_var.get()
        if current_value.endswith(" (auto)"):
            # User is typing, remove auto suffix and mark as custom
            actual_name = current_value.replace(" (auto)", "")
            self.sink_name_var.set(actual_name)
            self.sink_name_entry.config(foreground="black")
            self.user_has_custom_name = True
            # Position cursor at the end
            self.sink_name_entry.icursor(tk.END)

    def on_desc_key_press(self, event):
        """Handle key press event for the sink description entry."""
        self.user_has_custom_desc = True

    def on_format_selected(self, event):
        """Handle selection from the format dropdown."""
        selected_format_desc = event.widget.get()
        if selected_format_desc in self.format_mappings:
            # Update the underlying format variable with the actual format code
            self.format_mappings[selected_format_desc]
            # Don't set it back to avoid infinite loop, just update preview

        # Update command preview
        self.update_command_preview()

    def on_tab_changed(self, event):
        """Handle tab change event to reset form state."""
        # Get the currently selected tab
        selected_tab = self.tab_control.select()
        tab_text = self.tab_control.tab(selected_tab, "text")

        # If switching to Create tab, refresh the placeholder state
        if tab_text == "Create":
            self.refresh_create_tab_state()

    def refresh_create_tab_state(self):
        """Refresh the Create tab state to ensure proper placeholder behavior."""
        # Check if we should reset to auto-naming mode
        current_name = self.sink_name_var.get()

        # If the name looks auto-generated, ensure proper placeholder styling
        if current_name and current_name.endswith(" (auto)"):
            self.sink_name_entry.config(foreground="gray")
        elif not current_name or not self.user_has_custom_name:
            # Reset to auto-naming if field is empty or not customized
            self.user_has_custom_name = False
            self.on_audio_preset_selected(None)

    def refresh_preset_list(self):
        """Refresh the preset list in the audio preset combobox."""
        preset_names = self.preset_manager.get_preset_names()
        self.audio_preset_combo['values'] = preset_names

        # Set default to Stereo if current value is not in the list
        current_value = self.audio_preset_var.get()
        if current_value not in preset_names:
            self.audio_preset_var.set("Stereo")

    def save_current_preset(self):
        """Save the current Create tab configuration as a preset."""
        preset_name = self.audio_preset_var.get().strip()

        if not preset_name:
            messagebox.showerror("Error", "Please enter a preset name")
            return

        if self.preset_manager.is_builtin_preset(preset_name):
            messagebox.showerror("Error", f"Cannot overwrite builtin preset: {preset_name}")
            return

        # Collect current configuration
        preset_data = {
            "channels": self.channels_var.get(),
            "channel_map": self.channel_map_var.get(),
            "description": self.sink_desc_var.get() or f"{preset_name} Virtual Device"
        }

        # Add advanced options if they are set
        if hasattr(self, 'show_advanced_var') and self.show_advanced_var.get():
            rate = self.rate_var.get().strip()
            if rate and rate != "44100":
                preset_data["rate"] = rate

            format_desc = self.format_var.get().strip()
            if format_desc and hasattr(self, 'format_mappings'):
                actual_format = self.format_mappings.get(format_desc, format_desc)
                if actual_format and actual_format != "s16le":
                    preset_data["format"] = actual_format

            properties = self.properties_var.get().strip()
            if properties:
                preset_data["properties"] = properties

        # Save the preset
        if self.preset_manager.save_preset(preset_name, preset_data):
            self.add_output(f"Saved preset: {preset_name}")
            self.status_var.set(f"Saved preset: {preset_name}")
            self.refresh_preset_list()
            messagebox.showinfo("Success", f"Preset '{preset_name}' saved successfully!")
        else:
            self.add_output(f"Failed to save preset: {preset_name}")
            self.status_var.set("Error saving preset")
            messagebox.showerror("Error", f"Failed to save preset: {preset_name}")

    def delete_current_preset(self):
        """Delete the current preset."""
        preset_name = self.audio_preset_var.get().strip()

        if not preset_name:
            messagebox.showinfo("Info", "No preset selected")
            return

        if self.preset_manager.is_builtin_preset(preset_name):
            messagebox.showerror("Error", f"Cannot delete builtin preset: {preset_name}")
            return

        # Confirm deletion
        if not messagebox.askyesno("Confirm Delete", f"Are you sure you want to delete preset '{preset_name}'?"):
            return

        # Delete the preset
        if self.preset_manager.delete_preset(preset_name):
            self.add_output(f"Deleted preset: {preset_name}")
            self.status_var.set(f"Deleted preset: {preset_name}")
            self.refresh_preset_list()
            # Reset to default preset
            self.audio_preset_var.set("Stereo")
            self.on_audio_preset_selected(None)
            messagebox.showinfo("Success", f"Preset '{preset_name}' deleted successfully!")
        else:
            self.add_output(f"Failed to delete preset: {preset_name}")
            self.status_var.set("Error deleting preset")
            messagebox.showerror("Error", f"Failed to delete preset: {preset_name}")

    def on_preset_name_changed(self, event):
        """Handle changes to the preset name in the combobox."""
        # Update delete button state based on whether it's a builtin preset
        preset_name = self.audio_preset_var.get().strip()
        if self.preset_manager.is_builtin_preset(preset_name):
            self.delete_preset_btn.config(state="disabled")
        else:
            self.delete_preset_btn.config(state="normal")

    def update_details_display(self, details):
        """Update the details display with the given details."""
        self.details_text.config(state=tk.NORMAL)
        self.details_text.delete(1.0, tk.END)
        self.details_text.insert(tk.END, details)
        self.details_text.config(state=tk.DISABLED)

    def toggle_details_view(self):
        """Toggle between summary and full details view."""
        # Refresh the current selection to update the display
        selected = self.unified_tree.selection()
        if selected:
            # Trigger a refresh of the details
            self.on_unified_tree_select(None)

    def toggle_monitor_sources(self):
        """Toggle visibility of monitor sources."""
        # Refresh all views which will respect the current state of show_monitors_var
        self.refresh_all_views()

        # Update status
        if self.show_monitors_var.get():
            self.status_var.set("Showing monitor sources under parent devices")
        else:
            self.status_var.set("Monitor sources hidden")

    def _detect_device_type(self, sink_data=None, source_data=None, module_data=None):
        """
        Detect the hardware device type based on module, sink, or source data.
        Updated for PipeWire compatibility.

        Returns: One of 'builtin', 'usb', 'bluetooth', 'hdmi', 'unknown'
        """
        # Combine all available data for analysis
        all_data = []
        if module_data:
            all_data.append(module_data)
        if sink_data:
            all_data.append(sink_data)
        if source_data:
            all_data.append(source_data)

        # Check properties from all sources
        for data in all_data:
            if not data:
                continue

            # Check device name first (PipeWire pattern)
            name = data.get('name', '').lower()

            # PipeWire ALSA device patterns
            if 'alsa_output.usb-' in name or 'alsa_input.usb-' in name:
                return 'usb'
            if 'alsa_output.pci-' in name or 'alsa_input.pci-' in name:
                # Determine if PCI device is HDMI/GPU or built-in audio
                properties = data.get('properties', {})
                device_desc = properties.get('device.description', '').lower()
                if any(gpu in device_desc for gpu in ['nvidia', 'amd', 'radeon', 'intel hd', 'hdmi']):
                    return 'hdmi'
                else:
                    return 'builtin'
            if 'bluez' in name or 'bluetooth' in name:
                return 'bluetooth'

            # Traditional PulseAudio module name patterns
            if any(term in name for term in ['usb', 'usb-audio']):
                return 'usb'
            if any(term in name for term in ['bluez', 'bluetooth']):
                return 'bluetooth'
            if any(term in name for term in ['hdmi', 'displayport']):
                return 'hdmi'

            # Check properties
            properties = data.get('properties', {})

            # USB detection
            if properties.get('device.bus') == 'usb':
                return 'usb'
            if 'usb' in properties.get('device.api', '').lower():
                return 'usb'

            # Bluetooth detection
            if properties.get('device.api') == 'bluez5':
                return 'bluetooth'
            if 'bluetooth' in properties.get('device.description', '').lower():
                return 'bluetooth'

            # HDMI/DisplayPort detection
            if any(term in properties.get('device.description', '').lower()
                   for term in ['hdmi', 'displayport', 'dp']):
                return 'hdmi'
            if 'nvidia' in properties.get('device.description', '').lower():
                return 'hdmi'  # NVIDIA cards typically provide HDMI audio

            # PCI/Built-in detection
            if properties.get('device.bus') == 'pci':
                # Check if it's GPU audio (HDMI) or built-in audio
                description = properties.get('device.description', '').lower()
                if any(gpu in description for gpu in ['nvidia', 'amd', 'intel hd', 'radeon']):
                    return 'hdmi'
                else:
                    return 'builtin'

        # Default to built-in for unidentified hardware
        return 'builtin'

    def _categorize_hardware_devices(self, modules, sinks, sources):
        """
        Categorize hardware devices by connection type and group individual devices.
        Updated for PipeWire compatibility - works with direct device names instead of card modules.

        Returns: Dictionary with categories as keys and device groups as values.
        """
        categories = {
            'builtin': [],
            'usb': [],
            'bluetooth': [],
            'hdmi': []
        }

        # Track processed devices to avoid duplicates
        processed_sinks = set()
        processed_sources = set()
        processed_modules = set()

        # Create device groups by parsing PipeWire device names
        device_groups = {}  # Maps device identifier to device info

        # Process all sinks to identify hardware devices
        for sink in sinks:
            sink_name = sink.get('name', '')
            sink_id = sink.get('id', '')

            # Skip virtual device sinks
            if any(virtual_name in sink_name for virtual_name in ['test', 'voip']):
                continue

            # Skip monitor sources in this pass
            if '.monitor' in sink_name:
                continue

            # Parse hardware device info from sink name
            device_info = self._extract_hardware_device_info_from_name(sink_name, sink)
            if device_info:
                device_key = device_info['device_key']

                # Initialize device group if not exists
                if device_key not in device_groups:
                    device_groups[device_key] = {
                        'device_info': device_info,
                        'modules': [],
                        'sinks': [],
                        'sources': []
                    }

                device_groups[device_key]['sinks'].append(sink)
                processed_sinks.add(sink_id)

        # Process all sources to match them to existing device groups
        for source in sources:
            source_name = source.get('name', '')
            source_id = source.get('id', '')

            # Skip virtual device sources
            if any(virtual_name in source_name for virtual_name in ['test', 'voip']):
                continue

            # Handle monitor sources based on checkbox
            if '.monitor' in source_name and not self.show_monitors_var.get():
                continue

            # Parse device info from source name
            device_info = self._extract_hardware_device_info_from_name(source_name, source)
            if device_info:
                device_key = device_info['device_key']

                # Initialize device group if not exists (for input-only devices)
                if device_key not in device_groups:
                    device_groups[device_key] = {
                        'device_info': device_info,
                        'modules': [],
                        'sinks': [],
                        'sources': []
                    }

                device_groups[device_key]['sources'].append(source)
                processed_sources.add(source_id)

        # Handle remaining hardware modules (for PipeWire compatibility)
        for module in modules:
            module_id = module.get('id', '')
            module_name = module.get('name', '')

            if module_id in processed_modules:
                continue

            # Skip virtual device modules
            if 'null-sink' in module_name:
                continue

            # Only include relevant hardware modules
            if any(hw_term in module_name.lower()
                  for hw_term in ['alsa', 'bluetooth', 'bluez', 'usb', 'hdmi']):

                device_info = self._extract_hardware_device_info(module)
                if device_info:
                    device_key = device_info['device_key']

                    # Try to match to existing device group first
                    matched = False
                    for _existing_key, existing_group in device_groups.items():
                        if self._devices_match(device_info, existing_group['device_info']):
                            existing_group['modules'].append(module)
                            matched = True
                            break

                    # Create new group if no match
                    if not matched:
                        device_groups[device_key] = {
                            'device_info': device_info,
                            'modules': [module],
                            'sinks': [],
                            'sources': []
                        }

                processed_modules.add(module_id)

        # Process orphaned sinks/sources (those not matched to any device)
        for sink in sinks:
            if sink.get('id') not in processed_sinks:
                sink_name = sink.get('name', '')
                if any(virtual_name in sink_name for virtual_name in ['test', 'voip']):
                    continue

                device_type = self._detect_device_type(sink_data=sink)
                device_entry = {
                    'type': 'orphaned_sink',
                    'sink': sink
                }
                categories[device_type].append(device_entry)

        for source in sources:
            if source.get('id') not in processed_sources:
                source_name = source.get('name', '')
                if any(virtual_name in source_name for virtual_name in ['test', 'voip']):
                    continue
                if '.monitor' in source_name and not self.show_monitors_var.get():
                    continue

                device_type = self._detect_device_type(source_data=source)
                device_entry = {
                    'type': 'orphaned_source',
                    'source': source
                }
                categories[device_type].append(device_entry)

        # Finally, categorize the complete device groups
        for _device_key, device_group in device_groups.items():
            device_info = device_group['device_info']
            device_type = device_info['device_type']

            device_entry = {
                'type': 'hardware_device_group',
                'device_info': device_info,
                'modules': device_group['modules'],
                'sinks': device_group['sinks'],
                'sources': device_group['sources']
            }

            categories[device_type].append(device_entry)

        return categories

    def _extract_hardware_device_info(self, module_or_device):
        """Extract device information for hardware device identification."""
        properties = module_or_device.get('properties', {})
        module_name = module_or_device.get('name', '')
        module_args = module_or_device.get('argument', '')

        # Get device description - primary identifier
        device_description = (
            properties.get('device.description', '') or
            properties.get('alsa.card_name', '') or
            module_or_device.get('description', '')
        )

        # Get card name from module arguments if available
        if 'card=' in module_args:
            import re
            card_match = re.search(r'card=([^=\s]+)', module_args)
            if card_match:
                card_name = card_match.group(1).strip('"\'')
                if not device_description:
                    device_description = card_name

        # Fall back to module name if no description
        if not device_description:
            device_description = module_name.replace('module-', '').replace('-', ' ').title()

        # Determine device type
        device_type = self._detect_device_type(module_data=module_or_device)

        # Create unique device key for grouping
        # Use device.string if available, otherwise device description + bus info
        device_string = properties.get('device.string', '')
        if device_string:
            device_key = device_string
        else:
            bus_info = properties.get('device.bus', 'unknown')
            device_key = f"{device_description}_{bus_info}_{device_type}"

        return {
            'device_key': device_key,
            'device_type': device_type,
            'device_name': device_description,
            'device_string': device_string,
            'properties': properties
        }

    def _match_sink_to_device(self, sink, device_groups):
        """Match a sink to an existing hardware device group."""
        sink_properties = sink.get('properties', {})
        sink_device_string = sink_properties.get('device.string', '')

        # Try to match by device.string first (most reliable)
        if sink_device_string:
            for device_key, device_group in device_groups.items():
                if device_group['device_info']['device_string'] == sink_device_string:
                    return device_key

        # Try to match by device description
        sink_desc = sink.get('description', '')
        for device_key, device_group in device_groups.items():
            device_name = device_group['device_info']['device_name']
            # Check if sink description contains device name or vice versa
            if device_name in sink_desc or sink_desc in device_name:
                return device_key

        # Try to match by owner module
        sink_owner_module = sink.get('owner_module', '')
        if sink_owner_module:
            for device_key, device_group in device_groups.items():
                for module in device_group['modules']:
                    if str(module.get('id', '')) == str(sink_owner_module):
                        return device_key

        return None

    def _match_source_to_device(self, source, device_groups):
        """Match a source to an existing hardware device group."""
        source_properties = source.get('properties', {})
        source_device_string = source_properties.get('device.string', '')

        # Try to match by device.string first (most reliable)
        if source_device_string:
            for device_key, device_group in device_groups.items():
                if device_group['device_info']['device_string'] == source_device_string:
                    return device_key

        # Try to match by device description
        source_desc = source.get('description', '')
        for device_key, device_group in device_groups.items():
            device_name = device_group['device_info']['device_name']
            # Check if source description contains device name or vice versa
            if device_name in source_desc or source_desc in device_name:
                return device_key

        # Try to match by owner module
        source_owner_module = source.get('owner_module', '')
        if source_owner_module:
            for device_key, device_group in device_groups.items():
                for module in device_group['modules']:
                    if str(module.get('id', '')) == str(source_owner_module):
                        return device_key

        return None

    def _generate_hardware_device_group_summary(self, device_name, tree_item_id):
        """Generate comprehensive summary for hardware device groups."""
        children = self.unified_tree.get_children(tree_item_id)

        # Collect information from child components
        module_info = None
        sink_info = None
        source_info = None

        for child in children:
            child_item = self.unified_tree.item(child)
            child_values = child_item.get('values', [])
            if len(child_values) >= 3:
                child_id, child_type, child_name = child_values

                if child_type == "module":
                    # Get module details
                    modules = PactlRunner.list_modules()
                    for mod in modules:
                        if str(mod.get('id', '')) == str(child_id):
                            module_info = mod
                            break

                elif child_type == "sink":
                    # Get sink details
                    sinks = PactlRunner.list_sinks()
                    for sink in sinks:
                        if str(sink.get('id', '')) == str(child_id):
                            sink_info = sink
                            break

                elif child_type == "source":
                    # Get source details
                    sources = PactlRunner.list_sources()
                    for source in sources:
                        if str(source.get('id', '')) == str(child_id):
                            source_info = source
                            break

        if self.show_all_details_var.get():
            # Full details view - show all component information
            info = f"Hardware Device: {device_name}\n"
            info += f"{'='*50}\n\n"

            if module_info:
                info += "MODULE DETAILS:\n"
                info += f"ID: {module_info.get('id', 'Unknown')}\n"
                info += f"Name: {module_info.get('name', 'Unknown')}\n"
                info += f"Arguments: {module_info.get('argument', 'None')}\n"

                # Add module properties if available
                module_properties = module_info.get('properties', {})
                if module_properties:
                    info += "Module Properties:\n"
                    for prop_key, prop_value in module_properties.items():
                        info += f"  {prop_key} = {prop_value}\n"
                info += "\n"

            if sink_info:
                info += "SINK (OUTPUT) DETAILS:\n"
                for key, value in sink_info.items():
                    if key not in ['id', 'name']:
                        if key == 'properties' and isinstance(value, dict):
                            info += "Sink Properties:\n"
                            for prop_key, prop_value in value.items():
                                info += f"  {prop_key} = {prop_value}\n"
                        else:
                            info += f"{key}: {value}\n"
                info += "\n"

            if source_info:
                info += "SOURCE (INPUT) DETAILS:\n"
                for key, value in source_info.items():
                    if key not in ['id', 'name']:
                        if key == 'properties' and isinstance(value, dict):
                            info += "Source Properties:\n"
                            for prop_key, prop_value in value.items():
                                info += f"  {prop_key} = {prop_value}\n"
                        else:
                            info += f"{key}: {value}\n"

            return info
        else:
            # Summary view - hardware device specifications
            info = f"Hardware Device: {device_name}\n"

            # Device overview
            component_count = len(children)
            info += f"Components: {component_count}\n"

            # Get device information from the best available source
            device_info = sink_info or source_info or module_info
            if device_info:
                state = device_info.get('state', 'Unknown')
                info += f"State: {state}\n"

                driver = device_info.get('driver', 'Unknown')
                info += f"Driver: {driver}\n"

                # Hardware device properties
                properties = device_info.get('properties', {})

                # Device identification and connection info
                device_class = properties.get('device.class', 'Unknown')
                if device_class != 'Unknown':
                    info += f"Device Class: {device_class}\n"

                device_api = properties.get('device.api', 'Unknown')
                if device_api != 'Unknown':
                    info += f"API: {device_api}\n"

                # Connection info
                device_bus = properties.get('device.bus', 'Unknown')
                if device_bus != 'Unknown':
                    info += f"Connection: {device_bus.upper()}\n"

                # Vendor/Product information
                vendor_name = properties.get('device.vendor.name', '')
                product_name = properties.get('device.product.name', '')
                if vendor_name and product_name:
                    info += f"Manufacturer: {vendor_name}\n"
                    info += f"Product: {product_name}\n"
                elif vendor_name:
                    info += f"Vendor: {vendor_name}\n"

                # Hardware ID information
                vendor_id = properties.get('device.vendor.id', '')
                product_id = properties.get('device.product.id', '')
                if vendor_id and product_id:
                    info += f"Hardware ID: {vendor_id}:{product_id}\n"

                # Audio Engineering Essentials
                sample_spec = device_info.get('sample_spec', 'Unknown')
                if sample_spec != 'Unknown':
                    info += f"\nAudio Specification: {sample_spec}\n"

                channel_map = device_info.get('channel_map', 'Unknown')
                if channel_map != 'Unknown':
                    info += f"Channel Layout: {channel_map}\n"

                latency = device_info.get('latency', 'Unknown')
                if latency != 'Unknown':
                    info += f"Latency: {latency}\n"

                # Buffer settings from properties
                quantum_limit = properties.get('clock.quantum-limit', 'N/A')
                if quantum_limit != 'N/A':
                    info += f"Buffer Quantum Limit: {quantum_limit}\n"

                # Volume and mute status
                mute = device_info.get('mute', 'Unknown')
                if mute != 'Unknown':
                    info += f"\nMute: {mute}\n"

                volume = device_info.get('volume', 'Unknown')
                if volume != 'Unknown' and len(str(volume)) < 100:
                    info += f"Volume: {volume}\n"

            # Usage instructions for hardware devices
            info += "\nHardware Device:\n"
            info += "• Physical audio device connected to system\n"
            if sink_info:
                info += "• Applications can play audio through this device\n"
            if source_info and '.monitor' not in source_info.get('name', ''):
                info += "• Can record audio from this device\n"
            if module_info and 'card' in module_info.get('name', ''):
                info += "• Select module component to unload device driver\n"
            else:
                info += "• Hardware managed by system audio drivers\n"

            return info

    def _extract_hardware_device_info_from_name(self, device_name, device_data):
        """
        Extract device information from PipeWire device names.

        Examples:
        - alsa_output.usb-BOSS_GCS-8-01.pro-output-0 -> Device: BOSS GCS-8
        - alsa_input.usb-BEHRINGER_UMC404HD_192k-00.pro-input-0 -> Device: BEHRINGER UMC404HD
        - alsa_output.pci-0000_01_00.1.hdmi-stereo -> Device: GPU HDMI Audio
        """
        if not device_name:
            return None

        # Parse PipeWire ALSA device naming patterns
        if device_name.startswith('alsa_'):
            # Extract connection type and device identifier
            parts = device_name.split('.')
            if len(parts) >= 2:
                connection_part = parts[1]  # e.g., "usb-BOSS_GCS-8-01" or "pci-0000_01_00"

                # Determine connection type
                if connection_part.startswith('usb-'):
                    device_type = 'usb'
                    # Extract device name from USB identifier
                    usb_part = connection_part[4:]  # Remove "usb-"
                    # Parse patterns like "BOSS_GCS-8-01" or "BEHRINGER_UMC404HD_192k-00"
                    device_identifier = usb_part.rsplit('-', 1)[0]  # Remove trailing number
                    # Clean up device name
                    device_name_clean = device_identifier.replace('_', ' ').replace('-', ' ')
                    # Extract brand and model
                    if ' ' in device_name_clean:
                        parts = device_name_clean.split(' ', 1)
                        brand = parts[0]
                        model = parts[1] if len(parts) > 1 else ''
                        device_display_name = f"{brand} {model}".strip()
                    else:
                        device_display_name = device_name_clean

                elif connection_part.startswith('pci-'):
                    # PCI devices (usually GPU HDMI or built-in audio)
                    properties = device_data.get('properties', {})
                    device_desc = properties.get('device.description', '')

                    if any(gpu in device_desc.lower() for gpu in ['nvidia', 'amd', 'radeon', 'intel hd']):
                        device_type = 'hdmi'
                        device_display_name = device_desc or 'GPU Audio'
                        device_identifier = connection_part
                    else:
                        device_type = 'builtin'
                        device_display_name = device_desc or 'Built-in Audio'
                        device_identifier = connection_part

                elif connection_part.startswith('bluez-'):
                    device_type = 'bluetooth'
                    device_identifier = connection_part
                    properties = device_data.get('properties', {})
                    device_display_name = properties.get('device.description', 'Bluetooth Audio')

                else:
                    # Unknown connection type
                    device_type = 'builtin'
                    device_identifier = connection_part
                    device_display_name = device_data.get('description', device_name)

                # Create device key for grouping (without input/output suffix)
                device_key = f"{device_type}_{device_identifier}"

                return {
                    'device_key': device_key,
                    'device_type': device_type,
                    'device_name': device_display_name,
                    'device_identifier': device_identifier,
                    'connection_part': connection_part,
                    'properties': device_data.get('properties', {})
                }

        # Fallback for non-ALSA devices
        device_type = self._detect_device_type(sink_data=device_data if 'sink' in str(type(device_data)) else None,
                                             source_data=device_data if 'source' in str(type(device_data)) else None)
        device_display_name = device_data.get('description', device_name)

        return {
            'device_key': f"{device_type}_{device_name}",
            'device_type': device_type,
            'device_name': device_display_name,
            'device_identifier': device_name,
            'connection_part': device_name,
            'properties': device_data.get('properties', {})
        }

    def _devices_match(self, device_info1, device_info2):
        """Check if two device info objects represent the same physical device."""
        # Primary match: same device identifier
        if device_info1['device_identifier'] == device_info2['device_identifier']:
            return True

        # Secondary match: same connection part (for PipeWire ALSA devices)
        if (device_info1.get('connection_part') and device_info2.get('connection_part') and
            device_info1['connection_part'] == device_info2['connection_part']):
            return True

        # Tertiary match: similar device names
        name1 = device_info1['device_name'].lower()
        name2 = device_info2['device_name'].lower()
        if name1 == name2:
            return True

        return False
