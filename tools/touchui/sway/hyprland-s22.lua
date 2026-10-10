-- S22 Hyprland FALLBACK desktop (used only when the sway-pixman start fails,
-- or with /etc/s22-desktop = hyprland). Plain Hyprland, no Omarchy layer:
-- no Omarchy bar, menus, autostart or bindings. The UI is the touch shell,
-- which s22-touchd starts; squeekboard is the keyboard.
hl.monitor({ output = "DSI-1", mode = "preferred", position = "0x0", scale = 2 })
hl.config({
  debug = { disable_logs = true, enable_stdout_logs = false },
  animations = { enabled = false },
  decoration = { blur = { enabled = false }, shadow = { enabled = false } },
  xwayland = { enabled = false },
})
hl.on("hyprland.start", function()
  hl.exec_cmd("squeekboard >/tmp/s22-squeekboard.log 2>&1")
end)
