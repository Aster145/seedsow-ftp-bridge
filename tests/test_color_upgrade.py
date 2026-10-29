"""Execute the real generated upgrade in Lua 5.1; never opens hardware.

Set LUA51 to a Lua 5.1 executable if it is not discoverable on PATH. Only the
runtime tests are skipped when Lua 5.1 is unavailable; Python needs no packages.
"""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from make_color_upgrade import make_upgrade
from pack_bootstrap import minify_lua


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "ftp_console.lua"
COLOR = ROOT / "ftp_console_color.lua"


def find_lua51():
    candidates = [os.environ.get("LUA51")]
    candidates.extend(shutil.which(name) for name in ("lua5.1", "lua51", "lua"))
    candidates.append(r"C:\Program Files (x86)\Lua\5.1\lua.exe")
    for candidate in filter(None, candidates):
        try:
            result = subprocess.run(
                [candidate, "-e", "io.write(_VERSION)"],
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0 and result.stdout == "Lua 5.1":
            return candidate
    return None


LUA_HARNESS = r'''
local function read(name)
    local file=assert(io.open(name,'rb'))
    local value=file:read('*a');file:close();return value
end
local original,expected,case=read(arg[1]),read(arg[2]),arg[4]
local input=original
if case=='checksum' then
    input=string.char((input:byte(1)+1)%256)..input:sub(2)
elseif case=='length' then input=input..'x'
elseif case=='missing' then input=nil end
local files,writes={['ftp_console.lua']=input},{}
local state={input={valid=true,held=function()return false end},
    filename='before.lua',cursor=99,buffer='before',saved='before'}
local handler,rectangles,calibration=nil,0,0
local function adler(value)
    local a,b=1,0
    for i=1,#value do a=(a+value:byte(i))%65521;b=(b+a)%65521 end
    return b*65536+a
end
-- Put the game API in a separate environment so execution also exercises
-- the upgrade's setfenv propagation rather than relying on global fixtures.
local context=setmetatable({},{__index=_G});context._G=context
context.editor={
    write=function(name,value) writes[name]=true;files[name]=value;return true end,
    read=function(name)return files[name]end,
    screen=function()end,label=function()end,
    rect=function(id)
        rectangles=rectangles+1
        if type(id)=='string' and id:match('^cal%d$') then calibration=calibration+1 end
    end,
    receive=function()state.receive={};return true end,
}
context.engine={
    isPush=function()return false end,now=function()return 0 end,tag=function()end,
    setEventHandler=function(_,value)handler=value.onEnterFrame end,
}
context.seedsow_editor={test_state=function()return state end,storage={
    valid=function(name)return type(name)=='string' and #name>0 end,
    u32=function(n)return string.char(n%256,math.floor(n/256)%256,
        math.floor(n/65536)%256,math.floor(n/16777216)%256)end,
    adler=adler,
}}
context.seedsow_editor_frame=function()end
local chunk=assert(loadfile(arg[3]));setfenv(chunk,context)
local ok,reason=pcall(chunk)
assert(not writes['ftp_console.lua'],'old source was overwritten')
assert(files['ftp_console.lua']==input,'old source changed')
if case=='valid' then
    assert(ok,reason)
    assert(files['ftp_console_color.lua']==expected,'reconstructed source differs')
    assert(state.filename=='ftp_console_color.lua' and state.cursor==0,
        'editor filename or cursor differs')
    assert(state.buffer==expected and state.saved==expected,'editor source differs')
    assert(handler=='ss_ftp_frame' and type(context.ss_ftp_frame)=='function',
        'actual color console did not register its frame handler')
    assert(rectangles>8 and calibration==8,'actual color console did not draw')
else
    assert(not ok,'invalid baseline accepted')
    assert(next(writes)==nil,'write happened before baseline validation')
    assert(state.filename=='before.lua' and state.cursor==99 and
        state.buffer=='before' and state.saved=='before','failed upgrade changed state')
end
io.write(case..': passed\n')
'''


class ColorUpgradeGenerationTests(unittest.TestCase):
    def test_deterministic_upgrade_matches_published_current_sources(self):
        baseline, color = BASELINE.read_bytes(), COLOR.read_bytes()
        generated = make_upgrade(baseline, color)
        self.assertEqual(generated, make_upgrade(baseline, color))
        self.assertEqual(generated, (ROOT / "ftp_color_upgrade.lua").read_bytes())
        self.assertTrue(generated.isascii())

    def test_cli_refuses_to_overwrite_either_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            baseline, color = folder / "baseline.lua", folder / "color.lua"
            baseline.write_bytes(BASELINE.read_bytes())
            color.write_bytes(COLOR.read_bytes())
            originals = {path: path.read_bytes() for path in (baseline, color)}
            for output in (baseline, color):
                with self.subTest(output=output.name):
                    result = subprocess.run(
                        [sys.executable, str(ROOT / "make_color_upgrade.py"),
                         str(baseline), str(color), str(output)],
                        capture_output=True, text=True, timeout=10,
                    )
                    self.assertEqual(result.returncode, 2, result.stderr)
                    for path, expected in originals.items():
                        self.assertEqual(path.read_bytes(), expected)


class ColorUpgradeLuaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lua = find_lua51()
        if cls.lua is None:
            raise unittest.SkipTest("Lua 5.1 unavailable; set LUA51 to enable runtime checks")

    def run_upgrade(self, case):
        baseline, color = BASELINE.read_bytes(), COLOR.read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "baseline.lua").write_bytes(minify_lua(baseline))
            (folder / "color.lua").write_bytes(minify_lua(color))
            (folder / "upgrade.lua").write_bytes(make_upgrade(baseline, color))
            (folder / "harness.lua").write_text(LUA_HARNESS, encoding="ascii")
            result = subprocess.run(
                [self.lua, str(folder / "harness.lua"), str(folder / "baseline.lua"),
                 str(folder / "color.lua"), str(folder / "upgrade.lua"), case],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            self.assertIn(case + ": passed", result.stdout)

    def test_actual_color_source_is_saved_and_executed_with_old_source_preserved(self):
        self.run_upgrade("valid")

    def test_same_length_but_wrong_content_is_rejected_before_writing(self):
        self.run_upgrade("checksum")

    def test_wrong_length_is_rejected_before_writing(self):
        self.run_upgrade("length")

    def test_missing_baseline_is_rejected_before_writing(self):
        self.run_upgrade("missing")


if __name__ == "__main__":
    unittest.main()
