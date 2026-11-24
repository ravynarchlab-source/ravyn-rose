#!/usr/bin/env python3
"""rice react: the one audio analyser every panel listens to.

It records what the default output plays (PipeWire, through `pw-record` on the sink's monitor; it
never opens a microphone), and works out, 125 times a second:

    bass, mids, highs, loudness   0..1, each with a fast attack and a slow release
    beat onsets                   when a kick or snare lands, and how strong it was
    BPM                           from the last eight seconds of onsets
    32 spectrum bars              for the deck's spectrum strip (62 times a second)

Nothing is sent anywhere and no audio is kept: only these numbers are written, into a small ring of
the last 2 s in $XDG_RUNTIME_DIR/rice/react.ring (memory-mapped, see react/client.py). Every entry
carries the moment it should be SHOWN. That moment is the time the sound entered the audio graph plus
the output's own delay (Bluetooth earbuds play 0.1-0.3 s late, speakers a few ms), minus the time a
panel needs to get a frame on screen. So the deck, the spectrum and the lyrics bar read the same
clock and move together, with the sound you hear rather than ahead of it.

    analyser.py                 run (started by `rice on` unless `rice react off`)
    analyser.py --input F.raw   analyse a raw file (s16 mono 48 kHz) at real-time pace instead (tests)
    analyser.py --selftest      feed synthetic kicks, print how late onsets are detected, and exit
"""
import json, mmap, os, re, signal, struct, subprocess, sys, threading, time
from pathlib import Path

import numpy as np

RATE, HOP, WIN, BARWIN, NBARS, NSLOTS = 48000, 384, 1024, 2048, 32, 256
HOP_S = HOP / RATE                                # 8 ms: fine enough for a 16.7 ms frame, light enough to run all day
PER_S = round(1 / HOP_S)                          # hops per second
RUN = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "rice"
RING = RUN / "react.ring"
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "rice"
MAGIC = b"RICEREA1"
HEADER = struct.Struct("<8sIIIIQdddd")            # magic, version, slots, slot size, bars, written, hop s,
SLOT = struct.Struct("<d8fddII%df" % NBARS)       # output delay s, applied delay s, heartbeat (monotonic)
assert HEADER.size == 64
# What a panel needs between "this entry is due" and light leaving the screen: the wait for its next
# frame, the compositor's frame, and the detector's own reaction time. Measured on the test laptop
# (tools/react_bench.py); `rice react offset MS` shifts it if your screen or earbuds differ.
PIPELINE_S = 0.033
SILENCE = 2e-4                                    # RMS below this (about -74 dBFS) is treated as nothing playing


def sink_delay():
    """Seconds between a sample entering the default sink and being heard, as PipeWire reports it."""
    try:
        head = subprocess.run(["wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"], capture_output=True, text=True, timeout=3).stdout
        m = re.match(r"\s*id (\d+)", head)
        if not m:
            return None
        node = json.loads(subprocess.run(["pw-dump", m.group(1)], capture_output=True, text=True, timeout=3).stdout)[0]
        info = node["info"]
        quantum = 1024 / RATE
        total = 0.0
        for lat in info.get("params", {}).get("Latency", []):
            if lat.get("direction") == "Input":   # what lies behind the sink's input: the device
                rate = lat.get("maxRate", 0) / RATE
                total = max(total, lat.get("maxNs", 0) / 1e9 + rate + lat.get("maxQuantum", 0) * quantum)
        for lat in info.get("params", {}).get("ProcessLatency", []):
            total += lat.get("ns", 0) / 1e9 + lat.get("rate", 0) / RATE + lat.get("quantum", 0) * quantum
        return min(total, 1.0)
    except Exception:
        return None


def user_offset():
    try:
        return max(-0.5, min(0.5, float((CONFIG / "react-offset").read_text().strip()) / 1000.0))
    except Exception:
        return 0.0


class Analyser:
    def __init__(self, ring_path=RING):
        self.buf = np.zeros(BARWIN, np.float32)
        # the newest samples count fully: a long rise and a short fall, so a kick shows within a hop or two
        rise, fall = WIN - 96, 96
        self.win = np.concatenate([np.hanning(2 * rise)[:rise], np.hanning(2 * fall)[fall:]]).astype(np.float32)
        self.barwin = np.hanning(BARWIN).astype(np.float32)
        freqs = np.fft.rfftfreq(WIN, 1 / RATE)
        bass, mids = (freqs >= 35) & (freqs < 190), (freqs >= 190) & (freqs < 2000)
        highs, flux = (freqs >= 2000) & (freqs < 12000), (freqs >= 190) & (freqs < 8000)
        # one matrix product gives the mean power of the three bands (rows scaled so each lands near 0..1)
        self.bands = np.stack([bass / bass.sum() * 9.0, mids / mids.sum() * 64.0, highs / highs.sum() * 576.0]).astype(np.float32)
        self.nflux = int(np.nonzero(flux)[0][-1]) + 1                         # onset strength only looks below 8 kHz
        self.wflux = (bass * 1.0 + flux * 0.035).astype(np.float32)[:self.nflux]   # lows count most
        edges = np.geomspace(45, 14000, NBARS + 1) / (RATE / BARWIN)        # bar edges in (fractional) bins
        self.bar_pos = np.array([np.linspace(edges[i], edges[i + 1], 4) for i in range(NBARS)]).ravel()
        self.bar_bins = np.arange(BARWIN // 2 + 1)
        self.bar_tilt = np.linspace(1.0, 3.2, NBARS).astype(np.float32)      # music has far less energy up high
        self.prev_lm = np.zeros(self.nflux, np.float32)
        self.peak = np.full(4, 1e-3, np.float32)                             # slow peaks: bass, mids, highs, loud
        self.level = np.zeros(4, np.float32)
        self.raw = np.zeros(4, np.float32)
        att = np.array([0.006, 0.010, 0.006, 0.030]); rel = np.array([0.160, 0.200, 0.120, 0.420])
        self.k_att = (1 - np.exp(-HOP_S / att)).astype(np.float32)
        self.k_rel = (1 - np.exp(-HOP_S / rel)).astype(np.float32)
        self.bars = [0.0] * NBARS
        self.bars_np = np.zeros(NBARS, np.float32)
        self.bar_peak = 1e-3
        self.bar_every = max(1, round(0.016 / HOP_S))                        # spectrum bars: 62 times a second
        self.hist_n = 8 * PER_S
        self.odf_hist = np.zeros(self.hist_n, np.float32)                    # eight seconds of onset strength
        self.odf_n = 0
        self.odf_peak = 1e-3
        self.recent = np.zeros(round(0.35 / HOP_S), np.float32)              # for the moving threshold
        self.recent_sum = 0.0
        self.refractory = round(0.107 / HOP_S)                               # at most one onset per 107 ms
        self.hops = 0
        self.last_onset_hop = self.last_strong_hop = -100000
        self.beat_n = self.strong_n = 0
        self.beat_t = self.strong_t = 0.0
        self.beat_s = 0.0
        self.bpm, self.conf, self.bpm_cand, self.bpm_votes = 0.0, 0.0, 0.0, 0
        self.quiet_hops = 0
        self.delay = 0.0
        self.out_delay = 0.0
        self.written = 0
        self.onsets = []                                                     # (hop index, strong?) for the self-test
        ring_path.parent.mkdir(parents=True, exist_ok=True)
        size = HEADER.size + NSLOTS * SLOT.size
        tmp = ring_path.with_suffix(".tmp")
        with open(tmp, "wb") as f:
            f.write(b"\0" * size)
        os.chmod(tmp, 0o600)
        self.fd = os.open(tmp, os.O_RDWR)
        self.mm = mmap.mmap(self.fd, size)
        self._header()
        tmp.replace(ring_path)                                               # readers only ever see a complete file

    def _header(self):
        HEADER.pack_into(self.mm, 0, MAGIC, 1, NSLOTS, SLOT.size, NBARS, self.written, HOP_S,
                         self.out_delay, self.delay, time.monotonic())

    # ------------------------------------------------------------------ one hop
    def hop(self, samples, t_hop):
        """samples: float32[HOP] in -1..1; t_hop: monotonic time at which the last of them entered the graph."""
        buf = self.buf
        buf[:-HOP] = buf[HOP:]
        buf[-HOP:] = samples
        self.hops += 1
        rms = float(np.sqrt(samples.dot(samples) / HOP))
        silent = rms < SILENCE
        self.quiet_hops = self.quiet_hops + 1 if silent else 0
        mag = np.abs(np.fft.rfft(buf[-WIN:] * self.win)) * (2.0 / WIN)
        raw = self.raw
        np.sqrt(self.bands.dot(mag * mag), out=raw[:3])
        raw[3] = rms
        level = self.level
        if silent:
            level -= level * self.k_rel
        else:
            # automatic gain: follow each band's own peak, let it sink over about ten seconds
            np.maximum(self.peak * (1.0 - HOP_S / 10.0), np.maximum(raw, 1e-3), out=self.peak)
            norm = np.sqrt(np.minimum(raw / self.peak, 1.0))
            level += (norm - level) * np.where(norm > level, self.k_att, self.k_rel)

        # onset strength: how much the (log) spectrum grew against its short-term average; lows count most
        if silent:
            odf = 0.0
        else:
            lm = np.log1p(mag[:self.nflux] * 400.0)
            d = lm - self.prev_lm
            self.prev_lm += d * 0.5
            np.maximum(d, 0.0, out=d)
            odf = float(self.wflux.dot(d))
        self.odf_hist[self.odf_n % self.hist_n] = odf
        self.odf_n += 1
        self.odf_peak = max(self.odf_peak * (1.0 - HOP_S / 6.0), odf, 1e-3)
        k = self.hops % self.recent.size
        thr = 1.7 * self.recent_sum / self.recent.size + 0.22 * self.odf_peak
        self.recent_sum += odf - float(self.recent[k])
        self.recent[k] = odf
        since = self.hops - self.last_onset_hop
        if odf > thr and since > self.refractory and not silent:
            self.last_onset_hop = self.hops
            self.beat_n += 1
            self.beat_s = min(1.0, odf / self.odf_peak)
            self.beat_t = t_hop + self.delay
            self.onsets.append([self.hops, False])
            self._strong()
        elif since <= 2 and odf / self.odf_peak > self.beat_s:                # the hit was still rising when it fired
            self.beat_s = min(1.0, odf / self.odf_peak)
            if not self.onsets[-1][1]:
                self._strong()

        if self.hops % self.bar_every == 0:
            if silent:
                self.bars_np *= 0.84
            else:
                bm = np.abs(np.fft.rfft(buf * self.barwin)) * (2.0 / BARWIN)
                v = np.interp(self.bar_pos, self.bar_bins, bm).reshape(NBARS, 4).max(axis=1) * self.bar_tilt
                self.bar_peak = max(self.bar_peak * (1.0 - self.bar_every * HOP_S / 10.0), float(v.max()), 1e-3)
                v = np.sqrt(np.minimum(v / self.bar_peak, 1.0))
                self.bars_np += (v - self.bars_np) * np.where(v > self.bars_np, 0.75, 0.16)
            self.bars = self.bars_np.tolist()
        if self.hops % PER_S == 0:
            self._tempo()
        if self.quiet_hops > 3 * PER_S:                                       # three seconds of nothing: forget the tempo
            self.bpm = self.conf = 0.0

        i = self.written % NSLOTS
        lv = level.tolist()
        SLOT.pack_into(self.mm, HEADER.size + i * SLOT.size, t_hop + self.delay, lv[0], lv[1], lv[2], lv[3],
                       min(1.0, odf / self.odf_peak), self.bpm, self.conf, self.beat_s,
                       self.beat_t, self.strong_t, self.beat_n, self.strong_n, *self.bars)
        self.written += 1
        self._header()

    def _strong(self):
        """The big gestures (a ring, a glitch) belong to strong beats, and not to every one of them:
        in a steady four-on-the-floor that is every other kick."""
        period = 60.0 / self.bpm if self.bpm > 40 else 0.5
        if self.beat_s > 0.62 and (self.hops - self.last_strong_hop) * HOP_S > max(0.7, 1.6 * period):
            self.last_strong_hop = self.hops
            self.strong_n += 1
            self.strong_t = self.beat_t
            self.onsets[-1][1] = True

    def _tempo(self):
        """BPM from the autocorrelation of the last eight seconds of onset strength."""
        n = min(self.odf_n, self.hist_n)
        if n < 4 * PER_S:
            return
        x = np.roll(self.odf_hist, -(self.odf_n % self.hist_n))[-n:] if self.odf_n >= self.hist_n else self.odf_hist[:n]
        x = x - x.mean()
        if float(np.abs(x).max()) < 1e-6:
            return
        f = np.fft.rfft(x, 4096)
        top = int(60.0 / (58 * HOP_S)) + 3
        acf = np.fft.irfft(f * np.conj(f))[:top + 1]
        if acf[0] <= 0:
            return
        lags = np.arange(int(60.0 / (200 * HOP_S)), int(60.0 / (60 * HOP_S)) + 1)     # 200 .. 60 BPM
        bpm = 60.0 / (lags * HOP_S)
        prior = np.exp(-0.5 * (np.log2(bpm / 120.0) / 0.7) ** 2)              # most music sits near 120
        score = acf[lags] * prior
        k = int(score.argmax())
        lag = float(lags[k])
        if 0 < k < len(lags) - 1:                                             # finer than one hop: fit a parabola
            a, b, c = score[k - 1], score[k], score[k + 1]
            den = a - 2 * b + c
            if den < 0:
                lag += 0.5 * (a - c) / den
        est, conf = 60.0 / (lag * HOP_S), float(acf[lags[k]] / acf[0])
        if conf < 0.08:
            return
        if not self.bpm:
            self.bpm, self.conf = est, conf
            return
        # The tempo in use is only given up for one that scores clearly better three times in a row:
        # half, double and 3:2 relatives of the real tempo win single rounds all the time.
        here = int(round(60.0 / (self.bpm * HOP_S)))
        near = lags[(lags >= here - 2) & (lags <= here + 2)]
        if near.size:
            j = int(near[np.argmax(acf[near])])
            jf = float(j)
            if lags[0] < j < lags[-1]:                                        # the tempo in use, also finer than one hop
                a, b, c = acf[j - 1], acf[j], acf[j + 1]
                den = a - 2 * b + c
                if den < 0:
                    jf += 0.5 * (a - c) / den
            own = float(acf[j] * np.exp(-0.5 * (np.log2(60.0 / (j * HOP_S) / 120.0) / 0.7) ** 2))
        else:
            j, jf, own = here, float(here), 0.0
        if abs(est - self.bpm) / self.bpm < 0.04 or score[k] < 1.35 * own:
            self.bpm += (60.0 / (jf * HOP_S) - self.bpm) * 0.25           # follow small drifts of the tempo in use
            self.conf, self.bpm_votes = float(acf[j] / acf[0]), 0
        elif abs(est - self.bpm_cand) / est < 0.04:
            self.bpm_votes += 1
            if self.bpm_votes >= 3:
                self.bpm, self.conf, self.bpm_votes = est, conf, 0
        else:
            self.bpm_cand, self.bpm_votes = est, 1

    def close(self):
        try:
            self.mm.close(); os.close(self.fd)
        except Exception:
            pass


# ---------------------------------------------------------------------- sources
def capture_cmd():
    props = "{ stream.capture.sink = true node.name = rice-react node.description = \"rice react (analyser)\" node.passive = true }"
    cmd = ["pw-record", "-P", props, "--rate", str(RATE), "--channels", "1", "--format", "s16", "--raw", "-"]
    from shutil import which
    if which("stdbuf"):                                                       # pw-record buffers its output: 43 ms lumps without this
        cmd = ["stdbuf", "-o0"] + cmd
    return cmd


def run_live(an, stop):
    def delays():
        while not stop.is_set():
            out = sink_delay()
            if out is not None:
                an.out_delay = out
                an.delay = max(0.0, min(1.0, out - PIPELINE_S + user_offset()))
            stop.wait(5.0)
    threading.Thread(target=delays, daemon=True).start()
    while not stop.is_set():
        proc = subprocess.Popen(capture_cmd(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        fd = proc.stdout.fileno()
        pending = b""
        total, recent = 0, []                                                 # samples read; (arrival, clock offset) lately
        try:
            while not stop.is_set():
                data = os.read(fd, 16384)
                if not data:
                    break
                now = time.monotonic()
                pending += data
                n = len(pending) // 2
                total += len(data) // 2
                # The stream's own clock is sample count / rate. Its offset to the monotonic clock is taken
                # from the promptest arrival of the last second: a chunk can be handed over late, but not
                # before it exists, so scheduling jitter does not reach the timestamps. A window (and not
                # an all-time minimum) because after a pause, or when the output device changes, buffered
                # audio arrives in a burst that would otherwise pin the clock too early for good.
                if recent and now - recent[-1][0] > 0.5:
                    recent.clear()
                recent.append((now, now - total / RATE))
                while now - recent[0][0] > 1.0:
                    recent.pop(0)
                base = min(c for _, c in recent)
                whole = n // HOP
                if whole:
                    x = np.frombuffer(pending[:whole * HOP * 2], dtype="<i2").astype(np.float32) / 32768.0
                    pending = pending[whole * HOP * 2:]
                    first = total - len(pending) // 2 - whole * HOP          # index of the first sample used
                    for k in range(whole):
                        an.hop(x[k * HOP:(k + 1) * HOP], base + (first + (k + 1) * HOP) / RATE)
                # When the output plays late anyway (Bluetooth), nothing here is due for a while: sleep and
                # take several chunks at once. The timestamps stay exact; only the wake-ups get fewer.
                if an.delay > 0.09:
                    stop.wait(0.045)
        finally:
            proc.terminate()
        stop.wait(2.0)                                                        # audio server restarted: try again


def run_file(an, path, stop):
    data = np.fromfile(path, dtype="<i2").astype(np.float32) / 32768.0
    t0 = time.monotonic()
    for k in range(len(data) // HOP):
        if stop.is_set():
            break
        due = t0 + (k + 1) * HOP_S
        wait = due - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        an.hop(data[k * HOP:(k + 1) * HOP], due)


def selftest():
    """Kicks at known sample positions, mixed with hi-hats and a pad: how late does each onset fire?"""
    rng = np.random.default_rng(7)
    out = {}
    for bpm in (96, 120, 128, 174):
        secs, period = 24, 60.0 / bpm
        x = np.zeros(secs * RATE, np.float32)
        t = np.arange(int(0.25 * RATE)) / RATE
        kick = (np.sin(2 * np.pi * (48 * t + 60 * (1 - np.exp(-t * 28)) / 28 * 3)) * np.exp(-t * 14)).astype(np.float32)
        hat = (rng.standard_normal(int(0.03 * RATE)) * np.exp(-np.arange(int(0.03 * RATE)) / 300)).astype(np.float32) * 0.15
        pos = []
        b = 0
        while (b + 1) * period < secs - 1:
            s = int(b * period * RATE) + 4000
            x[s:s + kick.size] += kick * 0.8
            pos.append(s)
            h = s + int(period * RATE / 2)
            x[h:h + hat.size] += hat
            b += 1
        tt = np.arange(x.size) / RATE
        x += (0.05 * np.sin(2 * np.pi * 220 * tt) + 0.04 * np.sin(2 * np.pi * 277 * tt)).astype(np.float32)
        an = Analyser(RUN / "react-selftest.ring")
        for k in range(x.size // HOP):
            an.hop(x[k * HOP:(k + 1) * HOP], (k + 1) * HOP_S)
        fired = np.array([h for h, _ in an.onsets]) * HOP                     # sample index at which each onset was reported
        strong = np.array([h for h, st in an.onsets if st]) * HOP
        lat, miss = [], 0
        for s in pos[4:]:                                                     # the first hits train the automatic gain
            j = fired[(fired >= s) & (fired < s + int(0.08 * RATE))]
            if j.size:
                lat.append((j[0] - s) / RATE * 1000)
            else:
                miss += 1
        extra = sum(1 for f in fired if not any(0 <= f - s < 0.08 * RATE for s in pos))
        strong_on_kick = sum(1 for f in strong if any(0 <= f - s < 0.08 * RATE for s in pos[4:]))
        gaps = np.diff(strong) / RATE
        strong_else = sum(1 for f in strong if not any(0 <= f - s < 0.08 * RATE for s in pos))
        lat = np.array(lat) if lat else np.array([0.0])
        out[bpm] = dict(kicks=len(pos) - 4, detected=len(pos) - 4 - miss, hihat_onsets=extra,
                        strong_on_kicks=strong_on_kick, strong_elsewhere=strong_else,
                        strong_every_s=round(float(np.median(gaps)), 2) if gaps.size else None,
                        latency_ms_median=round(float(np.median(lat)), 1), latency_ms_max=round(float(lat.max()), 1),
                        bpm_found=round(an.bpm, 1))
        an.close()
    (RUN / "react-selftest.ring").unlink(missing_ok=True)
    print(json.dumps(out, indent=1))


def main():
    if "--selftest" in sys.argv:
        return selftest()
    stop = threading.Event()

    def leave(*_):                                                            # also ends a read that is waiting for audio
        stop.set()
        raise SystemExit(0)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, leave)
    an = Analyser()
    try:
        if "--input" in sys.argv:
            run_file(an, sys.argv[sys.argv.index("--input") + 1], stop)
        else:
            run_live(an, stop)
    finally:
        an.close()
        RING.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
