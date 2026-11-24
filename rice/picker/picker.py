#!/usr/bin/env python3
"""Rice flavour picker: one card per flavour with a small screenshot of it.

Click or tap a card, or press 1-5, and the rice switches to that flavour while the picker stays open,
so you can try them one after the other. Arrows move the highlight, Enter applies it, Esc closes.
Opened by `rice flavour` (no name), the "Rice flavour" launcher, the panel button or Meta+Alt+F.

The switch itself is `rice flavour NAME`, run in the background; a second choice made while one is
still being applied is kept and applied next."""
import os, signal, subprocess, sys
from pathlib import Path

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import flavour                                            # noqa: E402

RICE = str(HERE.parent / "rice")
ACTIVE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "rice" / "active"


def card(name):
    f = flavour.load(name)
    c = f["colors"]
    preview = HERE.parent / "flavours" / "previews" / f"{name}.jpg"
    return dict(name=name, title=f["title"], blurb=f["blurb"], light=f["light"], bg=c["bg"], window=c["window"],
                fg=c["fg"], dim=c["dim"], faint=c["faint"], accent=c["accent"], accent2=c["accent2"], hi=c["hi"],
                radius=min(int(f["shape"]["radius"]), 14), font=f["shape"]["ui_font"],
                preview=QUrl.fromLocalFile(str(preview)).toString() if preview.is_file() else "")


class Backend(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self._cards = [card(n) for n in flavour.names()]
        self._current = flavour.current()
        self._proc, self._next = None, ""
        self._on = ACTIVE.is_file()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(300)

    flavours = Property("QVariantList", lambda s: s._cards, constant=True)
    current = Property(str, lambda s: s._current, notify=changed)
    busy = Property(bool, lambda s: s._proc is not None, notify=changed)
    riceOn = Property(bool, lambda s: s._on, notify=changed)

    @Slot(str)
    def apply(self, name):
        if name not in [c["name"] for c in self._cards]:
            return
        self._current = name                              # the picker itself changes at once
        if self._proc is None:
            self._proc = subprocess.Popen([RICE, "flavour", name, "--from=picker"], stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL, start_new_session=True)
        else:
            self._next = name                             # one switch at a time; the latest choice wins
        self.changed.emit()

    def _tick(self):
        dirty = False
        if self._proc is not None and self._proc.poll() is not None:
            self._proc, dirty = None, True
            if self._next and self._next != flavour.current():
                name, self._next = self._next, ""
                self.apply(name)
                return
            self._next = ""
        if self._proc is None:                            # changed from outside (shortcut, command line)?
            cur = flavour.current()
            if cur != self._current:
                self._current, dirty = cur, True
        on = ACTIVE.is_file()
        if on != self._on:
            self._on, dirty = on, True
        if dirty:
            self.changed.emit()


def main():
    QGuiApplication.setDesktopFileName("rice-picker")      # the rice frames windows whose class starts with "rice-"
    app = QGuiApplication(sys.argv[:1])
    app.setApplicationName("rice-picker")
    backend = Backend()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", backend)
    engine.load(QUrl.fromLocalFile(str(HERE / "Picker.qml")))
    if not engine.rootObjects():
        sys.exit(1)
    script = [n for n in os.environ.get("RICE_PICKER_DEMO", "").split(",") if n]
    if script:                                            # demo/test: choose these flavours in turn, then close
        step = QTimer()
        gap = int(float(os.environ.get("RICE_PICKER_DEMO_GAP", "3")) * 1000)

        def go():
            if script:
                backend.apply(script.pop(0))
            else:
                step.stop()
                QTimer.singleShot(gap, app.quit)
        step.timeout.connect(go)
        step.start(gap)
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    keepalive = QTimer()
    keepalive.start(500)
    keepalive.timeout.connect(lambda: None)
    rc = app.exec()
    del engine
    sys.exit(rc)


if __name__ == "__main__":
    main()
