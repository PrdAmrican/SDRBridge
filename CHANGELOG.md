# Changelog

All notable changes to SDRBridge are recorded here. The project follows
[Semantic Versioning](https://semver.org/): MAJOR for changes that break
existing command lines or client setups, MINOR for new features, PATCH for
fixes. Each release is tagged `vX.Y.Z` in git.

## [Unreleased]

## [1.2.1] - 2026-11-18

### Added
- Copyright notice (Copyright (c) 2026 PrdAmrican. All rights reserved.) in every source file,
  the Windows launcher, `--version` output, the start-up log line, the
  README and a new `COPYRIGHT` file; author and licence in `pyproject.toml`.

## [1.2.0] - 2026-11-18

### Added
- `start-bridge.bat`: Windows launcher. Settings (radio serial, antenna,
  SDRconnect address, rtl_tcp port, extra options) at the top; finds
  Python, installs numpy/websockets if missing, offers to stop an old copy
  of the bridge holding the port, warns if SDRconnect's WebSocket server is
  not reachable, and passes extra command-line arguments through.
- `.gitattributes` keeps CRLF line endings for `.bat` files.

## [1.1.3] - 2026-11-18

### Fixed
- When a listening port is already in use (typically an earlier copy of the
  bridge still running), exit with a plain explanation and the command to
  find the other program, instead of a traceback.

## [1.1.2] - 2026-11-18

### Fixed
- Recognise a WebSocket that speaks a different (binary) protocol, such as
  the nRSP-ST's built-in web-client port 9001, and say so plainly in both
  `--probe` and normal mode instead of a generic failure.
- No more "Task exception was never retrieved" traceback when the far end
  closes the WebSocket.
- After a sample-rate change, samples still queued for a client at the old
  rate are dropped, so clients never see a burst at the wrong rate.

## [1.1.1] - 2026-11-18

### Changed
- `--probe` now prints the real connection error, and when the target does
  not answer like SDRconnect it investigates the port: TCP reachability,
  the web page and its scripts (WebSocket URLs and message names), and
  WebSocket paths, printing what each one sends. Used to work out the
  nRSP-ST's built-in web-client port (9001).

## [1.1.0] - 2026-11-18

### Added
- `--probe`: connects to SDRconnect, prints its properties and first raw
  messages, streams IQ for 5 s and says whether IQ is flowing and why not.
- Warning when an rtl_tcp client connects while the bridge is not connected
  to SDRconnect.
- Watchdog warning when IQ was requested but none arrives within 4 s, with
  SDRconnect's started/can_control/device state.
- Stats line shows SDRconnect connection state and binary frames by type;
  printed every 10 s instead of 30 s.
- Simulator options `--device-label` and `--no-iq` to reproduce faults.

### Fixed
- Messages whose `device` field was not exactly `primary` (e.g. `Primary`)
  were silently discarded, so property answers never arrived.
- Reconnect attempts to SDRconnect now retry at most every 5 s (was 30 s),
  so the bridge picks SDRconnect up quickly once it is started.

## [1.0.0] - 2026-11-18

First release.

### Added
- SDRconnect WebSocket API 1.0.3 client with automatic reconnect; settings
  are re-applied after SDRconnect restarts.
- rtl_tcp server (port 1234) mapping frequency, sample rate and gain
  (R820T gain table → `lna_state`) onto SDRconnect properties.
- rtl_tcp port for SoapyRTLTCP (port 1236) with I/Q pre-swapped to cancel
  SoapyRTLTCP's I/Q swap bug on little-endian Linux/macOS.
- Raw IQ TCP server (port 1235), cs16 or cu8.
- Sample-rate negotiation: exact rate first, otherwise the next known
  SDRconnect rate plus a streaming resampler; refusals remembered; samples
  held back while a rate change is in flight.
- Automatic digital gain before 8-bit conversion (`--digital-gain` to fix it).
- Device, network-mode, antenna and secondary-tuner selection; listen-only
  client control modes; IQ stream paused when no clients are connected.
- SDRconnect simulator (`tools/fake_sdrconnect.py`) and end-to-end tests.
