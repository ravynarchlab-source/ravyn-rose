// Rice tiler (KWin script). `rice on` asks flavour.py to fill in @MODE@ (auto | pear | fooyin), the gap and
// the tile map of the flavour in use, loads the result into KWin, and `rice off` unloads it.
// Places: windows whose class starts with "rice-", the lyrics window (title "rice-lyrics"),
// and ONE music player window in the music tile:
//   auto   -> Pear Desktop if it has a window, otherwise fooyin
//   pear   -> Pear Desktop only        fooyin -> fooyin only
// Uses the real usable area in logical pixels (after scaling, minus the panel) and re-tiles when the
// screen geometry changes (rotation / tablet mode) or when a player window opens or closes.
const MODE = "@MODE@";
const GAP = @GAP@;
// {landscape: {role: [x, y, width, height]}, portrait: {...}} as fractions of the usable screen area.
// The maps live in flavours/*.json (and in flavour.py for the classic layout).
const TILES = @TILES@;

function cls(w) { return String(w.resourceClass || "").toLowerCase(); }
function isMain(w) { return w.normalWindow && !w.transient; }
function isPear(w) { return isMain(w) && cls(w).indexOf("youtube-music") >= 0; }
function isFooyin(w) { return isMain(w) && cls(w).indexOf("fooyin") >= 0; }

function playerKind() {
  if (MODE === "pear" || MODE === "fooyin") return MODE;
  return workspace.windowList().some(isPear) ? "pear" : "fooyin";
}

function role(w, kind) {
  const c = cls(w);
  if (c.indexOf("rice-") === 0) return c.substring(5);
  if (String(w.caption) === "rice-lyrics") return "lyrics";
  if ((kind === "pear" && isPear(w)) || (kind === "fooyin" && isFooyin(w))) return "player";
  return null;
}

function place(w, kind) {
  const r = role(w, kind);
  if (!r) return;
  if (r === "picker") { w.keepAbove = true; return; }     // the flavour picker: never tiled, never behind a tile
  const a = workspace.clientArea(KWin.MaximizeArea, workspace.activeScreen, workspace.currentDesktop);
  const f = (a.height > a.width ? TILES.portrait : TILES.landscape)[r];
  if (!f) return;
  if (w.setMaximize) w.setMaximize(false, false);
  if (w.fullScreen) w.fullScreen = false;
  if (r !== "player") { w.skipTaskbar = true; w.skipPager = true; }   // players stay in the taskbar
  else if (workspace.raiseWindow) workspace.raiseWindow(w);             // chosen player in front of the other one
  const x = Math.round(a.x + f[0] * a.width + GAP / 2);
  const y = Math.round(a.y + f[1] * a.height + GAP / 2);
  w.frameGeometry = {x: x, y: y, width: Math.round(f[2] * a.width - GAP), height: Math.round(f[3] * a.height - GAP)};
  print("rice-tile:", r, kind, a.width + "x" + a.height, "->", x, y);
}

function tileAll() { const k = playerKind(); workspace.windowList().forEach(function (w) { place(w, k); }); }

workspace.windowAdded.connect(function (w) { if (isPear(w) || isFooyin(w)) tileAll(); else place(w, playerKind()); });
workspace.windowRemoved.connect(function (w) { if (MODE === "auto" && isPear(w)) tileAll(); });
workspace.screensChanged.connect(tileAll);
workspace.virtualScreenGeometryChanged.connect(tileAll);
tileAll();
