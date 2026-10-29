"""Pack the FTP console source into a self-installing Lua 5.1 bootstrap.

Usage: python pack_bootstrap.py ftp_console_color.lua ftp_bootstrap.lua
By default comments and unnecessary whitespace are removed before installation.
The bootstrap saves this equivalent source before compiling and running it.
Use --keep-source to preserve the original source bytes exactly.
Only Python's standard library and Lua's standard string/table functions are used.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


_NAME = re.compile(rb"[A-Za-z_][A-Za-z_0-9]*")
_NUMBER = re.compile(
    rb"0[xX][0-9A-Fa-f]+|(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
)
_LONG = re.compile(rb"\[(=*)\[")


def _long_end(source: bytes, start: int) -> int | None:
    match = _LONG.match(source, start)
    if not match:
        return None
    closing = b"]" + match[1] + b"]"
    end = source.find(closing, match.end())
    if end < 0:
        raise ValueError("Unterminated Lua long string or comment")
    return end + len(closing)


def _tokens(source: bytes) -> list[bytes]:
    """Read Lua 5.1 tokens, preserving string literals byte for byte."""
    result = []
    position = 0
    while position < len(source):
        char = source[position]
        if char in b" \t\r\n\v\f":
            position += 1
            continue
        if source[position:position + 2] == b"--":
            end = _long_end(source, position + 2)
            if end is None:
                end = position + 2
                while end < len(source) and source[end] not in b"\r\n":
                    end += 1
            position = end
            continue
        if char in b"\"'":
            end = position + 1
            while end < len(source):
                if source[end] == 92:
                    end += 2
                elif source[end] == char:
                    end += 1
                    break
                else:
                    end += 1
            else:
                raise ValueError("Unterminated Lua quoted string")
        elif char == 91 and (long_end := _long_end(source, position)) is not None:
            end = long_end
        elif (match := _NAME.match(source, position)) is not None:
            end = match.end()
        elif (match := _NUMBER.match(source, position)) is not None:
            end = match.end()
        elif source[position:position + 3] == b"...":
            end = position + 3
        elif source[position:position + 2] in (b"..", b"==", b"~=", b"<=", b">="):
            end = position + 2
        else:
            end = position + 1
        result.append(source[position:end])
        position = end
    return result


def minify_lua(source: bytes) -> bytes:
    """Remove only comments and whitespace; never rewrite Lua literals/tokens."""
    tokens = _tokens(source)
    output: list[bytes] = []
    previous = b""
    for token in tokens:
        if previous:
            try:
                needs_space = _tokens(previous + token) != [previous, token]
            except ValueError:
                needs_space = True
            # Lua 5.1's numeral scanner greedily consumes dots/letters, even
            # when the resulting text would be an invalid numeric literal.
            if (_NUMBER.fullmatch(previous) is not None
                    and (token[:1] == b"." or _NAME.match(token) is not None
                         or token[:1].isdigit())):
                needs_space = True
            if needs_space:
                output.append(b" ")
        output.append(token)
        previous = token
    return b"".join(output)


def _quote_lua(source: bytes) -> str:
    """Return the shortest ASCII Lua literal that preserves all source bytes."""
    choices = []
    for delimiter in (34, 39):
        body = "".join(
            chr(value) if 32 <= value <= 126 and value not in (delimiter, 92)
            else "\\" + chr(value) if value in (delimiter, 92)
            else f"\\{value:03d}"
            for value in source
        )
        choices.append(chr(delimiter) + body + chr(delimiter))
    if not source.startswith(b"\n") and all(value in (9, 10) or 32 <= value <= 126 for value in source):
        body = source.decode("ascii")
        # Plain [[...]] nesting is rejected by Lua 5.1.
        delimiter = "="
        while "]" + delimiter + "]" in body or "[" + delimiter + "[" in body:
            delimiter += "="
        choices.append("[" + delimiter + "[" + body + "]" + delimiter + "]")
    return min(choices, key=len)


def _lzss(source: bytes) -> bytes:
    """Encode groups of eight literals or 12-bit-distance/4-bit-length matches."""
    output = bytearray()
    position = 0
    while position < len(source):
        flag_position = len(output)
        output.append(0)
        flags = 0
        for bit in range(8):
            if position == len(source):
                break
            length = distance = 0
            if position + 3 <= len(source):
                start = max(0, position - 4096)
                prefix = source[position:position + 3]
                candidate = source.rfind(prefix, start, position + 2)
                while candidate >= start:
                    size = 3
                    while (size < 18 and position + size < len(source)
                           and source[candidate + size] == source[position + size]):
                        size += 1
                    if size > length:
                        length, distance = size, position - candidate
                    if size == 18:
                        break
                    candidate = source.rfind(prefix, start, candidate + 2)
            if length >= 3:
                match = (length - 3) * 4096 + distance - 1
                output.extend((match % 256, match // 256))
                position += length
            else:
                flags |= 1 << bit
                output.append(source[position])
                position += 1
        output[flag_position] = flags
    return bytes(output)


def _base85(source: bytes) -> str:
    """Encode every four bytes as five printable characters, without shortcuts."""
    result = []
    for position in range(0, len(source), 4):
        value = int.from_bytes(source[position:position + 4].ljust(4, b"\0"), "big")
        digits = []
        for _ in range(5):
            value, digit = divmod(value, 85)
            digits.append(chr(33 + digit))
        result.extend(reversed(digits))
    return "".join(result)


def pack_lua(source: bytes, *, minify: bool = True) -> bytes:
    """Return ASCII Lua saving and executing equivalent source.

    ``minify=False`` restores the input bytes exactly, including binary data.
    Select the smaller direct-source installer or LZSS installer. Both save the
    complete runnable source, not the wrapping bootstrap, to ftp_console_color.lua.
    """
    if minify:
        source = minify_lua(source)
    compressed = _lzss(source)
    payload = _base85(compressed)
    quoted = _quote_lua(payload.encode("ascii"))
    install = (
        'editor.write("ftp_console_color.lua",s);'
        'local t=seedsow_editor.test_state();'
        't.filename="ftp_console_color.lua";t.cursor=0;t.buffer=s;t.saved=s;'
        'local f=assert(loadstring(s,"@ftp_console_color.lua"));'
        'setfenv(f,getfenv());return f()\n'
    )
    script = (
        f'local z={quoted};local q={{}};'
        'for i=1,#z,5 do local n=0;'
        'for j=0,4 do n=n*85+z:byte(i+j)-33 end;'
        'local t={};for j=4,1,-1 do t[j]=n%256;n=math.floor(n/256)end;'
        'for j=1,4 do q[#q+1]=t[j]end end;'
        f'local o,i,m={{}},1,{len(compressed)};'
        'while i<=m do local f=q[i];i=i+1;'
        'for k=1,8 do if i>m then break end;'
        'if f%2==1 then o[#o+1]=string.char(q[i]);i=i+1;'
        'else local n=q[i]+q[i+1]*256;i=i+2;local p=#o-n%4096;'
        'for j=1,math.floor(n/4096)+3 do o[#o+1]=o[p];p=p+1 end end;'
        'f=math.floor(f/2)end end;'
        'local s=table.concat(o);' + install
    )
    direct = 'local s=' + _quote_lua(source) + ';' + install
    return min(script, direct, key=len).encode("ascii")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--keep-source", action="store_true",
                        help="Preserve exact source bytes; skip lexical minification")
    args = parser.parse_args()
    source = args.input.read_bytes()
    compact = source if args.keep_source else minify_lua(source)
    packed = pack_lua(compact, minify=False)
    args.output.write_bytes(packed)
    saved = len(source) - len(packed)
    percent = saved / len(source) * 100 if source else 0
    print("Source mode: " + ("exact original bytes" if args.keep_source
                              else "equivalent Lua with comments and extra whitespace removed"))
    print(f"Source: {len(source)} bytes; installed source: {len(compact)} bytes; "
          f"bootstrap: {len(packed)} bytes; "
          f"saved: {saved} bytes ({percent:.1f}%).")
    if saved <= 0:
        print("The installer is larger than the input, but still saves the complete source correctly.")


if __name__ == "__main__":
    main()
