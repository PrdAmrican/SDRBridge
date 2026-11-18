#!/usr/bin/env python3
"""
nrsp_bridge.py - expose an SDRplay nRSP-ST (or any SDRplay radio) that is
served through SDRconnect's WebSocket API (default port 5454) as:

  * an rtl_tcp server  (default :1234)  -> SoapySDR (SoapyRTLTCP), SDR++,
                                           SDR#, GQRX, rtl_433, OpenWebRX,
                                           dump1090, GNU Radio/osmosdr, ...
  * a raw IQ TCP feed  (default :1235)  -> GNU Radio TCP Source, csdr, nc,
                                           sox, custom scripts (cs16 or cu8)

Upstream protocol: "SDRconnect WebSocket API 1.0.3" (SDRplay Ltd).
  text frames : JSON {event_type, property, value, [device]}
  binary      : 2-byte little-endian payload type, then payload
                type 2 / 5 = int16 interleaved IQ, primary / secondary tuner

Requires: Python 3.9+, numpy, websockets (pip install numpy websockets)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import signal
import struct
import sys
import time
from typing import Callable, Optional

import numpy as np

try:  # websockets >= 13
    from websockets.asyncio.client import connect as ws_connect
except ImportError:  # pragma: no cover - older websockets
    from websockets import connect as ws_connect  # type: ignore

__version__ = "1.1.0"
log = logging.getLogger("nrsp-bridge")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Sample rates SDRconnect has been observed to accept and report back exactly
# (packet-net/pdn-soundmodem issue #701). Others may also work; the bridge
# tries the exact rate first and only falls back to these if it is refused.
KNOWN_NATIVE_RATES = [62_500, 125_000, 250_000, 500_000, 1_000_000,
                      2_000_000, 6_000_000, 8_000_000, 10_000_000]

# Gain table of an R820T tuner in tenths of a dB. rtl_tcp clients assume it,
# so we advertise an R820T and map its 0..49.6 dB range onto lna_state.
R820T_GAINS = [0, 9, 14, 27, 37, 77, 87, 125, 144, 157, 166, 197, 207, 229,
               254, 280, 297, 328, 338, 364, 372, 386, 402, 421, 434, 439,
               445, 480, 496]
RTLSDR_TUNER_R820T = 5

BIN_AUDIO, BIN_IQ, BIN_SPECTRUM = 1, 2, 3
BIN_IQ_SECONDARY = 5


# ---------------------------------------------------------------------------
# DSP helpers
# ---------------------------------------------------------------------------

class Resampler:
    """Streaming arbitrary-ratio resampler for complex IQ.

    Anti-alias FIR (windowed sinc, FFT overlap-save) followed by linear
    interpolation. The input is oversampled relative to the output whenever
    it is used (in_rate >= out_rate), so linear interpolation of the
    band-limited signal is accurate enough for receiving.
    """

    def __init__(self, in_rate: float, out_rate: float):
        self.in_rate = float(in_rate)
        self.out_rate = float(out_rate)
        self.step = self.in_rate / self.out_rate          # input samples per output sample
        cutoff = 0.5 * min(1.0, 1.0 / self.step) * 0.90   # cycles/sample at the input rate
        ntaps = int(min(511, max(31, 8 * self.step * 4)) // 2 * 2 + 1)
        n = np.arange(ntaps) - (ntaps - 1) / 2
        taps = 2 * cutoff * np.sinc(2 * cutoff * n) * np.blackman(ntaps)
        self.taps = (taps / taps.sum()).astype(np.complex128)
        self.hist = np.zeros(ntaps - 1, dtype=np.complex64)
        self._H: dict[int, np.ndarray] = {}
        self.prev = np.complex64(0)
        self.t = 0.0  # fractional read position, relative to self.prev

    def _filter(self, x: np.ndarray) -> np.ndarray:
        xf = np.concatenate((self.hist, x))
        L = 1 << int(math.ceil(math.log2(len(xf))))
        H = self._H.get(L)
        if H is None:
            H = np.fft.fft(self.taps, L)
            self._H = {L: H}
        y = np.fft.ifft(np.fft.fft(xf, L) * H)[len(self.taps) - 1:len(xf)]
        self.hist = xf[-(len(self.taps) - 1):]
        return y.astype(np.complex64)

    def process(self, x: np.ndarray) -> np.ndarray:
        if len(x) == 0:
            return x
        y = self._filter(x)
        yy = np.concatenate(([self.prev], y))
        last = len(yy) - 1
        if self.t >= last:
            self.t -= len(y)
            self.prev = yy[-1]
            return np.zeros(0, dtype=np.complex64)
        n = int(math.ceil((last - self.t) / self.step))
        pos = self.t + self.step * np.arange(n)
        i0 = np.floor(pos).astype(np.int64)
        frac = (pos - i0).astype(np.float32)
        i1 = np.minimum(i0 + 1, last)
        out = yy[i0] * (1 - frac) + yy[i1] * frac
        self.t = self.t + n * self.step - last
        self.prev = yy[-1]
        return out.astype(np.complex64)


class DigitalGain:
    """Gain applied before 8-bit quantisation.

    SDRconnect's int16 IQ typically sits around -40 dBFS, which would only use
    one or two of the 8 bits rtl_tcp clients receive. In auto mode the gain
    slowly tracks the signal peaks (99.9th percentile) to a target level, with
    hysteresis so it does not "pump". A fixed gain can be set instead.
    """

    def __init__(self, fixed_db: Optional[float], target: float = 0.35,
                 max_db: float = 60.0):
        self.fixed = fixed_db is not None
        self.db = fixed_db if fixed_db is not None else 30.0
        self.target = target
        self.max_db = max_db
        self._acc: list[float] = []
        self._last = time.monotonic()

    @property
    def linear(self) -> float:
        return 10 ** (self.db / 20)

    def observe(self, x: np.ndarray):
        if self.fixed or len(x) == 0:
            return
        sub = x[:: max(1, len(x) // 4096)]
        a = np.concatenate((np.abs(sub.real), np.abs(sub.imag)))
        self._acc.append(float(np.percentile(a, 99.9)))
        now = time.monotonic()
        if now - self._last < 0.5:
            return
        self._last = now
        peak = max(self._acc) if self._acc else 0.0
        self._acc.clear()
        if peak <= 1e-9:
            return
        want = 20 * math.log10(self.target / peak)
        err = want - self.db
        if abs(err) > 3.0:  # hysteresis
            self.db = float(np.clip(self.db + np.clip(err, -6, 6), 0.0, self.max_db))
            log.debug("auto digital gain -> %.1f dB", self.db)


def to_cu8(x: np.ndarray, gain: float) -> bytes:
    """complex float (full scale = 1.0) -> rtl-sdr unsigned 8-bit IQ."""
    out = np.empty(2 * len(x), dtype=np.float32)
    out[0::2] = x.real
    out[1::2] = x.imag
    out *= 127.5 * gain
    out += 127.5
    np.clip(out, 0, 255, out=out)
    return out.astype(np.uint8).tobytes()


def iq16_to_complex(raw: np.ndarray) -> np.ndarray:
    f = raw.astype(np.float32) * (1.0 / 32768.0)
    return f[0::2] + 1j * f[1::2]


# ---------------------------------------------------------------------------
# Upstream: SDRconnect WebSocket client
# ---------------------------------------------------------------------------

class SDRconnect:
    """Persistent connection to SDRconnect's WebSocket API with reconnect.

    Desired settings are remembered and re-applied after every reconnect.
    """

    def __init__(self, args, on_iq: Callable[[np.ndarray], None]):
        self.args = args
        self.url = ws_url(args.sdrconnect)
        self.device = "secondary" if args.secondary else "primary"
        self.iq_type = BIN_IQ_SECONDARY if args.secondary else BIN_IQ
        self.on_iq = on_iq
        self.ws = None
        self.connected = asyncio.Event()
        self.props: dict[str, str] = {}
        self.desired: dict[str, str] = {}
        self.iq_wanted = False
        self._waiters: dict[str, list[asyncio.Future]] = {}
        self._prop_events: dict[str, asyncio.Event] = {}
        self.frames = 0
        self.frame_types: dict[int, int] = {}
        self.raw_text: list[str] = []
        self.iq_enabled_at = 0.0
        self.samples = 0

    # -- messaging ---------------------------------------------------------
    async def _send(self, event_type: str, prop: str = "", value: str = "",
                    device: bool = False):
        msg = {"event_type": event_type, "property": prop, "value": value}
        if device:
            msg["device"] = self.device
        if self.ws is None:
            return
        try:
            await self.ws.send(json.dumps(msg))
            log.debug("-> %s", msg)
        except Exception as e:  # connection dropped; reconnect loop handles it
            log.debug("send failed: %s", e)

    async def set(self, prop: str, value, remember: bool = True):
        value = str(value)
        if remember:
            self.desired[prop] = value
        await self._send("set_property", prop, value, device=True)

    async def get(self, prop: str, timeout: float = 2.0) -> Optional[str]:
        if self.ws is None:
            return self.props.get(prop)
        fut = asyncio.get_running_loop().create_future()
        self._waiters.setdefault(prop, []).append(fut)
        await self._send("get_property", prop, "", device=True)
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            if fut in self._waiters.get(prop, []):
                self._waiters[prop].remove(fut)

    async def set_iq_stream(self, enable: bool):
        self.iq_wanted = enable
        if enable:
            self.iq_enabled_at = time.monotonic()
            asyncio.create_task(self._iq_watchdog(self.frames))
        await self._send("iq_stream_enable", "", "true" if enable else "false")

    async def _iq_watchdog(self, frames_before: int):
        """Explain loudly when IQ was asked for but none arrives."""
        await asyncio.sleep(4)
        if not self.iq_wanted or self.frames > frames_before or self.ws is None:
            return
        p = self.props
        other = {t: n for t, n in self.frame_types.items() if t != self.iq_type}
        log.warning(
            "NO IQ from SDRconnect 4 s after enabling it. started=%s can_control=%s device=%r "
            "rate=%s; other binary frames seen: %s. Check in SDRconnect that the nRSP-ST is "
            "selected and playing, in Full IQ network mode, and that nothing else (another "
            "client or the GUI) is holding it. 'python3 nrsp_bridge.py --probe' gives a full report.",
            p.get("started", "?"), p.get("can_control", "?"), p.get("active_device", "?"),
            p.get("device_sample_rate", "?"), other or "none")

    async def wait_for_value(self, prop: str, pred, timeout: float) -> Optional[str]:
        """Wait until property `prop` satisfies pred (from push or poll)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            v = await self.get(prop, timeout=min(1.0, max(0.05, deadline - time.monotonic())))
            if v is not None and pred(v):
                return v
            await asyncio.sleep(0.1)
        return self.props.get(prop)

    # -- receive -----------------------------------------------------------
    def _handle_text(self, text: str):
        if len(self.raw_text) < 20:
            self.raw_text.append(text)
        try:
            msg = json.loads(text)
        except ValueError:
            log.warning("non-JSON text from SDRconnect: %r", text[:200])
            return
        dev = str(msg.get("device") or "primary").strip().lower()
        if dev not in (self.device, "", "0" if self.device == "primary" else "1"):
            return
        et, prop, val = msg.get("event_type"), msg.get("property", ""), msg.get("value", "")
        if et in ("property_changed", "get_property_response") and prop:
            old = self.props.get(prop)
            self.props[prop] = val
            if et == "property_changed" and old != val and prop in (
                    "device_center_frequency", "device_sample_rate", "lna_state",
                    "active_antenna", "active_device", "started"):
                log.info("SDRconnect: %s = %s", prop, val)
            if et == "get_property_response":
                for fut in self._waiters.pop(prop, []):
                    if not fut.done():
                        fut.set_result(val)
            if prop == "overload" and val.lower() == "true":
                log.warning("SDRconnect reports ADC overload - reduce gain (raise lna_state)")

    def _handle_binary(self, data: bytes):
        if len(data) < 2:
            return
        (ptype,) = struct.unpack_from("<H", data, 0)
        self.frame_types[ptype] = self.frame_types.get(ptype, 0) + 1
        if ptype != self.iq_type:
            return
        n = (len(data) - 2) // 4 * 4
        if n <= 0:
            return
        iq = np.frombuffer(data, dtype="<i2", count=n // 2, offset=2)
        self.frames += 1
        self.samples += len(iq) // 2
        self.on_iq(iq)

    # -- lifecycle ---------------------------------------------------------
    async def _on_connect(self):
        a = self.args
        if a.device_serial:
            v = a.device_serial + (f":{a.network_mode}" if a.network_mode else "")
            await self._send("selected_device_serial", "", v)
        elif a.device_name:
            await self._send("selected_device_name", "", a.device_name)
        elif a.device_index is not None:
            await self._send("selected_device", "", str(a.device_index))
        if a.secondary:
            await self._send("set_secondary_device_enable", "", "true")

        for p in ("api_version", "valid_devices", "active_device", "can_control",
                  "lna_state_min", "lna_state_max", "valid_antennas",
                  "active_antenna", "device_sample_rate", "device_center_frequency",
                  "lna_state", "started"):
            await self.get(p, timeout=2.0)
        log.info("SDRconnect API %s, device '%s', antennas [%s], LNA %s..%s, can_control=%s",
                 self.props.get("api_version", "?"), self.props.get("active_device", "?"),
                 self.props.get("valid_antennas", "?"), self.props.get("lna_state_min", "?"),
                 self.props.get("lna_state_max", "?"), self.props.get("can_control", "?"))
        if self.props.get("can_control", "true").lower() == "false":
            log.warning("SDRconnect says can_control=false: tuning/gain requests will be ignored")

        await self._send("device_stream_enable", "", "true")
        for prop, val in list(self.desired.items()):
            await self._send("set_property", prop, val, device=True)
        if self.iq_wanted or a.always_stream:
            await self.set_iq_stream(True)

    async def run(self):
        backoff = 1.0
        while True:
            try:
                log.info("connecting to SDRconnect at %s", self.url)
                async with ws_connect(self.url, max_size=None, ping_interval=20,
                                      ping_timeout=20, open_timeout=10,
                                      compression=None) as ws:
                    self.ws = ws
                    backoff = 1.0
                    reader = asyncio.create_task(self._reader(ws))
                    await self._on_connect()
                    self.connected.set()
                    await reader
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("SDRconnect connection problem: %s", e)
            finally:
                self.ws = None
                self.connected.clear()
                for futs in self._waiters.values():
                    for f in futs:
                        if not f.done():
                            f.cancel()
                self._waiters.clear()
            log.info("reconnecting in %.0f s (is SDRconnect running with its WebSocket server switched on?)", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 5)

    async def _reader(self, ws):
        async for msg in ws:
            if isinstance(msg, (bytes, bytearray, memoryview)):
                self._handle_binary(bytes(msg))
            else:
                self._handle_text(msg)


# ---------------------------------------------------------------------------
# Downstream clients
# ---------------------------------------------------------------------------

class Client:
    _ids = 0

    def __init__(self, kind: str, reader, writer, maxq: int):
        Client._ids += 1
        self.id = Client._ids
        self.kind = kind
        self.reader = reader
        self.writer = writer
        self.q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=maxq)
        self.dropped = 0
        self.swap_iq = False
        peer = writer.get_extra_info("peername")
        self.peer = f"{peer[0]}:{peer[1]}" if peer else "?"

    def offer(self, data: bytes):
        try:
            self.q.put_nowait(data)
        except asyncio.QueueFull:
            # Slow client: drop the oldest block rather than stall everyone.
            try:
                self.q.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self.q.put_nowait(data)
            self.dropped += 1
            if self.dropped in (1, 10, 100) or self.dropped % 1000 == 0:
                log.warning("%s client %s too slow, %d blocks dropped (network too slow for this sample rate?)",
                            self.kind, self.peer, self.dropped)

    async def pump(self):
        while True:
            data = await self.q.get()
            self.writer.write(data)
            await self.writer.drain()


class Bridge:
    def __init__(self, args):
        self.args = args
        self.sdr = SDRconnect(args, self._on_iq)
        self.rtl_clients: list[Client] = []
        self.raw_clients: list[Client] = []
        self.controller: Optional[Client] = None
        self.gain = DigitalGain(args.digital_gain)

        self.client_rate = float(args.rate)  # what rtl_tcp clients are told / receive
        self.device_rate = float(args.rate)  # what SDRconnect actually delivers
        self.resampler: Optional[Resampler] = None
        self.rate_lock = asyncio.Lock()
        self.holding = False
        self._rate_target: Optional[float] = None
        self._rate_task: Optional[asyncio.Task] = None
        self._refused: set[float] = set()
        self._device_rate_for: dict[float, float] = {}
        self.gain_manual = True
        self._rx_count = 0
        self._rx_t0 = time.monotonic()

    # -- IQ path -------------------------------------------------------------
    def _on_iq(self, iq16: np.ndarray):
        if self.holding:
            return
        self._rx_count += len(iq16) // 2
        if self.raw_clients:
            if self.args.raw_format == "cs16":
                blob = iq16.astype("<i2", copy=False).tobytes()
            else:
                blob = ((iq16.astype(np.int32) >> 8) + 128).astype(np.uint8).tobytes()
            for c in self.raw_clients:
                c.offer(blob)
        if not self.rtl_clients:
            return
        x = iq16_to_complex(iq16)
        if self.resampler is not None:
            x = self.resampler.process(x)
        self.gain.observe(x)
        blob = to_cu8(x, self.gain.linear)
        swapped = None
        for c in self.rtl_clients:
            if c.swap_iq:
                if swapped is None:
                    swapped = np.frombuffer(blob, np.uint8).reshape(-1, 2)[:, ::-1].tobytes()
                c.offer(swapped)
            else:
                c.offer(blob)

    def _configure_resampler(self):
        if abs(self.device_rate - self.client_rate) / self.client_rate < 1e-6:
            self.resampler = None
            log.info("IQ: %.0f S/s straight through", self.client_rate)
        else:
            self.resampler = Resampler(self.device_rate, self.client_rate)
            log.info("IQ: SDRconnect delivers %.0f S/s, resampling to %.0f S/s for clients",
                     self.device_rate, self.client_rate)

    def request_rate(self, want: float):
        """Start a rate change without blocking the client's command stream
        (tune/gain commands that follow are applied immediately)."""
        self._rate_target = float(want)
        if self._rate_task is None or self._rate_task.done():
            self._rate_task = asyncio.create_task(self._rate_worker())

    async def _rate_worker(self):
        while self._rate_target is not None:
            want, self._rate_target = self._rate_target, None
            await self.apply_rate(want)

    async def apply_rate(self, want: float):
        """Ask SDRconnect for `want`; if refused, use the nearest known rate
        at or above it and resample down to exactly `want`. Samples are held
        back while the change is in flight so clients never get IQ at a rate
        they were not promised."""
        async with self.rate_lock:
            want = float(want)
            self.client_rate = want
            if not self.sdr.connected.is_set():
                self.sdr.desired["device_sample_rate"] = str(int(want))
                self.device_rate = want
                self._configure_resampler()
                return
            if (_close(self.sdr.props.get("device_sample_rate", "0"), self.device_rate)
                    and self._device_rate_for.get(want) == self.device_rate):
                self._configure_resampler()  # nothing to change on the radio
                return
            self.holding = True
            try:
                candidates = []
                if not self.args.native_rates_only and want not in self._refused:
                    candidates.append(want)
                higher = [r for r in KNOWN_NATIVE_RATES if r >= want]
                candidates += higher[:1] if higher else KNOWN_NATIVE_RATES[-1:]
                got = None
                for cand in dict.fromkeys(candidates):
                    if _close(self.sdr.props.get("device_sample_rate", "0"), cand):
                        got = cand
                        break
                    await self.sdr.set("device_sample_rate", int(cand))
                    v = await self.sdr.wait_for_value(
                        "device_sample_rate", lambda s, c=cand: _close(s, c),
                        timeout=0.6 if cand not in KNOWN_NATIVE_RATES else 2.0)
                    if v is not None and _close(v, cand):
                        got = float(v)
                        break
                    self._refused.add(cand)
                    log.info("SDRconnect did not take %.0f S/s (reports %s), will resample instead", cand, v)
                if got is None:
                    v = self.sdr.props.get("device_sample_rate")
                    got = float(v) if v else want
                    log.warning("using whatever SDRconnect runs at: %.0f S/s", got)
                if got < want:
                    log.warning("device rate %.0f is below the requested %.0f; samples will be upsampled",
                                got, want)
                self.device_rate = got
                self._device_rate_for[want] = got
                self._configure_resampler()
                # let frames already queued at the old rate drain past us
                await asyncio.sleep(0.1)
            finally:
                self.holding = False
            self._rx_count, self._rx_t0 = 0, time.monotonic()

    # -- control mapping ----------------------------------------------------
    def _lna_range(self) -> tuple[int, int]:
        try:
            lo = int(float(self.sdr.props.get("lna_state_min", "0")))
            hi = int(float(self.sdr.props.get("lna_state_max", "27")))
        except ValueError:
            lo, hi = 0, 27
        return lo, max(lo, hi)

    def gain_tenths_to_lna(self, tenths: int) -> int:
        # SDRplay lna_state is an attenuation index: min = most gain.
        lo, hi = self._lna_range()
        frac = min(max(tenths / R820T_GAINS[-1], 0.0), 1.0)
        return int(round(hi - frac * (hi - lo)))

    async def set_frequency(self, hz: int):
        hz = int(hz) + self.args.freq_offset
        if not 1_000 <= hz <= 2_000_000_000:
            log.warning("frequency %d Hz is outside 1 kHz - 2 GHz, ignored", hz)
            return
        await self.sdr.set("device_center_frequency", hz)
        if not self.args.no_vfo_sync:
            await self.sdr.set("device_vfo_frequency", hz)
        log.info("tune %.6f MHz", hz / 1e6)

    async def set_lna(self, lna: int):
        await self.sdr.set("lna_state", lna)
        log.info("lna_state %d (range %s..%s, lower = more gain)", lna, *self._lna_range())

    async def handle_rtl_command(self, c: Client, cmd: int, param: int):
        sparam = struct.unpack(">i", struct.pack(">I", param))[0]
        if self.args.control == "none" or (self.args.control == "first" and c is not self.controller):
            log.debug("ignoring command 0x%02x from listen-only client %s", cmd, c.peer)
            return
        if cmd == 0x01:
            await self.set_frequency(param)
        elif cmd == 0x02:
            if param > 0:
                log.info("client %s asks for %d S/s", c.peer, param)
                self.request_rate(param)
        elif cmd == 0x03:
            self.gain_manual = param == 1
            log.info("gain mode %s", "manual" if self.gain_manual else "auto")
            if not self.gain_manual and self.args.auto_lna is not None:
                await self.set_lna(self.args.auto_lna)
        elif cmd == 0x04:
            if self.gain_manual or not self.args.honour_auto:
                await self.set_lna(self.gain_tenths_to_lna(sparam))
        elif cmd == 0x0D:
            idx = min(max(param, 0), len(R820T_GAINS) - 1)
            await self.set_lna(self.gain_tenths_to_lna(R820T_GAINS[idx]))
        elif cmd == 0x05:
            log.info("frequency correction %d ppm ignored (nRSP-ST has a 0.5 ppm TCXO)", sparam)
        elif cmd == 0x0E:
            log.warning("bias-T request ignored: not available through the SDRconnect API "
                        "(enable it in SDRconnect for antenna B)")
        elif cmd == 0x09 and param:
            log.info("direct sampling request ignored (the nRSP-ST covers HF natively)")
        elif cmd in (0x06, 0x07, 0x08, 0x0A, 0x0B, 0x0C):
            log.debug("rtl_tcp command 0x%02x (%d) has no SDRconnect equivalent, ignored", cmd, param)
        else:
            log.debug("unknown rtl_tcp command 0x%02x %d", cmd, param)

    # -- servers --------------------------------------------------------------
    async def _update_streaming(self):
        want = bool(self.rtl_clients or self.raw_clients) or self.args.always_stream
        if want != self.sdr.iq_wanted:
            await self.sdr.set_iq_stream(want)
            log.info("IQ stream from SDRconnect %s", "enabled" if want else "paused (no clients)")

    async def soapy_session(self, reader, writer):
        await self.rtl_session(reader, writer, swap_iq=True)

    async def rtl_session(self, reader, writer, swap_iq: bool = False):
        c = Client("rtl_tcp/soapy" if swap_iq else "rtl_tcp", reader, writer, self.args.queue_blocks)
        c.swap_iq = swap_iq
        if self.args.control == "first" and self.controller is None:
            self.controller = c
        role = "control" if (self.args.control == "all" or c is self.controller) else "listen-only"
        log.info("rtl_tcp client %s connected (%s)", c.peer, role)
        if not self.sdr.connected.is_set():
            log.warning("...but the bridge is NOT connected to SDRconnect at %s, so there will be no "
                        "samples and tuning/gain go nowhere until it is. Is SDRconnect running with its "
                        "WebSocket server switched on, and is -s the right address?", self.sdr.url)
        writer.write(b"RTL0" + struct.pack(">II", RTLSDR_TUNER_R820T, len(R820T_GAINS)))
        self.rtl_clients.append(c)
        await self._update_streaming()
        pump = asyncio.create_task(c.pump())
        try:
            while True:
                pkt = await reader.readexactly(5)
                cmd, param = struct.unpack(">BI", pkt)
                await self.handle_rtl_command(c, cmd, param)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            pump.cancel()
            self.rtl_clients.remove(c)
            if self.controller is c:
                self.controller = self.rtl_clients[0] if self.rtl_clients else None
            writer.close()
            log.info("rtl_tcp client %s disconnected", c.peer)
            await self._update_streaming()

    async def raw_session(self, reader, writer):
        c = Client("raw", reader, writer, self.args.queue_blocks)
        log.info("raw IQ client %s connected (%s at %.0f S/s, %s)", c.peer, self.args.raw_format,
                 self.device_rate, "device rate - set by rtl_tcp clients or --rate")
        self.raw_clients.append(c)
        await self._update_streaming()
        pump = asyncio.create_task(c.pump())
        try:
            while await reader.read(4096):
                pass
        except ConnectionError:
            pass
        finally:
            pump.cancel()
            self.raw_clients.remove(c)
            writer.close()
            log.info("raw IQ client %s disconnected", c.peer)
            await self._update_streaming()

    async def stats(self):
        while True:
            await asyncio.sleep(self.args.stats)
            dt = time.monotonic() - self._rx_t0
            rate = self._rx_count / dt if dt > 0 else 0
            up = "connected" if self.sdr.connected.is_set() else "NOT CONNECTED"
            msg = (f"stats: SDRconnect {up}, frames by type {self.sdr.frame_types or '{}'}, "
                   f"in {rate:,.0f} S/s (expected {self.device_rate:,.0f}), "
                   f"rtl_tcp clients {len(self.rtl_clients)}, raw clients {len(self.raw_clients)}, "
                   f"digital gain {self.gain.db:.1f} dB")
            if self.sdr.iq_wanted and dt > 3 and rate < 0.9 * self.device_rate:
                msg += "  <-- IQ arriving slower than expected (network too slow, or nRSP-ST not in Full IQ mode?)"
            log.info(msg)
            self._rx_count, self._rx_t0 = 0, time.monotonic()

    async def run(self):
        a = self.args
        # Initial settings, applied on (re)connect.
        if a.antenna:
            self.sdr.desired["active_antenna"] = a.antenna
        if a.lna is not None:
            self.sdr.desired["lna_state"] = str(a.lna)
        if a.freq:
            self.sdr.desired["device_center_frequency"] = str(int(a.freq))
            if not a.no_vfo_sync:
                self.sdr.desired["device_vfo_frequency"] = str(int(a.freq))
        self.sdr.desired["device_sample_rate"] = str(int(a.rate))
        self._configure_resampler()

        tasks = [asyncio.create_task(self.sdr.run())]
        servers = []
        if a.rtl_port:
            servers.append(await asyncio.start_server(self.rtl_session, a.bind, a.rtl_port))
            log.info("rtl_tcp server listening on %s:%d", a.bind, a.rtl_port)
        if a.soapy_port:
            servers.append(await asyncio.start_server(self.soapy_session, a.bind, a.soapy_port))
            log.info("rtl_tcp for SoapyRTLTCP (I/Q pre-swapped) listening on %s:%d", a.bind, a.soapy_port)
        if a.raw_port:
            servers.append(await asyncio.start_server(self.raw_session, a.bind, a.raw_port))
            log.info("raw %s IQ server listening on %s:%d", a.raw_format, a.bind, a.raw_port)
        if a.stats:
            tasks.append(asyncio.create_task(self.stats()))

        # Once connected, confirm the start-up sample rate is really in effect.
        async def first_rate():
            await self.sdr.connected.wait()
            await self.apply_rate(a.rate)
        tasks.append(asyncio.create_task(first_rate()))
        try:
            await asyncio.gather(*tasks)
        finally:
            for s in servers:
                s.close()


def ws_url(s: str) -> str:
    from urllib.parse import urlsplit, urlunsplit
    if "://" not in s:
        s = "ws://" + s
    u = urlsplit(s)
    netloc = u.netloc if u.port else f"{u.hostname if ':' not in (u.hostname or '') else '[' + u.hostname + ']'}:5454"
    return urlunsplit((u.scheme, netloc, u.path or "/", u.query, ""))


def _close(v: str, target: float) -> bool:
    try:
        return abs(float(v) - target) <= max(1.0, target * 1e-6)
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_freq(s: str) -> float:
    s = s.strip().lower().replace("hz", "")
    mult = {"k": 1e3, "m": 1e6, "g": 1e9}
    if s and s[-1] in mult:
        return float(s[:-1]) * mult[s[-1]]
    return float(s)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Bridge SDRconnect's WebSocket API (SDRplay nRSP-ST etc.) to rtl_tcp and raw IQ TCP.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    g = p.add_argument_group("upstream (SDRconnect)")
    g.add_argument("-s", "--sdrconnect", default="127.0.0.1:5454",
                   help="SDRconnect WebSocket host[:port] or ws:// URL")
    g.add_argument("--device-serial", help="select device by serial (e.g. the nRSP-ST serial)")
    g.add_argument("--network-mode", default="Full IQ", choices=["Full IQ", "IQ Lite", "Compact"],
                   help="nRSP-ST network mode used with --device-serial")
    g.add_argument("--device-name", help="select device by SDRconnect display name")
    g.add_argument("--device-index", type=int, help="select device by index")
    g.add_argument("--secondary", action="store_true", help="use the secondary tuner (RSPduo)")
    g.add_argument("--antenna", help="antenna to select, as named in valid_antennas (e.g. 'Antenna B')")
    g.add_argument("--always-stream", action="store_true",
                   help="keep IQ flowing even with no clients (default: pause to save bandwidth)")

    g = p.add_argument_group("initial radio settings")
    g.add_argument("-f", "--freq", type=parse_freq, help="initial centre frequency, e.g. 100.1M")
    g.add_argument("-r", "--rate", type=parse_freq, default=2_000_000,
                   help="initial sample rate (rtl_tcp clients normally choose their own)")
    g.add_argument("--lna", type=int, help="initial lna_state (0 = max gain)")
    g.add_argument("--auto-lna", type=int,
                   help="lna_state to use when a client selects automatic gain (default: leave as is)")
    g.add_argument("--freq-offset", type=int, default=0, help="Hz added to every tune request (upconverter etc.)")
    g.add_argument("--native-rates-only", action="store_true",
                   help="never ask SDRconnect for non-standard rates; always resample from the nearest known rate")
    g.add_argument("--no-vfo-sync", action="store_true",
                   help="do not move SDRconnect's VFO along with the centre frequency")

    g = p.add_argument_group("downstream servers")
    g.add_argument("-b", "--bind", default="0.0.0.0", help="address to listen on")
    g.add_argument("-p", "--rtl-port", type=int, default=1234, help="rtl_tcp port (0 = off)")
    g.add_argument("--soapy-port", type=int, default=1236,
                   help="rtl_tcp port with I/Q pre-swapped to cancel SoapyRTLTCP's swap bug on "
                        "little-endian Linux/macOS when apps read CF32/CS16 (0 = off)")
    g.add_argument("--raw-port", type=int, default=1235, help="raw IQ port (0 = off)")
    g.add_argument("--raw-format", choices=["cs16", "cu8"], default="cs16", help="raw port sample format")
    g.add_argument("--control", choices=["all", "first", "none"], default="all",
                   help="which rtl_tcp clients may tune/change gain")
    g.add_argument("--honour-auto", action="store_true",
                   help="ignore gain values while a client has selected automatic gain")
    g.add_argument("--digital-gain", type=float,
                   help="fixed gain in dB before 8-bit conversion (default: automatic)")
    g.add_argument("--queue-blocks", type=int, default=64, help="per-client buffer depth in blocks")

    g = p.add_argument_group("misc")
    g.add_argument("--stats", type=float, default=10, help="seconds between status lines (0 = off)")
    g.add_argument("--probe", action="store_true",
                   help="check SDRconnect: print its properties, stream IQ for 5 s, report and exit")
    g.add_argument("-v", "--verbose", action="count", default=0)
    g.add_argument("--version", action="version", version=__version__)
    return p


async def probe(args) -> int:
    """Stand-alone health check of the SDRconnect side."""
    sdr = SDRconnect(args, lambda iq: None)
    task = asyncio.create_task(sdr.run())
    print(f"connecting to {sdr.url} ...")
    try:
        await asyncio.wait_for(sdr.connected.wait(), 8)
    except asyncio.TimeoutError:
        print("FAIL: could not connect. Is SDRconnect running, is its WebSocket server switched on "
              "(Settings), is the address/port right, and is the firewall open for port 5454?")
        task.cancel()
        return 1
    print("OK: connected\n\nproperties:")
    for prop in ("api_version", "valid_devices", "active_device", "started", "can_control",
                 "device_center_frequency", "device_sample_rate", "lna_state", "lna_state_min",
                 "lna_state_max", "valid_antennas", "active_antenna", "overload"):
        v = await sdr.get(prop, timeout=2.0)
        print(f"  {prop:24s} {v if v is not None else '(no answer)'}")
    raw = [m for m in sdr.raw_text[:8]]
    if raw:
        print("\nfirst messages received from SDRconnect (raw):")
        for m in raw:
            print("  " + m[:200])
    print("\nenabling IQ for 5 seconds ...")
    await sdr.set_iq_stream(True)
    sdr.frame_types.clear()
    s0, t0 = sdr.samples, time.monotonic()
    await asyncio.sleep(5)
    rate = (sdr.samples - s0) / (time.monotonic() - t0)
    await sdr.set_iq_stream(False)
    print(f"  binary frames by type: {sdr.frame_types or 'none'}  (2 = IQ, 1 = audio, 3 = spectrum)")
    print(f"  IQ rate: {rate:,.0f} S/s (SDRconnect says {sdr.props.get('device_sample_rate')})")
    ok = rate > 0
    if not ok:
        print("FAIL: no IQ. In SDRconnect make sure the nRSP-ST is selected and PLAYING (press play), "
              "in Full IQ network mode, and that can_control above is true.")
    elif sdr.props.get("device_sample_rate") and rate < 0.9 * float(sdr.props["device_sample_rate"]):
        print("WARN: IQ slower than the sample rate - network too slow or not in Full IQ mode")
    else:
        print("OK: IQ is flowing - the bridge should work")
    task.cancel()
    return 0 if ok else 2


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.probe:
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
        return asyncio.run(probe(args))
    logging.basicConfig(level=logging.DEBUG if args.verbose > 1 else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    if args.verbose == 1:
        log.setLevel(logging.DEBUG)
        logging.getLogger("websockets").setLevel(logging.INFO)
    log.info("SDRBridge %s (nrsp_bridge.py)", __version__)
    bridge = Bridge(args)

    async def runner():
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, task.cancel)
            except (NotImplementedError, RuntimeError):  # Windows
                pass
        try:
            await bridge.run()
        except asyncio.CancelledError:
            log.info("stopped")

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
