#!/system/bin/sh
# Runs from recovery RAM. Add Ethernet alongside ADB, then restore automatically.
set -eux
duration=${1:-90}
case "$duration" in ''|*[!0-9]*) exit 2 ;; esac
[ "$duration" -ge 1 ] && [ "$duration" -le 900 ]
g=/config/usb_gadget/g1
test "$(cat "$g/UDC")" = 10b00000.dwc3
test -L "$g/configs/b.1/f1"
test ! -e "$g/configs/b.1/f2"
test ! -e "$g/functions/ecm.usb0"

restore() {
    trap - EXIT HUP INT TERM
    printf '\n' > "$g/UDC"
    if [ -L "$g/configs/b.1/f2" ]; then rm "$g/configs/b.1/f2"; fi
    if [ -d "$g/functions/ecm.usb0" ]; then rmdir "$g/functions/ecm.usb0"; fi
    printf '%s\n' 10b00000.dwc3 > "$g/UDC"
    setprop sys.usb.config adb
    echo 'Restored original ADB-only gadget.'
}
trap restore EXIT HUP INT TERM
# Keep Android init's sys.usb.ffs.ready action from racing our UDC rebind.
# This value has no matching init action and does not stop adbd or its cgroup.
setprop sys.usb.config s22-ecm-test
test "$(getprop sys.usb.config)" = s22-ecm-test
mkdir "$g/functions/ecm.usb0"
printf '%s\n' 02:73:22:00:00:02 > "$g/functions/ecm.usb0/dev_addr"
printf '%s\n' 02:73:22:00:00:01 > "$g/functions/ecm.usb0/host_addr"
printf '\n' > "$g/UDC"
ln -s "$g/functions/ecm.usb0" "$g/configs/b.1/f2"
printf '%s\n' 10b00000.dwc3 > "$g/UDC"
iface=$(cat "$g/functions/ecm.usb0/ifname")
/system/bin/toybox ifconfig "$iface" 10.55.0.2 netmask 255.255.255.0 up
echo "ECM active on $iface, 10.55.0.2/24; ADB retained."
/system/bin/toybox ifconfig "$iface"
sleep "$duration"
