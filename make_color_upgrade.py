"""Build a compact Lua 5.1 upgrade using the installed monochrome FTP source.

Usage: python make_color_upgrade.py [baseline.lua] [color.lua] [upgrade.lua]
Inputs are lexically minified like the original bootstrap installation. The
upgrade verifies the installed ftp_console.lua, reconstructs the color source,
and saves/runs ftp_console_color.lua without changing the monochrome file.
"""

from __future__ import annotations

import argparse
import difflib
from pathlib import Path
import zlib

from pack_bootstrap import _quote_lua, minify_lua


def _source_expression(baseline: bytes, target: bytes) -> str:
    """Reuse worthwhile matching blocks; merge the remaining bytes as literals."""
    fragments = []
    position = 0
    matcher = difflib.SequenceMatcher(None, baseline, target, autojunk=False)
    for match in matcher.get_matching_blocks():
        if not match.size:
            continue
        copy = f"b:sub({match.a + 1},{match.a + match.size})"
        literal = _quote_lua(target[match.b:match.b + match.size])
        # Two new literal delimiters and commas can cost six characters when
        # a copy splits a literal. Small matches are cheaper inside that literal.
        if len(literal) <= len(copy) + 6:
            continue
        if match.b > position:
            fragments.append(_quote_lua(target[position:match.b]))
        fragments.append(copy)
        position = match.b + match.size
    if position < len(target):
        fragments.append(_quote_lua(target[position:]))
    if not fragments:
        expression = '""'
    elif len(fragments) == 1:
        expression = fragments[0]
    else:
        expression = "table.concat{" + ",".join(fragments) + "}"
    return min(expression, _quote_lua(target), key=len)


def make_upgrade(baseline: bytes, target: bytes) -> bytes:
    """Return ASCII Lua installing the equivalent minified color source."""
    baseline = minify_lua(baseline)
    target = minify_lua(target)
    expression = _source_expression(baseline, target)
    checksum = zlib.adler32(baseline) & 0xFFFFFFFF
    script = (
        'local E=seedsow_editor;'
        'local b=assert(editor.read("ftp_console.lua"),"Missing FTP baseline");'
        f'assert(#b=={len(baseline)} and E.storage.adler(b)=={checksum},'
        '"FTP baseline mismatch");'
        f'local s={expression};'
        'local n="ftp_console_color.lua";editor.write(n,s);'
        'local t=E.test_state();t.filename=n;t.buffer=s;t.saved=s;t.cursor=0;'
        'local f=assert(loadstring(s,"@"..n));setfenv(f,getfenv());return f()\n'
    )
    return script.encode("ascii")


def main() -> None:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", nargs="?", type=Path, default=root / "ftp_console.lua")
    parser.add_argument("target", nargs="?", type=Path, default=root / "ftp_console_color.lua")
    parser.add_argument("output", nargs="?", type=Path, default=root / "ftp_color_upgrade.lua")
    args = parser.parse_args()
    if args.output.resolve() in (args.baseline.resolve(), args.target.resolve()):
        parser.error("The upgrade output must be separate from both source files")
    baseline = args.baseline.read_bytes()
    target = args.target.read_bytes()
    compact_baseline, compact_target = minify_lua(baseline), minify_lua(target)
    upgrade = make_upgrade(baseline, target)
    args.output.write_bytes(upgrade)
    print(f"Required installed baseline: {len(compact_baseline)} bytes, "
          f"Adler32 {zlib.adler32(compact_baseline) & 0xFFFFFFFF:08x}.")
    print(f"Color source: {len(target)} bytes; installed minified source: "
          f"{len(compact_target)} bytes; controller upgrade: {len(upgrade)} bytes.")
    print("Saves ftp_console_color.lua; preserves the existing ftp_console.lua.")


if __name__ == "__main__":
    main()
