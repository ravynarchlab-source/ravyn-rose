#!/usr/bin/env python3
"""Tray icon and on-screen pill for pen-mode: both show DRAW or NAVIGATE, one tap on either switches.

The tray icon is painted here (a pencil on the accent colour for DRAW, a four-way arrow on grey
for NAVIGATE) so the two states stay easy to tell apart at any size and in any icon theme.
The pill (pen-mode-pill.qml) sits at the top centre of the screen while Rnote, Xournal++ or
Krita is running, so the switch is one tap away even when the panel hides itself.
PEN_MODE_PILL=0 turns the pill off, PEN_MODE_COLOR sets the DRAW colour.
Run by the user service pen-mode-tray.service.
"""
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Property, QObject, QPointF, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

HERE = Path(__file__).resolve().parent
CLI = str(HERE / "pen-mode")
STATE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "pen-mode" / "mode"
DRAW_COLOR = QColor(os.environ.get("PEN_MODE_COLOR", "#c2334d"))
NAV_COLOR = QColor("#6b7280")


def read_mode():
    try:
        m = STATE.read_text().strip()
    except OSError:
        m = ""
    return m if m in ("draw", "navigate") else "draw"


def make_icon(mode):
    icon = QIcon()
    for size in (22, 32, 48, 64, 96):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        s = size / 64.0
        p.setPen(Qt.NoPen)
        p.setBrush(DRAW_COLOR if mode == "draw" else NAV_COLOR)
        p.drawRoundedRect(2 * s, 2 * s, 60 * s, 60 * s, 14 * s, 14 * s)
        white = QColor("white")
        if mode == "draw":                       # pencil, tip at the lower left
            p.setPen(QPen(white, 9 * s, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(27 * s, 37 * s), QPointF(46 * s, 18 * s))
            p.setPen(Qt.NoPen)
            p.setBrush(white)
            p.drawPolygon(QPolygonF([QPointF(15 * s, 49 * s), QPointF(19 * s, 36 * s), QPointF(28 * s, 45 * s)]))
        else:                                    # four-way arrow
            p.setPen(QPen(white, 5 * s, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(32 * s, 16 * s), QPointF(32 * s, 48 * s))
            p.drawLine(QPointF(16 * s, 32 * s), QPointF(48 * s, 32 * s))
            p.setPen(Qt.NoPen)
            p.setBrush(white)
            for cx, cy, dx, dy in ((32, 10, 0, -1), (32, 54, 0, 1), (10, 32, -1, 0), (54, 32, 1, 0)):
                tip = QPointF((cx + dx * 2) * s, (cy + dy * 2) * s)
                if dx == 0:
                    base = [QPointF((cx - 8) * s, (cy - dy * 8) * s), QPointF((cx + 8) * s, (cy - dy * 8) * s)]
                else:
                    base = [QPointF((cx - dx * 8) * s, (cy - 8) * s), QPointF((cx - dx * 8) * s, (cy + 8) * s)]
                p.drawPolygon(QPolygonF([tip] + base))
        p.end()
        icon.addPixmap(pm)
    return icon


class Bridge(QObject):
    """What the pill sees: the mode, whether to show itself, and a way to switch."""
    changed = Signal()

    def __init__(self):
        super().__init__()
        self._mode = read_mode()
        self._shown = False

    @Property(str, notify=changed)
    def mode(self):
        return self._mode

    @Property(bool, notify=changed)
    def shown(self):
        return self._shown

    @Property(str, constant=True)
    def drawColor(self):
        return DRAW_COLOR.name()

    @Slot()
    def toggle(self):
        subprocess.Popen([sys.executable, CLI, "toggle"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def update(self, mode=None, shown=None):
        new_mode = self._mode if mode is None else mode
        new_shown = self._shown if shown is None else shown
        if (new_mode, new_shown) != (self._mode, self._shown):
            self._mode, self._shown = new_mode, new_shown
            self.changed.emit()


class Tray:
    def __init__(self, app, bridge):
        self.bridge = bridge
        self.icons = {m: make_icon(m) for m in ("draw", "navigate")}
        self.tray = QSystemTrayIcon()
        self.menu = QMenu()
        for label, cmd in (("Draw: touch draws", "draw"), ("Navigate: touch moves the page", "navigate")):
            act = QAction(label, self.menu)
            act.triggered.connect(lambda _=False, c=cmd: self.run(c))
            self.menu.addAction(act)
        self.menu.addSeparator()
        quit_act = QAction("Hide this icon", self.menu)
        quit_act.triggered.connect(app.quit)
        self.menu.addAction(quit_act)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self.clicked)
        self.mode = None
        self.stamp = None
        self.refresh()
        self.tray.show()
        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh)
        self.timer.start(400)                    # follows changes made by the shortcut or the command line
        self.apps = QTimer()
        self.apps.timeout.connect(self.check_apps)
        self.apps.start(2000)
        self.check_apps()

    def check_apps(self):                        # the pill is only on screen while a drawing app is open
        rc = subprocess.run(["pgrep", "-x", "rnote|xournalpp|krita"], stdout=subprocess.DEVNULL).returncode
        self.bridge.update(shown=rc == 0)

    def run(self, cmd):
        subprocess.Popen([sys.executable, CLI, cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def clicked(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.MiddleClick, QSystemTrayIcon.DoubleClick):
            self.run("toggle")

    def refresh(self):
        try:
            stamp = STATE.stat().st_mtime_ns
        except OSError:
            stamp = 0
        if stamp == self.stamp and self.mode is not None:
            return
        self.stamp = stamp
        mode = read_mode()
        if mode != self.mode:
            self.mode = mode
            self.bridge.update(mode=mode)
            self.tray.setIcon(self.icons[mode])
            self.tray.setToolTip("Pen mode: DRAW (touch draws). Tap to switch." if mode == "draw"
                                 else "Pen mode: NAVIGATE (touch moves the page). Tap to switch.")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("pen-mode")
    app.setApplicationDisplayName("Pen mode")
    app.setQuitOnLastWindowClosed(False)
    bridge = Bridge()
    tray = Tray(app, bridge)
    engine = None
    if os.environ.get("PEN_MODE_PILL", "1") != "0":
        try:
            from PySide6.QtQml import QQmlApplicationEngine
            engine = QQmlApplicationEngine()
            engine.rootContext().setContextProperty("bridge", bridge)
            engine.load(QUrl.fromLocalFile(str(HERE / "pen-mode-pill.qml")))
            if not engine.rootObjects():
                print("pen-mode: the on-screen pill could not be loaded; tray icon only", file=sys.stderr)
        except ImportError as e:
            print(f"pen-mode: no QML support ({e}); tray icon only", file=sys.stderr)
    sys.exit(app.exec())
