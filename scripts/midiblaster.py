#!/usr/bin/env python3
import os
import random
import re
import signal
import subprocess
import sys
import time

import pygame
import RPi.GPIO as GPIO
import smbus2 as smbus

# Set up MIDI refresh
REFRESH_INTERVAL = 5
last_refresh_time = time.time()

# Set up category selection
CATEGORY_CONFIRM_DELAY = 3.0
category_pending = False
category_preview_index = 0
last_category_change_time = 0

# GPIO setup
GPIO.setmode(GPIO.BCM)

BUTTON_PIN_PLAY_PAUSE = 26
BUTTON_PIN_NEXT_TRACK = 27
BUTTON_PIN_PREV_TRACK = 22
BUTTON_PIN_NEXT_SOUNDFONT = 23
BUTTON_PIN_PREV_SOUNDFONT = 24
BUTTON_PIN_NEXT_CATEGORY = 19
BUTTON_PIN_RANDOM_SONG = 6
BOUNCETIME = 400

for pin in [
    BUTTON_PIN_PLAY_PAUSE, BUTTON_PIN_NEXT_TRACK, BUTTON_PIN_PREV_TRACK,
    BUTTON_PIN_NEXT_SOUNDFONT, BUTTON_PIN_PREV_SOUNDFONT,
    BUTTON_PIN_NEXT_CATEGORY, BUTTON_PIN_RANDOM_SONG
]:
    GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

def handle_play_pause(channel):
    if is_playing:
        print(f"[DEBUG] Play/Stop pressed, stopping playback.")
        stop_playback()
    else:
        print(f"[DEBUG] Play/Stop pressed, starting playback.")
        play_midi(midi_files[current_midi])

def handle_next_track(channel):
    print(f"[DEBUG] Next track pressed.")
    next_track()

def handle_prev_track(channel):
    print(f"[DEBUG] Previous track pressed.")
    prev_track()

def handle_next_soundfont(channel):
    print(f"[DEBUG] Next soundfont pressed.")
    next_soundfont()

def handle_prev_soundfont(channel):
    print(f"[DEBUG] Previous soundfont pressed.")
    prev_soundfont()

def handle_random_song(channel):
    print(f"[DEBUG] Random track pressed.")
    random_track()

def handle_next_category(channel):
    global category_pending, category_preview_index, last_category_change_time, current_category_index
    if not soundfont_categories:
        return
    # If not already pending, start preview from current category
    if not category_pending:
        category_preview_index = current_category_index
    category_pending = True
    category_preview_index = (category_preview_index + 1) % len(soundfont_categories)
    last_category_change_time = time.time()
    print(f"[DEBUG] Next category pressed. Previewing: {soundfont_categories[category_preview_index]}")
    update_lcd() # Update display to show preview

GPIO.add_event_detect(BUTTON_PIN_PLAY_PAUSE, GPIO.FALLING, callback=handle_play_pause, bouncetime=BOUNCETIME)
GPIO.add_event_detect(BUTTON_PIN_NEXT_TRACK, GPIO.FALLING, callback=handle_next_track, bouncetime=BOUNCETIME)
GPIO.add_event_detect(BUTTON_PIN_PREV_TRACK, GPIO.FALLING, callback=handle_prev_track, bouncetime=BOUNCETIME)
GPIO.add_event_detect(BUTTON_PIN_NEXT_SOUNDFONT, GPIO.FALLING, callback=handle_next_soundfont, bouncetime=BOUNCETIME)
GPIO.add_event_detect(BUTTON_PIN_PREV_SOUNDFONT, GPIO.FALLING, callback=handle_prev_soundfont, bouncetime=BOUNCETIME)
GPIO.add_event_detect(BUTTON_PIN_RANDOM_SONG, GPIO.FALLING, callback=handle_random_song, bouncetime=BOUNCETIME)
GPIO.add_event_detect(BUTTON_PIN_NEXT_CATEGORY, GPIO.FALLING, callback=handle_next_category, bouncetime=BOUNCETIME)

# I2C config for LCD
I2C_ADDR = 0x27
LCD_BACKLIGHT = 0x08
LCD_CMD = 0
LCD_CHR = 1
LCD_LINE_1 = 0x80
LCD_LINE_2 = 0xC0
bus = smbus.SMBus(1)

# Paths
HOME = os.path.expanduser("~")
midi_folder = "/media/mididisk"
soundfont_root = f"{HOME}/soundfonts"
usb_device = '/dev/sda'

# Add mount retry logic
MOUNT_RETRIES = 5
MOUNT_RETRY_DELAY = 1.0

soundfont_categories = sorted(
    [d for d in os.listdir(soundfont_root) if os.path.isdir(os.path.join(soundfont_root, d))],
    key=lambda x: (x != 'Other Games', x.lower())
)

current_category_index = soundfont_categories.index("Other Games") if "Other Games" in soundfont_categories else 0
current_soundfont_index = 0
soundfonts = []
midi_files = []
current_midi = -1
is_playing = False
play_start_time = None
play_length = None
fs_proc = None
auto_advance = True
playback_initiating = False

# LCD Helpers
def lcd_byte(bits, mode):
    bus.write_byte(I2C_ADDR, mode | (bits & 0xF0) | 0b00000100 | LCD_BACKLIGHT)
    bus.write_byte(I2C_ADDR, mode | (bits & 0xF0) | LCD_BACKLIGHT)
    bus.write_byte(I2C_ADDR, mode | ((bits << 4) & 0xF0) | 0b00000100 | LCD_BACKLIGHT)
    bus.write_byte(I2C_ADDR, mode | ((bits << 4) & 0xF0) | LCD_BACKLIGHT)

def lcd_init():
    lcd_byte(0x33, LCD_CMD)
    lcd_byte(0x32, LCD_CMD)
    lcd_byte(0x06, LCD_CMD)
    lcd_byte(0x0C, LCD_CMD)
    lcd_byte(0x28, LCD_CMD)
    lcd_byte(0x01, LCD_CMD)
    time.sleep(0.005)

def lcd_string(message, line):
    lcd_byte(line, LCD_CMD)
    for char in message.ljust(16):
        lcd_byte(ord(char), LCD_CHR)

def display_title_screen():
    lcd_init()
    lcd_string("     MIDI     ", LCD_LINE_1)
    lcd_string("    BLASTER   ", LCD_LINE_2)
    time.sleep(2)

def is_midi_file(file_path):
    return os.path.isfile(file_path) and file_path.lower().endswith('.mid')

def is_soundfont_file(file_path):
    return os.path.isfile(file_path) and file_path.lower().endswith('.sf2')

def ensure_floppy_mounted():
    """Ensures the floppy device is mounted, with retries."""
    if not os.path.exists(midi_folder):
        print(f"[DEBUG] Mount directory {midi_folder} does not exist, creating.")
        try:
            subprocess.run(['sudo', 'mkdir', '-p', midi_folder], check=True)
            print(f"[DEBUG] Created mount directory {midi_folder}.")
        except Exception as e:
             print(f"[ERROR] Failed to create mount directory {midi_folder}: {e}")
             return False # Cannot proceed if directory creation fails

    # Check if already mounted
    result = subprocess.run(['mount'], capture_output=True, text=True)
    if midi_folder in result.stdout:
        # print(f"[DEBUG] {midi_folder} is already mounted.") # Optional: uncomment for frequent debug
        return True # Already mounted

    print(f"[DEBUG] {midi_folder} not currently mounted. Attempting to mount {usb_device}...")

    # Attempt to mount with retries
    for i in range(MOUNT_RETRIES):
        try:
            # Use noatime for potentially better performance on flash media
            # Add 'nofail' option: if the device does not exist, do not report errors for this device.
            # This can prevent mount from blocking indefinitely if the device is missing.
            print(f"[DEBUG] Mount attempt {i+1}/{MOUNT_RETRIES}...")
            mount_result = subprocess.run(
                ['sudo', 'mount', '-o', 'noatime,nofail', usb_device, midi_folder],
                check=False, # Do not raise exception on non-zero exit code immediately
                capture_output=True,
                text=True,
                timeout=10 # Add timeout to mount command
            )
            if mount_result.returncode == 0:
                 print(f"[DEBUG] Successfully mounted {usb_device} to {midi_folder}.")
                 return True # Successfully mounted
            else:
                 print(f"[DEBUG] Mount attempt failed. Return code: {mount_result.returncode}. Output: {mount_result.stdout.strip()}, Error: {mount_result.stderr.strip()}")
                 # Check if device node exists; if not, it's likely not plugged in yet
                 if not os.path.exists(usb_device):
                      print(f"[DEBUG] Device node {usb_device} does not exist. Waiting for device...")

        except subprocess.TimeoutExpired:
             print(f"[ERROR] Mount attempt {i+1} timed out.")
        except Exception as e:
            print(f"[ERROR] An unexpected error occurred during mount attempt {i+1}: {e}")

        if i < MOUNT_RETRIES - 1:
            time.sleep(MOUNT_RETRY_DELAY) # Wait before retrying

    print(f"[ERROR] Failed to mount {usb_device} to {midi_folder} after {MOUNT_RETRIES} attempts.")
    return False # Mount failed after retries

def unmount_floppy():
    """Unmounts the floppy device if mounted."""
    print(f"[DEBUG] Attempting to unmount {midi_folder}.")
    try:
        # Use lazy unmount (-l) and check=False to handle cases where it might not be mounted
        result = subprocess.run(['sudo', 'umount', '-l', midi_folder], check=False, capture_output=True, text=True, timeout=5)
        print(f"[DEBUG] umount command finished (check=False). Return code: {result.returncode}. Output: {result.stdout.strip()}, Error: {result.stderr.strip()}")
        if result.returncode == 0:
            print(f"[DEBUG] Floppy disk unmounted from {midi_folder}.")
            return True # Unmounted successfully
        elif "not mounted" in result.stderr.lower():
            print(f"[DEBUG] Floppy disk was not mounted at {midi_folder}.")
            return False # Was not mounted
        else:
            print(f"[ERROR] umount command returned non-zero exit code.")
            return False # Failed to unmount properly
    except subprocess.TimeoutExpired:
         print(f"[ERROR] umount command timed out.")
         return False # Failed due to timeout
    except Exception as e:
         print(f"[ERROR] An unexpected error occurred during explicit unmount: {e}")
         return False # Failed due to other error

def load_soundfonts():
    global soundfonts
    current_category = soundfont_categories[current_category_index]
    category_path = os.path.join(soundfont_root, current_category)
    soundfonts = sorted([f for f in os.listdir(category_path) if is_soundfont_file(os.path.join(category_path, f))])

def update_lcd():
    global category_pending, category_preview_index, soundfont_categories, current_category_index
    
    if category_pending and soundfont_categories:
        # Show category preview on Line 2
        preview_cat = soundfont_categories[category_preview_index][:16]
        lcd_string("Category:", LCD_LINE_1)
        lcd_string(preview_cat.ljust(16), LCD_LINE_2)
        return # Skip showing song/sf when previewing

    # If not pending, show song and soundfont
    if midi_files and 0 <= current_midi < len(midi_files):
    # Remove numeric prefix if it exists (e.g. 01_, 02-, 03 )
        display_name = re.sub(r'^\d+\W*', '', midi_files[current_midi])
        song = display_name[:15].ljust(15)
    else:
        song = "No MIDI Files".ljust(15)

    if is_playing:
        song += ">"
    else:
        song += " "

    sf = soundfonts[current_soundfont_index][:16] if soundfonts else "No Soundfonts"
    lcd_string(song, LCD_LINE_1)
    lcd_string(sf, LCD_LINE_2)

def play_midi(midi_file):
    global is_playing, play_start_time, fs_proc, auto_advance, playback_initiating
    
    # If a playback initiation sequence is already in progress, ignore this call
    if playback_initiating:
        print("[DEBUG] Playback initiation already in progress, ignoring new request.")
        return

    # Set flag to indicate playback initiation is starting
    playback_initiating = True

    stop_playback()  # Stop any previous playback (this will set fs_proc = None and auto_advance = False)

    current_category = soundfont_categories[current_category_index]
    sf_path = os.path.join(soundfont_root, current_category, soundfonts[current_soundfont_index])
    midi_path = os.path.join(midi_folder, midi_file)
    print(f"[DEBUG] Playing: {midi_file}")
    try:
        fs_proc = subprocess.Popen([
            "fluidsynth", "-ni", "-g", "2.0", sf_path, midi_path
        ])
        is_playing = True
        play_start_time = time.time()
        auto_advance = True
        update_lcd()
    except Exception as e:
        print(f"[ERROR] Could not start fluidsynth: {e}")
        is_playing = False
        play_start_time = None
        fs_proc = None
        auto_advance = False # Ensure auto_advance is false on failure
        update_lcd()
    finally:
        # Ensure flag is reset when initiation finishes
        playback_initiating = False

def stop_playback():
    global is_playing, play_start_time, fs_proc, auto_advance
    if is_playing and fs_proc:
        try:
            fs_proc.terminate()
            fs_proc.wait(timeout=2)
            print(f"[DEBUG] Playback stopped and fluidsynth process terminated.")
        except Exception as e:
            print(f"[ERROR] Error during stop_playback: {e}")
        fs_proc = None
    is_playing = False
    play_start_time = None
    auto_advance = False
    update_lcd()

def initialize_midi_files():
    global midi_files, current_midi, last_refresh_time

    if os.path.exists(midi_folder) and os.access(midi_folder, os.R_OK):
        try:
            midi_files = sorted(
                [f for f in os.listdir(midi_folder) if is_midi_file(os.path.join(midi_folder, f))]\
            )
        except Exception as e:
            print(f"[ERROR] Unable to initialize midi files: {e}")
        
        if len(midi_files) > 0:
            current_midi = 0

        last_refresh_time = time.time()
        
        update_lcd()
            

def refresh_midi_files():
    global midi_files, current_midi, last_refresh_time

    ensure_floppy_mounted()

    # Attempt to list files from the current mount point
    check_midi_files = []
    # Check if the mount point directory exists and is accessible before listing
    if os.path.exists(midi_folder) and os.access(midi_folder, os.R_OK):
        try:
             # Read the current file list from the directory
             check_midi_files = sorted(
                [f for f in os.listdir(midi_folder) if is_midi_file(os.path.join(midi_folder, f))]\
            )
             # print(f"[DEBUG] Listed directory. Found {len(check_midi_files)} midi files.") # Less frequent logging
        except Exception as e:
            # If listing fails for any reason, treat as empty
            print(f"[DEBUG] Error listing {midi_folder}: {e}. Treating as empty file list.")
            check_midi_files = []
    else:
        # If the mount folder doesn't exist or is not readable, treat as no disk/empty
        # print(f"[DEBUG] Mount folder {midi_folder} does not exist or is not readable during refresh.") # Less frequent logging
        check_midi_files = []


    # If the list of files has changed (e.g., disk swapped, or became empty/populated)
    if check_midi_files != midi_files:
        print(f"[DEBUG] Detected change in MIDI files. Old count: {len(midi_files)}, New count: {len(check_midi_files)})") # Verbose logging
        print("[DEBUG] Handling disk change: Stopping playback, unmounting, remounting, and updating list.")

        stop_playback() # Stop any currently playing track

        # *** Unconditionally attempt to unmount the old disk's file system view ***
        # This is crucial for clearing the cache of whatever was previously mounted.
        print(f"[DEBUG] Attempting unmount {midi_folder} to refresh file system view.")
        try:
            # Use lazy unmount (-l) and check=False to handle cases where it might not be mounted.
            # Adding timeout for robustness.
            subprocess.run(
                ['sudo', 'umount', '-l', midi_folder],
                check=False, # Ignore errors if not mounted or busy
                capture_output=True,
                text=True,
                timeout=5 # Add a timeout
            )
            print(f"[DEBUG] umount command finished.")
            time.sleep(0.5) # Give a moment after unmount

        except subprocess.TimeoutExpired:
             print(f"[ERROR] umount command timed out.")
        except Exception as e:
             print(f"[ERROR] An unexpected error occurred during unmount attempt: {e}")


        # Attempt to mount the new disk at the same location
        print(f"[DEBUG] Attempting to mount {usb_device} to {midi_folder}.")
        ensure_floppy_mounted() # Use existing function which mounts if not already mounted
        time.sleep(0.5) # Give a moment after mount attempt

        # Reload the midi files list from the newly mounted directory
        # We need to re-list from the directory to get the actual files on the new disk.
        reloaded_midi_files = []
        if os.path.exists(midi_folder) and os.access(midi_folder, os.R_OK):
            try:
                 reloaded_midi_files = sorted(
                    [f for f in os.listdir(midi_folder) if is_midi_file(os.path.join(midi_folder, f))]\
                 )
                 print(f"[DEBUG] Re-listed directory after remount. Found {len(reloaded_midi_files)} midi files.")
            except Exception as e:
                 print(f"[ERROR] Unable to re-list MIDI files after remount: {e}")


        # Update the global midi_files list to the reloaded list
        midi_files = reloaded_midi_files
        current_midi = 0 if midi_files else -1 # Reset index to the first track
        print(f"[DEBUG] Global midi_files updated. Final count: {len(midi_files)}")

        update_lcd() # Update display to show the new file list

    # else:
        # print(\"[DEBUG] No change in MIDI files detected.\") # Optional: uncomment for more frequent debug logging

    # Update the last refresh time regardless of whether files changed,
    # to ensure the check happens periodically.
    last_refresh_time = time.time()

def next_soundfont():
    global current_soundfont_index
    if not soundfonts:
        return
    current_soundfont_index = (current_soundfont_index + 1) % len(soundfonts)
    if is_playing and midi_files:
        play_midi(midi_files[current_midi])
    else:
        update_lcd()

def prev_soundfont():
    global current_soundfont_index
    if not soundfonts:
        return
    current_soundfont_index = (current_soundfont_index - 1) % len(soundfonts)
    if is_playing and midi_files:
        play_midi(midi_files[current_midi])
    else:
        update_lcd()

def next_track():
    global current_midi
    if midi_files:
        current_midi = (current_midi + 1) % len(midi_files)
    if is_playing:
        play_midi(midi_files[current_midi])
    else:
        update_lcd()

def prev_track():
    global current_midi
    if midi_files:
        current_midi = (current_midi - 1) % len(midi_files)
    if is_playing:
        play_midi(midi_files[current_midi])
    else:
        update_lcd()

def random_track():
    global current_midi
    if midi_files:
        current_midi = random.randint(0, len(midi_files) - 1)
    if is_playing:
        play_midi(midi_files[current_midi])
    else:
        update_lcd()

# Handle exit
def handle_exit(signum, frame):
    print(f"[DEBUG] Received signal {signum}, exiting gracefully.")
    stop_playback()
    if fs_proc:
        try:
            fs_proc.terminate()
        except Exception:
            pass
    GPIO.cleanup()
    pygame.quit()
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_exit)
signal.signal(signal.SIGINT, handle_exit)

# Main
pygame.init()
screen = pygame.display.set_mode((1, 1))
pygame.display.set_caption("MIDI Player")

lcd_init()
display_title_screen()
load_soundfonts()
ensure_floppy_mounted()
initialize_midi_files()


try:
    while True:
        current_time = time.time()

        # Handle category selection confirmation
        if category_pending and (current_time - last_category_change_time >= CATEGORY_CONFIRM_DELAY):
            print(f"[DEBUG] Category selection confirmed: {soundfont_categories[category_preview_index]}. Loading soundfonts...")
            current_category_index = category_preview_index
            category_pending = False
            load_soundfonts()
            # Optionally reset soundfont index or keep it
            current_soundfont_index = 0 # Reset to first soundfont in new category
            if is_playing and midi_files:
                 # Restart playback with new soundfont/category
                 play_midi(midi_files[current_midi])
            else:
                update_lcd() # Update display to show new category/soundfont

        # Refresh MIDI files based on REFRESH_INTERVAL
        if current_time - last_refresh_time >= REFRESH_INTERVAL:
            refresh_midi_files()
            last_refresh_time = current_time

        # Handle auto-advance when the song ends by monitoring the subprocess
        if is_playing and fs_proc is not None:
            ret = fs_proc.poll()
            if ret is not None:
                print(f"[DEBUG] Track finished or manually stopped. Poll result: {ret}")
                finished_proc = fs_proc # Store reference before setting to None
                fs_proc = None

                # Only auto-advance if this stop was NOT manually triggered
                if auto_advance:
                    print(f"[DEBUG] Auto-advancing to next track.")
                    next_track()
                else:
                    print(f"[DEBUG] Manual stop detected. Not auto-advancing.")
                    # Reset flag here if not auto-advancing, so next playback can auto-advance
                    auto_advance = True # Reset for next playback

        time.sleep(0.05)
except KeyboardInterrupt:
    pass
finally:
    stop_playback()
    GPIO.cleanup()
    pygame.quit()