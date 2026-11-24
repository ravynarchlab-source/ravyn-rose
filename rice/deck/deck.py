#!/usr/bin/env python3
"""Rice ASCII DECK: the centre panel. One PySide6 process: this backend plus the Qt Quick window Deck.qml.

What it does
  * follows the music player that is playing (Pear first, then fooyin; browsers ignored) with the same
    code as the lyrics panel (lyrics.py: poll / choose / art_file), once a second;
  * turns the player's cover art (mpris:artUrl; https art is cached in ~/.cache/rice/art by lyrics.py)
    into a small texture: cropped to the tile, contrast-corrected, sharpened, 2 x 4 texels per character
    cell. The shader ascii.frag draws the characters from it. No video is downloaded or streamed;
  * moves with the music: bass, loudness, beats and the 32 spectrum bars come from the shared analyser
    (react/analyser.py, read through react/client.py), the same clock the lyrics bar uses. While music
    plays and `rice react` is not off, the window draws every display frame; otherwise 15 times a second
    or less;
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
from react import client as react                         # noqa: E402  (the shared analyser's reading side)

RUN = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "rice"
TEX = RUN / "deck"                                        # textures for the shader (tmpfs)
SOCK, STATS = RUN / "deck.sock", RUN / "deck.stats"
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "rice"
MONOCRAFT = Path.home() / ".local/share/fonts/rice/Monocraft.otf"   # put there by install.sh
DEBUG = bool(os.environ.get("RICE_DECK_DEBUG"))          # extra logging and the test-event injector
MEASURE = DEBUG or bool(os.environ.get("RICE_DECK_STATS"))   # frame rate and lag, written to deck.stats every 2 s
FX = {"mono": 0, "rose": 1, "neon": 2, "amber": 3, "paper": 4}      # the reaction each flavour has (ascii.frag)
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
    # one texel per cell: the cell's average in a single read, and a cheap blur for glow and edges
    canvas.resize((cols, rows), Image.BOX).save(low_path(out), compress_level=1)


def low_path(path):
    return str(path)[:-4] + "-low.png"


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
    volumeRipple = Signal(int)              # +1 louder, -1 quieter: a wave crosses the deck
    reactChanged = Signal()
    bannerShown = Signal(str, str)
    _mprisIn = Signal(object)
    _volIn = Signal(int, bool)
    _artIn = Signal(str, int)
    _cmdIn = Signal(str)
    _seekable = Signal(str, bool)
    _reactIn = Signal(float, bool)

    def __init__(self):
        super().__init__()
        TEX.mkdir(parents=True, exist_ok=True)
        self._reader = react.Reader()
        self._reduced = react.reduced_motion()
        self._amount = react.amount()
        self._fake = None                   # (debug) a pretend player, so the window can be tested without one
        self._stat = dict(swaps=[], age=[], build=[], beat=[], t0=time.monotonic())
        self._built_at = self._beat_due = 0.0
        self._beats_seen = 0
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
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.window = None
        self._pool = ThreadPoolExecutor(max_workers=2)
        self._mprisIn.connect(self._on_mpris, Qt.QueuedConnection)
        self._volIn.connect(self._on_vol, Qt.QueuedConnection)
        self._artIn.connect(self._on_art, Qt.QueuedConnection)
        self._cmdIn.connect(self.command, Qt.QueuedConnection)
        self._seekable.connect(self._on_seekable, Qt.QueuedConnection)
        self._reactIn.connect(self._on_react, Qt.QueuedConnection)
        for target in (self._poll_loop, self._sock_loop):
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
    amount = Property(float, lambda s: s._amount, notify=reactChanged)        # 0 off, 0.45 subtle, 1 full
    reducedMotion = Property(bool, lambda s: s._reduced, notify=reactChanged)
    fx = Property(int, lambda s: FX.get(THEME.get("react", THEME["name"]), 1), constant=True)

    # ------------------------------------------------------------ player state (once a second)
    def _poll_loop(self):
        while not self._stop.is_set():
            try:
                cur = mpris.choose(mpris.poll(), self._inst)   # stays with the player it follows while that is paused
                if cur:
                    cur = dict(cur, art_local=local_path(mpris.art_file(cur["art"])))
                self._mprisIn.emit(cur)
                v = read_volume()
                if v:
                    self._volIn.emit(*v)
                self._reactIn.emit(react.amount(), react.reduced_motion())   # `rice react ...` takes effect within a second
            except Exception as e:                        # never let the panel die on a bad poll
                print(f"[deck] poll: {e}", file=sys.stderr)
            self._wake.wait(1.0)
            self._wake.clear()

    @Slot(float, bool)
    def _on_react(self, amount, reduced):
        if (amount, reduced) != (self._amount, self._reduced):
            self._amount, self._reduced = amount, reduced
            self.reactChanged.emit()

    @Slot(object)
    def _on_mpris(self, cur):
        if self._fake is not None:
            f = self._fake
            pos = f["pos"] + (time.monotonic() - f["at"] if f["status"] == "Playing" else 0.0)
            cur = dict(inst="fake", kind="fooyin", status=f["status"], pos=pos, length=f["length"],
                       artist=f["artist"], title=f["title"], art_local="")
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
            if path:
                Path(path).unlink(missing_ok=True)
                Path(low_path(path)).unlink(missing_ok=True)
            return
        new, self._pending_new = self._pending_new, False
        if path:
            self._files.append(path)
            for old in self._files[:-3]:                  # the window still shows the last two
                Path(old).unlink(missing_ok=True)
                Path(low_path(old)).unlink(missing_ok=True)
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
        if changed:
            self.volumeRipple.emit(1 if v > self._volume else -1)
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
        self.volumeRipple.emit(-1 if self._muted else 1)
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
            self.volumeRipple.emit(1 if (v > self._volume or (self._muted and not muted)) else -1)
            self._volume, self._muted = v, muted
            self.volumeChanged.emit()
            self.volumeShown.emit()

    # ------------------------------------------------------------ the music, once per drawn frame
    @Slot(result="QVariantList")
    def frame(self):
        """What the window needs for the frame it is about to draw:
        [amount, bass, mids, highs, loud, kick, glow, ring, glitch, seed, bpm, sound, 32 bars, alive]."""
        now = time.monotonic()
        if self._reduced:
            o = self._reader.zero
        else:
            o = self._reader.frame(now)
        if MEASURE:
            self._built_at = now
            if o["sound"]:
                self._stat["age"].append(o["age"])
            if o["beats"] != self._beats_seen:
                self._beats_seen, self._beat_due = o["beats"], o.get("beat_t", 0.0)
        return [self._amount, o["bass"], o["mids"], o["highs"], o["loud"], o["kick"], o["glow"], o["ring"],
                o["glitch"], o["seed"], o["bpm"], 1.0 if o["sound"] else 0.0, *o["bars"], 1.0 if o["alive"] else 0.0]

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
        elif cmd == "fake" and DEBUG:                     # fake Playing|Paused|- title|artist|seconds
            status, _, rest = arg.partition(" ")
            title, artist, length = (rest.split("|") + ["", "", ""])[:3]
            self._fake = None if status == "-" else dict(status=status, title=title, artist=artist,
                                                         length=float(length or 180), pos=0.0, at=time.monotonic())
            self._wake.set()
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
        """A frame was handed to the compositor (queued here from the render thread)."""
        now = time.monotonic()
        st = self._stat
        st["swaps"].append(now)
        if self._built_at:
            st["build"].append(now - self._built_at)
        if self._beat_due:                                # first frame that shows a new beat: how late after it was due?
            st["beat"].append(now - self._beat_due)
            self._beat_due = 0.0

    @Slot()
    def reportFrames(self):
        st, now = self._stat, time.monotonic()
        span, swaps = now - st["t0"], st["swaps"]
        gaps = sorted((b - a) * 1000 for a, b in zip(swaps, swaps[1:]))
        pct = lambda xs, q: round(sorted(xs)[min(len(xs) - 1, int(len(xs) * q))] * 1000, 1) if xs else None
        out = dict(at=time.time(), seconds=round(span, 2), fps=round(len(swaps) / span, 1) if span > 0 else 0.0,
                   frame_ms_p50=round(gaps[len(gaps) // 2], 2) if gaps else None,
                   frame_ms_p95=round(gaps[int(len(gaps) * 0.95)], 2) if gaps else None,
                   frame_ms_max=round(gaps[-1], 2) if gaps else None,
                   over_25ms=sum(1 for g in gaps if g > 25.0),
                   data_age_ms_p50=pct(st["age"], 0.5), data_age_ms_p95=pct(st["age"], 0.95),
                   build_to_swap_ms_p50=pct(st["build"], 0.5), build_to_swap_ms_p95=pct(st["build"], 0.95),
                   beats=len(st["beat"]), beat_due_to_swap_ms_p50=pct(st["beat"], 0.5),
                   beat_due_to_swap_ms_p95=pct(st["beat"], 0.95), beat_due_to_swap_ms_max=pct(st["beat"], 0.999))
        self._stat = dict(swaps=[], age=[], build=[], beat=[], t0=now)
        try:
            with open(STATS, "a") as f:
                f.write(json.dumps(out) + "\n")
        except OSError:
            pass
        if DEBUG:
            print(f"[deck] {out}", file=sys.stderr, flush=True)

    def shutdown(self):
        self._stop.set()
        self._wake.set()
        self._reader.close()
        SOCK.unlink(missing_ok=True)


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
    if MEASURE:                                           # frames actually handed over, and how late after the beat
        # frameSwapped is emitted on Qt's render thread: it must only be QUEUED to a slot of an object
        # that lives in the main thread. Running Python on the render thread corrupts memory.
        backend.window.frameSwapped.connect(backend.countFrame, Qt.QueuedConnection)
        fps = QTimer()
        fps.timeout.connect(backend.reportFrames)
        fps.start(2000)
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
