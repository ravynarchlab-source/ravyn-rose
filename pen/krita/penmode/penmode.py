"""Krita side of pen-mode.

Krita has no outside switch for "touch draws" versus "touch moves the canvas", but with touch
painting on, one finger does whatever the selected tool does. So:

  DRAW      -> the tool you were using (the freehand brush the first time)
  NAVIGATE  -> the pan tool: one finger moves the canvas, nothing is painted

The mode comes from the file that `pen-mode` writes; it is checked twice a second.
"""
import os

try:
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QToolButton
except ImportError:                      # Krita 5
    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QToolButton

from krita import Extension, Krita

STATE = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "pen-mode", "mode")
BRUSH = "KritaShape/KisToolBrush"
PAN = "PanTool"


class PenMode(Extension):
    def __init__(self, parent):
        super().__init__(parent)
        self.applied = None              # mode last applied to a window
        self.stamp = None
        self.draw_tool = BRUSH
        self.timer = None

    def setup(self):
        self.timer = QTimer()
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    def createActions(self, window):
        pass

    def read(self):
        try:
            with open(STATE) as f:
                mode = f.read().strip()
        except OSError:
            return "draw"
        return mode if mode in ("draw", "navigate") else "draw"

    def current_tool(self, qwin):
        """Name of the selected toolbox tool, if it is one this Krita has an action for."""
        app = Krita.instance()
        for button in qwin.findChildren(QToolButton):
            name = button.objectName()
            if button.isChecked() and name and name != PAN and app.action(name) is not None:
                return name
        return None

    def poll(self):
        try:
            stamp = os.stat(STATE).st_mtime_ns
        except OSError:
            stamp = 0
        if stamp == self.stamp and self.applied is not None:
            return
        app = Krita.instance()
        window = app.activeWindow()
        if window is None or window.qwindow() is None or window.activeView() is None:
            return                       # no canvas yet: try again on the next tick
        mode = self.read()
        if mode == "navigate":
            if self.applied != "navigate":
                self.draw_tool = self.current_tool(window.qwindow()) or self.draw_tool
            action = app.action(PAN)
        else:
            action = app.action(self.draw_tool) or app.action(BRUSH)
        if action is None:
            return
        first = self.applied is None
        if not (first and mode == "draw"):       # a fresh Krita in DRAW keeps whatever tool it opened with
            action.trigger()
        self.applied = mode
        self.stamp = stamp
        if not first:
            text = "Pen: DRAW  ·  touch paints" if mode == "draw" else "Pen: NAVIGATE  ·  touch moves the canvas"
            icon = app.icon("draw-freehand" if mode == "draw" else "tool_pan")
            try:
                window.activeView().showFloatingMessage(text, icon, 1500, 1)
            except Exception:
                pass
