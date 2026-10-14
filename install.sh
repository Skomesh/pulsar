#!/bin/bash

# Exit on error
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}Installing Pulsar...${NC}"

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to check Python tkinter module
check_tkinter() {
    python3 -c "import tkinter" >/dev/null 2>&1
}

# Dependency checks
echo -e "${YELLOW}Checking dependencies...${NC}"

# Check for Python3
if ! command_exists python3; then
    echo -e "${RED}Error: Python3 is not installed${NC}"
    echo "Please install Python3 and try again"
    exit 1
fi
echo -e "${GREEN}✓ Python3 found${NC}"

# Check for Tkinter
if ! check_tkinter; then
    echo -e "${RED}Error: Python3 Tkinter module is not available${NC}"
    echo "Please install python3-tkinter package:"
    echo "  Ubuntu/Debian: sudo apt install python3-tkinter"
    echo "  Fedora/RHEL:   sudo dnf install python3-tkinter"
    echo "  Arch Linux:    sudo pacman -S tk"
    exit 1
fi
echo -e "${GREEN}✓ Python3 Tkinter found${NC}"

# Check for pactl (PulseAudio)
if ! command_exists pactl; then
    echo -e "${RED}Error: pactl (PulseAudio) is not installed${NC}"
    echo "Please install PulseAudio and try again:"
    echo "  Ubuntu/Debian: sudo apt install pulseaudio pulseaudio-utils"
    echo "  Fedora/RHEL:   sudo dnf install pulseaudio pulseaudio-utils"
    echo "  Arch Linux:    sudo pacman -S pulseaudio"
    exit 1
fi
echo -e "${GREEN}✓ PulseAudio (pactl) found${NC}"

echo -e "${GREEN}All dependencies satisfied!${NC}"
echo ""

# Define directories (user-specific)
APP_NAME="pulsar"
APP_DIR="$HOME/.local/share/$APP_NAME"
DESKTOP_DIR="$HOME/.local/share/applications"
BIN_DIR="$HOME/.local/bin"
ICON_DIR="$HOME/.local/share/icons/hicolor"

# Resolve the script's own directory, not the caller's cwd.
#
# The copy below used to be `cp -r ./* "$APP_DIR/"`, which globs the INVOKER's
# working directory. Running `bash /path/to/install.sh` from an unrelated
# directory installed that directory's contents instead of Pulsar's -- and
# `chmod +x "$APP_DIR/pulsar"` then aborted the install. Anchoring to the
# script location makes `bash ~/src/pulsar/install.sh` work from anywhere.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Create directories if they don't exist
echo -e "${YELLOW}Creating installation directories...${NC}"
mkdir -p "$APP_DIR"
mkdir -p "$DESKTOP_DIR"
mkdir -p "$BIN_DIR"
mkdir -p "$ICON_DIR/48x48/apps"
mkdir -p "$ICON_DIR/64x64/apps"
mkdir -p "$ICON_DIR/128x128/apps"
mkdir -p "$ICON_DIR/scalable/apps"

# Preserve user data across reinstalls.
#
# The app resolves its user presets dir to
# $HOME/.local/share/pulsar/presets (see src/config/paths.py), which lives
# INSIDE APP_DIR. This script then copied the repo's presets/user_presets.json
# straight over that file, so every upgrade silently replaced the user's saved
# audio routing with the repo's sample entries, and `uninstall.sh`'s
# `rm -rf "$APP_DIR"` deleted them outright. Back the file up before
# overwriting, and restore it afterwards.
USER_PRESETS="$APP_DIR/presets/user_presets.json"
PRESETS_BACKUP=""
if [ -f "$USER_PRESETS" ]; then
    PRESETS_BACKUP="$(mktemp)"
    cp "$USER_PRESETS" "$PRESETS_BACKUP"
    echo -e "${YELLOW}Preserved your saved profiles (will be restored after install)${NC}"
fi

# Copy application files
echo -e "${YELLOW}Copying application files to $APP_DIR...${NC}"
# Prune the worktree noise that used to get installed alongside the app:
# build artifacts, caches, VCS metadata, and coverage data.
rm -rf "$APP_DIR/build" "$APP_DIR/dist" "$APP_DIR/.git" "$APP_DIR/.pytest_cache"
cp -r "$SCRIPT_DIR"/* "$APP_DIR/"

# Restore the user's own profiles over the freshly copied defaults.
if [ -n "$PRESETS_BACKUP" ]; then
    mkdir -p "$(dirname "$USER_PRESETS")"
    cp "$PRESETS_BACKUP" "$USER_PRESETS"
    rm -f "$PRESETS_BACKUP"
fi

# Remove installation scripts from the installed location (they're not needed there)
# uninstall.sh is deliberately KEPT: the completion message points users at it.
# Deleting it here meant the documented uninstall command did not exist.
rm -f "$APP_DIR/install.sh" "$APP_DIR/commit-to-install.sh"

# Make the launcher script executable
chmod +x "$APP_DIR/pulsar"

# Create symlink in bin directory
echo -e "${YELLOW}Creating symbolic link in $BIN_DIR...${NC}"
ln -sf "$APP_DIR/pulsar" "$BIN_DIR/pulsar"
# Symlink the uninstaller too. The completion message tells users to run
# $BIN_DIR/uninstall.sh, and advertising a path that was never created is
# exactly the bug this replaces (it previously pointed at $APP_DIR/uninstall.sh
# after deleting that file).
ln -sf "$APP_DIR/uninstall.sh" "$BIN_DIR/uninstall.sh"

# Install desktop file with correct paths
echo -e "${YELLOW}Installing desktop file to $DESKTOP_DIR...${NC}"
# Escape the Exec path per the desktop-entry spec: a value containing spaces
# must be quoted, otherwise a $HOME like "/home/John Doe" makes the launcher
# tokenize as "/home/John" plus an argument. It works fine from a terminal and
# fails only from the desktop menu, which is where it matters.
EXEC_PATH="$BIN_DIR/pulsar"
case "$EXEC_PATH" in
    *[[:space:]]*) ESCAPED_EXEC="\"$EXEC_PATH\"" ;;
    *)             ESCAPED_EXEC="$EXEC_PATH" ;;
esac

cat > "$DESKTOP_DIR/pulsar.desktop" << EOL
[Desktop Entry]
Version=1.0
Type=Application
Name=Pulsar
GenericName=Audio Control
Comment=Graphical audio routing manager
Exec=$ESCAPED_EXEC
Icon=audio-card
Terminal=false
Categories=AudioVideo;Audio;Settings;
Keywords=sound;audio;volume;pulseaudio;pulse;mixer;
StartupNotify=true
X-KDE-SubstituteUID=false
X-KDE-StartupNotify=true
EOL

chmod +x "$DESKTOP_DIR/pulsar.desktop"

# Update desktop database
echo -e "${YELLOW}Updating desktop database...${NC}"
if command_exists update-desktop-database; then
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
fi

# For KDE specifically
if command_exists kbuildsycoca5; then
    echo -e "${YELLOW}Refreshing KDE application cache...${NC}"
    kbuildsycoca5 --noincremental >/dev/null 2>&1 || true
elif command_exists kbuildsycoca6; then
    echo -e "${YELLOW}Refreshing KDE application cache...${NC}"
    kbuildsycoca6 --noincremental >/dev/null 2>&1 || true
fi

# Add ~/.local/bin to PATH if it's not already there
if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo ""
    echo -e "${YELLOW}Note: $HOME/.local/bin is not in your PATH${NC}"
    echo "To run 'pulsar' from anywhere, add this line to your ~/.bashrc or ~/.zshrc:"
    echo "export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo ""
fi

echo ""
echo -e "${GREEN}Installation complete!${NC}"
echo ""
echo -e "${BLUE}You can now launch Pulsar:${NC}"
echo "• From your application menu (Audio → Pulsar)"
echo "• By typing 'pulsar' in a terminal"
echo "• By running '$BIN_DIR/pulsar'"
echo ""
echo -e "${YELLOW}Note: If the application doesn't appear in your menu immediately:${NC}"
echo "1. Log out and log back in to your desktop session"
echo "2. Or restart your desktop environment"
echo ""
# uninstall.sh IS kept in $APP_DIR. The copy step above used to delete it
# alongside install.sh and then point the user at the path it had just removed.
echo -e "${BLUE}To uninstall, run: $BIN_DIR/uninstall.sh${NC}" 