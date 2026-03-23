================================================================================
 webb-clock  v1.15
 Spencer Webb  |  webb@antennasys.com
================================================================================

A precision NTP-synchronized clock for the Adafruit ESP32-S3 Reverse TFT
Feather, running CircuitPython.  Time is obtained from the internet via NTP
and displayed as large 7-segment digits on the built-in 240x135 TFT display.


--------------------------------------------------------------------------------
 HARDWARE REQUIREMENT
--------------------------------------------------------------------------------

  Adafruit ESP32-S3 Reverse TFT Feather
  https://www.adafruit.com/product/5691

  Tested on CircuitPython v8 and v10.


--------------------------------------------------------------------------------
 PACKAGE CONTENTS
--------------------------------------------------------------------------------

  code.py
      The main CircuitPython application.  Copy this to the root of the
      CIRCUITPY drive.

  webb_ntp.py
      A custom NTP client module written for sub-second timestamp precision.
      The standard Adafruit NTP library has historically discarded the
      fractional-second component of the NTP timestamp; this module retains
      it for millisecond-level phase alignment of the software clock.
      Copy this to the root of the CIRCUITPY drive alongside code.py.

  lib/
      Required third-party CircuitPython libraries.  Copy the entire lib
      folder to the root of the CIRCUITPY drive.  The following libraries
      must be present:
        - adafruit_display_text
        - adafruit_max1704x  (battery monitor; required for battery level
                              display, safe to omit if no battery is used)

  settings.toml
      User configuration file.  Must be edited before first use.
      Copy to the root of the CIRCUITPY drive.  See CONFIGURATION below.


--------------------------------------------------------------------------------
 QUICK START
--------------------------------------------------------------------------------

  1. Copy code.py, webb_ntp.py, the lib/ folder, and settings.toml to the
     root of the CIRCUITPY drive.

  2. Edit settings.toml — at minimum, fill in your WiFi credentials
     (WIFI_SSID and WIFI_PASSWORD).

  3. Power cycle the board.  The display will show a lamp test (all segments
     lit) while connecting to WiFi and performing the first NTP sync.  Once
     synchronized the clock starts ticking.


--------------------------------------------------------------------------------
 CONFIGURATION  (settings.toml)
--------------------------------------------------------------------------------

  WIFI_SSID / WIFI_PASSWORD
      Your WiFi network credentials.  Required.

  NTP_SERVER
      Hostname of the NTP server.  Defaults to "pool.ntp.org", which is a
      globally distributed pool that works well for most locations.

  NTP_SYNC_INTERVAL
      How often to synchronize with the NTP server, in seconds.
      Default: 3600 (one hour).  Shorter intervals improve long-term
      accuracy at the cost of slightly more network activity.

  NTP_SYNC_FUZZ_PCT
      Randomization applied to the sync interval, expressed as a percentage
      of the current interval.  The actual sync time is the current interval
      ± up to this percentage, chosen randomly.  Using a percentage rather
      than a fixed number of seconds means the fuzz scales correctly as the
      adaptive interval grows or shrinks over time.  Prevents multiple
      devices with identical settings from hitting the NTP server
      simultaneously (recommended by RFC 5905).
      Default: 10 (10%).  Set to 0 to disable.

  TIME_FORMAT
      24 for 24-hour display, 12 for 12-hour display.  Default: 24.

  BRIGHTNESS
      Power-on backlight brightness, 0.0 to 1.0.  Default: 1.0 (full).
      Can be adjusted at runtime using the D2 button (see below).

  INFO_BRIGHTNESS
      Brightness of the status bar text (sync countdown and NTP ping),
      expressed as a grey level from 0.0 to 1.0.  Allows the status bar
      to be dimmer than the clock digits.  Default: 1.0.

  DEFAULT_TZ_OFFSET
      Startup timezone as a whole-hour offset from UTC.  Examples: 0 for
      UTC, -5 for UTC-5, 1 for UTC+1.  For fractional-hour zones (e.g.
      UTC+5:30), set the nearest whole hour here and fine-tune at runtime
      with the D1 button.  Default: 0 (UTC).

  DEBUG
      Set to 1 to enable verbose output on the serial console.  Useful
      when the board is connected to a computer via USB.  Default: 0.


--------------------------------------------------------------------------------
 DISPLAY LAYOUT
--------------------------------------------------------------------------------

  Normal mode (status bar visible):
    Large 7-segment HH:MM:SS digits fill the upper portion of the screen.
    Below the digits, a single line shows the current timezone and the
    result of the last NTP sync attempt:
      "UTC-5  NTP SYNC OK  14:23:05"
      "UTC-5  NTP SYNC FAIL  (OK 14:23:05)"
    The bottom status bar shows the time until the next sync on the left,
    the battery level in the centre (if a battery is present), and the
    last NTP round-trip (ping) time on the right.  The battery level is
    updated once per minute.  The sync countdown reflects the live adaptive
    interval, which may differ from the NTP_SYNC_INTERVAL setting.

  Clean mode (status bar hidden, D2 short press):
    The status bar is hidden.  If a battery is present, the timezone
    label is shown left-justified and the battery percentage is shown
    right-justified in the freed row, both at a larger size.  Without
    a battery, the timezone label fills the full width at maximum size.

  Brightness adjust mode (D2 long press):
    All digit segments light up as a full-load brightness reference.
    The zone label area shows the current brightness level as a percentage.
    Short presses cycle through the five available levels.

  Error mode:
    If WiFi or NTP fails, the clock digits dim and a bright red error
    message appears overlaid across three lines in the digit area.
    The clock continues running from the last known-good time while
    retrying automatically with exponential backoff.


--------------------------------------------------------------------------------
 BUTTONS
--------------------------------------------------------------------------------

  The three buttons are on the left edge of the board, labeled D0, D1, D2
  from top to bottom.

  D0  (short press)
      Cycles the display color through Green → Red → Blue → Green.

  D0  (hold 0.5s)
      Opens the System Info screen, which remains visible after release.
      Displays: firmware version, NTP server, baseline and current adaptive
      sync interval with fuzz, WiFi SSID, IP address, MAC address, battery
      level (if present), free memory, and uptime.

  D0  (short press while info screen is showing)
      Dismisses the info screen and returns to the clock.

  D1  (short press)
      Steps forward through all available timezone offsets, from UTC-12
      to UTC+14, including all fractional-hour zones in current use
      (e.g. UTC+5:30, UTC+5:45, UTC+9:30).

  D2  (short press)
      Toggles the status bar on and off.  When off, the timezone label
      enlarges.  If a battery is present, the battery percentage is
      shown alongside the timezone in the freed space.

  D2  (hold 0.5s)
      Enters brightness adjustment mode.  All digit segments light up as
      a reference.  Short presses of D2 cycle through five brightness
      levels: 10% / 25% / 50% / 75% / 100%.

  D2  (hold 0.5s while in brightness adjust)
      Exits brightness adjustment and returns to the previous display state.


--------------------------------------------------------------------------------
 ACCURACY NOTES
--------------------------------------------------------------------------------

  This clock uses a software clock driven by time.monotonic_ns() between
  NTP syncs.  The nanosecond integer counter is used deliberately: the
  32-bit float returned by time.monotonic() loses sub-millisecond resolution
  after about one week of uptime, which would silently introduce up to
  ±500ms of display error.  See the technical note at the top of code.py
  for a full explanation.

  The NTP sync applies a half-RTT correction so the displayed time reflects
  true UTC at the moment of the sync, not the moment the server sent its
  response.

  The hardware crystal oscillator will drift between syncs — typically a
  few hundred milliseconds per hour.  With the default one-hour sync
  interval, worst-case drift before correction is on the order of seconds.
  Reducing NTP_SYNC_INTERVAL to 900 seconds (15 minutes) keeps the
  displayed time within roughly 150ms of true UTC at all times.

  Adaptive sync interval:
  After each successful sync the clock measures the correction — the
  difference between what the software clock believed and what NTP
  reported.  The correction is compared against a dead band centred on
  NTP_ADAPT_THRESHOLD with a width of NTP_ADAPT_BAND percent:

    Below dead band  (correction small)  → extend interval by 20%
    Inside dead band (correction typical) → leave interval unchanged
    Above dead band  (correction large)  → shorten interval by 20%

  The dead band prevents the system from hunting — without it, a
  correction consistently near the threshold would cause the interval
  to oscillate up and down indefinitely.  With default settings
  (threshold=100ms, band=20%) the dead band spans 80ms to 120ms.
  The interval is bounded between 5 minutes and 2 hours.
  NTP_SYNC_INTERVAL in settings.toml is the starting point; the live
  (adapted) interval is shown on the System Info screen alongside it.
  The fuzz (NTP_SYNC_FUZZ_PCT) is applied as a percentage of the
  current adaptive interval so it remains proportionate at all times.


--------------------------------------------------------------------------------
 USEFUL LINKS
--------------------------------------------------------------------------------

  NTP pool project    : https://www.ntppool.org
  Timezone map        : https://www.timeanddate.com/time/map/
  Adafruit ESP32-S3   : https://www.adafruit.com/product/5691
  CircuitPython       : https://circuitpython.org


--------------------------------------------------------------------------------
 LICENSE
--------------------------------------------------------------------------------

  This software is provided under the MIT License.
  See the license header at the top of code.py and webb_ntp.py for the
  full license text.


================================================================================
