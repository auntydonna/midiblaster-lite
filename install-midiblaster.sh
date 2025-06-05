#!/bin/bash
set -euo pipefail

# Detect current user
CURRENT_USER=$(logname)
HOME_DIR="/home/$CURRENT_USER"

log() {
  echo -e "\e[32m[INFO]\e[0m $1"
}

error() {
  echo -e "\e[31m[ERROR]\e[0m $1" >&2
}

# Ensure user is part of necessary groups
log "Ensuring user $CURRENT_USER has necessary group access..."
for group in gpio i2c audio plugdev; do
  if ! id -nG "$CURRENT_USER" | grep -qw "$group"; then
    sudo usermod -aG "$group" "$CURRENT_USER"
    log "Added $CURRENT_USER to $group group."
  fi
done

# Copy resources
log "Creating application directories..."
mkdir -p "$HOME_DIR/scripts" "$HOME_DIR/soundfonts" "$HOME_DIR/logs"

log "Checking and moving scripts..."
if [ -d ./scripts ] && [ "$(ls -A ./scripts)" ]; then
  mv -n ./scripts/* "$HOME_DIR/scripts/"
  log "Copied scripts from local ./scripts"
elif [ ! -d "$HOME_DIR/scripts" ] || [ -z "$(ls -A "$HOME_DIR/scripts")" ]; then
  error "No scripts found in ./scripts or $HOME_DIR/scripts. Aborting."
  exit 1
else
  log "Scripts already exist in destination. Skipping copy."
fi

log "Checking and moving soundfonts..."
if [ -d ./soundfonts ] && [ "$(ls -A ./soundfonts)" ]; then
  mv -n ./soundfonts/* "$HOME_DIR/soundfonts/"
  log "Copied soundfonts from local ./soundfonts"
elif [ ! -d "$HOME_DIR/soundfonts" ] || [ -z "$(ls -A "$HOME_DIR/soundfonts")" ]; then
  error "No soundfonts found in ./soundfonts or $HOME_DIR/soundfonts. Aborting."
  exit 1
else
  log "Soundfonts already exist in destination. Skipping copy."
fi

chown -R "$CURRENT_USER:$CURRENT_USER" "$HOME_DIR/scripts" "$HOME_DIR/soundfonts"
log "Installing required packages..."
sudo apt update
sudo apt install -y \
  python3-pip \
  fluidsynth

# Install Python packages required for midiblaster.py
log "Installing Python dependencies..."
PYTHON_DEPS=(pygame smbus2 rpi-lgpio)
for pkg in "${PYTHON_DEPS[@]}"; do
  if ! python3 -c "import $pkg" >/dev/null 2>&1; then
    log "Installing missing Python package: $pkg"
    sudo pip3 install --break-system-packages "$pkg"
  else
    log "Python package $pkg already installed."
  fi
done

log "Configuring analog audio output..."

# Force audio to AUX (headphone jack)
sudo amixer cset numid=3 1 >/dev/null 2>&1 || true

# Set volume to 100% and unmute
sudo amixer set Master 100% unmute >/dev/null 2>&1 || true

# Add I2C enable line if it's not already present
CONFIG_FILE="/boot/firmware/config.txt"
if ! grep -E '^\s*dtparam=i2c_arm=on' "$CONFIG_FILE" >/dev/null; then
    echo "Enabling I2C in $CONFIG_FILE..."
    echo "dtparam=i2c_arm=on" | sudo tee -a "$CONFIG_FILE"
fi

# Create the i2c-dev autoload config only if it doesn't already exist or contains the wrong line
MODULES_FILE="/etc/modules-load.d/i2c.conf"
if [ ! -f "$MODULES_FILE" ] || ! grep -q "^i2c-dev" "$MODULES_FILE"; then
    echo "Creating $MODULES_FILE to load i2c-dev on boot..."
    echo "i2c-dev" | sudo tee "$MODULES_FILE" > /dev/null
fi

# Create systemd service for auto-start
SERVICE_FILE="/etc/systemd/system/midiblaster.service"
log "Creating systemd service..."
sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=MIDI Blaster Autostart
After=network.target sound.target
Requires=local-fs.target

[Service]
ExecStart=/usr/bin/python3 $HOME_DIR/scripts/midiblaster.py
WorkingDirectory=$HOME_DIR/scripts
StandardOutput=append:$HOME_DIR/logs/fmb.log
StandardError=append:$HOME_DIR/logs/fmb.err
Restart=on-failure
User=$CURRENT_USER

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable midiblaster.service

log "Setup complete. Rebooting in 5 seconds..."
sleep 5
sudo reboot
