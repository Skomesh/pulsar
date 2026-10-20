"""Launch the real app under Xvfb and screenshot each tab.

This is the verification the whole session lacked: a real Tk window,
real widgets, real layout -- not a bare object.__new__ instance.

Usage: .venv/bin/python scripts/screenshot_app.py <display> <outdir>
"""
import os
import subprocess
import sys
import time

display = sys.argv[1] if len(sys.argv) > 1 else ":77"
outdir = sys.argv[2] if len(sys.argv) > 2 else "/tmp/pulsar-shots"

os.makedirs(outdir, exist_ok=True)

import tkinter as tk  # noqa: E402  (after DISPLAY is read from argv, below)

sys.path.insert(0, "src")
from pulsar.ui.main_window import MainWindow  # noqa: E402

if display != ":77":
    os.environ["DISPLAY"] = display

root = tk.Tk()
app = MainWindow(root)
root.update_idletasks()
root.update()


def shoot(name):
    """Grab the root window via xwd and convert it to PNG.

    ImageMagick and Pillow's XWD plugin are both unavailable here, so this
    shells out to xwd and converts with scripts/xwd_to_png.py.
    """
    path = f"{outdir}/{name}.png"
    root.update()
    raw = subprocess.run(
        ["xwd", "-root", "-silent"], capture_output=True
    )
    if raw.returncode != 0 or not raw.stdout:
        print(f"  {name}: xwd failed")
        return
    tmp = f"{outdir}/{name}.xwd"
    with open(tmp, "wb") as fh:
        fh.write(raw.stdout)
    conv = subprocess.run(
        [sys.executable, "scripts/xwd_to_png.py", tmp, path],
        capture_output=True, text=True,
    )
    if conv.returncode != 0:
        print(f"  {name}: convert failed: {conv.stderr.strip()[:200]}")
    else:
        w, h = root.winfo_width(), root.winfo_height()
        print(f"  {name}: captured ({w}x{h} window)")
    os.unlink(tmp)


print("tabs:", [app.tab_control.tab(t, "text") for t in app.tab_control.tabs()])
shoot("00-initial")

for i, tab in enumerate(app.tab_control.tabs(), 1):
    label = app.tab_control.tab(tab, "text").replace("/", "-")
    app.tab_control.select(tab)
    root.update_idletasks()
    root.update()
    time.sleep(0.4)
    root.update()
    shoot(f"{i:02d}-{label.replace(' ', '-')}")

# And specifically the App Audio panel with a stream selected, to see the
# two routing buttons and the explanatory label.
app.tab_control.select(
    [t for t in app.tab_control.tabs()
     if app.tab_control.tab(t, "text") == "App Audio"][0]
)
root.update()
time.sleep(0.5)
root.update()
shoot("99-app-audio-detail")

print("exclusive button label:", app.capture_exclusive_button.cget("text"))
print("route button label:    ", app.capture_route_button.cget("text"))
print("combobox width:        ", app.capture_route_combo.cget("width"))

root.destroy()
print("done ->", outdir)
