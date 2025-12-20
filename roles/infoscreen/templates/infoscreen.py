import time
import subprocess
import board
import busio
import psutil
from PIL import Image, ImageDraw, ImageFont
import adafruit_ssd1306
import RPi.GPIO as GPIO
from enum import Enum, auto
from dataclasses import dataclass

# --- CONFIGURATION ---
WIDTH = 128
HEIGHT = 32
PIN_BUTTON = 20

# Timing
LOOP_SPEED = 0.1
SCREEN_TIMEOUT_TICKS = int(15.0 / LOOP_SPEED)
REBOOT_HOLD_TICKS = int(3.0 / LOOP_SPEED)
SHUTDOWN_HOLD_TICKS = int(6.0 / LOOP_SPEED)
STATS_UPDATE_TICKS = int(0.5 / LOOP_SPEED)
INFO_CYCLE_TICKS = int(2.0 / LOOP_SPEED)


# --- ENUMS & STATE ---
class MenuState(Enum):
    INFO = auto()
    REBOOT_WAIT = auto()
    SHUTDOWN_WAIT = auto()


class InfoScreen(Enum):
    NETWORK = auto()
    PERFORMANCE = auto()


@dataclass
class AppState:
    """Holds all mutable state for the application."""
    mode: MenuState = MenuState.INFO
    screen: InfoScreen = InfoScreen.NETWORK
    
    # Timers & Counters
    display_timer: int = SCREEN_TIMEOUT_TICKS
    btn_hold_ticks: int = 0
    stats_tick: int = STATS_UPDATE_TICKS  # Force update on start
    cycle_tick: int = 0
    
    # Data Cache
    cache_net: tuple = ("Loading...", "...")
    cache_perf: tuple = (0, 0, 0)


# --- HARDWARE SETUP ---
GPIO.setmode(GPIO.BCM)
GPIO.setup(PIN_BUTTON, GPIO.IN, pull_up_down=GPIO.PUD_UP)

i2c = busio.I2C(board.SCL, board.SDA)
disp = adafruit_ssd1306.SSD1306_I2C(WIDTH, HEIGHT, i2c)
disp.rotation = 2

image = Image.new("1", (WIDTH, HEIGHT))
draw = ImageDraw.Draw(image)
font = ImageFont.load_default()


# --- DATA HELPERS ---
def get_network_info():
    host = subprocess.check_output("hostname", shell=True).decode("utf-8").strip()
    try:
        ip = subprocess.check_output("hostname -I | cut -d' ' -f1", shell=True).decode("utf-8").strip()
    except:
        ip = "No IP"
    return host, ip


def get_performance_info():
    return psutil.cpu_percent(), psutil.virtual_memory().percent, psutil.disk_usage("/").percent


def run_sys_command(cmd):
    subprocess.Popen(cmd, shell=True)


# --- LOGIC PHASES ---
def handle_input(state: AppState):
    """Phase 1: Process Button Input & State Transitions"""
    is_pressed = GPIO.input(PIN_BUTTON) == 0

    if is_pressed:
        state.display_timer = SCREEN_TIMEOUT_TICKS # Wake/Keep Awake
        state.btn_hold_ticks += 1

        # Determine Hold State
        if state.btn_hold_ticks >= SHUTDOWN_HOLD_TICKS:
            state.mode = MenuState.SHUTDOWN_WAIT
        elif state.btn_hold_ticks >= REBOOT_HOLD_TICKS:
            state.mode = MenuState.REBOOT_WAIT
    
    else: # Button Released
        if state.btn_hold_ticks > 0:
            # Execute Action if needed
            if state.mode == MenuState.REBOOT_WAIT:
                draw_overlay("Rebooting...") # Instant feedback
                disp.image(image)
                disp.show()
                run_sys_command("sudo reboot now")
                exit(0) # Stop script
            elif state.mode == MenuState.SHUTDOWN_WAIT:
                draw_overlay("Shutting Down...")
                disp.image(image)
                disp.show()
                run_sys_command("sudo shutdown now")
                exit(0)
            
            # Reset to Info Mode
            state.mode = MenuState.INFO
            state.screen = InfoScreen.NETWORK
            state.cycle_tick = 0
            state.btn_hold_ticks = 0


def update_data(state: AppState):
    """Phase 2: Update Timers, Cycle Screens, and Fetch Stats"""
    if state.display_timer <= 0 or state.mode != MenuState.INFO:
        return

    # 1. Cycle Screens
    if state.cycle_tick >= INFO_CYCLE_TICKS:
        state.screen = InfoScreen.PERFORMANCE if state.screen == InfoScreen.NETWORK else InfoScreen.NETWORK
        state.stats_tick = STATS_UPDATE_TICKS # Force refresh on switch
        state.cycle_tick = 0
    else:
        state.cycle_tick += 1

    # 2. Fetch Stats
    if state.stats_tick >= STATS_UPDATE_TICKS:
        if state.screen == InfoScreen.NETWORK:
            state.cache_net = get_network_info()
        else:
            state.cache_perf = get_performance_info()
        state.stats_tick = 0
    else:
        state.stats_tick += 1


def draw_interface(state: AppState):
    """Phase 3: Render the current state to the buffer."""
    draw.rectangle((0, 0, WIDTH, HEIGHT), outline=0, fill=0) # Clear

    if state.display_timer > 0:
        if state.mode == MenuState.INFO:
            if state.screen == InfoScreen.NETWORK:
                h, ip = state.cache_net
                draw.text((0, 0),  f"HOST: {h}", font=font, fill=255)
                draw.text((0, 11), f"IP  : {ip}", font=font, fill=255)
                draw.text((0, 21), "-" * 20,     font=font, fill=255)
            else:
                c, m, d = state.cache_perf
                draw.text((0, 0),  f"CPU : {c:.1f}%", font=font, fill=255)
                draw.text((0, 11), f"RAM : {m:.1f}%", font=font, fill=255)
                draw.text((0, 21), f"DISK: {d:.1f}%", font=font, fill=255)
        
        elif state.mode == MenuState.REBOOT_WAIT:
            draw_overlay("REBOOT")
        elif state.mode == MenuState.SHUTDOWN_WAIT:
            draw_overlay("SHUTDOWN")
        
        state.display_timer -= 1
    
    # Push to Hardware
    disp.image(image)
    disp.show()


def draw_overlay(text_or_action):
    """Helper for drawing menu overlays."""
    draw.rectangle((0, 0, WIDTH, HEIGHT), outline=0, fill=0)
    if "..." in text_or_action: # Message Mode (e.g. "Rebooting...")
         draw.text((0, 0), text_or_action.center(20, "-"), font=font, fill=255)
    else: # Menu Mode (e.g. "REBOOT")
        draw.text((0, 0),  text_or_action.upper().center(18, "."), font=font, fill=255)
        draw.text((0, 11), "  Release Button  ", font=font, fill=255)
        draw.text((0, 21), f"To {text_or_action.capitalize()}".center(21, " "), font=font, fill=255)


# --- MAIN ---
def main():
    # Initial Splash
    draw_overlay("Infoscreen Started...")
    disp.image(image)
    disp.show()
    time.sleep(2)

    # Initialize State
    state = AppState()

    try:
        while True:
            handle_input(state)
            update_data(state)
            draw_interface(state)
            time.sleep(LOOP_SPEED)

    except KeyboardInterrupt:
        draw_overlay("Closing...")
        time.sleep(2)
    finally:
        disp.fill(0)
        disp.show()
        GPIO.cleanup()

if __name__ == "__main__":
    main()