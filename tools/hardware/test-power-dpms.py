#!/usr/bin/env python3
"""Bounded S22 display-only test; never requests Linux suspend or reboot.

Run inside native Alpine, for example through tools/s22-ssh with stdin.
Only --exercise changes state. It requires an initially enabled display and
always requests enable in finally, leaving the rescue shell untouched.
"""
import argparse
import json
import subprocess
import time
import urllib.request

CTL = ["chroot", "/mnt/omarchy-trial", "/usr/bin/env",
       "XDG_RUNTIME_DIR=/run/user/0", "/usr/bin/hyprctl", "-i", "0"]


def ctl(*args):
    return subprocess.run(CTL + list(args), check=True, text=True,
                          capture_output=True, timeout=10).stdout


def state():
    monitors = json.loads(ctl("-j", "monitors"))
    if len(monitors) != 1:
        raise RuntimeError("expected exactly one phone display")
    return {key: monitors[0][key] for key in ("name", "dpmsStatus")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exercise", action="store_true")
    args = parser.parse_args()
    before = state()
    result = {"before": before}
    if args.exercise:
        if before["dpmsStatus"] is not True:
            raise RuntimeError("display is not initially enabled")
        try:
            ctl("dispatch", 'hl.dsp.dpms({ action = "disable" })')
            time.sleep(0.5)
            result["disabled"] = state()
        finally:
            ctl("dispatch", 'hl.dsp.dpms({ action = "enable" })')
        time.sleep(0.5)
        result["restored"] = state()
        with urllib.request.urlopen("http://127.0.0.1:8089/health", timeout=5) as response:
            result["model"] = json.load(response)
        print(json.dumps(result, sort_keys=True))
        assert result["disabled"]["dpmsStatus"] is False, "disable not observed"
        assert result["restored"]["dpmsStatus"] is True, "display not restored"
        assert result["model"]["status"] == "ok", "model health not ok"
    else:
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
