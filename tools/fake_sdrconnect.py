#!/usr/bin/env python3
"""
fake_sdrconnect.py - a stand-in for SDRconnect's WebSocket API 1.0.3, for
testing nrsp_bridge.py without a radio.

It answers get_property/set_property, pushes property_changed, honours
iq_stream_enable per connection and streams int16 IQ (binary type 2) in
20 ms frames: noise at about -40 dBFS (as measured on real SDRconnect) plus
a carrier at a fixed absolute frequency (default 100.1 MHz), so tuning can be
checked by where the carrier lands in the spectrum.

Only the sample rates real SDRconnect is known to accept are accepted,
which exercises the bridge's resampling fallback.

    python3 tools/fake_sdrconnect.py --port 5454 --tone 100.1e6
"""
import argparse
import asyncio
import json
import struct

import numpy as np
from websockets.asyncio.server import serve

ACCEPTED_RATES = [62_500, 125_000, 250_000, 500_000, 1_000_000,
                  2_000_000, 6_000_000, 8_000_000, 10_000_000]


class FakeSDRconnect:
    def __init__(self, tone_hz: float, tone_dbfs: float = -20.0, device_label: str = "primary",
                 no_iq: bool = False):
        self.device_label = device_label
        self.no_iq = no_iq
        self.tone_hz = tone_hz
        self.tone_amp = 10 ** (tone_dbfs / 20)
        self.props = {
            "api_version": "1.0.3",
            "valid_devices": "nRSP-ST (FAKE0001)",
            "active_device": "nRSP-ST (FAKE0001)",
            "can_control": "true",
            "started": "true",
            "device_center_frequency": "100000000",
            "device_vfo_frequency": "100000000",
            "device_sample_rate": "2000000",
            "lna_state": "4",
            "lna_state_min": "0",
            "lna_state_max": "27",
            "valid_antennas": "Antenna A,Antenna B,Antenna C",
            "active_antenna": "Antenna A",
            "overload": "false",
        }
        self.log: list[dict] = []
        self.clients = set()
        self.phase = 0.0
        self.rng = np.random.default_rng(1)

    async def push(self, prop):
        msg = json.dumps({"event_type": "property_changed", "property": prop,
                          "device": self.device_label, "value": self.props[prop]})
        for ws in list(self.clients):
            try:
                await ws.send(msg)
            except Exception:
                pass

    async def handler(self, ws):
        ws.iq_on = False
        self.clients.add(ws)
        try:
            async for text in ws:
                m = json.loads(text)
                self.log.append(m)
                et, prop, val = m["event_type"], m.get("property", ""), m.get("value", "")
                if et == "get_property":
                    await ws.send(json.dumps({"event_type": "get_property_response", "property": prop,
                                              "device": self.device_label, "value": self.props.get(prop, "")}))
                elif et == "set_property":
                    if prop == "device_sample_rate" and int(float(val)) not in ACCEPTED_RATES:
                        continue  # refused silently, keeps old rate
                    if prop == "active_antenna" and val not in self.props["valid_antennas"].split(","):
                        continue
                    self.props[prop] = val
                    await self.push(prop)
                elif et == "iq_stream_enable":
                    ws.iq_on = val == "true"
        except Exception:
            pass
        finally:
            self.clients.discard(ws)

    async def streamer(self):
        loop = asyncio.get_running_loop()
        t_next = loop.time()
        while True:
            fs = float(self.props["device_sample_rate"])
            n = int(fs * 0.02)
            fc = float(self.props["device_center_frequency"])
            off = self.tone_hz - fc
            k = np.arange(n)
            ph = self.phase + 2 * np.pi * off / fs * k
            self.phase = float((self.phase + 2 * np.pi * off / fs * n) % (2 * np.pi))
            sig = (self.tone_amp * np.exp(1j * ph)) if abs(off) < fs / 2 else 0
            noise = (self.rng.standard_normal(n) + 1j * self.rng.standard_normal(n)) * 0.007
            x = (sig + noise) * 32767
            iq = np.empty(2 * n, dtype="<i2")
            iq[0::2] = np.clip(x.real, -32768, 32767)
            iq[1::2] = np.clip(x.imag, -32768, 32767)
            frame = struct.pack("<H", 2) + iq.tobytes()
            for ws in list(self.clients):
                if getattr(ws, "iq_on", False) and not self.no_iq:
                    try:
                        await ws.send(frame)
                    except Exception:
                        pass
            t_next += 0.02
            await asyncio.sleep(max(0, t_next - loop.time()))


async def run(port: int, tone: float, ready: asyncio.Event = None, holder: list = None,
              device_label: str = "primary", no_iq: bool = False):
    fake = FakeSDRconnect(tone, device_label=device_label, no_iq=no_iq)
    if holder is not None:
        holder.append(fake)
    async with serve(fake.handler, "127.0.0.1", port, max_size=None, compression=None):
        task = asyncio.create_task(fake.streamer())
        if ready:
            ready.set()
        await asyncio.Future()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5454)
    ap.add_argument("--tone", type=float, default=100.1e6)
    ap.add_argument("--device-label", default="primary", help="value of the 'device' field it sends")
    ap.add_argument("--no-iq", action="store_true", help="never send IQ (simulates a stalled radio)")
    a = ap.parse_args()
    print(f"fake SDRconnect on ws://127.0.0.1:{a.port}, carrier at {a.tone/1e6:.4f} MHz")
    asyncio.run(run(a.port, a.tone, device_label=a.device_label, no_iq=a.no_iq))
