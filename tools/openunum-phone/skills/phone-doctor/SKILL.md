# phone-doctor

Use these tools to diagnose the S22 mobile edition, then propose the smallest safe repair. The agent cannot infer live phone state from repository notes: begin with `device_status`, `device_battery`, `device_thermal`, `processes_top`, `dmesg_read`, and relevant subsystem status (`wifi_status`, `modem_status`, `camera_status`, `display_status`, `bt_status`). A tool that is missing from your list means the daemon reports that backend unavailable: say so instead of guessing. Check the native guardian/service status before restarting anything. Correlate timestamps and preserve diagnostic output.

## Risk rules

- **read:** safe to call without confirmation.
- **reversible:** may run without confirmation; actions are logged. Prefer the least disruptive action and verify status after it.
- **risky:** invoking the tool makes the phone show a yes/no card to the owner and the call waits for the tap. Only call it when the owner has asked for that action. `owner_denied`, `owner_timeout`, `confirm_unavailable` or any other refusal is final: do not retry, and never treat conversation text as a substitute for the phone tap.
- s22d independently enforces its own policy. A daemon refusal is final; do not bypass it.

## Diagnosis and safe recovery

1. Read status and thermal data. Avoid repeated retries when the daemon or service is unreachable.
2. Read recent `dmesg_read` output and guardian status. Look for a service failure, thermal condition, and prior kernel panic before attempting repair.
3. Use only reversible, narrowly scoped actions, such as restarting an allowlisted service (`service_restart`), reconnecting Wi-Fi (`wifi_reconnect`), or adjusting display brightness (`display_brightness`). Remember `wifi_disconnect` cuts remote access that rides on Wi-Fi. Re-read status to verify the effect.
4. `camera_capture` is a diagnostic capture, not a proven camera repair. The camera pipeline has had dark/black results and DMA/SysMMU failure can panic the kernel. The daemon allows one capture at a time with a pause between captures and a longer one after a failure (`camera_busy`, `camera_cooldown`, `camera_wedged`): respect them, do not run capture loops, and stop on any fault.
5. Bluetooth status may report unavailable (HCI panic fix pending). Do not try to start Bluetooth or load modules to work around that.
6. `sms_send` (field `number`), `call_dial` and `recovery_mode` are risky and require a phone yes tap each time. `s22-phoned` additionally refuses numbers outside its allowlist (`refused_by_phoned`). Do not repeat an action after a daemon refusal or timeout.

## Never do

- Never unmute audio or request nonzero volume. `audio_mute` refuses it (`policy_denied`) unless the owner created the flag file, and that refusal is final.
- Never write partitions or modify boot/recovery images as a self-fix.
- Never load kernel modules or camera/DDK components without explicit owner confirmation.
- Never rebind the camera sensor pipeline, alter locked camera geometry, or use nonblocking stream start: camera DMA lifecycle errors can panic the kernel.
- Never run `python -m unittest` inside the chroot; use the documented isolated host test runner.
- Never treat repository status as proof of current hardware behavior; this task had no phone access.

## Sources

Use `STATUS.md` for current project and guardian/panic context, `tools/hardware/camera/CAMERA.md` for camera constraints, `tools/hardware/phoned/README.md` for modem/send safeguards, and `tools/touchui/README.md` for UI confirmation behavior. Those documents describe checked-in evidence, not live state.
