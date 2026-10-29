"""Exercise the standalone color installer in Lua 5.1 without any saved baseline.

Set LUA51 to a Lua 5.1 executable if it is not discoverable on PATH. Only the
runtime tests are skipped without Lua; Python checks need no extra packages.
"""

import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

from pack_bootstrap import minify_lua, pack_lua


ROOT = Path(__file__).resolve().parents[1]
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
local file=assert(io.open(arg[1],'rb'));local expected=file:read('*a');file:close()
local files={}
local state={input={valid=true,held=function()return false end},
    filename='before.lua',cursor=99,buffer='before',saved='before'}
local handler,rectangles,calibration=nil,0,0
local function adler(value)
    local a,b=1,0
    for i=1,#value do a=(a+value:byte(i))%65521;b=(b+a)%65521 end
    return b*65536+a
end
-- A separate API environment verifies setfenv propagation during execution.
local context=setmetatable({},{__index=_G});context._G=context
context.editor={
    write=function(name,value)files[name]=value;return true end,
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
local chunk=assert(loadfile(arg[2]));setfenv(chunk,context)
local ok,reason=pcall(chunk)
assert(files['ftp_console_color.lua']==expected,'restored source bytes differ')
assert(state.filename=='ftp_console_color.lua' and state.cursor==0,
    'editor filename or cursor differs')
assert(state.buffer==expected and state.saved==expected,'editor source differs')
if arg[3]=='binary' then
    assert(not ok,'arbitrary binary unexpectedly compiled as Lua')
else
    assert(ok,reason)
    if arg[3]=='color' then
        assert(handler=='ss_ftp_frame' and type(context.ss_ftp_frame)=='function',
            'actual color console did not register its frame handler')
        assert(rectangles>8 and calibration==8,'actual color console did not draw')
    end
end
io.write('PASS: restored bytes, editor state and expected execution\n')
'''


class BootstrapGenerationTests(unittest.TestCase):
    def test_deterministic_bootstrap_matches_published_color_source(self):
        source = COLOR.read_bytes()
        generated = pack_lua(source)
        self.assertEqual(generated, pack_lua(source))
        self.assertEqual(generated, (ROOT / "ftp_bootstrap.lua").read_bytes())
        self.assertTrue(generated.isascii())

    def test_cli_generates_the_standalone_color_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "install.lua"
            result = subprocess.run(
                [sys.executable, str(ROOT / "pack_bootstrap.py"), str(COLOR), str(output)],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_bytes(), pack_lua(COLOR.read_bytes()))


class BootstrapLuaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lua = find_lua51()
        if cls.lua is None:
            raise unittest.SkipTest("Lua 5.1 unavailable; set LUA51 to enable runtime checks")

    def run_bootstrap(self, source, *, minify=True, mode="plain"):
        expected = minify_lua(source) if minify else source
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "source.lua").write_bytes(expected)
            (folder / "install.lua").write_bytes(pack_lua(source, minify=minify))
            (folder / "harness.lua").write_text(LUA_HARNESS, encoding="ascii")
            result = subprocess.run(
                [self.lua, str(folder / "harness.lua"), str(folder / "source.lua"),
                 str(folder / "install.lua"), mode],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            self.assertIn("PASS: restored bytes", result.stdout)

    def test_color_installs_and_runs_with_empty_storage(self):
        self.run_bootstrap(COLOR.read_bytes(), mode="color")

    def test_keep_source_restores_and_runs_the_exact_complete_source(self):
        self.run_bootstrap(COLOR.read_bytes(), minify=False, mode="color")

    def test_empty_utf8_and_overlapping_compressed_matches_roundtrip(self):
        cases = [b"", 'return "关注果冻谢谢喵"\r\n'.encode("utf-8")]
        cases.extend(b'return "' + pattern * 4096 + b'"' for pattern in (b"a", b"ab"))
        for source in cases:
            with self.subTest(length=len(source)):
                self.run_bootstrap(source, minify=False)

    def test_all_byte_values_and_incompressible_bytes_are_restored_exactly(self):
        cases = [bytes(range(256)) * 40, b"\0" + random.Random(20261029).randbytes(20000)]
        for source in cases:
            with self.subTest(length=len(source)):
                self.run_bootstrap(source, minify=False, mode="binary")

    def test_lexical_minification_preserves_strings_and_token_boundaries(self):
        source = r'''-- removable comment
local quoted='quote\'--'; assert(quoted=="quote'--")
--[=[ removable long comment ]=]
local long=[==[first
--literal [=[ nested ]=]
]==]
assert(long=="first\n--literal [=[ nested ]=]\n")
assert(1.5 .. 'x'=='1.5x' and 1 .. 2=='12')
assert(0xFF==255 and 1e-3==0.001 and 3 - -2==5)
local t={};t[ [=[key]=] ]='ok';assert(t.key=='ok')
local function f(...)return ... end;assert(f(7)==7)
'''.encode("ascii").replace(b"first\n", b"first\r\n")
        self.run_bootstrap(source)


if __name__ == "__main__":
    unittest.main()
