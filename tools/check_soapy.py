#!/usr/bin/env python3
# SDRBridge - Copyright (C) 2026 PrdAmrican and SDRBridge contributors
# SPDX-License-Identifier: AGPL-3.0-only
"""
check_soapy.py - check that SoapySDR + SoapyRTLTCP work with SDRBridge, and
which bridge port gives the correct (not mirrored) spectrum on THIS machine.

No radio and no SDRconnect needed: it starts the bundled SDRconnect
simulator (a carrier at 100.1 MHz) and a private bridge on spare ports,
then reads samples through SoapySDR from both rtl_tcp ports.

    python tools/check_soapy.py

Needs SoapySDR's Python bindings (included in radioconda) and numpy.
"""
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WS, RTL, SOAPY = 25454, 21234, 21236
RATE, TUNE, TONE = 1_000_000, 100.05e6, 100.1e6  # carrier should be at +50 kHz


def wait_port(port, timeout=15):
    t = time.time() + timeout
    while time.time() < t:
        try:
            socket.create_connection(("127.0.0.1", port), 0.5).close()
            return True
        except OSError:
            time.sleep(0.2)
    return False


def measure(SoapySDR, np, port):
    dev = SoapySDR.Device(f"driver=rtltcp,rtltcp=127.0.0.1:{port}")
    dev.setSampleRate(SoapySDR.SOAPY_SDR_RX, 0, RATE)
    dev.setFrequency(SoapySDR.SOAPY_SDR_RX, 0, TUNE)
    st = dev.setupStream(SoapySDR.SOAPY_SDR_RX, SoapySDR.SOAPY_SDR_CF32)
    dev.activateStream(st)
    buf = np.zeros(16384, np.complex64)
    got, need, t_end = [], int(RATE * 2.5), time.time() + 15
    while sum(map(len, got)) < need and time.time() < t_end:
        r = dev.readStream(st, [buf], len(buf), timeoutUs=1_000_000)
        if r.ret > 0:
            got.append(buf[:r.ret].copy())
    dev.deactivateStream(st)
    dev.closeStream(st)
    del dev
    x = np.concatenate(got)[int(RATE * 1.0):]  # skip start-up / settling
    if len(x) < 65536:
        return None
    n = 1 << 16
    spec = np.abs(np.fft.fftshift(np.fft.fft(x[:n] * np.hanning(n))))
    return (np.argmax(spec) - n / 2) * RATE / n


def main():
    try:
        import numpy as np
        import SoapySDR
    except ImportError as e:
        sys.exit(f"FAIL: cannot import {e.name}. Run this from the Python that has SoapySDR "
                 "(e.g. the radioconda prompt).")
    print(f"SoapySDR {SoapySDR.getAPIVersion()} from {os.path.dirname(SoapySDR.__file__)}")
    mods = SoapySDR.listModules()
    rt = [m for m in mods if "rtltcp" in os.path.basename(m).lower()]
    if not rt:
        print("FAIL: SoapyRTLTCP module not found. Modules SoapySDR loaded:")
        for m in mods:
            print("   ", m)
        print(f"It should be installed in: {SoapySDR.getRootPath()}/lib/SoapySDR/modules{SoapySDR.getABIVersion()}")
        return 1
    print(f"SoapyRTLTCP module: {rt[0]}")

    py = sys.executable
    procs = [subprocess.Popen([py, os.path.join(HERE, "fake_sdrconnect.py"), "--port", str(WS),
                               "--tone", str(TONE)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
    try:
        if not wait_port(WS):
            print("FAIL: the simulator did not start")
            return 1
        procs.append(subprocess.Popen(
            [py, os.path.join(ROOT, "nrsp_bridge.py"), "-s", f"127.0.0.1:{WS}", "-b", "127.0.0.1",
             "-p", str(RTL), "--soapy-port", str(SOAPY), "--raw-port", "0", "--stats", "0"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        if not (wait_port(RTL) and wait_port(SOAPY)):
            print("FAIL: the bridge did not start (python nrsp_bridge.py --help to check it runs)")
            return 1
        time.sleep(1.5)
        results = {}
        for name, port, normal in (("rtl_tcp port (normally 1234)", RTL, 1234),
                                   ("SoapySDR port (normally 1236)", SOAPY, 1236)):
            try:
                off = measure(SoapySDR, np, port)
            except Exception as e:
                print(f"  {name}: FAIL - {e}")
                continue
            if off is None:
                print(f"  {name}: FAIL - no samples")
                continue
            ok = abs(off - 50e3) < 3e3
            mirrored = abs(off + 50e3) < 3e3
            verdict = "CORRECT" if ok else ("MIRRORED (I/Q swapped)" if mirrored else "unexpected")
            print(f"  {name}: carrier at {off / 1e3:+.1f} kHz (expected +50.0) -> {verdict}")
            results[normal] = ok
        good = [p for p, ok in results.items() if ok]
        print()
        if good:
            print(f"RESULT: SoapySDR works. In Soapy apps on this machine use port {good[0]}:")
            print(f"    driver=rtltcp,rtltcp=127.0.0.1:{good[0]}")
            if good[0] == 1236:
                print("(This SoapyRTLTCP build has the I/Q swap bug; port 1236 cancels it out.)")
            return 0
        print("RESULT: no port gave a correct spectrum - please report this output.")
        return 2
    finally:
        for p in reversed(procs):
            p.terminate()
        for p in procs:
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    sys.exit(main())
