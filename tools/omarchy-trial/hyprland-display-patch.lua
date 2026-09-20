-- Display-only control: real Hyprland and a Wayland terminal, no provisioner.
dofile("/root/hyprland-minimal.lua")
hl.on("hyprland.start", function()
  hl.exec_cmd("foot --title=S22-Hyprland-test sh -c 'printf \"S22 Hyprland display test\\nPatched Aquamarine; software rendering\\n\"; exec sh'")
end)
