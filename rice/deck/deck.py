#!/usr/bin/env python3
"""Rice ASCII DECK: the centre panel. One PySide6 process: this backend plus the Qt Quick window Deck.qml.

What it does
  * follows the music player that is playing (Pear first, then fooyin; browsers ignored) with the same
    code as the lyrics panel (lyrics.py: poll / choose / art_file), once a second;
  * turns the player's cover art (mpris:artUrl; https art is cached in ~/.cache/rice/art by lyrics.py)
    into a small texture: cropped to the tile, contrast-corrected, sharpened, 2 x 4 texels per character
    cell. The shader ascii.frag draws the characters from it. No video is downloaded or streamed;
  * runs one cava in raw mode writing to a FIFO and turns it into 32 spectrum bars and a beat envelope;
  * controls: play/pause, previous/next, seek through MPRIS (playerctl); volume and mute through
    PipeWire (wpctl), clamped to 0-100 %;
  * listens on a datagram socket for the global shortcuts (deck/ctl.py).
Nothing here reads browser data, and nothing is sent anywhere."""
import json, os, re, signal, socket, subprocess, sys, threading, time, urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps
from PySide6.QtCore import QObject, Property, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import lyrics as mpris                                    # noqa: E402  (poll, choose, art_file)

RUN = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "rice"
TEX = RUN / "deck"                                        # textures for the shader (tmpfs)
SOCK, FIFO, CAVA_CONF = RUN / "deck.sock", RUN / "cava.fifo", RUN / "cava-deck.conf"
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "rice"
MONOCRAFT = Path.home() / ".local/share/fonts/rice/Monocraft.otf"   # put there by install.sh
NBARS = 32
DEBUG = bool(os.environ.get("RICE_DECK_DEBUG"))          # extra logging and the test-event injector
TEST_WINDOW = bool(os.environ.get("RICE_DECK_TEST"))     # window never takes focus or real input (testing)
MODES = ["classic", "blocks", "braille", "tinted"]


def load_theme():
    """The flavour in use: theme.json from `rice on`, or built here when the deck is started by hand."""
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        return json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    import flavour
    flavour.build(flavour.current(), RUN / "flavour")
    return json.loads((RUN / "flavour" / "theme.json").read_text(encoding="utf-8"))


THEME = load_theme()
SETTINGS = CONFIG / f"deck-{THEME['name']}.json"      # art mode, zoom and fill are remembered per flavour
SINK = "@DEFAULT_AUDIO_SINK@"


def run(cmd, timeout=3):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except Exception:
        return ""


def read_volume():
    """(percent 0-100, muted) of the default PipeWire sink, or None."""
    m = re.search(r"Volume:\s*([0-9.]+)(.*)", run(["wpctl", "get-volume", SINK]))
    if not m:
        return None
    return max(0, min(100, round(float(m.group(1)) * 100))), "MUTED" in m.group(2)


# ---------------------------------------------------------------- glyph atlas
def atlas_font():
    """Monocraft if install.sh put it there; otherwise whatever monospace font fontconfig offers (less crisp)."""
    if MONOCRAFT.is_file():
        return MONOCRAFT
    found = (run(["fc-list", "-f", "%{file}\n", "Monocraft:style=Regular"]).splitlines() or [""])[0] \
        or run(["fc-match", "-f", "%{file}", "monospace"])
    if not found:
        sys.exit("[deck] no usable font: install Monocraft (see install.sh) or any monospace font")
    return Path(found)


FONT = atlas_font()


def build_atlas(zoom):
    """Monocraft is a pixel font: at 9 px (and multiples) every glyph lands exactly on the pixel grid.
    Measures how much ink each printable ASCII glyph has, picks an even brightness ramp from them and
    writes them side by side into one image. Returns (path, number of glyphs)."""
    cw, ch = 6 * zoom, 9 * zoom
    font = ImageFont.truetype(str(FONT), 9 * zoom)
    ink = []
    for code in range(32, 127):
        im = Image.new("L", (cw, ch), 0)
        ImageDraw.Draw(im).text((0, 0), chr(code), font=font, fill=255)
        ink.append((sum(im.tobytes()) / (255.0 * cw * ch), chr(code), im))
    ink.sort(key=lambda t: t[0])
    top, levels, ramp, used = ink[-1][0], 16, [], set()
    for k in range(levels):
        want = top * k / (levels - 1)
        best = min((t for t in ink if t[1] not in used), key=lambda t: abs(t[0] - want))
        used.add(best[1]); ramp.append(best)
    ramp.sort(key=lambda t: t[0])
    atlas = Image.new("L", (cw * len(ramp), ch), 0)
    for i, (_, _, im) in enumerate(ramp):
        atlas.paste(im, (i * cw, 0))
    out = TEX / f"atlas-{zoom}.png"
    atlas.save(out)
    return out, len(ramp)


# ---------------------------------------------------------------- art -> texture
def render_art(src, cols, rows, zoom, fill, out):
    """Crop (fill) or letterbox (fit) the cover to the tile, correct contrast, scale to 2 x 4 texels
    per character cell and sharpen at that final size, so edges, faces and text survive."""
    cw, ch = 6 * zoom, 9 * zoom
    W, H = cols * 2, rows * 4
    tile = (cols * cw) / (rows * ch)                      # aspect ratio of the grid on screen
    im = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    iw, ih = im.size
    if fill:
        if iw / ih > tile:
            nw = max(1, round(ih * tile)); x0 = (iw - nw) // 2
            im = im.crop((x0, 0, x0 + nw, ih))
        else:
            nh = max(1, round(iw / tile)); y0 = int((ih - nh) * 0.40)   # keep a little more of the top (faces)
            im = im.crop((0, y0, iw, y0 + nh))
        tw, th, ox, oy = W, H, 0, 0
    else:
        if iw / ih > tile:
            dw = cols * cw; dh = dw * ih / iw
        else:
            dh = rows * ch; dw = dh * iw / ih
        tw = max(2, int(round(dw / (cw / 2.0))) // 2 * 2)   # whole cells only
        th = max(4, int(round(dh / (ch / 4.0))) // 4 * 4)
        tw, th = min(tw, W), min(th, H)
        ox, oy = (W - tw) // 4 * 2, (H - th) // 8 * 4
    im = ImageOps.autocontrast(im, cutoff=(0.5, 0.5))
    im = im.resize((tw, th), Image.LANCZOS, reducing_gap=3.0)
    im = ImageEnhance.Contrast(im).enhance(1.12)
    im = ImageEnhance.Color(im).enhance(1.15)
    im = im.filter(ImageFilter.UnsharpMask(radius=1.0, percent=130, threshold=2))
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    canvas.paste(im.convert("RGBA"), (ox, oy))
    canvas.save(out, compress_level=1)


def local_path(uri):
    return urllib.parse.unquote(urllib.parse.urlparse(uri).path) if uri.startswith("file://") else ""


class Backend(QObject):
    stateChanged = Signal()
    volumeChanged = Signal()
    settingsChanged = Signal()
    atlasChanged = Signal()
    artReady = Signal(str, bool)            # texture url ("" = none), is it a new track (animate)?
    trackChanged = Signal()                 # show the title banner
    volumeShown = Signal()                  # show the volume OSD
    bannerShown = Signal(str, str)
    _mprisIn = Signal(object)
    _volIn = Signal(int, bool)
    _artIn = Signal(str, int)
    _cmdIn = Signal(str)
    _seekable = Signal(str, bool)

    def __init__(self):
        super().__init__()
        TEX.mkdir(parents=True, exist_ok=True)
        for old in TEX.glob("art-*.png"):
            old.unlink(missing_ok=True)
        s = {}
        try:
            s = json.loads(SETTINGS.read_text())
        except Exception:
            pass
        self._mode = s.get("mode") if s.get("mode") in (0, 1, 2, 3) else int(THEME.get("art_mode", 0))
        self._zoom = 2 if s.get("zoom") == 2 else 1
        self._fill = bool(s.get("fill", True))
        self._atlas = {z: build_atlas(z) for z in (1, 2)}
        self._title = self._artist = self._status = self._player = self._inst = ""
        self._pos = self._len = 0.0
        self._can_seek = False
        self._track = None
        self._art_src = ""                  # local file of the art being shown
        self._art_miss = 0
        self._test_art = None
        self._cols = self._rows = 0
        self._serial = 0
        self._pending_new = False
        self._files = []
        v = read_volume() or (50, False)
        self._volume, self._muted = v
        self._vol_set_at = 0.0
        self._bars = [0.0] * NBARS
        self._beat = 0.0
        self._bass_avg = self._bass_prev = 0.0
        self._frame_at = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._cava = None
        self.window = None
        self._pool = ThreadPoolExecutor(max_workers=2)
        self._mprisIn.connect(self._on_mpris, Qt.QueuedConnection)
        self._volIn.connect(self._on_vol, Qt.QueuedConnection)
        self._artIn.connect(self._on_art, Qt.QueuedConnection)
        self._cmdIn.connect(self.command, Qt.QueuedConnection)
        self._seekable.connect(self._on_seekable, Qt.QueuedConnection)
        for target in (self._poll_loop, self._cava_loop, self._sock_loop):
            threading.Thread(target=target, daemon=True).start()

    # ------------------------------------------------------------ properties for QML
    title = Property(str, lambda s: s._title, notify=stateChanged)
    artist = Property(str, lambda s: s._artist, notify=stateChanged)
    status = Property(str, lambda s: s._status, notify=stateChanged)
    player = Property(str, lambda s: s._player, notify=stateChanged)
    position = Property(float, lambda s: s._pos, notify=stateChanged)
    length = Property(float, lambda s: s._len, notify=stateChanged)
    canSeek = Property(bool, lambda s: s._can_seek, notify=stateChanged)
    volume = Property(int, lambda s: s._volume, notify=volumeChanged)
    muted = Property(bool, lambda s: s._muted, notify=volumeChanged)
    mode = Property(int, lambda s: s._mode, notify=settingsChanged)
    modeName = Property(str, lambda s: MODES[s._mode], notify=settingsChanged)
    zoom = Property(int, lambda s: s._zoom, notify=settingsChanged)
    fill = Property(bool, lambda s: s._fill, notify=settingsChanged)
    atlasUrl = Property(str, lambda s: QUrl.fromLocalFile(str(s._atlas[s._zoom][0])).toString(), notify=settingsChanged)
    nGlyphs = Property(int, lambda s: s._atlas[s._zoom][1], notify=settingsChanged)
    testWindow = Property(bool, lambda s: TEST_WINDOW, constant=True)

    # ------------------------------------------------------------ player state (once a second)
    def _poll_loop(self):
        while not self._stop.is_set():
            try:
                cur = mpris.choose(mpris.poll())
                if cur:
                    cur = dict(cur, art_local=local_path(mpris.art_file(cur["art"])))
                self._mprisIn.emit(cur)
                v = read_volume()
                if v:
                    self._volIn.emit(*v)
            except Exception as e:                        # never let the panel die on a bad poll
                print(f"[deck] poll: {e}", file=sys.stderr)
            self._wake.wait(1.0)
            self._wake.clear()

    @Slot(object)
    def _on_mpris(self, cur):
        if not cur:
            cur = dict(inst="", kind="", status="", pos=0.0, length=0.0, artist="", title="", art_local="")
        if cur["inst"] != self._inst:
            self._inst, self._can_seek = cur["inst"], False
            if cur["inst"]:
                inst = cur["inst"]
                self._pool.submit(lambda: self._seekable.emit(inst, run(
                    ["qdbus6", f"org.mpris.MediaPlayer2.{inst}", "/org/mpris/MediaPlayer2",
                     "org.mpris.MediaPlayer2.Player.CanSeek"]) == "true"))
        self._title, self._artist, self._status = cur["title"], cur["artist"], cur["status"]
        self._player, self._pos, self._len = cur["kind"], cur["pos"], cur["length"]
        track = (cur["inst"], cur["title"], cur["artist"])
        new_track = track != self._track
        if new_track:
            self._track, self._art_miss = track, 0
        self.stateChanged.emit()
        if new_track and cur["title"]:
            self.trackChanged.emit()
        if self._test_art is not None:
            return
        art = cur["art_local"] if os.path.isfile(cur["art_local"] or "") else ""
        if art and art != self._art_src:
            self._art_src = art
            self._render(True)
        elif not art and self._art_src:
            self._art_miss += 1                           # https art takes a moment to download
            if self._art_miss >= 4:
                self._art_src = ""
                self._pending_new = False
                self.artReady.emit("", True)

    @Slot(str, bool)
    def _on_seekable(self, inst, ok):
        if inst == self._inst:
            self._can_seek = ok
            self.stateChanged.emit()

    # ------------------------------------------------------------ art
    def _render(self, new_track):
        src = self._test_art if self._test_art is not None else self._art_src
        if not src or not self._cols:
            return
        self._serial += 1
        self._pending_new = self._pending_new or new_track
        serial, cols, rows, zoom, fill = self._serial, self._cols, self._rows, self._zoom, self._fill

        def job():
            out = TEX / f"art-{serial}.png"
            try:
                render_art(src, cols, rows, zoom, fill, out)
                self._artIn.emit(str(out), serial)
            except Exception as e:
                print(f"[deck] art: {e}", file=sys.stderr)
                self._artIn.emit("", serial)
        self._pool.submit(job)

    @Slot(str, int)
    def _on_art(self, path, serial):
        if serial != self._serial:                          # a newer render is on its way
            Path(path).unlink(missing_ok=True) if path else None
            return
        new, self._pending_new = self._pending_new, False
        if path:
            self._files.append(path)
            for old in self._files[:-3]:                  # the window still shows the last two
                Path(old).unlink(missing_ok=True)
            self._files = self._files[-3:]
        self.artReady.emit(QUrl.fromLocalFile(path).toString() if path else "", new)

    @Slot(int, int)
    def setGrid(self, cols, rows):
        if (cols, rows) != (self._cols, self._rows) and cols > 0 and rows > 0:
            self._cols, self._rows = cols, rows
            self._render(False)

    # ------------------------------------------------------------ settings
    def _save(self):
        try:
            SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS.write_text(json.dumps({"mode": self._mode, "zoom": self._zoom, "fill": self._fill}))
        except Exception:
            pass
        self.settingsChanged.emit()

    @Slot(int)
    def setMode(self, m):
        if 0 <= m < len(MODES) and m != self._mode:
            self._mode = m
            self._save()

    @Slot()
    def cycleMode(self):
        self.setMode((self._mode + 1) % len(MODES))

    @Slot()
    def toggleZoom(self):
        self._zoom = 2 if self._zoom == 1 else 1
        self._save()                                      # the window sends the new grid, which re-renders

    @Slot()
    def toggleFill(self):
        self._fill = not self._fill
        self._save()
        self._render(False)

    # ------------------------------------------------------------ transport (MPRIS)
    def _player_cmd(self, *args):
        if DEBUG:
            print(f"[deck] player command {args}", file=sys.stderr, flush=True)
        if self._inst:
            inst = self._inst
            self._pool.submit(lambda: (run(["playerctl", "-p", inst, *args]), time.sleep(0.25), self._wake.set()))

    @Slot()
    def playPause(self):
        self._player_cmd("play-pause")

    @Slot()
    def next(self):
        self._player_cmd("next")

    @Slot()
    def previous(self):
        self._player_cmd("previous")

    @Slot(float)
    def seek(self, fraction):
        if self._can_seek and self._len > 0:
            self._pos = max(0.0, min(1.0, fraction)) * self._len
            self.stateChanged.emit()
            self._player_cmd("position", f"{self._pos:.2f}")

    # ------------------------------------------------------------ volume (PipeWire)
    @Slot(int)
    def setVolume(self, v):
        v = max(0, min(100, int(v)))                      # never above 100 %
        if DEBUG:
            print(f"[deck] setVolume {self._volume} -> {v} (muted={self._muted})", file=sys.stderr, flush=True)
        unmute = self._muted and v > self._volume
        changed = v != self._volume
        self._volume, self._vol_set_at = v, time.monotonic()
        if unmute:
            self._muted = False
        self.volumeChanged.emit()
        self.volumeShown.emit()
        if changed or unmute:
            self._pool.submit(lambda: (run(["wpctl", "set-volume", SINK, f"{v}%"]),
                                       unmute and run(["wpctl", "set-mute", SINK, "0"])))

    @Slot(int)
    def volumeStep(self, d):
        self.setVolume(self._volume + d)

    @Slot()
    def toggleMute(self):
        self._muted, self._vol_set_at = not self._muted, time.monotonic()
        m = "1" if self._muted else "0"
        self.volumeChanged.emit()
        self.volumeShown.emit()
        self._pool.submit(lambda: run(["wpctl", "set-mute", SINK, m]))

    @Slot(int, bool)
    def _on_vol(self, v, muted):                          # changed outside the deck (laptop keys, mixer)
        if time.monotonic() - self._vol_set_at < 1.5:
            return
        if (v, muted) != (self._volume, self._muted):
            if DEBUG:
                print(f"[deck] volume changed outside the deck: {self._volume} -> {v} muted={muted}", file=sys.stderr, flush=True)
            self._volume, self._muted = v, muted
            self.volumeChanged.emit()
            self.volumeShown.emit()

    # ------------------------------------------------------------ cava (raw output through a FIFO)
    def _cava_loop(self):
        CAVA_CONF.write_text(
            "[general]\nframerate = 15\nbars = %d\nsleep_timer = 2\n\n[input]\nmethod = pulse\nsource = auto\n\n"
            "[output]\nmethod = raw\nraw_target = %s\ndata_format = binary\nbit_format = 8bit\nchannels = mono\n"
            "mono_option = average\n\n[smoothing]\nnoise_reduction = 60\n" % (NBARS, FIFO))
        while not self._stop.is_set():
            try:
                if not FIFO.is_fifo():
                    FIFO.unlink(missing_ok=True)
                    os.mkfifo(FIFO, 0o600)
                self._cava = subprocess.Popen(["cava", "-p", str(CAVA_CONF)], stdin=subprocess.DEVNULL,
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                with open(FIFO, "rb", buffering=0) as f:  # waits here until cava opens its end
                    buf = b""
                    while not self._stop.is_set():
                        chunk = f.read(4096)
                        if not chunk:
                            break
                        buf += chunk
                        if len(buf) >= NBARS:
                            frame = buf[len(buf) - (len(buf) % NBARS) - NBARS:][:NBARS]   # newest whole frame
                            buf = buf[len(buf) - (len(buf) % NBARS):]
                            self._feed([b / 255.0 for b in frame])
            except Exception as e:
                print(f"[deck] cava: {e}", file=sys.stderr)
            finally:
                if self._cava and self._cava.poll() is None:
                    self._cava.terminate()
            self._stop.wait(3.0)                          # cava ended (audio server restart?): try again

    def _feed(self, bars):
        bass = sum(bars[:5]) / 5.0
        self._bass_avg = self._bass_avg * 0.94 + bass * 0.06
        hit = max(0.0, (bass - self._bass_avg * 1.08) * 3.0) + max(0.0, bass - self._bass_prev) * 2.5
        self._bass_prev = bass
        with self._lock:
            self._bars = bars
            self._beat = max(self._beat * 0.78, min(1.0, hit))
            self._frame_at = time.monotonic()

    @Slot(result="QVariantList")
    def levels(self):
        """32 bars + the beat envelope; asked for by the window on each of its (at most 15) ticks a second."""
        with self._lock:
            if time.monotonic() - self._frame_at > 0.35:  # cava goes quiet when nothing plays
                self._bars = [b * 0.6 for b in self._bars]
                self._beat *= 0.6
            return self._bars + [self._beat]

    # ------------------------------------------------------------ commands from deck/ctl.py (global shortcuts)
    def _sock_loop(self):
        try:
            SOCK.unlink(missing_ok=True)
            s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            s.bind(str(SOCK))
            os.chmod(SOCK, 0o600)
        except Exception as e:
            print(f"[deck] socket: {e}", file=sys.stderr)
            return
        while not self._stop.is_set():
            try:
                self._cmdIn.emit(s.recv(4096).decode("utf-8", "replace").strip())
            except Exception:
                time.sleep(0.2)

    @Slot(str)
    def command(self, text):
        cmd, _, arg = text.partition(" ")
        if cmd == "vol+":
            self.volumeStep(5)
        elif cmd == "vol-":
            self.volumeStep(-5)
        elif cmd == "mute":
            self.toggleMute()
        elif cmd == "next":
            self.next()
        elif cmd == "prev":
            self.previous()
        elif cmd == "playpause":
            self.playPause()
        elif cmd == "mode":
            self.setMode(int(arg)) if arg.isdigit() else self.cycleMode()
        elif cmd == "zoom":
            self.toggleZoom()
        elif cmd == "fill":
            self.toggleFill()
        # the three below exist for testing and demos: show a banner, show a picture, save what the window shows
        elif cmd == "banner":
            title, _, artist = arg.partition("|")
            self.bannerShown.emit(title, artist)
        elif cmd == "art":
            self._test_art = None if arg in ("", "-") else arg
            if self._test_art is None:
                self._art_src = ""                        # the next poll puts the player's art back
            else:
                self._render(True)
        elif cmd == "grab" and self.window is not None:
            self.window.grabWindow().save(arg)            # exactly what the window shows, as a PNG
        elif cmd == "ev" and DEBUG and self.window is not None:
            self._inject(arg)

    def _inject(self, arg):
        """Debug only (RICE_DECK_DEBUG=1): send a synthetic key / mouse / wheel / touch event to the window,
        so the real QML handlers can be tested without hands. Positions are fractions of the window."""
        from PySide6.QtCore import QCoreApplication, QPoint, QPointF
        from PySide6.QtGui import QWheelEvent
        from PySide6.QtTest import QTest
        w, a = self.window, arg.split()
        pt = lambda fx, fy: QPoint(int(float(fx) * w.width()), int(float(fy) * w.height()))
        kind = a[0]
        if kind == "key":                                 # straight to the item that handles keys: no focus needed
            from PySide6.QtGui import QKeyEvent
            from PySide6.QtCore import QEvent
            from PySide6.QtQuick import QQuickItem
            item = w.findChild(QQuickItem, "deckRoot")
            key = getattr(Qt, "Key_" + a[1])
            for t in (QEvent.KeyPress, QEvent.KeyRelease):
                QCoreApplication.sendEvent(item, QKeyEvent(t, key, Qt.NoModifier))
        elif kind == "click":
            QTest.mouseClick(w, Qt.LeftButton, Qt.NoModifier, pt(a[1], a[2]))
        elif kind == "drag":
            p0, p1 = pt(a[1], a[2]), pt(a[3], a[4])
            QTest.mousePress(w, Qt.LeftButton, Qt.NoModifier, p0)
            for i in range(1, 9):
                QTest.mouseMove(w, p0 + (p1 - p0) * i / 8)
            QTest.mouseRelease(w, Qt.LeftButton, Qt.NoModifier, p1)
        elif kind == "wheel":
            p = QPointF(pt(a[2], a[3]))
            ev = QWheelEvent(p, w.mapToGlobal(p), QPoint(), QPoint(0, int(a[1])), Qt.NoButton, Qt.NoModifier,
                             Qt.NoScrollPhase, False)
            for _ in range(int(a[4]) if len(a) > 4 else 1):       # optional count: a burst, like a touchpad flick
                QCoreApplication.sendEvent(w, ev)
        elif kind in ("tap", "swipe"):
            if not hasattr(self, "_touch"):
                self._touch = QTest.createTouchDevice()
            p0 = pt(a[1], a[2])
            p1 = pt(a[3], a[4]) if kind == "swipe" else p0
            QTest.touchEvent(w, self._touch).press(0, p0, w).commit()
            if kind == "swipe":
                for i in range(1, 9):
                    QTest.touchEvent(w, self._touch).move(0, p0 + (p1 - p0) * i / 8, w).commit()
            QTest.touchEvent(w, self._touch).release(0, p1, w).commit()

    @Slot()
    def countFrame(self):
        self._frames = getattr(self, "_frames", 0) + 1

    @Slot()
    def reportFrames(self):
        n, self._frames = getattr(self, "_frames", 0), 0
        print(f"[deck] frames in last 5 s: {n} = {n / 5:.1f} fps", file=sys.stderr, flush=True)

    def shutdown(self):
        self._stop.set()
        self._wake.set()
        if self._cava and self._cava.poll() is None:
            self._cava.terminate()
        SOCK.unlink(missing_ok=True)
        FIFO.unlink(missing_ok=True)


def main():
    QGuiApplication.setDesktopFileName("rice-deck")        # Wayland app id: the rice tiler and frame rule match it
    app = QGuiApplication(sys.argv)
    app.setApplicationName("rice-deck")
    # Native (FreeType) text instead of Qt's distance-field glyphs: sharper for a pixel font, and the
    # distance-field generator crashed the render thread here (heap corruption while caching glyphs).
    QQuickWindow.setTextRenderType(QQuickWindow.TextRenderType.NativeTextRendering)
    backend = Backend()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("backend", backend)
    engine.rootContext().setContextProperty("theme", THEME)
    engine.load(QUrl.fromLocalFile(str(HERE / "Deck.qml")))
    if not engine.rootObjects():
        backend.shutdown()
        sys.exit(1)
    backend.window = engine.rootObjects()[0]
    if DEBUG:                                             # frames actually shown per second (the cap is 15)
        # frameSwapped is emitted on Qt's render thread: it must only be QUEUED to a slot of an object
        # that lives in the main thread. Running Python on the render thread corrupts memory.
        backend.window.frameSwapped.connect(backend.countFrame, Qt.QueuedConnection)
        fps = QTimer()
        fps.timeout.connect(backend.reportFrames)
        fps.start(5000)
        backend._fps_timer = fps
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    keepalive = QTimer()                                  # lets Python see the signals above
    keepalive.start(500)
    keepalive.timeout.connect(lambda: None)
    rc = app.exec()
    backend.shutdown()
    del engine                                            # the window goes first, so no binding outlives the backend
    sys.exit(rc)


if __name__ == "__main__":
    main()
