#!/bin/sh
# s22-buttons hook "power" (short Power press), installed as /srv/s22/buttons/hooks/power.
# Screen on  -> s22-touchd shows the lock cover, then DPMS off.
# Screen off -> DPMS on (the lock cover is already up; swipe up to unlock).
# If s22-touchd is not running, fall back to the old default (plain toggle).
/usr/bin/python3 /srv/s22/hardware/bin/s22-touchd power >/dev/null 2>&1 && exit 0
exec /srv/s22/hardware/bin/s22-display toggle
