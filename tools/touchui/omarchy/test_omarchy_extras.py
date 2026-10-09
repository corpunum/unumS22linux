#!/usr/bin/env python3
"""Hardware-free tests for the S22 Omarchy extras (agent skill, keyboard bindings, installer).

Run: python3 -I -B tools/touchui/omarchy/test_omarchy_extras.py
No bare asserts (safe under python -O). If a Lua interpreter is present, the
keyboard file is also executed against stubbed hl.* (on/off/idempotence).
"""
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
LUA = HERE / 'omarchy-keyboard.lua'
SKILL = HERE / 'SKILL.md'
HELPER = HERE / 's22-kbd-bindings.sh'
INSTALL = HERE / 'install-omarchy-extras.sh'

# Combos bound by the pinned Omarchy bindings (default/hypr/bindings/*.lua) that the
# curated file may reuse, plus the phone-only equivalents. Anything else is a mistake.
OMARCHY_COMBOS = {
    'SUPER + RETURN', 'SUPER + W', 'SUPER + F', 'SUPER + ALT + F', 'SUPER + T', 'SUPER + J',
    'SUPER + LEFT', 'SUPER + RIGHT', 'SUPER + UP', 'SUPER + DOWN',
    'SUPER + SHIFT + LEFT', 'SUPER + SHIFT + RIGHT', 'SUPER + SHIFT + UP', 'SUPER + SHIFT + DOWN',
    'SUPER + TAB', 'SUPER + SHIFT + TAB', 'SUPER + CTRL + TAB', 'ALT + TAB', 'ALT + SHIFT + TAB',
    'SUPER + S', 'SUPER + ALT + S', 'SUPER + SPACE', 'SUPER + ALT + SPACE', 'SUPER + CTRL + E',
    'SUPER + CTRL + V', 'SUPER + comma', 'SUPER + SHIFT + comma', 'SUPER + CTRL + L', 'SUPER + ESCAPE',
}
LUA_STUB = r'''
local binds, unbound = {}, 0
local function call(name) return function(...) return { dsp = name, args = {...} } end end
hl = { dsp = setmetatable({}, { __index = function(_, k)
  return setmetatable({}, { __index = function(_, k2) return call(k .. "." .. k2) end,
                            __call = function(_, ...) return { dsp = k, args = {...} } end })
end }) }
hl.bind = function(keys, disp, opts)
  assert(type(keys) == "string" and type(disp) == "table" and opts.description:find("^s22: "))
  binds[#binds + 1] = keys
  return { unbind = function() unbound = unbound + 1 end }
end
'''
LUA_CHECK = r'''
assert(#binds == 0, "bound without the flag file")
local a = s22_kbd_on()
local b = s22_kbd_on()
assert(a == b and #s22_kbd.handles == tonumber(a:match("%d+")), "on is not idempotent")
local seen = {}
for i = #binds - #s22_kbd.handles + 1, #binds do
  assert(not seen[binds[i]], "duplicate combo " .. binds[i]); seen[binds[i]] = true
end
s22_kbd_off()
assert(#s22_kbd.handles == 0, "off left handles")
print("LUA-OK " .. a)
'''


class KeyboardLuaTest(unittest.TestCase):
    src = LUA.read_text()

    def combos(self):
        return re.findall(r'\{\s*"([^"]+)",\s*"[^"]+",', self.src)

    def test_off_by_default_behind_flag(self):
        self.assertIn('local FLAG = "/root/.config/s22/omarchy-keyboard.enabled"', self.src)
        tail = self.src[self.src.rindex('local f = io.open(FLAG'):]
        self.assertIn('s22_kbd_on()', tail)
        self.assertIn('if f then', tail)

    def test_only_curated_combos(self):
        found = self.combos()
        self.assertGreaterEqual(len(found), 25)
        self.assertEqual(sorted(set(found) - OMARCHY_COMBOS), [])
        self.assertEqual(len(found), len(set(found)), 'duplicate combo')

    def test_no_broken_or_unsafe_actions(self):
        # omarchy-* scripts need jq/uwsm/systemd (missing on the phone); audio is pinned muted
        code = re.sub(r'^\s*--.*$', '', self.src, flags=re.M)
        cmds = re.findall(r'exec_cmd\("([^"]*)"\)', code) + re.findall(r'ipc\("((?:[^"\\]|\\.)*)"\)', code)
        self.assertGreaterEqual(len(cmds), 8)
        for cmd in cmds:
            self.assertFalse(cmd.startswith('omarchy-'), cmd)
        for bad in ('XF86Audio', 'volume', 'uwsm', 'monitor', 'wtype', 'systemctl'):
            self.assertNotIn(bad, code, bad)

    def test_executes_against_stubbed_hyprland(self):
        lua = shutil.which('lua5.4') or shutil.which('lua')
        if not lua:
            self.skipTest('no lua interpreter')
        r = subprocess.run([lua, '-'], input=LUA_STUB + self.src + LUA_CHECK, capture_output=True,
                           text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('LUA-OK on ', r.stdout)


class SkillTest(unittest.TestCase):
    src = SKILL.read_text()

    def test_frontmatter(self):
        m = re.match(r'^---\nname: (\S+)\ndescription: (.+?)\n---\n', self.src, re.S)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), 's22-omarchy-actions')

    def test_dispatchers_match_keyboard_file(self):
        lua = LUA.read_text()
        skill_dsp = set(re.findall(r'hl\.dsp\.[a-z_.]+', self.src))
        lua_dsp = set(re.findall(r'hl\.dsp\.[a-z_.]+', lua))
        # every dispatcher the keyboard file uses is documented for the agent
        self.assertEqual(sorted(lua_dsp - skill_dsp - {'hl.dsp.exec_cmd'}), [])

    def test_safety_rules_present(self):
        for needle in ('ui_confirm', 'muted', 'omarchy-shell is not running', '/opt/s22-ui/shell',
                       'HYPRLAND_INSTANCE_SIGNATURE'):
            self.assertIn(needle, self.src)


class ScriptsTest(unittest.TestCase):
    def test_shell_syntax(self):
        for f in (HELPER, INSTALL):
            r = subprocess.run(['sh', '-n', str(f)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f'{f.name}: {r.stderr}')

    def test_helper_usage_and_status(self):
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / 'hyprctl'
            fake.write_text('#!/bin/sh\necho \'[{"description": "s22: Close window"}]\'\n')
            fake.chmod(0o755)
            env = {'PATH': f'{d}:/usr/bin:/bin', 'XDG_RUNTIME_DIR': d, 'HYPRLAND_INSTANCE_SIGNATURE': 'x'}
            r = subprocess.run(['sh', str(HELPER), 'bogus'], env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)
            r = subprocess.run(['sh', str(HELPER), 'status'], env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('active s22 keyboard bindings: 1', r.stdout)

    def test_install_and_rollback_in_fake_chroot(self):
        with tempfile.TemporaryDirectory() as d:
            c, state = Path(d) / 'chroot', Path(d) / 'state'
            (c / 'root').mkdir(parents=True)
            (c / 'opt/s22-touch').mkdir(parents=True)
            (c / 'usr/local/bin').mkdir(parents=True)
            env = {'PATH': '/usr/bin:/bin', 'C': str(c), 'STATE': str(state)}
            r = subprocess.run(['sh', str(INSTALL)], env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            skill = c / 'root/.openunum/skills/custom/s22-omarchy-actions/SKILL.md'
            lua = c / 'opt/s22-touch/omarchy-keyboard.lua'
            helper = c / 'usr/local/bin/s22-kbd-bindings'
            for p in (skill, lua, helper):
                self.assertTrue(p.is_file(), p)
            self.assertTrue(helper.stat().st_mode & stat.S_IXUSR)
            self.assertFalse((c / 'root/.config/s22/omarchy-keyboard.enabled').exists(), 'must stay off')
            # second install backs up the first; rollback then restores that version
            skill.write_text('previous\n')
            r = subprocess.run(['sh', str(INSTALL)], env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = subprocess.run(['sh', str(INSTALL), '--rollback'], env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(skill.read_text(), 'previous\n')
            # rolling back past the first install removes everything
            for b in sorted(state.glob('backup-*'))[1:]:
                shutil.rmtree(b)
            for b in state.glob('backup-*'):
                for f in b.iterdir():
                    f.unlink()
            r = subprocess.run(['sh', str(INSTALL), '--rollback'], env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            for p in (skill, lua, helper):
                self.assertFalse(p.exists(), p)

    def test_missing_touchui_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            c = Path(d) / 'chroot'
            (c / 'root').mkdir(parents=True)
            env = {'PATH': '/usr/bin:/bin', 'C': str(c), 'STATE': str(Path(d) / 'state')}
            r = subprocess.run(['sh', str(INSTALL)], env=env, capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('touch UI', r.stderr)


if __name__ == '__main__':
    unittest.main()
