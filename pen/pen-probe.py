#!/usr/bin/env python3
"""pen-probe: shows how the compositor delivers a stylus to applications.

A real pen (Wacom AES / MPP) arrives as *tablet tool* events: pressure, hover, buttons.
A finger-emulating stylus arrives as plain *touch* events, same as a fingertip.
Everything is logged as JSON lines; a summary and a picture of the page are written on exit.

    pen-probe.py <output-dir>
"""
import json
import sys
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
OUT.mkdir(parents=True, exist_ok=True)
IDLE_LIMIT = 300  # seconds without any input before the window closes itself

PHASES = [
    ("pen", "1 / 3   Scribble with the PEN only.\n"
            "Also: hover the tip about 1 cm above the glass, and press any button on the pen."),
    ("palm", "2 / 3   Write a word with the PEN while your PALM rests on the glass,\n"
             "the way you would write on paper."),
    ("finger", "3 / 3   Draw a few lines with one FINGER, then a two-finger pinch."),
]
PEN_COLOR = QColor("#2f6fed")     # tablet tool events
TOUCH_COLOR = QColor("#e8590c")   # touch events
MOUSE_COLOR = QColor("#888888")


class Probe(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("pen-probe")
        self.setAttribute(Qt.WA_AcceptTouchEvents, True)
        self.setTabletTracking(True)
        self.setMouseTracking(True)
        self.phase = 0
        self.log = open(OUT / "probe-events.jsonl", "w")
        self.canvas = None
        self.last = {}           # (kind, id) -> last point
        self.last_input = time.monotonic()
        self.last_button = 0.0
        self.stats = {name: self._blank() for name, _ in PHASES}
        self.proximity_events = 0
        t = QTimer(self)
        t.timeout.connect(self._tick)
        t.start(1000)

    @staticmethod
    def _blank():
        return {"tablet_events": 0, "tablet_hover_moves": 0, "tablet_max_pressure": 0.0,
                "tablet_distinct_pressures": set(), "tablet_buttons_seen": set(),
                "tablet_pointer_types": set(), "tablet_devices": set(), "tablet_max_tilt": 0.0,
                "touch_events": 0, "touch_max_simultaneous": 0, "touch_sequences": 0,
                "touch_max_pressure": 0.0, "touch_max_ellipse": 0.0, "touch_devices": set(),
                "touch_cancels": 0, "mouse_events": 0}

    # ---- helpers
    def _st(self):
        return self.stats[PHASES[min(self.phase, len(PHASES) - 1)][0]]

    def _write(self, rec):
        if self.log.closed:
            return
        rec["t"] = round(time.monotonic(), 4)
        rec["phase"] = PHASES[min(self.phase, len(PHASES) - 1)][0]
        self.log.write(json.dumps(rec) + "\n")
        self.last_input = time.monotonic()

    def _button_rect(self):
        return QRectF(self.width() - 330, 20, 310, 110)

    def _hit_button(self, pos):
        if self._button_rect().contains(pos) and time.monotonic() - self.last_button > 0.7:
            self.last_button = time.monotonic()
            self.phase += 1
            if self.phase >= len(PHASES):
                self.finish()
            self.update()
            return True
        return self._button_rect().contains(pos)

    def _line(self, key, pos, color, width):
        if self.canvas is None:
            return
        prev = self.last.get(key)
        self.last[key] = pos
        if prev is None:
            return
        p = QPainter(self.canvas)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawLine(prev, pos)
        p.end()
        self.update()

    def _tick(self):
        if time.monotonic() - self.last_input > IDLE_LIMIT:
            self.finish(timed_out=True)
        self.update(QRectF(0, 0, self.width(), 220).toRect())

    # ---- events
    def resizeEvent(self, e):
        old = self.canvas
        self.canvas = QPixmap(self.size())
        self.canvas.fill(QColor("#fbfaf7"))
        if old is not None:
            p = QPainter(self.canvas)
            p.drawPixmap(0, 0, old)
            p.end()

    def tabletEvent(self, e):
        st = self._st()
        pos = e.position()
        typ = {QEvent.TabletPress: "press", QEvent.TabletMove: "move", QEvent.TabletRelease: "release"}.get(e.type(), "?")
        pressure = float(e.pressure())
        buttons = int(e.buttons().value)
        st["tablet_events"] += 1
        st["tablet_max_pressure"] = max(st["tablet_max_pressure"], pressure)
        st["tablet_distinct_pressures"].add(round(pressure, 3))
        st["tablet_max_tilt"] = max(st["tablet_max_tilt"], abs(e.xTilt()), abs(e.yTilt()))
        st["tablet_pointer_types"].add(e.pointerType().name)
        st["tablet_devices"].add(e.pointingDevice().name())
        if buttons:
            st["tablet_buttons_seen"].add(buttons)
        if typ == "move" and pressure == 0 and not (buttons & 1):
            st["tablet_hover_moves"] += 1
        self._write({"k": "tablet", "type": typ, "x": pos.x(), "y": pos.y(), "pressure": pressure,
                     "xtilt": e.xTilt(), "ytilt": e.yTilt(), "buttons": buttons,
                     "ptype": e.pointerType().name, "dev": e.pointingDevice().name()})
        if typ == "press" and self._hit_button(pos):
            e.accept()
            return
        if typ == "press":
            self.last.pop(("tablet", 0), None)
        if pressure > 0 or typ == "press":
            self._line(("tablet", 0), pos, PEN_COLOR, 1.5 + 9 * pressure)
        else:
            self.last.pop(("tablet", 0), None)
        e.accept()

    def event(self, e):
        t = e.type()
        if t in (QEvent.TouchBegin, QEvent.TouchUpdate, QEvent.TouchEnd, QEvent.TouchCancel):
            st = self._st()
            st["touch_events"] += 1
            if t == QEvent.TouchCancel:
                st["touch_cancels"] += 1
            pts = e.points()
            st["touch_max_simultaneous"] = max(st["touch_max_simultaneous"], len(pts))
            st["touch_devices"].add(e.pointingDevice().name())
            rec = {"k": "touch", "type": t.name, "dev": e.pointingDevice().name(), "pts": []}
            for p in pts:
                pos = p.position()
                ell = p.ellipseDiameters()
                st["touch_max_pressure"] = max(st["touch_max_pressure"], float(p.pressure()))
                st["touch_max_ellipse"] = max(st["touch_max_ellipse"], ell.width(), ell.height())
                state = p.state().name
                rec["pts"].append({"id": p.id(), "x": pos.x(), "y": pos.y(), "state": state,
                                   "pressure": float(p.pressure()), "ew": ell.width(), "eh": ell.height()})
                key = ("touch", p.id())
                if state == "Pressed":
                    st["touch_sequences"] += 1
                    self.last.pop(key, None)
                    if self._hit_button(pos):
                        continue
                if not self._button_rect().contains(pos):
                    self._line(key, pos, TOUCH_COLOR, 4)
                if state == "Released":
                    self.last.pop(key, None)
            self._write(rec)
            e.accept()
            return True
        return super().event(e)

    def mousePressEvent(self, e):
        self._st()["mouse_events"] += 1
        self._write({"k": "mouse", "type": "press", "x": e.position().x(), "y": e.position().y()})
        self.last.pop(("mouse", 0), None)
        self._hit_button(e.position())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self._st()["mouse_events"] += 1
            self._line(("mouse", 0), e.position(), MOUSE_COLOR, 2)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.finish()

    def paintEvent(self, _):
        p = QPainter(self)
        if self.canvas is not None:
            p.drawPixmap(0, 0, self.canvas)
        idx = min(self.phase, len(PHASES) - 1)
        p.setPen(QColor("#1b1b1b"))
        p.setFont(QFont("Sans", 15, QFont.Bold))
        p.drawText(QRectF(30, 20, self.width() - 390, 110), Qt.AlignLeft | Qt.TextWordWrap, PHASES[idx][1])
        st = self._st()
        p.setFont(QFont("Sans", 11))
        p.setPen(PEN_COLOR)
        p.drawText(QPointF(30, 160), f"blue = REAL PEN events: {st['tablet_events']}   "
                                     f"max pressure {st['tablet_max_pressure']:.2f}   hover moves {st['tablet_hover_moves']}")
        p.setPen(TOUCH_COLOR)
        p.drawText(QPointF(30, 185), f"orange = TOUCH (finger-type) events: {st['touch_events']}   "
                                     f"most contacts at once {st['touch_max_simultaneous']}")
        left = int(IDLE_LIMIT - (time.monotonic() - self.last_input))
        p.setPen(QColor("#777777"))
        p.drawText(QPointF(30, 210), f"closes by itself after {left} s without input  ·  Esc also closes")
        r = self._button_rect()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#8a1c2b"))
        p.drawRoundedRect(r, 18, 18)
        p.setPen(QColor("white"))
        p.setFont(QFont("Sans", 20, QFont.Bold))
        p.drawText(r, Qt.AlignCenter, "NEXT  ▶" if self.phase < len(PHASES) - 1 else "DONE  ✔")
        p.end()

    def finish(self, timed_out=False):
        if getattr(self, "_done", False):
            return
        self._done = True
        out = {"timed_out": timed_out, "reached_phase": self.phase, "proximity_events": self.proximity_events, "phases": {}}
        for name, st in self.stats.items():
            d = {}
            for k, v in st.items():
                if isinstance(v, set):
                    d[k] = sorted(v) if k != "tablet_distinct_pressures" else len(v)
                else:
                    d[k] = v
            out["phases"][name] = d
        (OUT / "probe-summary.json").write_text(json.dumps(out, indent=2))
        self.grab().save(str(OUT / "probe-page.png"))
        self.log.close()
        QApplication.quit()


class App(QApplication):
    def __init__(self, argv):
        super().__init__(argv)
        self.win = None

    def event(self, e):
        if e.type() in (QEvent.TabletEnterProximity, QEvent.TabletLeaveProximity) and self.win:
            self.win.proximity_events += 1
            self.win._write({"k": "proximity", "type": e.type().name})
        return super().event(e)


if __name__ == "__main__":
    app = App(sys.argv)
    w = Probe()
    app.win = w
    w.showFullScreen()
    sys.exit(app.exec())
