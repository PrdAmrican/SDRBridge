# Changelog

All notable changes to SDRBridge are recorded here. The project follows
[Semantic Versioning](https://semver.org/): MAJOR for changes that break
existing command lines or client setups, MINOR for new features, PATCH for
fixes. Each release is tagged `vX.Y.Z` in git.

## [Unreleased]

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
