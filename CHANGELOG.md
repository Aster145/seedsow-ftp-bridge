# Changelog

## 0.2.0 — 2026-10-29

- Optional calibrated 8-color optical download protocol: 1664 payload bytes per page, with page and whole-file checksums. Existing 512-byte monochrome pages remain supported.
- Reuse game-side rectangle layers when turning pages and reuse one OBS connection per file request.
- Preserve the installed monochrome script and create a separate color script with a checked delta upgrade.
- Report request setup, optical transfer, page retries and total duration separately. Controller upload timing and the 64 KiB file limit are unchanged.
- Add color decoding, OBS session, upgrade execution and rectangle-pool regression tests.
- On one NS2 setup, three verified 16 KiB FTP downloads per mode reduced median total time from 26.02 s to 17.69 s (32%); the optical response phase fell from 13.59 s to 5.17 s. All 31 offline tests passed.

## 0.1.0 — 2026-10-29

Initial experimental release.

- Local passive FTP server with game-backed listing, upload and download.
- EasyCon ESP32-S3 serial input and OBS optical response decoding.
- Lua installer and game-side file index.
- Windows install, start, stop and return-to-editor entrypoints.
- Small-file round trip verified on one NS2 setup; 13 offline tests included.
