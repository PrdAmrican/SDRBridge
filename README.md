# SDRBridge

Use an SDRplay **nRSP-ST** (or any SDRplay radio) from software that speaks
**rtl_tcp** or **SoapySDR**: SDR++, SDR#, GQRX, CubicSDR, rtl_433, OpenWebRX,
dump1090, welle.io, GNU Radio, and anything built on SoapySDR.

```
nRSP-ST ──(SDRplay private protocol)──► SDRconnect / SDRconnect Headless
                                              │  WebSocket API, port 5454
                                              ▼
                                         nrsp_bridge.py
                          ┌───────────────────┼────────────────────┐
                   :1234 rtl_tcp       :1236 rtl_tcp for     :1235 raw IQ
                   (SDR++, SDR#,       SoapyRTLTCP           (GNU Radio,
                    GQRX, rtl_433…)    (any Soapy app)        csdr, nc…)
```

The WebSocket API SDRplay publishes (API 1.0.3, SDRconnect 1.0.6 and later)
is served by **SDRconnect**, not by the nRSP-ST box itself, so SDRconnect
(the GUI, or SDRconnect Headless on a Pi/server) must be running and
connected to the nRSP-ST. The bridge talks to SDRconnect.

## Install

Python 3.9+ on Windows, macOS or Linux. Either run the script directly:

```
pip install numpy websockets
python3 nrsp_bridge.py --version
```

or install it as a command (`sdrbridge`) from a checkout or a release tag:

```
pip install .                                                # from a clone
pip install git+https://github.com/PrdAmrican/SDRBridge@v1.0.0   # a release (needs repo access)
sdrbridge --version
```

## Run

1. In SDRconnect, open **Settings** and switch on the **WebSocket** server
   (port 5454). Select the nRSP-ST, ideally in **Full IQ** network mode.
2. Start the bridge, ideally on the same computer as SDRconnect:

```
python3 nrsp_bridge.py                       # SDRconnect on this machine
python3 nrsp_bridge.py -s 192.168.1.50       # SDRconnect elsewhere
python3 nrsp_bridge.py -s 192.168.1.50 --antenna "Antenna B" -f 100.1M
```

On **Windows**, you can instead double-click **`start-bridge.bat`**. Edit
the settings at its top first (radio serial number, antenna, SDRconnect
address). It installs the Python packages if needed, offers to stop an old
copy of the bridge that is still holding the port, warns if SDRconnect's
WebSocket server isn't reachable, and passes anything you add after its
name on to the bridge (`start-bridge.bat --antenna "Antenna B" -v`).

Then point your SDR program at the bridge's address:

| Program | How to connect |
|---|---|
| **SDR++** | Source: *RTL-TCP*, host = bridge IP, port 1234 |
| **SDR#** | Source: *RTL-SDR (TCP)*, host = bridge IP, port 1234 |
| **GQRX** | Device string: `rtl_tcp=192.168.1.20:1234` |
| **rtl_433** | `rtl_433 -d rtl_tcp:192.168.1.20:1234` |
| **OpenWebRX** | SDR device type *RTL-SDR (rtl_tcp)*, port 1234 |
| **welle.io** | `welle-cli -F rtl_tcp,192.168.1.20:1234` |
| **SoapySDR apps** (CubicSDR, SDRangel, Pothos, Python/C++ SoapySDR, rtl_433 -d soapy) | Install the **SoapyRTLTCP** module, then use device string `driver=rtltcp,rtltcp=192.168.1.20:1236` (note port **1236**) |
| **GNU Radio** | *osmocom Source* with `rtl_tcp=192.168.1.20:1234`, or *TCP Source* on port 1235 for 16-bit `short` IQ (interleaved, device rate) |
| **csdr / scripts** | `nc 192.168.1.20 1235 > capture.cs16` |

### Why a separate SoapySDR port?

SoapyRTLTCP has a bug: it tests a macro (`__LITTLE_ENDIAN__`) that GCC/Clang
on Linux do not define, so on ordinary x86/ARM machines it swaps I and Q
whenever an app reads CF32/CS16 samples (which is nearly always). The
spectrum comes out mirrored. Port 1236 sends I/Q pre-swapped so the two
swaps cancel. If your app reads CS8, or lets you set the module's
`iq_swap` setting, use port 1234 instead.

A second SoapyRTLTCP quirk: its plain `setGain(value)` sends dB where
rtl_tcp expects tenths of dB, so gain comes out 10× too low. Set the named
gain element instead, e.g. `setGain(RX, 0, "TUNER", 30)` or
`rtl_433 -g TUNER=30`. Most GUI apps already do this.

## What maps to what

| rtl_tcp command | SDRconnect property |
|---|---|
| set frequency | `device_center_frequency` (and `device_vfo_frequency`, so SDRconnect's display follows; `--no-vfo-sync` disables) |
| set sample rate | `device_sample_rate` (see below) |
| set gain / gain index | `lna_state`: the client's 0–49.6 dB is spread across the radio's `lna_state_min…max`, inverted because SDRplay's LNA state is attenuation (0 = most gain) |
| gain mode auto | leaves the gain alone, or sets `--auto-lna N` |
| ppm, bias-T, direct sampling, AGC, offset tuning | ignored; the API has no equivalent. Bias-T for antenna B can be switched on in SDRconnect itself |

Antenna (A/B/C) has no rtl_tcp command, so choose it with `--antenna`.

**Sample rates.** SDRconnect is known to accept 62.5 k, 125 k, 250 k, 500 k,
1, 2, 6, 8 and 10 MS/s. When a client asks for something else (2.048 or
2.4 MS/s are common), the bridge first asks SDRconnect for that exact rate;
if refused, it runs the radio at the next known rate above it and resamples
to exactly what the client asked for. Refusals are remembered, and samples
are held back during a change so a client never gets IQ at the wrong rate.
Resampling costs about a quarter of one desktop core at 6 → 2.4 MS/s; on a
Raspberry Pi, prefer native rates (2 MS/s, 1 MS/s, …) in your client.

**8-bit levels.** rtl_tcp carries 8-bit samples while SDRconnect sends
16-bit IQ that typically sits around −40 dBFS. A slow automatic digital
gain keeps the signal in a useful part of the 8-bit range; use
`--digital-gain 30` for a fixed value. The raw port (1235) carries the
original 16-bit samples untouched.

## Bandwidth

Full IQ from SDRconnect is 16-bit: 2 MS/s is about 64 Mbit/s. Running the
bridge on the same machine as SDRconnect keeps that off the network; the
rtl_tcp side is 8-bit, half as much. The bridge pauses the IQ stream when no
clients are connected (`--always-stream` to keep it running). If the
periodic stats line says IQ is arriving slower than expected, the network
or the nRSP-ST network mode (IQ Lite / Compact) is the likely cause.

## Options

```
-s/--sdrconnect HOST[:PORT]   SDRconnect address (default 127.0.0.1:5454)
--device-serial SERIAL        pick a device; --network-mode "Full IQ"|"IQ Lite"|"Compact"
--device-name / --device-index
--antenna NAME                e.g. "Antenna B" (names come from valid_antennas)
-f/--freq, -r/--rate, --lna   start-up settings
--freq-offset HZ              added to every tune (up/down-converters)
--control all|first|none      which rtl_tcp clients may retune (default all)
-p/--rtl-port, --soapy-port, --raw-port, --raw-format cs16|cu8, -b/--bind
--digital-gain DB             fixed instead of automatic 8-bit gain
--native-rates-only           never try non-standard rates; always resample
--secondary                   second tuner of an RSPduo
--stats SEC, -v/-vv
```

Run `python3 nrsp_bridge.py --help` for the full list.

## Run as a service (Linux)

`/etc/systemd/system/nrsp-bridge.service`:

```
[Unit]
Description=nRSP-ST rtl_tcp bridge
After=network-online.target

[Service]
ExecStart=/usr/bin/python3 /opt/nrsp-bridge/nrsp_bridge.py -s 127.0.0.1 --antenna "Antenna A"
Restart=always

[Install]
WantedBy=multi-user.target
```

`sudo systemctl enable --now nrsp-bridge`

## Testing without a radio

`tools/fake_sdrconnect.py` imitates SDRconnect's WebSocket API with a
carrier at 100.1 MHz:

```
python3 tools/fake_sdrconnect.py --port 5454 &
python3 nrsp_bridge.py
# connect SDR++ to 127.0.0.1:1234, tune 100 MHz: the carrier is at +100 kHz
python3 tests/test_bridge.py   # 34 automated checks
```

## Versioning

SDRBridge uses [Semantic Versioning](https://semver.org/). The version lives
in one place, `__version__` in `nrsp_bridge.py`; `--version`, the start-up
log line and `pyproject.toml` all read it from there. Every release is a git
tag `vX.Y.Z`, and [CHANGELOG.md](CHANGELOG.md) lists what changed.

To cut a release, note the changes under `## [Unreleased]` in the
changelog, then:

```
python3 tools/release.py patch --push    # or minor / major / 1.2.3
```

It refuses to run with uncommitted changes or an empty changelog entry,
runs the tests, bumps the version, commits, tags and pushes.

## Status and limits

* Built from SDRplay's published API specification (1.0.3). Tested against
  the simulator above and against a real SoapySDR + SoapyRTLTCP build; not
  yet against a physical nRSP-ST. The stats line and `-v` logging are the
  places to look if something misbehaves.
* rtl_tcp is 8-bit and cannot report settings back, so changes made in the
  SDRconnect GUI are not shown in your client (they are logged by the
  bridge).
* rtl_tcp clients generally top out around 3.2 MS/s; use the raw port for
  wider captures.
* The rtl_tcp protocol has no authentication. Keep the ports inside your
  LAN or a VPN.

## Copyright

Copyright (c) 2026 PrdAmrican. All rights reserved. See [COPYRIGHT](COPYRIGHT).

SDRplay, SDRconnect and nRSP-ST are trademarks of SDRplay Ltd. SDRBridge is an
independent project, not affiliated with or endorsed by SDRplay Ltd.
