#!/bin/sh
set -eu
for patch in tools/hardware/osk-bar-toggle.patch tools/hardware/osk-bar-toggle-template.patch; do
  grep -q 'sm.puri.OSK0.SetVisible true' "$patch"
  grep -q '"type": "command"' "$patch"
  ! grep -Eq 'EVIOCGRAB|uinput|suspend|reboot|poweroff' "$patch"
done
echo 'OSK bar toggle candidate: static safety check passed'
