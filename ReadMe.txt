webb-clock v1.10


Hardware requirements:  Adafruit ESP32-S3 REV TFT


There are three components to this package:

	code.py - contains the Circuit Python code to run on an Adafruit ESP32-s3 REV TFT.  It has been tested on CircuitPython v8 and v10.

	lib - contains the necessary library modules for the code to run.  Within it is webb-ntp, an NTP client improved for sub-second precision over that provided by Adafruit.

	settings.toml - This file needs to be edited to include your WiFi credentials so the clock can access an NTP server.  Also, it has the default NTP server of pool.ntp.org, which should work fine, but you can change it to suit your needs.  You can also alter how frequently the clock will sync with the NTP server, and a "fuzz" time, offering some randomness to when it will do so.  The comments in the file should make things clear.


Once the settings.toml has at least been edited for the WiFi information, it should be operable.

There are three buttons on the left of the display:  D0, D1, and D2.

A short press on D0 will change the color of the clock display.  It will cycle from green to red to blue and back.  A long press on D0 brings up nerd information which is well-labelled.  A short press bring it back to the normal display.

D1 will cycle through the available time zones.  All are simply labelled with the difference from Coordinated Universal Time (UTC).

D2 will cycle the display between having some status information or just having the time zone below the clock.  It will show the status of the NTP sync, and the time it was accomplished.  It also shows the time to next sync, the drift of the ESP32 oscillator, and the ping time to the NTP Server.  Choosing to have just the time zone results in a nice, clean display.

I hope you enjoy this simple, yet strangely accurate clock.

Learn more about the publicly available NTP pool at www.ntp.org .

There is a nice time zone map at https://www.timeanddate.com/time/map/ .

There are ample comments in the code.py file to satisfy any software nerd.

This software is provided under the MIT License.  


-Spencer Webb webb@antennasys.com

