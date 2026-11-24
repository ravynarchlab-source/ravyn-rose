#!/usr/bin/env python3
"""Rice lyrics window: lyrics.qml in one small PySide6 process (started by `rice on`).

    lyricswin.py <theme.json> <now.json>

It shows what the lyrics backend (lyrics.py) writes to now.json, and it reads the same beat clock as
the deck (react/client.py), so the word being sung lights up on the beat the deck pulses on.
While nothing plays, or with `rice react off`, the window is only redrawn when the line changes."""
import json, os, signal, sys, time
from pathlib import Path

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow                 # noqa: F401  (gives the window its Qt Quick type)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from react import client as react                         # noqa: E402


class Backend(QObject):
    stateChanged = Signal()
    reactChanged = Signal()

    def __init__(self, now_path):
        super().__init__()
        self._path = now_path
        self._stamp = None
        self._state = dict(line="♪", sub="", art="", rtl=False, status="", seq=-1, t0=-1.0, t1=-1.0, pos=0.0, at=0.0)
        self._reader = react.Reader()
        self._amount = react.amount()
        self._reduced = react.reduced_motion()
        self._poll = QTimer(self)
        self._poll.timeout.connect(self.load)
        self._poll.start(120)
        self._slow = QTimer(self)
        self._slow.timeout.connect(self._level)
        self._slow.start(1000)
        self.load()

    state = Property("QVariantMap", lambda s: s._state, notify=stateChanged)
    amount = Property(float, lambda s: s._amount, notify=reactChanged)
    reducedMotion = Property(bool, lambda s: s._reduced, notify=reactChanged)

    def load(self):
        try:
            stamp = os.stat(self._path).st_mtime_ns
            if stamp == self._stamp:
                return
            self._stamp = stamp
            with open(self._path, encoding="utf-8") as f:
                o = json.load(f)
        except (OSError, ValueError):
            return
        if o.get("seq") != self._state.get("seq"):
            self._state = dict(self._state, **o)
            self.stateChanged.emit()

    def _level(self):
        amount, reduced = react.amount(), react.reduced_motion()
        if (amount, reduced) != (self._amount, self._reduced):
            self._amount, self._reduced = amount, reduced
            self.reactChanged.emit()

    @Slot(result="QVariantList")
    def frame(self):
        """[kick, beats so far, sound, playback position now]: asked for 60 times a second while music
        plays; the window only repaints while a pulse is fading."""
        now = time.monotonic()
        o = self._reader.frame(now)
        s = self._state
        pos = s.get("pos", 0.0) + (now - s.get("at", now) if s.get("status") == "Playing" else 0.0)
        kick = o["kick"]
        return [kick if kick > 0.03 else 0.0, o["beats"], 1.0 if o["sound"] else 0.0, pos]

    @Slot(result=float)
    def position(self):
        s = self._state
        return s.get("pos", 0.0) + (time.monotonic() - s.get("at", 0.0) if s.get("status") == "Playing" else 0.0)


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    theme = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    QGuiApplication.setDesktopFileName("rice-lyrics")
    app = QGuiApplication(sys.argv[:1])
    app.setApplicationName("rice-lyrics")
    backend = Backend(sys.argv[2])
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty("theme", theme)
    engine.load(QUrl.fromLocalFile(str(HERE / "lyrics.qml")))
    if not engine.rootObjects():
        sys.exit(1)
    grab = os.environ.get("RICE_LYRICS_GRAB")            # debug: save what the window shows, 8 times a second
    if grab:
        win, n = engine.rootObjects()[0], [0]
        shots = QTimer()
        shots.timeout.connect(lambda: (win.grabWindow().save(f"{grab}{n[0]:03d}.png"), n.__setitem__(0, n[0] + 1)))
        shots.start(125)
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    keepalive = QTimer()                                  # lets Python see the signals above
    keepalive.start(500)
    keepalive.timeout.connect(lambda: None)
    rc = app.exec()
    del engine
    sys.exit(rc)


if __name__ == "__main__":
    main()
