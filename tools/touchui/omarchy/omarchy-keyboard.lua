-- S22 curated hardware-keyboard bindings (Bluetooth/USB keyboard), OFF by default.
--
-- The phone's Hyprland config turns all of Omarchy's ~205 bindings off. This file
-- re-adds only those that work on the phone: hyprctl dispatchers and direct
-- Omarchy-shell IPC. It adds no omarchy-* script, because they need jq, uwsm or
-- systemd, which the phone lacks. Combos match Omarchy's, so muscle memory carries over.
--
-- Loading this file only defines s22_kbd_on() / s22_kbd_off(). It binds keys only
-- when the flag file exists. Use the s22-kbd-bindings helper, which does this:
--   on:  touch FLAG; hyprctl eval 'dofile("/opt/s22-touch/omarchy-keyboard.lua")'
--   off: rm FLAG only; takes effect at the next desktop restart (live unbind crashes Hyprland)
-- Hyprland's config is NOT edited: an edit would trigger a live config autoreload.
-- After a Hyprland restart the flag survives but the binds do not. Run
-- `s22-kbd-bindings on` again (`status` shows "flag: on, active: 0" in that case).

local FLAG = "/root/.config/s22/omarchy-keyboard.enabled"
local SHELL = "quickshell ipc -n -p /opt/s22-ui/shell call -- "

s22_kbd = s22_kbd or { handles = {} }

local function ipc(args)
  return hl.dsp.exec_cmd(SHELL .. args)
end

local function bindings()
  local b = {
    { "SUPER + RETURN", "Terminal (foot)", hl.dsp.exec_cmd("foot") },
    { "SUPER + W", "Close window", hl.dsp.window.close() },
    { "SUPER + F", "Full screen", hl.dsp.window.fullscreen({ mode = "fullscreen" }) },
    { "SUPER + ALT + F", "Full width", hl.dsp.window.fullscreen({ mode = "maximized" }) },
    { "SUPER + T", "Toggle window floating/tiling", hl.dsp.window.float({ action = "toggle" }) },
    { "SUPER + J", "Toggle window split", hl.dsp.layout("togglesplit") },
    { "SUPER + LEFT", "Focus on left window", hl.dsp.focus({ direction = "l" }) },
    { "SUPER + RIGHT", "Focus on right window", hl.dsp.focus({ direction = "r" }) },
    { "SUPER + UP", "Focus on above window", hl.dsp.focus({ direction = "u" }) },
    { "SUPER + DOWN", "Focus on below window", hl.dsp.focus({ direction = "d" }) },
    { "SUPER + SHIFT + LEFT", "Swap window to the left", hl.dsp.window.swap({ direction = "l" }) },
    { "SUPER + SHIFT + RIGHT", "Swap window to the right", hl.dsp.window.swap({ direction = "r" }) },
    { "SUPER + SHIFT + UP", "Swap window up", hl.dsp.window.swap({ direction = "u" }) },
    { "SUPER + SHIFT + DOWN", "Swap window down", hl.dsp.window.swap({ direction = "d" }) },
    { "SUPER + TAB", "Next workspace", hl.dsp.focus({ workspace = "e+1" }) },
    { "SUPER + SHIFT + TAB", "Previous workspace", hl.dsp.focus({ workspace = "e-1" }) },
    { "SUPER + CTRL + TAB", "Former workspace", hl.dsp.focus({ workspace = "previous" }) },
    { "ALT + TAB", "Focus on next window", hl.dsp.window.cycle_next() },
    { "ALT + SHIFT + TAB", "Focus on previous window", hl.dsp.window.cycle_next({ next = false }) },
    { "SUPER + S", "Toggle scratchpad", hl.dsp.workspace.toggle_special("scratchpad") },
    { "SUPER + ALT + S", "Move window to scratchpad", hl.dsp.window.move({ workspace = "special:scratchpad", follow = false }) },
    { "SUPER + SPACE", "Omarchy menu", ipc("shell toggle omarchy.menu '{\"menu\":\"root\"}'") },
    { "SUPER + ALT + SPACE", "Apps menu", ipc("shell toggle omarchy.menu '{\"menu\":\"apps\"}'") },
    { "SUPER + CTRL + E", "Emojis", ipc("shell toggle omarchy.emojis '{}'") },
    { "SUPER + CTRL + V", "Clipboard manager", ipc("shell toggle omarchy.clipboard '{}'") },
    { "SUPER + comma", "Dismiss last notification", ipc("notifications dismissOne") },
    { "SUPER + SHIFT + comma", "Dismiss all notifications", ipc("notifications dismissAll") },
    -- phone equivalents for Omarchy combos whose Omarchy action cannot run here
    { "SUPER + CTRL + L", "Lock (S22 soft lock cover)", hl.dsp.exec_cmd("s22-ui lock") },
    { "SUPER + ESCAPE", "S22 home screen", hl.dsp.exec_cmd("s22-ui home") },
  }
  for ws = 1, 5 do
    local key = "code:" .. tostring(ws + 9)
    table.insert(b, { "SUPER + " .. key, "Switch to workspace " .. ws, hl.dsp.focus({ workspace = tostring(ws) }) })
    table.insert(b, { "SUPER + SHIFT + " .. key, "Move window to workspace " .. ws, hl.dsp.window.move({ workspace = tostring(ws) }) })
  end
  return b
end

-- NEVER unbind live: on Hyprland 0.56 (Lua config) a live handle:unbind() crashes the
-- compositor natively (reproduced on the S22 2026-10-09: Hyprland gone within 5 s of
-- "off"; pcall cannot catch it). "off" therefore only clears the flag file; the
-- bindings disappear at the next desktop restart (they are never loaded without the flag).
function s22_kbd_off()
  return "off after next desktop restart (" .. tostring(#s22_kbd.handles) .. " still bound now)"
end

function s22_kbd_on()
  if #s22_kbd.handles > 0 then  -- idempotent without unbinding: never stack duplicates
    return "on " .. tostring(#s22_kbd.handles) .. " (already)"
  end
  for _, spec in ipairs(bindings()) do
    table.insert(s22_kbd.handles, hl.bind(spec[1], spec[3], { description = "s22: " .. spec[2] }))
  end
  return "on " .. tostring(#s22_kbd.handles)
end

local f = io.open(FLAG, "r")
if f then
  f:close()
  s22_kbd_on()
end
