-- Omarchy defaults with phone-specific software-rendering overrides.
-- No installer, first-run provisioner, automounter or host services.
dofile("/root/hyprland-trial.lua")
hl.monitor({ output = "DSI-1", mode = "preferred", position = "0x0", scale = 2 })
hl.config({
  debug = { disable_logs = true, enable_stdout_logs = false },
  animations = { enabled = false },
  decoration = { blur = { enabled = false }, shadow = { enabled = false } },
  xwayland = { enabled = false },
})
hl.on("hyprland.start", function()
  hl.exec_cmd("env OMARCHY_UI_CANDIDATE=/opt/s22-ui QT_QUICK_BACKEND=software QSG_RHI_BACKEND=software sh /opt/s22-ui/launch-ui.sh >/tmp/s22-omarchy-ui.log 2>&1")
  hl.exec_cmd("foot --font='DejaVu Sans Mono:size=12' --title=S22-local-chat sh -c 'python3 /usr/local/bin/s22-chat; exec bash --noprofile --norc' >/tmp/s22-foot.log 2>&1")
end)
