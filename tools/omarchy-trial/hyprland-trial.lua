-- Load the real pinned Omarchy configuration without its provisioning/services.
-- No autostart or user keybindings may execute during this diagnostic launch.
omarchy_default_bindings = false
omarchy_preinstalled_bindings = false
package.loaded["default.hypr.autostart"] = true
package.loaded["hypr.autostart"] = true
package.loaded["hypr.bindings"] = true
dofile("/opt/omarchy-source/config/hypr/hyprland.lua")
hl.config({ debug = { disable_logs = false, enable_stdout_logs = true } })
