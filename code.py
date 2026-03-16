# ============================================================================
# NTP Clock
# Adafruit ESP32-S3 Reverse TFT Feather
#
# Version : 1.10  (2026-03-16)
# Author  : Spencer Webb
# License : MIT
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the "Software"),
# to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.
# ============================================================================
#
# Displays HH:MM:SS as large 7-segment digits on the 240x135 TFT.
# Time is obtained via NTP with sub-second precision and maintained between
# syncs by a software clock driven by time.monotonic_ns().
#
# ** IMPORTANT NOTE ON TIMEKEEPING PRECISION **
#
# CircuitPython on the ESP32-S3 uses 32-bit single-precision floats for
# time.monotonic().  A 32-bit float has only 24 bits of mantissa, giving a
# precision of approximately 1 part in 16 million.  After one week of uptime
# (≈604,800 seconds), the resolution of time.monotonic() degrades to roughly
# 72ms — values smaller than ~36ms cannot be represented and are silently
# rounded away.
#
# Consequence: if sync_mono is stored as a float, the sub-second correction
# applied at each NTP sync (remain_ms / 1000.0) becomes completely invisible
# to the floating-point representation after extended uptime.  The clock
# anchors to the hard start of sync_s regardless of the true sub-second
# offset, introducing up to ±500ms of systematic display error.
#
# Fix: store sync_mono_ns as an integer in nanoseconds using
# time.monotonic_ns().  Python integers have arbitrary precision — no
# rounding ever occurs, regardless of uptime.  All time arithmetic in
# current_time() uses integer division (//) which is exact.
#
# time.monotonic() (float) is still used for NTP scheduling, button hold
# timing, and uptime display — none of those need sub-millisecond precision.
#
# Display layout (status bar visible):
#   y=  5-95  Large 7-segment HH:MM:SS digits
#   y=111     Zone + sync status at scale 1 — e.g. "UTC-5  NTP SYNC OK  14:23:05"
#             or "UTC-5  NTP SYNC FAIL  (OK 14:23:05)" when last attempt failed
#   y=128     Status bar — sync countdown | drift | NTP ping
#
# Display layout (status bar hidden, D2 toggled off):
#   y=  5-95  Large 7-segment HH:MM:SS digits
#   y=115     Timezone label grows to scale 3, centered in freed space
#
# Button functions (D0 is BOOT button, active LOW; D1/D2 active HIGH):
#   D0 short press  — cycle display color (Green / Red / Blue)
#   D0 hold 0.5s    — show system info screen (stays on after release)
#   D0 short press  — dismiss info screen and return to clock
#   D1 short press  — advance to next timezone
#   D2 short press  — toggle status bar; zone label resizes and shows sync status
#
# Required libraries in /lib on CIRCUITPY:
#   adafruit_display_text, adafruit_max1704x, webb_ntp
#
# All user settings live in settings.toml — see that file for options.
# ============================================================================

# Standard library
import gc
import os
import random
import time

# CircuitPython built-ins
import bitmaptools
import board
import digitalio
import displayio
import socketpool
import terminalio
import wifi

# Third-party / project
import adafruit_max1704x
import webb_ntp
from adafruit_display_text import label

# ---------------------------------------------------------------------------
# Boot timestamp — captured before any blocking calls so uptime is accurate.
# Uses time.monotonic() (float) — uptime only needs second precision.
# ---------------------------------------------------------------------------
boot_mono = time.monotonic()

VERSION = "1.10"   # shown on the info screen

# ---------------------------------------------------------------------------
# Configuration — all values come from settings.toml
# ---------------------------------------------------------------------------
WIFI_SSID         = os.getenv("WIFI_SSID")
WIFI_PASSWORD     = os.getenv("WIFI_PASSWORD")
NTP_SERVER        = os.getenv("NTP_SERVER",        "pool.ntp.org")
NTP_SYNC_INTERVAL = int(os.getenv("NTP_SYNC_INTERVAL", "3600"))  # seconds between syncs
NTP_SYNC_FUZZ     = int(os.getenv("NTP_SYNC_FUZZ",     "0"))    # max random offset +/- applied to sync interval
NTP_RETRY_BASE    = 5     # seconds before first retry after a failed sync
NTP_RETRY_MAX     = 300   # cap on retry backoff (5 minutes)
TIME_FORMAT       = int(os.getenv("TIME_FORMAT",       "24"))    # 12 or 24
DEBUG             = int(os.getenv("DEBUG",             "0"))     # 1 = verbose serial output
BRIGHTNESS        = float(os.getenv("BRIGHTNESS",     "1.0"))   # backlight level 0.0-1.0
INFO_BRIGHTNESS   = float(os.getenv("INFO_BRIGHTNESS", "1.0"))  # status bar text 0.0-1.0
SHOW_DRIFT        = int(os.getenv("SHOW_DRIFT",        "1"))     # 1 = show drift label
DEFAULT_TZ_OFFSET = int(os.getenv("DEFAULT_TZ_OFFSET", "0")) * 60  # hours -> minutes

# Status bar text color: a neutral grey scaled by INFO_BRIGHTNESS.
# This lets the status bar be dimmed independently of the backlight.
_ib        = max(0, min(255, int(INFO_BRIGHTNESS * 255)))
INFO_COLOR = (_ib << 16) | (_ib << 8) | _ib

# ---------------------------------------------------------------------------
# Timezone table
# Each entry: (offset_minutes, display_string)
# Stored in minutes so fractional zones (e.g. UTC+5:30 = 330 min) work cleanly.
# Covers all inhabited offsets from UTC-12 to UTC+14.
# ---------------------------------------------------------------------------
TIMEZONES = [
    (-720, "UTC-12"),
    (-660, "UTC-11"),
    (-600, "UTC-10"),
    (-570, "UTC-9:30"),
    (-540, "UTC-9"),
    (-480, "UTC-8"),
    (-420, "UTC-7"),
    (-360, "UTC-6"),
    (-300, "UTC-5"),
    (-240, "UTC-4"),
    (-210, "UTC-3:30"),
    (-180, "UTC-3"),
    (-120, "UTC-2"),
    ( -60, "UTC-1"),
    (   0, "UTC"),
    (  60, "UTC+1"),
    ( 120, "UTC+2"),
    ( 180, "UTC+3"),
    ( 210, "UTC+3:30"),
    ( 240, "UTC+4"),
    ( 270, "UTC+4:30"),
    ( 300, "UTC+5"),
    ( 330, "UTC+5:30"),
    ( 345, "UTC+5:45"),
    ( 360, "UTC+6"),
    ( 390, "UTC+6:30"),
    ( 420, "UTC+7"),
    ( 480, "UTC+8"),
    ( 525, "UTC+8:45"),
    ( 540, "UTC+9"),
    ( 570, "UTC+9:30"),
    ( 600, "UTC+10"),
    ( 630, "UTC+10:30"),
    ( 660, "UTC+11"),
    ( 720, "UTC+12"),
    ( 765, "UTC+12:45"),
    ( 780, "UTC+13"),
    ( 840, "UTC+14"),
]

# Resolve DEFAULT_TZ_OFFSET to an index; fall back to UTC (index 14) if not found
tz_index = 14
for _i, (_off, _) in enumerate(TIMEZONES):
    if _off == DEFAULT_TZ_OFFSET:
        tz_index = _i
        break

# ---------------------------------------------------------------------------
# Color schemes — cycled by D0 short press
# Each entry: (active_segment_color, inactive_segment_shadow_color)
# The shadow color is a dim version of the active color, giving the classic
# LCD ghost-segment look against a black background.
# ---------------------------------------------------------------------------
COLOR_SCHEMES = [
    (0x00FF00, 0x001800),  # Green (default)
    (0xFF0000, 0x180000),  # Red
    (0x0000FF, 0x000018),  # Blue
]
color_scheme_index = 0

# ---------------------------------------------------------------------------
# 7-segment digit geometry  (all values in pixels)
# Display is 240 wide x 135 tall.
#
#  DW   = digit width
#  DH   = digit height
#  ST   = segment thickness
#  GAP  = spacing between every adjacent element (digit↔digit, digit↔colon)
#  CW   = colon column width
#  LEFT = left margin
#  TOP  = top margin
#
# Layout is designed to fill the full 240px width with equal spacing everywhere:
#   A GAP of 6px separates every adjacent element — digit↔digit, digit↔colon,
#   and colon↔digit — giving visually centred colons.
#   Total = 6*DW + 7*GAP + 2*CW + 2*LEFT
#         = 6*28 + 7*6  + 2*8  + 2*7 = 168+42+16+14 = 240px
#
#  UTC_Y       = zone label vertical centre when status bar is visible (scale 1)
#  UTC_Y_LARGE = zone label vertical centre when status bar is hidden  (scale 3)
#  INFO_Y      = status bar vertical centre
#
# Zone label geometry:
#   scale 1: font 12px tall, centered at UTC_Y=111
#   scale 3: font 36px tall, centered at UTC_Y_LARGE=115 (top=97, bottom=133)
#   Both fit in the 40px gap between digit bottom (y=95) and display edge (y=135)
# ---------------------------------------------------------------------------
DW          = 28
DH          = 90
ST          = 4
GAP         = 6    # spacing between every adjacent element (digit↔digit, digit↔colon)
CW          = 8    # colon column width
LEFT        = 7    # left margin (= right margin for symmetry)
TOP         = 5
UTC_Y       = 111   # zone label centre, status bar visible, scale=1
UTC_Y_LARGE = 115   # zone label centre, status bar hidden,  scale=3
INFO_Y      = 128   # status bar centre

# Segment rectangles as (x, y, w, h) relative to each digit's top-left corner.
# Segment order: 0=top, 1=top-left, 2=top-right, 3=middle,
#                4=bottom-left, 5=bottom-right, 6=bottom
def _seg_rects(dw, dh, st):
    hw  = dw - 2 * st           # horizontal bar inner width
    vhl = (dh - 3 * st) // 2   # vertical bar height
    return [
        (st,      0,        hw, st),   # 0 top
        (0,       st,       st, vhl),  # 1 top-left
        (dw - st, st,       st, vhl),  # 2 top-right
        (st,      st + vhl, hw, st),   # 3 middle
        (0,       2*st+vhl, st, vhl),  # 4 bottom-left
        (dw - st, 2*st+vhl, st, vhl),  # 5 bottom-right
        (st,      dh - st,  hw, st),   # 6 bottom
    ]

# Segment on/off maps for digits 0-9.
# Tuple order matches _seg_rects above: (top, TL, TR, mid, BL, BR, bottom)
DIGIT_SEGS = [
    (True,  True,  True,  False, True,  True,  True),   # 0
    (False, False, True,  False, False, True,  False),  # 1
    (True,  False, True,  True,  True,  False, True),   # 2
    (True,  False, True,  True,  False, True,  True),   # 3
    (False, True,  True,  True,  False, True,  False),  # 4
    (True,  True,  False, True,  False, True,  True),   # 5
    (True,  True,  False, True,  True,  True,  True),   # 6
    (True,  False, True,  False, False, True,  False),  # 7
    (True,  True,  True,  True,  True,  True,  True),   # 8
    (True,  True,  True,  True,  False, True,  True),   # 9
]

SEGS = _seg_rects(DW, DH, ST)

# Precomputed left-edge x positions for each of the six digit slots (0-1=HH, 2-3=MM, 4-5=SS).
# Each colon column is CW wide; a full GAP is added after each colon so the
# spacing is symmetric on both sides of every colon.
DIGIT_X = [
    LEFT,
    LEFT +   DW + GAP,
    LEFT + 2*(DW + GAP) +     (CW + GAP),   # GAP after each colon matches GAP before
    LEFT + 3*(DW + GAP) +     (CW + GAP),
    LEFT + 4*(DW + GAP) + 2 * (CW + GAP),
    LEFT + 5*(DW + GAP) + 2 * (CW + GAP),
]

# Precomputed left-edge x positions for the two colons
COLON_X = [
    LEFT + 2*(DW + GAP),                  # between HH and MM
    LEFT + 4*(DW + GAP) + (CW + GAP),    # between MM and SS (accounts for gap after first colon)
]

# ---------------------------------------------------------------------------
# Display and clock face setup
# ---------------------------------------------------------------------------
display            = board.DISPLAY
display.rotation   = 0
display.brightness = BRIGHTNESS

# Shared three-entry palette used by both the clock bitmap and info screen bitmap:
#   index 0 = black background
#   index 1 = active segment / foreground text  (updated by apply_color_scheme)
#   index 2 = inactive segment shadow           (updated by apply_color_scheme)
# Since the clock bitmap uses palette indices, changing palette[1] and palette[2]
# immediately recolors all drawn segments — no redraw required.
palette    = displayio.Palette(3)
palette[0] = 0x000000
palette[1] = COLOR_SCHEMES[0][0]  # green on
palette[2] = COLOR_SCHEMES[0][1]  # green shadow

# Full-screen bitmap; all digit and colon drawing goes directly into this
bmp  = displayio.Bitmap(240, 135, 3)
tile = displayio.TileGrid(bmp, pixel_shader=palette)

# Root display group for the clock face
group = displayio.Group()
group.append(tile)
display.root_group = group

# -- Status bar labels (bottom row) ------------------------------------------
# Strings are padded to a consistent width to avoid repeated heap allocation
# as the label's internal bitmap resizes.  Format specs:
#   sync_label : "Sync NNNNs" — 4-digit field keeps width stable from 1s-9999s
#   ping_label : "Ping NNNNms" — 4-digit field covers 1ms-9999ms
sync_label = label.Label(terminalio.FONT, text="", color=INFO_COLOR, scale=1)
sync_label.anchor_point      = (0.0, 0.5)
sync_label.anchored_position = (2, INFO_Y)
group.append(sync_label)

ping_label = label.Label(terminalio.FONT, text="", color=INFO_COLOR, scale=1)
ping_label.anchor_point      = (1.0, 0.5)
ping_label.anchored_position = (238, INFO_Y)
group.append(ping_label)

# Drift label starts with a placeholder; replaced after the first full-interval sync
drift_label = label.Label(
    terminalio.FONT,
    text  = "Drift ---ms/h" if SHOW_DRIFT else "",
    color = INFO_COLOR,
    scale = 1,
)
drift_label.anchor_point      = (0.5, 0.5)
drift_label.anchored_position = (120, INFO_Y)
group.append(drift_label)

# -- Timezone / sync-status label --------------------------------------------
# Two display modes controlled by _update_zone_label():
#   Status bar visible (info_visible=True):
#     scale=1, combined text: "UTC-5  NTP SYNC OK  14:23:05"
#                          or "UTC-5  NTP SYNC FAIL  (OK 14:23:05)"
#   Status bar hidden (info_visible=False):
#     scale=3, timezone only, centered in the freed space below the digits
zone_label = label.Label(
    terminalio.FONT,
    text  = TIMEZONES[tz_index][1],
    color = COLOR_SCHEMES[0][0],  # matches palette[1] at startup
    scale = 1,
)
zone_label.anchor_point      = (0.5, 0.5)
zone_label.anchored_position = (120, UTC_Y)
group.append(zone_label)

# -- Error label (centre screen, hidden until a fault occurs) ----------------
error_label = label.Label(terminalio.FONT, text="", color=0xFF0000, scale=1)
error_label.hidden            = True
error_label.anchor_point      = (0.5, 0.5)
error_label.anchored_position = (120, 67)
group.append(error_label)

# ---------------------------------------------------------------------------
# Info screen — triggered by holding D0, dismissed by a subsequent short press
# Uses a separate displayio Group; switching screens is a single assignment to
# display.root_group.  Shares the same palette as the clock face for the
# background bitmap (index 0 = black).
#
# Info label text colors are NOT palette-driven — they use the .color property
# directly and must be updated explicitly in apply_color_scheme().
# ---------------------------------------------------------------------------
_INFO_LINE_H = 13   # px per row at scale=1 (12px font + 1px gap)
_INFO_X      = 2    # left margin in pixels

def _make_info_label(y):
    """Return a left-aligned scale-1 label at (_INFO_X, y), initially empty."""
    lbl = label.Label(terminalio.FONT, text="", color=COLOR_SCHEMES[0][0], scale=1)
    lbl.anchor_point      = (0.0, 0.0)
    lbl.anchored_position = (_INFO_X, y)
    return lbl

info_group = displayio.Group()
info_bmp   = displayio.Bitmap(240, 135, 3)  # cleared to palette[0] (black) by default
info_tile  = displayio.TileGrid(info_bmp, pixel_shader=palette)
info_group.append(info_tile)

# Nine text rows: one title + eight data fields
_y = 2
info_title_lbl  = _make_info_label(_y); info_group.append(info_title_lbl);  _y += _INFO_LINE_H + 2
info_ntp_lbl    = _make_info_label(_y); info_group.append(info_ntp_lbl);    _y += _INFO_LINE_H
info_fuzz_lbl   = _make_info_label(_y); info_group.append(info_fuzz_lbl);   _y += _INFO_LINE_H
info_ssid_lbl   = _make_info_label(_y); info_group.append(info_ssid_lbl);   _y += _INFO_LINE_H
info_ip_lbl     = _make_info_label(_y); info_group.append(info_ip_lbl);     _y += _INFO_LINE_H
info_mac_lbl    = _make_info_label(_y); info_group.append(info_mac_lbl);    _y += _INFO_LINE_H
info_batt_lbl   = _make_info_label(_y); info_group.append(info_batt_lbl);   _y += _INFO_LINE_H
info_mem_lbl    = _make_info_label(_y); info_group.append(info_mem_lbl);    _y += _INFO_LINE_H
info_uptime_lbl = _make_info_label(_y); info_group.append(info_uptime_lbl)
del _y  # finished layout; clean up the temp variable

# Tuple of all info screen labels whose .color must track the active color scheme
INFO_LABELS = (
    info_title_lbl, info_ntp_lbl, info_fuzz_lbl, info_ssid_lbl, info_ip_lbl,
    info_mac_lbl, info_batt_lbl, info_mem_lbl, info_uptime_lbl,
)

# ---------------------------------------------------------------------------
# Button setup
# D0 (BOOT): Pull.UP — resting HIGH, pressed LOW  (active low)
# D1, D2:    Pull.DOWN — resting LOW,  pressed HIGH (active high)
# ---------------------------------------------------------------------------
btn_d0 = digitalio.DigitalInOut(board.D0)
btn_d0.switch_to_input(pull=digitalio.Pull.UP)

btn_d1 = digitalio.DigitalInOut(board.D1)
btn_d1.switch_to_input(pull=digitalio.Pull.DOWN)

btn_d2 = digitalio.DigitalInOut(board.D2)
btn_d2.switch_to_input(pull=digitalio.Pull.DOWN)

HOLD_THRESHOLD     = 0.5   # seconds D0 must be held to open the info screen
info_visible       = True  # whether the status bar labels are currently shown
info_screen_active = False # whether the info screen is the active root_group
btn_d0_last        = True  # D0 rests HIGH (not pressed)
btn_d0_held_since  = None  # monotonic time of last D0 press, or None
btn_d1_last        = False
btn_d2_last        = False

# Sync status — updated by sync_ntp() on every attempt.
# last_sync_ok_hms is recorded in local timezone.
last_sync_ok_hms = "--:--:--"  # time of last good sync ("--:--:--" until first sync)
last_sync_ok     = False       # True if the most recent sync attempt succeeded

# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------
def _fill_rect(x, y, w, h, color_idx):
    """Fill a rectangle in the clock bitmap with the given palette index."""
    bitmaptools.fill_region(bmp, x, y, x + w, y + h, color_idx)

def _draw_digit(digit, ox, oy):
    """Draw one 7-segment digit at bitmap origin (ox, oy)."""
    segs_on = DIGIT_SEGS[digit]
    for i, (sx, sy, sw, sh) in enumerate(SEGS):
        _fill_rect(ox + sx, oy + sy, sw, sh, 1 if segs_on[i] else 2)

def _draw_colon(ox, oy):
    """Draw a colon glyph (two square dots) at bitmap origin (ox, oy)."""
    dot = ST + 1
    cx  = ox + CW // 2 - dot // 2
    _fill_rect(cx, oy +     DH // 3 - dot // 2, dot, dot, 1)
    _fill_rect(cx, oy + 2 * DH // 3 - dot // 2, dot, dot, 1)

# Colons are static — draw them once at startup, never touch them again
_draw_colon(COLON_X[0], TOP)
_draw_colon(COLON_X[1], TOP)

# Lamp test — light all segments on every digit while waiting for the first NTP sync.
# draw_time() will overwrite these naturally once we have a valid time.
for _lamp_i in range(6):
    _draw_digit(8, DIGIT_X[_lamp_i], TOP)
del _lamp_i

# Per-slot cache of the last drawn digit value; -1 means "not yet drawn"
_last_digits = [-1, -1, -1, -1, -1, -1]

def draw_time(hour, minute, second):
    """Redraw only the digit slots whose value has changed since the last call."""
    digits = [
        hour   // 10, hour   % 10,
        minute // 10, minute % 10,
        second // 10, second % 10,
    ]
    for i, d in enumerate(digits):
        if d != _last_digits[i]:
            _draw_digit(d, DIGIT_X[i], TOP)
            _last_digits[i] = d

def apply_color_scheme():
    """Push the current COLOR_SCHEMES entry into the shared palette, zone label,
    and all info screen labels.

    The clock digit bitmap uses palette indices, so changing palette[1] and
    palette[2] recolors all drawn segments immediately — no digit redraw needed.
    Info screen labels use the .color property directly and must be updated
    explicitly since they are not palette-driven.
    """
    on_color, off_color = COLOR_SCHEMES[color_scheme_index]
    palette[1]       = on_color
    palette[2]       = off_color
    zone_label.color = on_color
    for lbl in INFO_LABELS:
        lbl.color = on_color

def _update_zone_label():
    """Rebuild zone_label text, scale, and position to match current state.

    Status bar visible (info_visible=True):
        scale=1, timezone + NTP sync status on one line:
          "UTC-5  NTP SYNC OK  14:23:05"
          "UTC-5  NTP SYNC FAIL  (OK 14:23:05)"
    Status bar hidden (info_visible=False):
        scale=3, timezone only, centered in the freed space below the digits.

    Called after any sync attempt, timezone change, or D2 toggle.
    """
    tz_str = TIMEZONES[tz_index][1]
    if info_visible:
        if last_sync_ok:
            zone_label.text = "{}  NTP SYNC OK  {}".format(tz_str, last_sync_ok_hms)
        else:
            zone_label.text = "{}  NTP SYNC FAIL  (OK {})".format(tz_str, last_sync_ok_hms)
        zone_label.scale             = 1
        zone_label.anchored_position = (120, UTC_Y)
    else:
        zone_label.text              = tz_str
        zone_label.scale             = 3
        zone_label.anchored_position = (120, UTC_Y_LARGE)

def show_error(msg):
    """Show a red error message centred on the clock face."""
    if DEBUG: print("Error:", msg)
    error_label.text   = msg
    error_label.hidden = False

# ---------------------------------------------------------------------------
# Info screen helpers
# ---------------------------------------------------------------------------
def show_info_screen(mono):
    """Populate the info group with current system data and switch to it."""
    global info_screen_active

    ip  = str(wifi.radio.ipv4_address) if wifi.radio.connected else "Not connected"
    mac = ":".join("{:02X}".format(b) for b in wifi.radio.mac_address)

    if battery_monitor:
        try:
            batt_str = "{:.2f}V  {:.0f}%".format(
                battery_monitor.cell_voltage, battery_monitor.cell_percent)
        except Exception:
            batt_str = "Read error"
    else:
        batt_str = "No battery"

    up_secs = int(mono - boot_mono)
    uptime  = "{}h {:02d}m {:02d}s".format(
        up_secs // 3600, (up_secs % 3600) // 60, up_secs % 60)

    info_title_lbl.text  = "-- System Info v{} --".format(VERSION)
    info_ntp_lbl.text    = "NTP:  " + NTP_SERVER
    info_fuzz_lbl.text   = "Interval: {}sec.  Fuzz: +/-{}sec.".format(NTP_SYNC_INTERVAL, NTP_SYNC_FUZZ)
    info_ssid_lbl.text   = "WiFi: " + (WIFI_SSID or "?")
    info_ip_lbl.text     = "IP:   " + ip
    info_mac_lbl.text    = "MAC:  " + mac
    info_batt_lbl.text   = "Batt: " + batt_str
    info_mem_lbl.text    = "Mem:  {} bytes free".format(gc.mem_free())
    info_uptime_lbl.text = "Up:   " + uptime

    display.root_group = info_group
    info_screen_active = True

def hide_info_screen():
    """Return to the clock face."""
    global info_screen_active
    display.root_group = group
    info_screen_active = False
    # No digit redraw needed — the clock bitmap is unchanged while the info
    # screen was shown, and _last_digits still accurately reflects its contents.

# ---------------------------------------------------------------------------
# NTP scheduling helper
# ---------------------------------------------------------------------------
def _next_sync_time(mono):
    """Return the monotonic time of the next NTP sync attempt.

    Adds NTP_SYNC_INTERVAL to the current time, then applies a random fuzz
    offset in the range [-NTP_SYNC_FUZZ, +NTP_SYNC_FUZZ] seconds.  Fuzz
    prevents multiple devices sharing the same settings from hitting the NTP
    server in lockstep (RFC 5905 recommends this practice).

    The result is clamped so it is never in the past — the next attempt is
    always at least one second from now regardless of the fuzz value.
    """
    if NTP_SYNC_FUZZ > 0:
        offset = random.uniform(-NTP_SYNC_FUZZ, NTP_SYNC_FUZZ)
    else:
        offset = 0
    return mono + max(1, NTP_SYNC_INTERVAL + offset)

# ---------------------------------------------------------------------------
# WiFi connection
# ---------------------------------------------------------------------------
if DEBUG: print("Connecting to WiFi...")
try:
    wifi.radio.connect(WIFI_SSID, WIFI_PASSWORD)
    if DEBUG: print("Connected:", wifi.radio.ipv4_address)
except Exception as e:
    show_error("WiFi failed: " + str(e))
    while True:
        time.sleep(1)  # yield CPU — nothing can be done without network

# ---------------------------------------------------------------------------
# Socket pool — created once after WiFi connects and reused for all NTP syncs.
# Creating a new SocketPool on every sync is wasteful, and especially harmful
# during the failure-retry loop where syncs can occur in rapid succession.
# ---------------------------------------------------------------------------
pool = socketpool.SocketPool(wifi.radio)

# ---------------------------------------------------------------------------
# Battery monitor (MAX17048, I2C address 0x36)
# Note: adafruit_max1704x is imported at the top of this file, so if the
# library is absent from /lib the program will fail at import, not here.
# This try/except only guards against hardware absence (no battery attached).
# ---------------------------------------------------------------------------
try:
    battery_monitor = adafruit_max1704x.MAX17048(board.I2C())
except Exception:
    battery_monitor = None

# ---------------------------------------------------------------------------
# Software clock
#
# The hardware RTC has only one-second resolution.  Instead we record the
# exact UTC time at each NTP sync as (sync_h, sync_m, sync_s), together with
# a nanosecond-precision monotonic anchor (sync_mono_ns), then derive the
# current time as:
#
#   elapsed_s = (time.monotonic_ns() - sync_mono_ns) // 1_000_000_000
#   current   = (sync_h, sync_m, sync_s) + elapsed_s seconds
#
# WHY NANOSECONDS?  See the extended note at the top of this file.
# In brief: time.monotonic() is a 32-bit float.  After a week of uptime its
# resolution degrades to ~72ms, silently discarding the sub-second correction
# applied at each sync and causing up to ±500ms of display error.
# time.monotonic_ns() returns a Python integer — exact at any uptime.
#
# sync_mono_ns is back-dated by remain_ms (the sub-second NTP offset) so the
# software clock ticks from the true start of sync_s, not from the moment
# the sync code happened to execute.
# ---------------------------------------------------------------------------
sync_h       = 0
sync_m       = 0
sync_s       = 0
sync_mono_ns = 0   # integer nanosecond anchor for the above H:M:S

# Drift state — valid only after two syncs separated by at least NTP_SYNC_INTERVAL.
# Stored in ms to keep the drift arithmetic straightforward.
prev_sync_ntp_ms  = 0.0   # NTP wall-clock time (ms since midnight) at previous sync
prev_sync_mono_ns = 0     # monotonic_ns value at previous sync (integer)

def sync_ntp():
    """Fetch UTC from the NTP server and update the software clock.

    Applies a half-RTT correction so the displayed time reflects true UTC at
    the moment of the call, not the moment the server sent its response.

    The monotonic anchor (sync_mono_ns) is stored as an integer in nanoseconds
    to avoid the 32-bit float precision loss that would otherwise corrupt the
    sub-second correction after extended uptime.  See the note at the top of
    this file for a full explanation.

    Returns True on success, False on any error.
    """
    global sync_h, sync_m, sync_s, sync_mono_ns
    global prev_sync_ntp_ms, prev_sync_mono_ns
    global last_sync_ok, last_sync_ok_hms

    if DEBUG: print("Syncing NTP...")
    try:
        unix_secs, frac_ms, rtt_ms = webb_ntp.get_time(pool, NTP_SERVER)

        # Adjust for the fractional second already elapsed plus half the RTT
        total_ms   = frac_ms + rtt_ms / 2.0
        extra_secs = int(total_ms // 1000)  # whole seconds to fold into H:M:S
        remain_ms  = total_ms % 1000.0      # sub-second remainder (0.0 – 999.999)

        sync_h, sync_m, sync_s = webb_ntp.unix_to_hms(unix_secs + extra_secs)

        # Store the monotonic anchor as integer nanoseconds — exact at any uptime.
        # Back-date by remain_ms so the clock counts from the true start of sync_s.
        # int() conversion of remain_ms * 1_000_000 is exact for values < 1000ms.
        sync_mono_ns = time.monotonic_ns() - int(remain_ms * 1_000_000)

        ping_label.text = "Ping {:4d}ms".format(int(rtt_ms))

        # Record successful sync time in local timezone for the zone label
        last_sync_ok = True
        _utc  = sync_h * 3600 + sync_m * 60 + sync_s
        _loc  = (_utc + TIMEZONES[tz_index][0] * 60) % 86400
        last_sync_ok_hms = "{:02d}:{:02d}:{:02d}".format(
            _loc // 3600, (_loc % 3600) // 60, _loc % 60)

        # Drift: measure how much the software clock gained or lost vs NTP.
        # Requires two syncs at least 90% of NTP_SYNC_INTERVAL apart to avoid
        # noise from retries or closely-spaced syncs.
        # Using integer ns for elapsed time gives exact arithmetic.
        ntp_ms_now             = (sync_h * 3600 + sync_m * 60 + sync_s) * 1000.0 + remain_ms
        elapsed_between_syncs_ns = sync_mono_ns - prev_sync_mono_ns
        elapsed_between_syncs_s  = elapsed_between_syncs_ns / 1_000_000_000.0
        if prev_sync_ntp_ms > 0.0 and elapsed_between_syncs_s >= NTP_SYNC_INTERVAL * 0.9:
            sw_elapsed_ms  = elapsed_between_syncs_ns / 1_000_000.0  # exact integer→float
            ntp_elapsed_ms = ntp_ms_now - prev_sync_ntp_ms
            if ntp_elapsed_ms < 0:
                ntp_elapsed_ms += 86400000.0   # midnight rollover
            if ntp_elapsed_ms > 0:
                # Drift in ms per hour: error fraction scaled to one hour
                drift = (sw_elapsed_ms - ntp_elapsed_ms) / ntp_elapsed_ms * 3600000.0
                if SHOW_DRIFT:
                    drift_label.text = "Drift {:+.1f}ms/h".format(drift)
                if DEBUG: print("Drift: {:+.1f}ms/h".format(drift))

        prev_sync_ntp_ms  = ntp_ms_now
        prev_sync_mono_ns = sync_mono_ns

        if DEBUG: print("Synced. rtt={}ms frac={}ms".format(int(rtt_ms), int(frac_ms)))
        return True

    except Exception as e:
        last_sync_ok = False
        show_error("NTP sync failed: " + str(e))
        return False

def current_time(mono_ns):
    """Return (h, m, s) in the active timezone, derived from the software clock.

    Accepts a time.monotonic_ns() value (integer nanoseconds).  Integer
    floor-division by 1_000_000_000 gives exact elapsed seconds at any uptime,
    unlike the float subtraction it replaces which lost precision after ~1 week.

    UTC seconds are computed first, then the timezone offset (stored in minutes)
    is added.  Modulo 86400 handles both midnight rollover and negative offsets.
    """
    elapsed_s  = (mono_ns - sync_mono_ns) // 1_000_000_000   # exact integer division
    utc_secs   = sync_h * 3600 + sync_m * 60 + sync_s + elapsed_s
    local_secs = (utc_secs + TIMEZONES[tz_index][0] * 60) % 86400
    return local_secs // 3600, (local_secs % 3600) // 60, local_secs % 60

# ---------------------------------------------------------------------------
# Initial NTP sync
#
# have_time tracks whether we have ever obtained a valid time.  It is set
# True on the first successful sync and never cleared — the software clock
# keeps running on the last good fix even if later syncs fail.
#
# next_ntp_try is the monotonic time of the next sync attempt (float seconds,
# precision is adequate for scheduling which only needs ~second accuracy).
# retry_s is the current retry backoff interval, doubled on each failure
# (capped at NTP_RETRY_MAX) so we don't hammer the server during an outage.
# ---------------------------------------------------------------------------
have_time    = sync_ntp()
mono_now     = time.monotonic()
retry_s      = NTP_RETRY_BASE
next_ntp_try = _next_sync_time(mono_now) if have_time else mono_now + retry_s

if have_time:
    error_label.hidden = True
_update_zone_label()

last_second = -1

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
while True:
    mono    = time.monotonic()      # float — used for scheduling and button timing
    mono_ns = time.monotonic_ns()   # integer — used for sub-second clock display

    # --- Periodic NTP sync with backoff on failure --------------------------
    if mono >= next_ntp_try:
        ok = sync_ntp()
        if ok:
            have_time    = True
            retry_s      = NTP_RETRY_BASE          # reset backoff on success
            next_ntp_try = _next_sync_time(mono)
            error_label.hidden = True
        else:
            # Keep the software clock running on the last good fix.
            # Schedule a retry with exponential backoff, capped at NTP_RETRY_MAX.
            next_ntp_try = mono + retry_s
            retry_s      = min(retry_s * 2, NTP_RETRY_MAX)
        _update_zone_label()

    # --- Clock display (skipped while info screen is visible) ---------------
    if have_time and not info_screen_active:
        h, m, s = current_time(mono_ns)   # integer ns path — exact at any uptime
        if s != last_second:
            last_second = s
            if TIME_FORMAT == 12:
                h = h % 12 or 12  # 0 -> 12, 13 -> 1, etc.
            draw_time(h, m, s)
            sync_label.text = "Sync {:4d}s".format(int(next_ntp_try - mono))

    # --- D0: hold = info screen, quick press = color cycle or dismiss --------
    # Pull.UP: resting state True (HIGH), pressed False (LOW)
    #
    # State machine:
    #   Press & hold (>HOLD_THRESHOLD) -> info screen opens, stays after release
    #   Quick press while info screen showing -> dismiss, return to clock
    #   Quick press normally -> advance color scheme
    btn_d0_now = btn_d0.value
    if not btn_d0_now and btn_d0_last:
        # Falling edge — button just pressed; start timing the hold
        btn_d0_held_since = mono
    elif not btn_d0_now and not btn_d0_last:
        # Still held — open info screen once threshold is crossed
        if btn_d0_held_since is not None and not info_screen_active:
            if mono - btn_d0_held_since >= HOLD_THRESHOLD:
                show_info_screen(mono)
                btn_d0_held_since = None  # prevent re-triggering on continued hold
    elif btn_d0_now and not btn_d0_last:
        # Rising edge — button released
        if btn_d0_held_since is not None:
            # Released before threshold: treat as a quick press
            if info_screen_active:
                hide_info_screen()
            else:
                color_scheme_index = (color_scheme_index + 1) % len(COLOR_SCHEMES)
                apply_color_scheme()
        # If held_since is None, the hold already fired; do nothing on release.
        btn_d0_held_since = None
    btn_d0_last = btn_d0_now

    # --- D1: advance timezone on rising edge --------------------------------
    btn_d1_now = btn_d1.value
    if btn_d1_now and not btn_d1_last:
        tz_index    = (tz_index + 1) % len(TIMEZONES)
        last_second = -1   # force draw_time() to run on next loop tick
        _update_zone_label()
    btn_d1_last = btn_d1_now

    # --- D2: toggle status bar; resize zone label to use freed space --------
    # When the status bar is hidden the zone label grows from scale 1 to scale 3
    # and recenters at UTC_Y_LARGE to fill the space between the digits and the
    # bottom of the display.  Reversing the toggle restores the original layout.
    btn_d2_now = btn_d2.value
    if btn_d2_now and not btn_d2_last:
        info_visible       = not info_visible
        sync_label.hidden  = not info_visible
        ping_label.hidden  = not info_visible
        drift_label.hidden = not info_visible
        _update_zone_label()
    btn_d2_last = btn_d2_now

    time.sleep(0.02)  # ~50 Hz — responsive to buttons, easy on the CPU
