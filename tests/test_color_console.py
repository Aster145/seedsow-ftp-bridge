"""Run the actual color console against a persistent-layer Lua 5.1 mock."""

from pathlib import Path
import subprocess
import unittest

from test_bootstrap import find_lua51


ROOT = Path(__file__).resolve().parents[1]


class ColorConsoleLuaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lua = find_lua51()
        if cls.lua is None:
            raise unittest.SkipTest("Lua 5.1 unavailable; set LUA51 to enable runtime checks")

    def test_frame_bytes_pool_reuse_page_wraparound_and_editor_restore(self):
        result = subprocess.run(
            [self.lua, str(ROOT / "tests" / "fixtures" / "color_console.lua"),
             str(ROOT / "ftp_console_color.lua")],
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("PASS: SF02 rect-to-byte reconstruction", result.stdout)


if __name__ == "__main__":
    unittest.main()
