"""rice react, the reading side: used by every panel that moves with the music (deck, lyrics bar).

The analyser (react/analyser.py) writes one entry per 5.3 ms into a memory-mapped ring, each stamped
with the moment it should be shown. A panel calls Reader.frame() once per frame it draws and gets the
entry that is due NOW, plus the beat envelopes worked out from the beat times. Because every panel
derives them from the same stamps and the same clock, they pulse together.

No numpy here on purpose: a panel only unpacks one small struct per frame.
"""
import math, mmap, os, struct, time
from pathlib import Path

RUN = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "rice"
RING = RUN / "react.ring"
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "rice"
LEVELS = {"off": 0.0, "subtle": 0.45, "full": 1.0}
MAGIC = b"RICEREA1"
HEADER = struct.Struct("<8sIIIIQdddd")
NBARS = 32
SLOT = struct.Struct("<d8fddII%df" % NBARS)


def reduced_motion():
    """True when the desktop asks for no animation: Plasma's animation speed set to "instant"
    (AnimationDurationFactor=0 in kdeglobals), or RICE_REDUCED_MOTION=1."""
    env = os.environ.get("RICE_REDUCED_MOTION")
    if env is not None:
        return env not in ("", "0")
    try:
        group = ""
        for line in (Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "kdeglobals").read_text(
                encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("["):
                group = line
            elif group == "[KDE]" and line.startswith("AnimationDurationFactor="):
                return float(line.split("=", 1)[1]) == 0.0
    except Exception:
        pass
    return False


def level_name():
    """What `rice react` was last set to: off, subtle or full (full when never set)."""
    try:
        name = (CONFIG / "react").read_text().strip()
    except Exception:
        name = "full"
    return name if name in LEVELS else "full"


def amount():
    """0 = no reaction, 1 = full. Reduced motion always wins."""
    return 0.0 if reduced_motion() else LEVELS[level_name()]


class Reader:
    """frame() -> dict with: alive (the analyser is delivering), sound (something is audible), bass, mids,
    highs, loud, kick, glow, ring, glitch, seed, bpm, bars."""
    KICK_S, GLITCH_S, GLOW_S = 0.17, 0.11, 0.65

    def __init__(self):
        self.mm = None
        self.inode = None
        self.pos = 0                    # entries consumed so far (index of the next one to look at)
        self.cur = None
        self.glow = 0.0
        self.last = time.monotonic()
        self.checked = 0.0
        self.wake_t = -1e9
        self.had_sound = False
        self.quiet_since = time.monotonic()
        self.zero = dict(sound=False, bass=0.0, mids=0.0, highs=0.0, loud=0.0, kick=0.0, glow=0.0, ring=-1.0,
                         glitch=0.0, seed=0.0, bpm=0.0, bars=[0.0] * NBARS, beats=0, age=9.0, alive=False)
        self.out = dict(self.zero)

    def _open(self, now):
        if now - self.checked < 1.0:
            return self.mm is not None
        self.checked = now
        try:
            st = os.stat(RING)
            if self.mm is not None and st.st_ino == self.inode:
                return True
            self.close()
            with open(RING, "rb") as f:
                mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            head = HEADER.unpack_from(mm, 0)
            if head[0] != MAGIC or head[3] != SLOT.size:
                mm.close()
                return False
            self.mm, self.inode, self.nslots = mm, st.st_ino, head[2]
            self.pos = max(0, head[5] - 1)
            self.cur = None
            return True
        except OSError:
            self.close()
            return False

    def close(self):
        if self.mm is not None:
            try:
                self.mm.close()
            except Exception:
                pass
        self.mm = self.inode = None

    def frame(self, now=None):
        now = time.monotonic() if now is None else now
        dt = max(0.0, min(0.25, now - self.last))
        self.last = now
        o = self.out
        if not self._open(now):
            return self._fade(o, dt, now)
        mm, n = self.mm, self.nslots
        written = struct.unpack_from("<Q", mm, 24)[0]
        if written - self.pos > n - 2:                      # fell far behind (or the analyser restarted)
            self.pos = max(0, written - n // 2)
        base = HEADER.size
        while self.pos < written:                           # take every entry that is due
            s = SLOT.unpack_from(mm, base + (self.pos % n) * SLOT.size)
            if s[0] > now:
                break
            self.cur = s
            self.pos += 1
        s = self.cur
        if s is None or now - s[0] > 0.30:                  # nothing due lately: the analyser is idle or gone
            return self._fade(o, dt, now)
        sound = s[4] > 0.004 or s[1] > 0.004
        if sound and not self.had_sound and now - self.quiet_since > 0.6:
            self.wake_t = now                               # music is back: one pulse
        if not sound and self.had_sound:
            self.quiet_since = now
        self.had_sound = sound
        beat_age, strong_age = now - s[9], now - s[10]
        kick = s[8] * math.exp(-beat_age / self.KICK_S) if s[11] and beat_age >= 0 else 0.0
        wake_age = now - self.wake_t
        if wake_age < 1.2:
            kick = max(kick, math.exp(-wake_age / 0.25))
            strong_age = min(strong_age, wake_age) if s[12] else wake_age
        elif not s[12]:
            strong_age = 9.0
        period = 60.0 / s[6] if s[6] > 40 and s[7] > 0.12 else 0.5
        travel = 0.85 * max(0.36, min(0.75, period))        # the ring reaches the corners just before the next beat
        self.glow = max(self.glow * math.exp(-dt / self.GLOW_S), kick)
        o["sound"], o["bass"], o["mids"], o["highs"], o["loud"] = sound, s[1], s[2], s[3], s[4]
        o["kick"], o["glow"] = kick, self.glow
        o["ring"] = 1.6 * strong_age / travel if 0 <= strong_age < travel else -1.0
        o["glitch"] = math.exp(-strong_age / self.GLITCH_S) if 0 <= strong_age < 0.6 else 0.0
        o["seed"], o["bpm"], o["beats"], o["age"] = float(s[12] % 1024), (s[6] if s[7] > 0.12 else 0.0), s[11], now - s[0]
        o["beat_t"], o["alive"] = s[9], True
        o["bars"] = s[13:]
        return o

    def _fade(self, o, dt, now):
        """No fresh data: let everything sink smoothly instead of freezing or snapping to zero."""
        if self.had_sound:
            self.quiet_since = now
        self.had_sound = False
        k = math.exp(-dt / 0.18)
        for key in ("bass", "mids", "highs", "loud", "kick", "glitch"):
            o[key] *= k
        self.glow *= math.exp(-dt / self.GLOW_S)
        o["glow"], o["ring"], o["sound"], o["bpm"], o["age"], o["alive"] = self.glow, -1.0, False, 0.0, 9.0, False
        o["bars"] = [b * k for b in o["bars"]]
        return o
