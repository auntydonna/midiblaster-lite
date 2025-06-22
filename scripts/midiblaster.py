#!/usr/bin/env python3
import json
import os
import random
import re
import signal
import subprocess
import sys
import threading
import time

import pygame
import RPi.GPIO as GPIO
import smbus2 as smbus

# Set up MIDI refresh
REFRESH_INTERVAL = 5

# Set up category selection
CATEGORY_CONFIRM_DELAY = 3.0

# GPIO setup
BUTTON_PIN_PLAY_PAUSE = 26
BUTTON_PIN_NEXT_TRACK = 27
BUTTON_PIN_PREV_TRACK = 22
BUTTON_PIN_NEXT_SOUNDFONT = 23
BUTTON_PIN_PREV_SOUNDFONT = 24
BUTTON_PIN_NEXT_CATEGORY = 19
BUTTON_PIN_RANDOM_SONG = 6
BOUNCETIME = 400

# I2C config for LCD
I2C_ADDR = 0x27
LCD_BACKLIGHT = 0x08
LCD_CMD = 0
LCD_CHR = 1
LCD_LINE_1 = 0x80
LCD_LINE_2 = 0xC0

# Paths
HOME = os.path.expanduser("~")
MIDI_FOLDER = "/media/mididisk"
SOUNDFONT_ROOT = f"{HOME}/soundfonts"
STATE_FILE = f"{HOME}/.midiblaster_state.json"
USB_DEVICE = '/dev/sda'

# Add mount retry logic
MOUNT_RETRIES = 5
MOUNT_RETRY_DELAY = 1.0


class MidiBlaster:
    def __init__(self):
        self.state_lock = threading.RLock()

        # Player State
        self.category_pending = False
        self.category_preview_index = 0
        self.last_category_change_time = 0
        self.soundfont_categories = []
        self.current_category_index = 0
        self.current_soundfont_index = 0
        self.soundfonts = []
        self.midi_files = []
        self.current_midi = -1
        self.is_playing = False
        self.fs_proc = None
        self.auto_advance = True
        self.last_refresh_time = time.time()
        self.state_save_timer = None
        self.state_save_delay = 10.0 
        
        # Hardware & System
        self.bus = smbus.SMBus(1)
        pygame.init()
        self.screen = pygame.display.set_mode((1, 1))
        pygame.display.set_caption("MIDI Player")

        # Initial Setup
        self.setup_gpio()
        self.lcd_init()
        self.display_title_screen()
        self.load_soundfont_categories()
        self.load_soundfonts()
        self._load_state()
        self.ensure_floppy_mounted()
        self.initialize_midi_files()

    def setup_gpio(self):
        GPIO.setmode(GPIO.BCM)
        button_pins = [
            BUTTON_PIN_PLAY_PAUSE, BUTTON_PIN_NEXT_TRACK, BUTTON_PIN_PREV_TRACK,
            BUTTON_PIN_NEXT_SOUNDFONT, BUTTON_PIN_PREV_SOUNDFONT,
            BUTTON_PIN_NEXT_CATEGORY, BUTTON_PIN_RANDOM_SONG
        ]
        for pin in button_pins:
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

        GPIO.add_event_detect(BUTTON_PIN_PLAY_PAUSE, GPIO.FALLING, callback=self.handle_play_pause, bouncetime=BOUNCETIME)
        GPIO.add_event_detect(BUTTON_PIN_NEXT_TRACK, GPIO.FALLING, callback=self.handle_next_track, bouncetime=BOUNCETIME)
        GPIO.add_event_detect(BUTTON_PIN_PREV_TRACK, GPIO.FALLING, callback=self.handle_prev_track, bouncetime=BOUNCETIME)
        GPIO.add_event_detect(BUTTON_PIN_NEXT_SOUNDFONT, GPIO.FALLING, callback=self.handle_next_soundfont, bouncetime=BOUNCETIME)
        GPIO.add_event_detect(BUTTON_PIN_PREV_SOUNDFONT, GPIO.FALLING, callback=self.handle_prev_soundfont, bouncetime=BOUNCETIME)
        GPIO.add_event_detect(BUTTON_PIN_RANDOM_SONG, GPIO.FALLING, callback=self.handle_random_song, bouncetime=BOUNCETIME)
        GPIO.add_event_detect(BUTTON_PIN_NEXT_CATEGORY, GPIO.FALLING, callback=self.handle_next_category, bouncetime=BOUNCETIME)

    @staticmethod
    def is_midi_file(file_path):
        return os.path.isfile(file_path) and file_path.lower().endswith('.mid')

    @staticmethod
    def is_soundfont_file(file_path):
        return os.path.isfile(file_path) and file_path.lower().endswith('.sf2')

    # -- Button Handlers --
    def handle_play_pause(self, channel):
        with self.state_lock:
            if self.is_playing:
                print("[DEBUG] Play/Stop pressed, stopping playback.")
                self.stop_playback()
            elif self.midi_files:
                print("[DEBUG] Play/Stop pressed, starting playback.")
                self.play_midi(self.midi_files[self.current_midi])

    def handle_next_track(self, channel):
        with self.state_lock:
            print("[DEBUG] Next track pressed.")
            self.next_track()

    def handle_prev_track(self, channel):
        with self.state_lock:
            print("[DEBUG] Previous track pressed.")
            self.prev_track()

    def handle_next_soundfont(self, channel):
        with self.state_lock:
            print("[DEBUG] Next soundfont pressed.")
            self.next_soundfont()

    def handle_prev_soundfont(self, channel):
        with self.state_lock:
            print("[DEBUG] Previous soundfont pressed.")
            self.prev_soundfont()

    def handle_random_song(self, channel):
        with self.state_lock:
            print("[DEBUG] Random track pressed.")
            self.random_track()

    def handle_next_category(self, channel):
        with self.state_lock:
            if not self.soundfont_categories:
                return
            if not self.category_pending:
                self.category_preview_index = self.current_category_index
            self.category_pending = True
            self.category_preview_index = (self.category_preview_index + 1) % len(self.soundfont_categories)
            self.last_category_change_time = time.time()
            print(f"[DEBUG] Next category pressed. Previewing: {self.soundfont_categories[self.category_preview_index]}")
            self.update_lcd()

    # -- LCD Methods --
    def lcd_byte(self, bits, mode):
        self.bus.write_byte(I2C_ADDR, mode | (bits & 0xF0) | 0b00000100 | LCD_BACKLIGHT)
        self.bus.write_byte(I2C_ADDR, mode | (bits & 0xF0) | LCD_BACKLIGHT)
        self.bus.write_byte(I2C_ADDR, mode | ((bits << 4) & 0xF0) | 0b00000100 | LCD_BACKLIGHT)
        self.bus.write_byte(I2C_ADDR, mode | ((bits << 4) & 0xF0) | LCD_BACKLIGHT)

    def lcd_init(self):
        self.lcd_byte(0x33, LCD_CMD)
        self.lcd_byte(0x32, LCD_CMD)
        self.lcd_byte(0x06, LCD_CMD)
        self.lcd_byte(0x0C, LCD_CMD)
        self.lcd_byte(0x28, LCD_CMD)
        self.lcd_byte(0x01, LCD_CMD)
        time.sleep(0.005)

    def lcd_string(self, message, line):
        self.lcd_byte(line, LCD_CMD)
        for char in message.ljust(16):
            self.lcd_byte(ord(char), LCD_CHR)

    def display_title_screen(self):
        self.lcd_init()
        self.lcd_string("     MIDI     ", LCD_LINE_1)
        self.lcd_string("    BLASTER   ", LCD_LINE_2)
        time.sleep(2)
        
    def update_lcd(self):
        if self.category_pending and self.soundfont_categories:
            preview_cat = self.soundfont_categories[self.category_preview_index][:16]
            self.lcd_string("Category:", LCD_LINE_1)
            self.lcd_string(preview_cat.ljust(16), LCD_LINE_2)
            return

        if self.midi_files and 0 <= self.current_midi < len(self.midi_files):
            display_name = re.sub(r'^\d+\W*', '', self.midi_files[self.current_midi])
            song = display_name[:15].ljust(15)
        else:
            song = "No MIDI Files".ljust(15)

        song += ">" if self.is_playing else " "
        sf = self.soundfonts[self.current_soundfont_index][:16] if self.soundfonts else "No Soundfonts"
        
        self.lcd_string(song, LCD_LINE_1)
        self.lcd_string(sf, LCD_LINE_2)

    # -- File and Device Management --
    def ensure_floppy_mounted(self):
        if not os.path.exists(MIDI_FOLDER):
            print(f"[DEBUG] Mount directory {MIDI_FOLDER} does not exist, creating.")
            try:
                subprocess.run(['sudo', 'mkdir', '-p', MIDI_FOLDER], check=True)
            except Exception as e:
                print(f"[ERROR] Failed to create mount directory {MIDI_FOLDER}: {e}")
                return False

        if MIDI_FOLDER in subprocess.run(['mount'], capture_output=True, text=True).stdout:
            return True

        print(f"[DEBUG] {MIDI_FOLDER} not mounted. Attempting to mount {USB_DEVICE}...")
        for i in range(MOUNT_RETRIES):
            try:
                print(f"[DEBUG] Mount attempt {i+1}/{MOUNT_RETRIES}...")
                mount_result = subprocess.run(
                    ['sudo', 'mount', '-o', 'noatime,nofail', USB_DEVICE, MIDI_FOLDER],
                    check=False, capture_output=True, text=True, timeout=10
                )
                if mount_result.returncode == 0:
                    print(f"[DEBUG] Successfully mounted {USB_DEVICE} to {MIDI_FOLDER}.")
                    return True
                else:
                    print(f"[DEBUG] Mount attempt failed. RC: {mount_result.returncode}, Error: {mount_result.stderr.strip()}")
                    if not os.path.exists(USB_DEVICE):
                        print(f"[DEBUG] Device node {USB_DEVICE} does not exist. Waiting...")
            except subprocess.TimeoutExpired:
                print(f"[ERROR] Mount attempt {i+1} timed out.")
            except Exception as e:
                print(f"[ERROR] An unexpected error occurred during mount attempt {i+1}: {e}")
            
            if i < MOUNT_RETRIES - 1:
                time.sleep(MOUNT_RETRY_DELAY)
        
        print(f"[ERROR] Failed to mount {USB_DEVICE} to {MIDI_FOLDER} after {MOUNT_RETRIES} attempts.")
        return False

    def load_soundfont_categories(self):
        self.soundfont_categories = sorted(
            [d for d in os.listdir(SOUNDFONT_ROOT) if os.path.isdir(os.path.join(SOUNDFONT_ROOT, d))],
            key=lambda x: (x != 'Other Games', x.lower())
        )
        if "Other Games" in self.soundfont_categories:
            self.current_category_index = self.soundfont_categories.index("Other Games")
        else:
            self.current_category_index = 0
            
    def load_soundfonts(self):
        current_category = self.soundfont_categories[self.current_category_index]
        category_path = os.path.join(SOUNDFONT_ROOT, current_category)
        self.soundfonts = sorted([
            f for f in os.listdir(category_path) 
            if self.is_soundfont_file(os.path.join(category_path, f))
        ])

    def initialize_midi_files(self):
        if os.path.exists(MIDI_FOLDER) and os.access(MIDI_FOLDER, os.R_OK):
            try:
                self.midi_files = sorted([
                    f for f in os.listdir(MIDI_FOLDER) 
                    if self.is_midi_file(os.path.join(MIDI_FOLDER, f))
                ])
            except Exception as e:
                print(f"[ERROR] Unable to initialize midi files: {e}")
            
            if self.midi_files:
                self.current_midi = 0
        self.last_refresh_time = time.time()
        self.update_lcd()
            
    def refresh_midi_files(self):
        self.ensure_floppy_mounted()
        check_midi_files = []
        if os.path.exists(MIDI_FOLDER) and os.access(MIDI_FOLDER, os.R_OK):
            try:
                 check_midi_files = sorted([
                    f for f in os.listdir(MIDI_FOLDER) 
                    if self.is_midi_file(os.path.join(MIDI_FOLDER, f))
                ])
            except Exception as e:
                print(f"[DEBUG] Error listing {MIDI_FOLDER}: {e}. Treating as empty.")
        
        if check_midi_files != self.midi_files:
            print(f"[DEBUG] Detected change in MIDI files. Old: {len(self.midi_files)}, New: {len(check_midi_files)}")
            self.stop_playback()

            print(f"[DEBUG] Attempting unmount to refresh file system view.")
            try:
                subprocess.run(['sudo', 'umount', '-l', MIDI_FOLDER], check=False, capture_output=True, text=True, timeout=5)
                time.sleep(0.5)
            except Exception as e:
                print(f"[ERROR] An unexpected error occurred during unmount: {e}")

            self.ensure_floppy_mounted()
            time.sleep(0.5)

            reloaded_midi_files = []
            if os.path.exists(MIDI_FOLDER) and os.access(MIDI_FOLDER, os.R_OK):
                try:
                     reloaded_midi_files = sorted([
                        f for f in os.listdir(MIDI_FOLDER) 
                        if self.is_midi_file(os.path.join(MIDI_FOLDER, f))
                    ])
                except Exception as e:
                     print(f"[ERROR] Unable to re-list MIDI files after remount: {e}")
            
            self.midi_files = reloaded_midi_files
            self.current_midi = 0 if self.midi_files else -1
            self.update_lcd()

        self.last_refresh_time = time.time()

    def _save_state(self):
        with self.state_lock:
            if not self.soundfont_categories or not self.soundfonts:
                return
            
            current_state = {
                "category": self.soundfont_categories[self.current_category_index],
                "soundfont": self.soundfonts[self.current_soundfont_index],
            }

            try:
                with open(STATE_FILE, 'w') as f:
                    json.dump(current_state, f, indent=4)
            except IOError as e:
                print(f"[ERROR] Could not write to state file {STATE_FILE}: {e}")

    def _load_state(self):
        with self.state_lock:
            if not self.soundfont_categories or not self.soundfonts:
                return

            if not os.path.exists(STATE_FILE):
                print(f"[INFO] State file not found at {STATE_FILE}. Creating with default.")
                self._save_state()
                return
            
            try:
                with open(STATE_FILE, 'r') as f:
                    current_state = json.load(f)
            except (IOError, json.JSONDecodeError) as e:
                print(f"[ERROR] Could not read or parse state file {STATE_FILE}: {e}")
                return

            saved_category = current_state.get("category")
            saved_soundfont = current_state.get("soundfont")

            if saved_category in self.soundfont_categories:
                category_index = self.soundfont_categories.index(saved_category)
                self.current_category_index = category_index
                self.load_soundfonts()

                if saved_soundfont in self.soundfonts:
                    soundfont_index = self.soundfonts.index(saved_soundfont)
                    self.current_soundfont_index = soundfont_index
                    print(f"[INFO] Loaded state: {saved_category} -> {saved_soundfont}")
                else:
                    print(f"[WARN] Saved soundfont '{saved_soundfont}' not found. Using default.")
            else:
                print(f"[WARN] Saved category '{saved_category}' not found. Using default.")
  

    def _schedule_save_state(self):
        with self.state_lock:
            # Cancel any existing running timer
            if self.state_save_timer:
                self.state_save_timer.cancel()
            
            # Create a timer that will save the state once elapsed
            self.state_save_timer = threading.Timer(self.state_save_delay, self._save_state)
            self.state_save_timer.start()
            

    # -- Playback Control --
    def play_midi(self, midi_file):
        with self.state_lock:
            self.stop_playback()
            current_category = self.soundfont_categories[self.current_category_index]
            sf_path = os.path.join(SOUNDFONT_ROOT, current_category, self.soundfonts[self.current_soundfont_index])
            midi_path = os.path.join(MIDI_FOLDER, midi_file)
            print(f"[DEBUG] Playing: {midi_file}")
            try:
                self.fs_proc = subprocess.Popen(["fluidsynth", "-ni", "-g", "2.0", sf_path, midi_path])
                self.is_playing = True
                self.auto_advance = True
                self.update_lcd()
            except Exception as e:
                print(f"[ERROR] Could not start fluidsynth: {e}")
                self.is_playing = False
                self.fs_proc = None
                self.update_lcd()

    def stop_playback(self):
        with self.state_lock:
            if self.is_playing and self.fs_proc:
                try:
                    self.fs_proc.terminate()
                    self.fs_proc.wait(timeout=2)
                except Exception as e:
                    print(f"[ERROR] Error during stop_playback: {e}")
            self.fs_proc = None
            self.is_playing = False
            self.auto_advance = False
            self.update_lcd()

    def next_soundfont(self):
        if not self.soundfonts: return
        self.current_soundfont_index = (self.current_soundfont_index + 1) % len(self.soundfonts)
        self._schedule_save_state()
        if self.is_playing and self.midi_files:
            self.play_midi(self.midi_files[self.current_midi])
        else:
            self.update_lcd()

    def prev_soundfont(self):
        if not self.soundfonts: return
        self.current_soundfont_index = (self.current_soundfont_index - 1 + len(self.soundfonts)) % len(self.soundfonts)
        self._schedule_save_state()
        if self.is_playing and self.midi_files:
            self.play_midi(self.midi_files[self.current_midi])
        else:
            self.update_lcd()

    def next_track(self):
        if not self.midi_files: return
        self.current_midi = (self.current_midi + 1) % len(self.midi_files)
        if self.is_playing:
            self.play_midi(self.midi_files[self.current_midi])
        else:
            self.update_lcd()

    def prev_track(self):
        if not self.midi_files: return
        self.current_midi = (self.current_midi - 1 + len(self.midi_files)) % len(self.midi_files)
        if self.is_playing:
            self.play_midi(self.midi_files[self.current_midi])
        else:
            self.update_lcd()

    def random_track(self):
        if not self.midi_files: return
        self.current_midi = random.randint(0, len(self.midi_files) - 1)
        if self.is_playing:
            self.play_midi(self.midi_files[self.current_midi])
        else:
            self.update_lcd()
            
    def cleanup(self):
        print("[INFO] Cleaning up resources.")
        self._save_state()
        self.stop_playback()
        GPIO.cleanup()
        pygame.quit()
        
    def run(self):
        """Main loop for the MIDI Blaster."""
        try:
            while True:
                current_time = time.time()
                
                with self.state_lock:
                    # Handle category selection confirmation
                    if self.category_pending and (current_time - self.last_category_change_time >= CATEGORY_CONFIRM_DELAY):
                        print(f"[DEBUG] Category selection confirmed: {self.soundfont_categories[self.category_preview_index]}.")
                        self.current_category_index = self.category_preview_index
                        self.category_pending = False
                        self.load_soundfonts()
                        self.current_soundfont_index = 0
                        self._schedule_save_state()

                        if self.is_playing and self.midi_files:
                            self.play_midi(self.midi_files[self.current_midi])
                        else:
                            self.update_lcd()

                    # Handle auto-advance
                    if self.is_playing and self.fs_proc and self.fs_proc.poll() is not None:
                        if self.auto_advance:
                            print("[DEBUG] Auto-advancing to next track.")
                            self.next_track()
                        else: # Manual stop
                            self.auto_advance = True # Reset for next playback
                            self.is_playing = False # Update status
                            self.update_lcd()

                # Refresh MIDI files
                if current_time - self.last_refresh_time >= REFRESH_INTERVAL:
                    self.refresh_midi_files()

                time.sleep(0.05)
        except KeyboardInterrupt:
            print("\n[INFO] Keyboard interrupt detected. Exiting.")
        finally:
            self.cleanup()


def main():
    """Main function to run the MIDI Blaster."""
    blaster = MidiBlaster()

    def handle_exit(signum, frame):
        print(f"[INFO] Received signal {signum}, exiting gracefully.")
        # No need to call cleanup here as the finally block in run() will handle it.
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_exit)
    signal.signal(signal.SIGINT, handle_exit)

    blaster.run()


if __name__ == '__main__':
    main()