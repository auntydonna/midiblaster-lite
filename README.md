# MIDI Blaster Lite

A lightweight, headless version of the MIDI Blaster project by Luke The Maker, designed to run on Raspberry Pi OS 64-bit Lite. This version offers improved performance, better control, and additional features while maintaining the core functionality of playing MIDI files through a floppy disk drive interface.

## Features

- Runs headless on Raspberry Pi OS 64-bit Lite for faster boot times and better performance
- Plays MIDI files from a USB floppy disk drive
- Supports multiple soundfont categories and soundfonts
- State persistence - remembers your last-used soundfont and category across restarts
- Automatic track advancement when a song finishes
- Numeric track ordering with two-digit prefixes (e.g., "01_", "02-", "03 ")
- Graceful service management with proper shutdown handling
- LCD display support for track and soundfont information
- Physical button controls for playback and navigation
- Runs as a systemd service for automatic startup
- Thread-safe operation - prevents display/playback synchronization issues
- Smart state saving - automatically saves preferences after 10 seconds of no changes

## Hardware Requirements

- Raspberry Pi (3B+ or newer recommended)
- USB Floppy Disk Drive
- I2C LCD Display (16x2)
- Physical buttons for control
- Audio output (3.5mm jack or HDMI)

## Installation

1. Install Raspberry Pi OS 64-bit Lite on your Raspberry Pi
2. Clone this repository:
   ```bash
   git clone https://github.com/auntydonna/midiblaster-lite.git
   cd midiblaster-lite
   ```

3. Add your soundfonts to `/home/<user>/midiblaster-lite/soundfonts/` organized by category:
   ```
   soundfonts/
   ├── category1/
   │   ├── soundfont1.sf2
   │   └── soundfont2.sf2
   ├── category2/
   │   ├── soundfont3.sf2
   │   └── soundfont4.sf2
   └── category3/
       ├── soundfont5.sf2
       └── soundfont6.sf2
   ```
   Each category folder should contain one or more .sf2 soundfont files.

4. Run the installation script:
   ```bash
   sudo bash install-midiblaster.sh
   ```

The installation script will:
- Add necessary user groups (gpio, i2c, audio, plugdev)
- Install required packages (python3-pip, fluidsynth)
- Install Python dependencies (pygame, smbus2, rpi-lgpio)
- Configure audio output
- Enable I2C interface
- Set up the systemd service for auto-start
- Create necessary directories for scripts, soundfonts, and logs

## Directory Structure

- `/home/<user>/scripts/` - Contains the main Python script
- `/home/<user>/soundfonts/` - Soundfont files organized by category
- `/home/<user>/logs/` - Log files for the service
- `/home/<user>/.midiblaster_state.json` - State persistence file (created automatically)
- `/media/mididisk` - Mount point for the USB floppy drive

## Usage

1. Insert a floppy disk with MIDI files into the USB drive
2. The system will automatically detect and mount the disk
3. Use the physical buttons to control playback:
   - Play/Pause
   - Next/Previous Track
   - Next/Previous Soundfont
   - Next Category
   - Random Track

State Persistence: Your last-used soundfont and category are automatically remembered. When you change soundfonts or categories, the system waits 10 seconds before saving your preference, preventing unnecessary writes during rapid browsing.

## MIDI File Organization

- MIDI files can be numbered with two-digit prefixes (e.g., "01_", "02-", "03 ")
- Files are played in sorted order
- Prefixes are not displayed on the LCD screen during playback

## Service Management

The MIDI Blaster runs as a systemd service. You can manage it using standard systemd commands:

```bash
# Start the service
sudo systemctl start midiblaster

# Stop the service
sudo systemctl stop midiblaster

# Check service status
sudo systemctl status midiblaster

# Enable/disable auto-start
sudo systemctl enable midiblaster
sudo systemctl disable midiblaster
```

## Improvements Over Original

This version offers several enhancements while maintaining the core functionality:

- Runs headless on Raspberry Pi OS 64-bit Lite
- Supports numeric track ordering (e.g., "01_", "02-", "03 ")
- Automatic track advancement
- Improved disk handling and file system updates
- Runs as a systemd service
- Better resource management
- State persistence across restarts
- Thread-safe operation
- Class-based architecture for better maintainability

## Credits

Original MIDI Blaster project by Luke The Maker ([Patreon](https://www.patreon.com/c/LukeTheMaker/posts))

## SoundFont Licensing

This project includes the FluidR3_GM.sf2 SoundFont, developed by Frank Wen and contributors.

It is licensed under the MIT License. See the LICENSE file for full details.
