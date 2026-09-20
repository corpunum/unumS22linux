-- Real Omarchy defaults, with S22 software-rendering and phone overrides.
-- The imported trial disables provisioning, automount and system services.
dofile("/root/hyprland-trial.lua")
hl.monitor({ output = "DSI-1", mode = "preferred", position = "0x0", scale = 2 })
hl.config({
  animations = { enabled = false },
  decoration = { blur = { enabled = false }, shadow = { enabled = false } },
  xwayland = { enabled = false },
})
hl.on("hyprland.start", function()
  hl.exec_cmd("foot --font='DejaVu Sans Mono:size=12' --title=S22-Omarchy sh -c 'printf \"Native S22: Arch + Omarchy defaults\\nDisplay: software rendering\\nGPU compute not ready; NPU unproven\\n\"; exec sh' >/tmp/s22-foot.log 2>&1")
end)
