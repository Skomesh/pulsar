#!/bin/bash

# Exit on error
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}Uninstalling PulseAudio Control GUI...${NC}"

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Define directories (user-specific)
APP_NAME="pulsar"
APP_DIR="$HOME/.local/share/$APP_NAME"
DESKTOP_DIR="$HOME/.local/share/applications"
BIN_DIR="$HOME/.local/bin"

# Check if installation exists
if [ ! -d "$APP_DIR" ] && [ ! -f "$BIN_DIR/pulsar" ] && [ ! -f "$DESKTOP_DIR/pulsar.desktop" ]; then
    echo -e "${YELLOW}PulseAudio Control GUI doesn't appear to be installed${NC}"
    echo "Nothing to uninstall."
    exit 0
fi

# Remove application directory
#
# The user's saved audio profiles live at $APP_DIR/presets/user_presets.json
# (src/config/paths.py resolves the user presets dir inside the app dir), so a
# blanket `rm -rf "$APP_DIR"` destroyed irreplaceable routing setups. Stash the
# profiles first, wipe the app, then restore them to a directory that is NOT
# inside the app dir.
PRESETS_REL="presets"
USER_PRESETS="$APP_DIR/$PRESETS_REL/user_presets.json"
PRESETS_KEEP="$HOME/.local/share/pulsar-user-profiles"

if [ -d "$APP_DIR" ]; then
    echo -e "${YELLOW}Removing application files from $APP_DIR...${NC}"
    if [ -d "$APP_DIR/$PRESETS_REL" ]; then
        echo -e "${YELLOW}Saving your audio profiles before removal...${NC}"
        mkdir -p "$PRESETS_KEEP"
        # Copy the CONTENTS, not the directory itself. `cp -r dir dest/` creates
        # dest/dir/, which then doesn't match the flat user_presets.json path
        # the restore step below looks for -- profiles would be saved and then
        # silently not restored.
        cp -r "$APP_DIR/$PRESETS_REL/." "$PRESETS_KEEP/"
        echo -e "${GREEN}✓ Profiles preserved in $PRESETS_KEEP${NC}"
    fi
    rm -rf "$APP_DIR"
    echo -e "${GREEN}✓ Application files removed${NC}"
else
    echo -e "${YELLOW}Application directory not found, skipping...${NC}"
fi

# Put the preserved profiles back where the app will look for them on next run.
if [ -f "$PRESETS_KEEP/user_presets.json" ]; then
    echo -e "${YELLOW}Restoring your audio profiles...${NC}"
    mkdir -p "$(dirname "$USER_PRESETS")"
    cp "$PRESETS_KEEP/user_presets.json" "$USER_PRESETS"
    echo -e "${GREEN}✓ Profiles restored to $PRESETS_REL${NC}"
fi

# Remove symlinks from bin directory
if [ -L "$BIN_DIR/pulsar" ] || [ -f "$BIN_DIR/pulsar" ]; then
    echo -e "${YELLOW}Removing symlink from $BIN_DIR...${NC}"
    rm -f "$BIN_DIR/pulsar"
    echo -e "${GREEN}✓ Command-line launcher removed${NC}"
else
    echo -e "${YELLOW}Command-line launcher not found, skipping...${NC}"
fi

# The uninstaller symlink was added alongside the launcher; clean it up too,
# otherwise uninstalling leaves a dangling $BIN_DIR/uninstall.sh.
rm -f "$BIN_DIR/uninstall.sh"

# Remove desktop file
if [ -f "$DESKTOP_DIR/pulsar.desktop" ]; then
    echo -e "${YELLOW}Removing desktop file...${NC}"
    rm -f "$DESKTOP_DIR/pulsar.desktop"
    echo -e "${GREEN}✓ Desktop integration removed${NC}"
else
    echo -e "${YELLOW}Desktop file not found, skipping...${NC}"
fi

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

echo ""
echo -e "${GREEN}Uninstallation complete!${NC}"
echo -e "${BLUE}PulseAudio Control GUI has been removed from your user account.${NC}" 