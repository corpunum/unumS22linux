-- Standalone Hyprland control test: no Omarchy imports or autostart commands.
hl.monitor({ output = "DSI-1", mode = "preferred", position = "0x0", scale = 2 })
hl.config({
  animations = { enabled = false },
  decoration = { blur = { enabled = false }, shadow = { enabled = false } },
  xwayland = { enabled = false },
  debug = { disable_logs = false, enable_stdout_logs = true },
})
