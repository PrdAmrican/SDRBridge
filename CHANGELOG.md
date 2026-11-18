# Changelog

All notable changes to SDRBridge are recorded here. The project follows
[Semantic Versioning](https://semver.org/): MAJOR for changes that break
existing command lines or client setups, MINOR for new features, PATCH for
fixes. Each release is tagged `vX.Y.Z` in git.

## [Unreleased]

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
