# ============================================================================
# webb_ntp.py
# Custom NTP client for CircuitPython with sub-second timestamp precision.
#
# Version : 1.1  (2026-03-25)
# Author  : Spencer Webb
# Developed with : Claude Sonnet 4.6 (Anthropic)
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
# The standard adafruit_ntp library now also parses fractional seconds and
# returns UTC in nanoseconds (utc_ns).  This module offers a simpler interface
# that returns (unix_seconds, frac_ms, rtt_ms) directly, which is convenient
# for the software-clock pattern used in code.py.
#
# Usage:
#   import webb_ntp
#   unix_secs, frac_ms, rtt_ms = webb_ntp.get_time(pool, "pool.ntp.org")
#   h, m, s = webb_ntp.unix_to_hms(unix_secs)
#   year, month, day, weekday = webb_ntp.unix_to_date(unix_secs)
#
# The caller is responsible for applying the half-RTT correction to get the
# true time at the moment of the call:
#   true_time_ms = unix_secs * 1000 + frac_ms + rtt_ms / 2
#
# NTP packet format (48 bytes, all fields big-endian):
#   Byte  0      : LI (2 bits), Version (3 bits), Mode (3 bits)
#   Byte  1      : Stratum  (0 = kiss-o'-death / unspecified)
#   Byte  2      : Poll interval (log2 seconds)
#   Byte  3      : Precision (log2 seconds)
#   Bytes  4-7   : Root Delay (fixed-point)
#   Bytes  8-11  : Root Dispersion (fixed-point)
#   Bytes 12-15  : Reference Identifier
#   Bytes 16-23  : Reference Timestamp (NTP 64-bit)
#   Bytes 24-31  : Originate Timestamp (NTP 64-bit)
#   Bytes 32-39  : Receive Timestamp (NTP 64-bit)
#   Bytes 40-47  : Transmit Timestamp (NTP 64-bit)  <-- we use this one
#
# Each NTP 64-bit timestamp = 32-bit seconds | 32-bit fraction.
# Fraction field value / 2^32 = sub-second part in seconds.
#
# NTP epoch : January 1, 1900
# Unix epoch : January 1, 1970
# Offset     : 70 years = 2,208,988,800 seconds
# ============================================================================

import struct
import time

_NTP_EPOCH_DELTA = 2208988800   # seconds between NTP epoch (1900) and Unix epoch (1970)
_NTP_PORT        = 123
_NTP_TIMEOUT     = 5            # socket timeout in seconds (applies to all blocking operations)
_FRAC_TO_MS      = 1000.0 / 4294967296.0  # converts 32-bit NTP fraction field to milliseconds

# True if this CircuitPython build supports time.monotonic_ns().
# Evaluated once at import time — the result never changes at runtime.
# monotonic_ns() returns a Python integer so RTT arithmetic is exact;
# monotonic() returns a 32-bit float whose resolution degrades at high uptime.
_USE_NS = hasattr(time, "monotonic_ns")

# Cached DNS result: (server_str, sockaddr) from the last successful lookup.
# Avoids a DNS round-trip on every sync call.  Invalidated automatically when
# the server argument changes, so a different hostname always triggers a fresh
# lookup.
_dns_cache = None   # None, or (server_str, sockaddr)


def get_time(pool, server):
    """Query an NTP server and return the current UTC time with sub-second precision.

    Sends a single NTPv4 client request, validates the response, and returns
    the server's Transmit Timestamp (T3) together with the measured round-trip
    time.  The caller is expected to apply the half-RTT correction:
        true_time_ms = unix_seconds * 1000 + frac_ms + rtt_ms / 2

    DNS resolution is cached after the first successful call and reused until
    the server argument changes.

    RTT is measured with time.monotonic_ns() when available (integer nanoseconds,
    no float rounding) and falls back to time.monotonic() on older builds.

    The socket is always created as AF_INET/SOCK_DGRAM regardless of what
    getaddrinfo returns — passing the raw family/socktype integers back into
    pool.socket() is unreliable on CircuitPython's socketpool implementation.

    Note: recvfrom_into() is not used for sender verification because it is not
    reliably supported across all CircuitPython socketpool implementations.
    recv_into() is used instead; stray UDP packets are therefore not filtered,
    but in practice this is not an issue on a typical home network.

    Args:
        pool:   A CircuitPython socketpool.SocketPool instance.
        server: Hostname or IP address string of the NTP server.

    Returns:
        A tuple of (unix_seconds, frac_ms, rtt_ms) where:
          unix_seconds (int)   : Whole seconds since Unix epoch (Jan 1 1970 UTC)
          frac_ms      (float) : Sub-second fraction in milliseconds (0.0 – 999.999)
          rtt_ms       (float) : Round-trip time to the server in milliseconds

    Raises:
        OSError: On network failure, timeout, short response, or invalid packet.
    """
    global _dns_cache

    # --- DNS lookup (cached) ------------------------------------------------
    if _dns_cache is None or _dns_cache[0] != server:
        addr       = pool.getaddrinfo(server, _NTP_PORT)[0][4]
        _dns_cache = (server, addr)
    else:
        _, addr = _dns_cache

    # --- Build NTP client request packet ------------------------------------
    # All 48 bytes are zero-initialised by bytearray().
    # Byte 0: LI=0 (no leap warning), VN=4 (NTPv4), Mode=3 (client) = 0b00100011
    packet    = bytearray(48)
    packet[0] = 0b00100011

    # --- Send, receive, and measure RTT -------------------------------------
    # sock.settimeout() is inside the try block so the socket is guaranteed to
    # be closed by the finally clause even if settimeout() raises.
    # The timing brackets wrap only the network calls so RTT reflects actual
    # wire time as closely as possible.
    sock = pool.socket(pool.AF_INET, pool.SOCK_DGRAM)
    try:
        sock.settimeout(_NTP_TIMEOUT)
        if _USE_NS:
            t0 = time.monotonic_ns()
            sock.sendto(packet, addr)
            n      = sock.recv_into(packet)
            rtt_ms = (time.monotonic_ns() - t0) / 1_000_000
        else:
            t0 = time.monotonic()
            sock.sendto(packet, addr)
            n      = sock.recv_into(packet)
            rtt_ms = (time.monotonic() - t0) * 1000.0
    finally:
        sock.close()

    # --- Validate response --------------------------------------------------
    if n < 48:
        raise OSError("Short NTP response: {} bytes".format(n))

    # Low 3 bits of byte 0 are the Mode field; 4 = server reply
    if (packet[0] & 0x07) != 4:
        raise OSError("Unexpected NTP mode: {}".format(packet[0] & 0x07))

    # Stratum 0 means kiss-o'-death or unspecified — not a usable time source
    if packet[1] == 0:
        raise OSError("NTP stratum 0 (kiss-o'-death) received")

    # --- Parse transmit timestamp (bytes 40-47) -----------------------------
    # Both 32-bit words are unpacked in a single call for efficiency.
    # The transmit timestamp is the best available estimate of "what time is
    # it now" — it is the moment the server finished composing its reply.
    ntp_seconds, ntp_fraction = struct.unpack_from("!II", packet, 40)

    unix_seconds = ntp_seconds - _NTP_EPOCH_DELTA
    frac_ms      = ntp_fraction * _FRAC_TO_MS

    return unix_seconds, frac_ms, rtt_ms


def unix_to_hms(unix_seconds):
    """Extract UTC hour, minute, and second from a Unix epoch timestamp.

    Uses modulo 86400 (seconds per day) to isolate the time-of-day component.
    This works correctly for any integer value of unix_seconds: positive values
    give the correct UTC time, and Python's always-positive modulo means
    negative values (timestamps before 1970) also produce a valid result rather
    than wrapping unexpectedly.

    Args:
        unix_seconds (int): Seconds since January 1, 1970 UTC.

    Returns:
        Tuple of (hour, minute, second) as integers.
    """
    total  = unix_seconds % 86400   # seconds elapsed so far today (0 – 86399)
    hour   = total // 3600
    minute = (total % 3600) // 60
    second = total % 60
    return hour, minute, second


# Short names for months and weekdays — used by callers to format the date.
MONTHS_SHORT = ("JAN","FEB","MAR","APR","MAY","JUN",
                "JUL","AUG","SEP","OCT","NOV","DEC")
DAYS_SHORT   = ("MON","TUE","WED","THU","FRI","SAT","SUN")


def unix_to_date(unix_seconds):
    """Extract UTC year, month, day, and weekday from a Unix epoch timestamp.

    Uses the Gregorian calendar algorithm (Euclidean affine functions method)
    which is correct for all dates from the Unix epoch onward.

    Args:
        unix_seconds (int): Seconds since January 1, 1970 UTC.

    Returns:
        Tuple of (year, month, day, weekday) where:
          year     (int) : Four-digit year e.g. 2026
          month    (int) : Month 1-12
          day      (int) : Day of month 1-31
          weekday  (int) : 0=Monday ... 6=Sunday
                           (compatible with DAYS_SHORT index)
    """
    days    = unix_seconds // 86400
    # Weekday: Unix epoch (Jan 1 1970) was a Thursday = index 3 in DAYS_SHORT
    weekday = (days + 3) % 7
    # Gregorian calendar decomposition
    z   = days + 719468
    era = z // 146097
    doe = z - era * 146097                                  # day of era  [0, 146096]
    yoe = (doe - doe//1460 + doe//36524 - doe//146096)//365 # year of era [0, 399]
    y   = yoe + era * 400
    doy = doe - (365*yoe + yoe//4 - yoe//100)              # day of year [0, 365]
    mp  = (5*doy + 2) // 153                               # month prime  [0, 11]
    d   = doy - (153*mp + 2)//5 + 1                        # day          [1, 31]
    m   = mp + (3 if mp < 10 else -9)                      # month        [1, 12]
    y  += (1 if m <= 2 else 0)
    return y, m, d, weekday
