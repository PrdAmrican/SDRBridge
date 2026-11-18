# Contributing to SDRBridge

Thanks for taking an interest! SDRBridge started as a way to get an SDRplay
nRSP-ST into SDR++, SDR Console and SoapySDR, and it gets better every time
someone tries it with a new radio, a new program or a new idea.

## Please share what you build

The licence (AGPL v3) doesn't make you send your changes back, and nobody
is going to chase you. But I'd really like to see what you've done with it,
even if it's rough, half-finished or only useful to you. Any of these is
great:

- **Open a pull request** with your change.
- **Open an issue** titled "Show and tell: …" describing what you built, with
  a link to your fork or a screenshot.
- **Just fork it publicly on GitHub.** Forks show up on the repository page,
  so others can find your version too.

If your version runs on the internet, a link to it in an issue is welcome.

## Ways to help without writing code

- **Test it on real hardware.** The bridge was built from SDRplay's published
  API and a simulator, then verified with one nRSP-ST. Reports from other
  setups (RSP1B, RSPdx, RSPduo, SDRconnect Headless on a Pi, Linux, macOS)
  are valuable. Please include:
  - the output of `python nrsp_bridge.py --probe`
  - your SDRconnect version, OS and the client program you used
  - what worked and what didn't
- **Try it with other programs** (GQRX, rtl_433, OpenWebRX, CubicSDR,
  SDRangel, GNU Radio…) and tell us which settings worked, so they can go
  in the README.
- **Report bugs** with the bridge's log, run with `-v` for more detail.

## Making a change

1. Fork the repository and create a branch for your change.
2. Set up and run the tests. No radio is needed; they use a simulated
   SDRconnect:
   ```
   pip install numpy websockets
   python tests/test_bridge.py
   ```
   All checks must pass. For changes to the IQ path, rates or the rtl_tcp
   protocol, please add a check to `tests/test_bridge.py`.
3. To try the bridge by hand without hardware:
   ```
   python tools/fake_sdrconnect.py --port 5454
   python nrsp_bridge.py
   ```
   Then connect SDR++ (RTL-TCP, 127.0.0.1:1234). The simulator has a carrier
   at 100.1 MHz.
4. Note your change under `## [Unreleased]` in `CHANGELOG.md`.
5. Add your name to `AUTHORS.md` if you'd like credit.
6. Open a pull request saying what you changed and why, and how you tested
   it (simulator, real radio, which client).

### Style

- Python 3.9+, standard library plus `numpy` and `websockets`. Please ask
  in an issue before adding a dependency.
- Keep `nrsp_bridge.py` a single file that runs on its own. That's what
  makes it easy to copy onto a Pi or a Windows PC.
- Log messages are for radio users, not programmers: say what went wrong
  and what to do about it.
- New source files start with the licence header:
  ```
  # SDRBridge - Copyright (C) <year> <your name> and SDRBridge contributors
  # SPDX-License-Identifier: AGPL-3.0-only
  ```
- Windows `.bat` files keep CRLF line endings (`.gitattributes` handles
  this).

## Licensing of contributions

SDRBridge is offered with no warranty (see `DISCLAIMER.md`), and that
covers your contributions too.

SDRBridge is licensed under the GNU Affero General Public License v3
(`AGPL-3.0-only`, see `LICENSE`). By contributing, you agree that your
contribution is licensed under the same terms.

You keep the copyright on what you write. The original author's notice
stays on the original code, and yours goes on yours (in `AUTHORS.md`, and
in a file's header if you wrote most of it).

If you publish a changed version, or run one that other people use over a
network, the AGPL asks you to keep the copyright notices, keep it under
AGPL v3, and let those people get your source. Updating `SOURCE_URL` in
`nrsp_bridge.py` to point at your source is the easy way to do that last
part.

## Releases (maintainers)

Releases follow semantic versioning. With the changes listed under
`[Unreleased]` in `CHANGELOG.md`:

```
python tools/release.py patch --push    # or minor / major / X.Y.Z
```

This runs the tests, bumps `__version__`, moves the changelog notes under
the new version, commits, tags `vX.Y.Z` and pushes.

## Be kind

Be patient and friendly. Radio is a hobby, and many people asking questions
are new to SDR, Python or Git. Treat everyone the way you'd want to be
treated on a busy net.
