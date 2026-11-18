#!/usr/bin/env python3
# SDRBridge - Copyright (c) 2026 PrdAmrican. All rights reserved.
# Proprietary and confidential. See COPYRIGHT for terms.
"""End-to-end tests: fake SDRconnect <- bridge -> rtl_tcp / raw clients.

    python3 tests/test_bridge.py
"""
import asyncio
import os
import struct
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import nrsp_bridge as nb  # noqa: E402
import fake_sdrconnect as fake_mod  # noqa: E402

WS_PORT, RTL_PORT, RAW_PORT = 15454, 11234, 11235
TONE = 100.1e6
passed = 0


def check(cond, what):
    global passed
    if not cond:
        raise AssertionError(what)
    passed += 1
    print(f"  ok  {what}")


def peak_offset(iq: np.ndarray, fs: float) -> float:
    n = 1 << int(np.log2(len(iq)))
    spec = np.abs(np.fft.fftshift(np.fft.fft(iq[:n] * np.hanning(n))))
    return (np.argmax(spec) - n / 2) * fs / n


def test_units():
    print("unit tests")
    check(nb.ws_url("192.168.1.5") == "ws://192.168.1.5:5454/", "ws_url adds default port")
    check(nb.ws_url("pi.local:6000") == "ws://pi.local:6000/", "ws_url keeps explicit port")
    check(nb.ws_url("ws://h:1/x") == "ws://h:1/x", "ws_url keeps full URL")
    check(nb.parse_freq("100.1M") == 100.1e6 and nb.parse_freq("250k") == 250e3, "parse_freq suffixes")

    fs_in, fs_out, f0 = 6e6, 2.048e6, 300e3
    rs = nb.Resampler(fs_in, fs_out)
    t = np.arange(int(fs_in * 0.2))
    x = np.exp(2j * np.pi * f0 / fs_in * t).astype(np.complex64)
    out = np.concatenate([rs.process(x[i:i + 120_000]) for i in range(0, len(x), 120_000)])
    check(abs(len(out) - len(x) * fs_out / fs_in) <= 2, f"resampler output length ({len(out)})")
    check(abs(peak_offset(out[1000:], fs_out) - f0) < 500, "resampler keeps tone frequency")
    seg = out[2000:]
    check(np.std(np.abs(seg)) < 0.02, "resampler output has no block-boundary glitches")
    # alias rejection: a tone at 1.5 MHz must be suppressed when going to 2.048 MS/s
    rs2 = nb.Resampler(fs_in, fs_out)
    y = rs2.process(np.exp(2j * np.pi * 1.5e6 / fs_in * t).astype(np.complex64))[2000:]
    check(20 * np.log10(np.sqrt(np.mean(np.abs(y) ** 2)) + 1e-12) < -40, "resampler rejects out-of-band tone (>40 dB)")

    lo = nb.to_cu8(np.array([1 + 1j, -1 - 1j, 0], dtype=np.complex64), 1.0)
    check(list(lo) == [255, 255, 0, 0, 127, 127], "cu8 conversion range")


async def rtl_client():
    r, w = await asyncio.open_connection("127.0.0.1", RTL_PORT)
    hdr = await r.readexactly(12)
    return r, w, hdr


def cmd(w, c, p):
    w.write(struct.pack(">BI", c, p & 0xFFFFFFFF))


async def read_iq(r, seconds, fs):
    want = int(fs * seconds) * 2
    buf = bytearray()
    t0 = time.monotonic()
    while len(buf) < want:
        buf += await asyncio.wait_for(r.read(65536), 5)
    dt = time.monotonic() - t0
    u = np.frombuffer(bytes(buf[:want]), dtype=np.uint8).astype(np.float32) - 127.5
    return (u[0::2] + 1j * u[1::2]), dt


async def measure_rate(r, seconds):
    # drain the backlog first, then count bytes over a fixed wall-clock window
    t0 = time.monotonic()
    while time.monotonic() - t0 < 0.5:
        await asyncio.wait_for(r.read(1 << 20), 5)
    n, t0 = 0, time.monotonic()
    while time.monotonic() - t0 < seconds:
        n += len(await asyncio.wait_for(r.read(1 << 20), 5))
    return n / 2 / (time.monotonic() - t0)


async def test_e2e():
    print("end-to-end tests (fake SDRconnect)")
    holder = []
    ready = asyncio.Event()
    fake_task = asyncio.create_task(fake_mod.run(WS_PORT, TONE, ready, holder))
    await ready.wait()
    fake = holder[0]

    args = nb.build_parser().parse_args(
        ["-s", f"127.0.0.1:{WS_PORT}", "-p", str(RTL_PORT), "--raw-port", str(RAW_PORT), "--soapy-port", "11236",
         "-b", "127.0.0.1", "--stats", "0", "--antenna", "Antenna B"])
    bridge = nb.Bridge(args)
    btask = asyncio.create_task(bridge.run())
    await asyncio.wait_for(bridge.sdr.connected.wait(), 5)
    await asyncio.sleep(0.5)
    check(fake.props["active_antenna"] == "Antenna B", "antenna selected on connect")
    check(any(m["event_type"] == "device_stream_enable" for m in fake.log), "device stream enabled")
    check(not bridge.sdr.iq_wanted, "IQ stream paused while nobody is connected")

    r, w, hdr = await rtl_client()
    check(hdr[:4] == b"RTL0" and struct.unpack(">II", hdr[4:]) == (5, 29), "rtl_tcp header RTL0/R820T/29 gains")

    # Typical SDR++/SDR# start-up: rate 2.048M (not native -> fallback), tune, manual gain max
    cmd(w, 0x02, 2_048_000)
    cmd(w, 0x01, 100_000_000)
    cmd(w, 0x03, 1)
    cmd(w, 0x04, 496)
    await w.drain()
    await asyncio.sleep(0.3)
    check(fake.props["device_center_frequency"] == "100000000",
          "tune applied at once, not held up by the rate negotiation")
    await asyncio.sleep(1.5)
    check(fake.props["device_sample_rate"] == "6000000", "2.048 MS/s refused -> device at 6 MS/s")
    check(bridge.resampler is not None and bridge.client_rate == 2_048_000, "bridge resamples 6 -> 2.048 MS/s")
    check(fake.props["device_center_frequency"] == "100000000", "tuned to 100 MHz")
    check(fake.props["device_vfo_frequency"] == "100000000", "VFO follows centre frequency")
    check(fake.props["lna_state"] == "0", "max rtl gain -> lna_state 0 (max gain)")
    check(bridge.sdr.iq_wanted, "IQ stream enabled once a client connects")

    rate = await measure_rate(r, 2.0)
    check(abs(rate - 2.048e6) / 2.048e6 < 0.08, f"2.048 MS/s delivered in real time (measured {rate/1e6:.3f} MS/s)")
    iq, _ = await read_iq(r, 1.0, 2.048e6)
    off = peak_offset(iq, 2.048e6)
    check(abs(off - 100e3) < 2e3, f"carrier at +100 kHz after tuning (found {off/1e3:+.1f} kHz)")
    rms = np.sqrt(np.mean(np.abs(iq) ** 2))
    check(10 < rms < 110, f"8-bit samples use the range sensibly (rms {rms:.1f})")

    # Retune: carrier should move to -50 kHz
    cmd(w, 0x01, 100_150_000)
    await w.drain()
    await asyncio.sleep(0.8)
    await read_iq(r, 0.3, 2.048e6)
    iq, _ = await read_iq(r, 0.5, 2.048e6)
    off = peak_offset(iq, 2.048e6)
    check(abs(off + 50e3) < 2e3, f"retune moves carrier to -50 kHz (found {off/1e3:+.1f} kHz)")

    # Native rate: passthrough, no resampler
    cmd(w, 0x02, 250_000)
    await w.drain()
    await asyncio.sleep(1.5)
    check(fake.props["device_sample_rate"] == "250000" and bridge.resampler is None, "250 kS/s passes straight through")
    await read_iq(r, 0.3, 250e3)
    iq, dt = await read_iq(r, 1.0, 250e3)
    check(abs(peak_offset(iq, 250e3) + 50e3) < 1e3, "carrier correct at 250 kS/s")

    # Gain by index and mid gain
    cmd(w, 0x0D, 0)
    await w.drain()
    await asyncio.sleep(0.3)
    check(fake.props["lna_state"] == "27", "gain index 0 -> lna_state max (min gain)")
    cmd(w, 0x04, 248)
    await w.drain()
    await asyncio.sleep(0.3)
    check(fake.props["lna_state"] in ("13", "14"), "half gain -> mid lna_state")

    # Unsupported commands don't break the session
    for c in (0x05, 0x06, 0x08, 0x09, 0x0E):
        cmd(w, c, 1)
    await w.drain()
    await read_iq(r, 0.2, 250e3)
    check(True, "unsupported commands ignored, stream continues")

    # Raw port: cs16 at device rate, alongside rtl client
    rr, rw = await asyncio.open_connection("127.0.0.1", RAW_PORT)
    buf = bytearray()
    while len(buf) < 250_000 * 4:
        buf += await asyncio.wait_for(rr.read(65536), 5)
    s = np.frombuffer(bytes(buf[:250_000 * 4]), dtype="<i2").astype(np.float32)
    raw = s[0::2] + 1j * s[1::2]
    check(abs(peak_offset(raw, 250e3) + 50e3) < 1e3, "raw cs16 port carries the same signal")
    rw.close()

    # Soapy port: same stream with I and Q exchanged (mirror image)
    sr, sw = await asyncio.open_connection("127.0.0.1", 11236)
    await sr.readexactly(12)
    iq, _ = await read_iq(sr, 0.5, 250e3)
    check(abs(peak_offset(iq, 250e3) - 50e3) < 1e3, "soapy port delivers I/Q-swapped stream (mirror)")
    sw.close()
    await asyncio.sleep(0.2)

    # Second rtl client and disconnects
    r2, w2, _ = await rtl_client()
    await read_iq(r2, 0.2, 250e3)
    check(len(bridge.rtl_clients) == 2, "two rtl_tcp clients served at once")
    w2.close()
    w.close()
    await asyncio.sleep(0.5)
    check(not bridge.sdr.iq_wanted, "IQ stream paused again after the last client leaves")

    # Reconnect: kill fake and restart, bridge should re-apply settings
    fake_task.cancel()
    await asyncio.sleep(0.5)
    holder.clear()
    ready = asyncio.Event()
    fake_task = asyncio.create_task(fake_mod.run(WS_PORT, TONE, ready, holder))
    await ready.wait()
    await asyncio.wait_for(bridge.sdr.connected.wait(), 10)
    await asyncio.sleep(0.8)
    f2 = holder[0]
    check(f2.props["device_center_frequency"] == "100150000" and f2.props["active_antenna"] == "Antenna B",
          "settings re-applied after SDRconnect restart")

    btask.cancel()
    fake_task.cancel()
    await asyncio.gather(btask, fake_task, return_exceptions=True)


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)
    test_units()
    asyncio.run(test_e2e())
    print(f"\nall {passed} checks passed")
