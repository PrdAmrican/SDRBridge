# Disclaimer: use at your own risk

SDRBridge is a free, hobby project. It is provided **"AS IS", WITHOUT
WARRANTY OF ANY KIND**, express or implied, including but not limited to the
warranties of merchantability, fitness for a particular purpose and
non-infringement. **You use it entirely at your own risk.**

To the fullest extent permitted by law, the authors and contributors are
**not liable** for any damage or loss of any kind arising from using or not
being able to use this software. That includes damage to radios, antennas,
computers or other equipment, lost data, lost recordings, missed signals,
network or security problems, and legal consequences. The full legal terms
are sections 15 and 16 of the GNU Affero General Public License v3 (see
`LICENSE`); this page explains what they mean in practice and does not
replace them.

## What that means in practice

**Your radio.** SDRBridge only sends ordinary settings (frequency, sample
rate, gain, antenna) through SDRconnect's published API. It does not change
firmware or protect your receiver. The nRSP-ST, like other SDRplay radios,
can be permanently damaged by strong RF (its stated maximum input is 0 dBm
continuous) and by lightning or static. Follow SDRplay's own warnings about
nearby transmitters, antenna disconnection and thunderstorms.

**Your network and security.** The rtl_tcp and raw IQ ports have **no
password and no encryption**. Anyone who can reach them can listen to your
radio, retune it and use your bandwidth. Keep them on your own network or a
VPN. Exposing them to the internet, for example with port forwarding, is at
your own risk.

**Bandwidth and data costs.** Full IQ streams use tens of megabits per
second. If the nRSP-ST, SDRconnect or the bridge are on a metered, mobile or
shared connection, you are responsible for any usage costs or slowdowns.

**The law.** What you may listen to, decode, record or share differs from
country to country. Some transmissions (for example mobile phone, private,
encrypted or emergency-service traffic) may be illegal to receive, decode or
disclose where you live. Knowing and following those laws, and any licence
conditions for your station, is your responsibility.

**Accuracy and reliability.** Frequencies, levels, sample rates and
timestamps passed on by SDRBridge may be wrong, delayed or interrupted.
Don't rely on it for safety-of-life, navigation, emergency or other critical
purposes.

**Third-party software and hardware.** SDRBridge depends on SDRplay's
SDRconnect, its WebSocket API and the client programs you connect, none of
which we control. Updates to any of them may change or break how SDRBridge
works without notice.

**Not affiliated with SDRplay.** SDRplay, SDRconnect and nRSP-ST are
trademarks of SDRplay Ltd. SDRBridge is an independent project, not made,
endorsed or supported by SDRplay Ltd. Please don't contact SDRplay support
about SDRBridge problems; open an issue on the SDRBridge repository instead.
Using SDRBridge does not change the terms of your SDRplay hardware warranty
or software licence, which you should read for yourself.

**No support obligation.** Help is offered on a best-effort basis by
volunteers, when they can. Nobody is obliged to fix bugs, answer questions
or keep the project going.

If you do not accept these terms, do not use SDRBridge.
