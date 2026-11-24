#!/usr/bin/env python3
"""vtouch: a simulated touchscreen (uinput) for testing touch drawing, two-finger pan, pinch
and palm contacts without a hand on the glass. Applications see it exactly like the real
touch panel: plain touch contacts, no pressure, no contact size.

Library use:
    from vtouch import Touch
    with Touch() as t:
        t.play([{"pts": [(0.2, 0.5), (0.8, 0.5)], "dur": 0.8}])            # one finger
        t.play([{"pts": [(0.4, 0.5), (0.3, 0.5)]}, {"pts": [(0.6, 0.5), (0.7, 0.5)]}])   # pinch out

Coordinates are fractions of the screen (0..1). Contacts given in one play() call run at the same
time; "delay" starts a contact later, "hold" keeps it down after its last point, "jitter" adds
hand-shake noise (fraction of the screen) so smoothing has something to remove.

CLI:  vtouch.py 'x0,y0,x1,y1[,...][:dur]' ['A + B' for simultaneous contacts]
The device disappears again when the script ends.
"""
import fcntl
import os
import random
import struct
import sys
import time

EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
ABS_X, ABS_Y = 0, 1
ABS_MT_SLOT, ABS_MT_POSITION_X, ABS_MT_POSITION_Y, ABS_MT_TRACKING_ID = 0x2f, 0x35, 0x36, 0x39
BTN_TOUCH = 0x14a
INPUT_PROP_DIRECT = 1
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_ABSBIT, UI_SET_PROPBIT = 0x40045564, 0x40045565, 0x40045567, 0x4004556e
UI_DEV_SETUP, UI_ABS_SETUP, UI_DEV_CREATE, UI_DEV_DESTROY = 0x405c5503, 0x401c5504, 0x5501, 0x5502
MAXX, MAXY, SLOTS = 19200, 10800, 10
FRAME = 0.008


class Touch:
    def __init__(self, name=b"pen-setup test touchscreen", settle=2.0):
        self.fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        for ev in (EV_KEY, EV_ABS):
            fcntl.ioctl(self.fd, UI_SET_EVBIT, ev)
        fcntl.ioctl(self.fd, UI_SET_KEYBIT, BTN_TOUCH)
        fcntl.ioctl(self.fd, UI_SET_PROPBIT, INPUT_PROP_DIRECT)
        for code, mx, res in ((ABS_X, MAXX, 62), (ABS_Y, MAXY, 62), (ABS_MT_SLOT, SLOTS - 1, 0),
                              (ABS_MT_POSITION_X, MAXX, 62), (ABS_MT_POSITION_Y, MAXY, 62),
                              (ABS_MT_TRACKING_ID, 65535, 0)):
            fcntl.ioctl(self.fd, UI_SET_ABSBIT, code)
            fcntl.ioctl(self.fd, UI_ABS_SETUP, struct.pack("HHiiiiii", code, 0, 0, 0, mx, 0, 0, res))
        fcntl.ioctl(self.fd, UI_DEV_SETUP, struct.pack("HHHH80sI", 0x06, 0x1209, 0x7e58, 1, name, 0))
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        self.next_id = 100
        time.sleep(settle)  # let libinput and KWin pick the device up

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def close(self):
        time.sleep(0.3)
        fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        os.close(self.fd)

    def _emit(self, t, c, v):
        os.write(self.fd, struct.pack("llHHi", 0, 0, t, c, int(v)))

    @staticmethod
    def _at(pts, f):
        if len(pts) == 1:
            return pts[0]
        g = min(f * (len(pts) - 1), len(pts) - 1 - 1e-9)
        i = int(g)
        t = g - i
        return (pts[i][0] + (pts[i + 1][0] - pts[i][0]) * t, pts[i][1] + (pts[i + 1][1] - pts[i][1]) * t)

    def play(self, contacts, pause=0.35):
        """Run the given contacts simultaneously (each: pts, dur=0.6, delay=0, hold=0, jitter=0)."""
        cs = []
        for slot, c in enumerate(contacts):
            cs.append({"slot": slot, "pts": [tuple(p) for p in c["pts"]], "dur": c.get("dur", 0.6),
                       "delay": c.get("delay", 0.0), "hold": c.get("hold", 0.0),
                       "jitter": c.get("jitter", 0.0), "down": False, "done": False,
                       "rng": random.Random(c.get("seed", 7 + slot))})
        t0 = time.monotonic()
        active = 0
        while not all(c["done"] for c in cs):
            now = time.monotonic() - t0
            for c in cs:
                if c["done"] or now < c["delay"]:
                    continue
                f = (now - c["delay"]) / max(c["dur"], 1e-6)
                self._emit(EV_ABS, ABS_MT_SLOT, c["slot"])
                if f > 1 and now > c["delay"] + c["dur"] + c["hold"]:
                    self._emit(EV_ABS, ABS_MT_TRACKING_ID, -1)
                    c["done"] = True
                    active -= 1
                    if active == 0:
                        self._emit(EV_KEY, BTN_TOUCH, 0)
                    continue
                x, y = self._at(c["pts"], min(f, 1.0))
                if c["jitter"] and 0 < f < 1:
                    x += c["rng"].uniform(-1, 1) * c["jitter"]
                    y += c["rng"].uniform(-1, 1) * c["jitter"] * 16 / 9
                x = min(max(x, 0.0), 1.0)
                y = min(max(y, 0.0), 1.0)
                if not c["down"]:
                    self._emit(EV_ABS, ABS_MT_TRACKING_ID, self.next_id)
                    self.next_id += 1
                    c["down"] = True
                    active += 1
                    if active == 1:
                        self._emit(EV_KEY, BTN_TOUCH, 1)
                self._emit(EV_ABS, ABS_MT_POSITION_X, x * MAXX)
                self._emit(EV_ABS, ABS_MT_POSITION_Y, y * MAXY)
                if c["slot"] == 0:
                    self._emit(EV_ABS, ABS_X, x * MAXX)
                    self._emit(EV_ABS, ABS_Y, y * MAXY)
            self._emit(EV_SYN, 0, 0)
            time.sleep(FRAME)
        time.sleep(pause)

    def stroke(self, pts, dur=0.6, jitter=0.0, **kw):
        self.play([dict(pts=pts, dur=dur, jitter=jitter, **kw)])

    def tap(self, x, y):
        self.play([{"pts": [(x, y)], "dur": 0.06}], pause=0.25)


def _parse(spec):
    dur = 0.6
    if ":" in spec:
        spec, d = spec.rsplit(":", 1)
        dur = float(d)
    nums = [float(v) for v in spec.split(",")]
    return {"pts": list(zip(nums[0::2], nums[1::2])), "dur": dur}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    with Touch() as t:
        for arg in sys.argv[1:]:
            t.play([_parse(s.strip()) for s in arg.split("+")])
