# Related projects and dependencies

- [Seedsow Lua Editor](https://github.com/DialingSpoon527/seedsow-lua-editor): supplies the game-side editor, storage API and SSE2 transfer receiver. The locally inspected upstream source uses the BSD Zero Clause License, copyright 2026 Seedsow Lua Editor contributors. The editor and its installation save are not bundled here.
- [EasyCon](https://github.com/EasyConNS/EasyCon): the controller firmware provides the serial report protocol used by this bridge. Firmware is installed separately and is not included in this repository or release package.
- [OBS Studio](https://github.com/obsproject/obs-studio): supplies the local WebSocket API and capture-source screenshots. OBS is installed separately and is not included here.
- Python packages `pyserial`, `websockets` and `Pillow` are installed separately from `requirements.txt`; their packages retain their own licenses. No third-party Python runtime or package is bundled in the release ZIP.

The repository license covers this bridge's source files. It does not relicense any game, save archive, firmware, editor or separately installed dependency.
