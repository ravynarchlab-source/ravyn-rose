#!/usr/bin/env python3
"""Rice lyrics backend: follows the music player that is actually playing and writes the current
lyric line to ~/.cache/rice/now.json, which the Qt Quick window (lyrics.qml) displays.

Players: only music players are followed (Pear Desktop first, then fooyin). Browsers are ignored.
Lyrics:  1) the .lrc next to the playing file (same name)  2) LRCLIB (lrclib.net, public API, no key),
         matched on artist + title + album + duration. Results, including "not found", are cached in
         ~/.cache/rice/lyrics/. Only artist, title, album and duration are sent to lrclib.net.
Art:     the player's mpris:artUrl; http(s) art is cached in ~/.cache/rice/art/.
Polls the players once a second (one playerctl call) and interpolates the position in between."""
import hashlib, json, os, re, subprocess, sys, threading, time, urllib.parse, urllib.request
from pathlib import Path

CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "rice"
LYR, ART, NOW = CACHE / "lyrics", CACHE / "art", CACHE / "now.json"
for d in (LYR, ART):
    d.mkdir(parents=True, exist_ok=True)
UA = "rice-lyrics/1.0 (personal desktop lyrics widget)"
ALLOW = [("pear", re.compile(r"youtube.?music|pear", re.I)), ("fooyin", re.compile(r"^fooyin", re.I))]
TAG = re.compile(r"\[(\d+):(\d+(?:[.:]\d+)?)\]")
ARABIC = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
FMT = "\t".join(["{{playerInstance}}", "{{status}}", "{{position}}", "{{mpris:length}}", "{{xesam:url}}",
                 "{{xesam:artist}}", "{{xesam:title}}", "{{xesam:album}}", "{{mpris:artUrl}}"])

def kind(instance):
    for k, rx in ALLOW:
        if rx.search(instance):
            return k
    return None                                    # browsers and anything else: ignored

def poll():
    try:
        r = subprocess.run(["playerctl", "-a", "metadata", "--format", FMT], capture_output=True, text=True, timeout=3)
    except Exception:
        return []
    out = []
    for line in r.stdout.splitlines():
        f = (line.split("\t") + [""] * 9)[:9]
        k = kind(f[0])
        if not k:
            continue
        num = lambda s: int(s) if s.strip().lstrip("-").isdigit() else 0
        out.append(dict(inst=f[0], kind=k, status=f[1], pos=num(f[2]) / 1e6, length=num(f[3]) / 1e6,
                        url=f[4], artist=f[5], title=f[6], album=f[7], art=f[8]))
    return out

def choose(players):
    prio = {"pear": 0, "fooyin": 1}
    for wanted in ("Playing", "Paused"):
        c = [p for p in players if p["status"] == wanted]
        if c:
            return sorted(c, key=lambda p: prio[p["kind"]])[0]
    return sorted(players, key=lambda p: prio[p["kind"]])[0] if players else None

# ---------------- lyrics ----------------
def parse_lrc(text):
    offset, lines = 0.0, []
    for raw in text.splitlines():
        m = re.match(r"\[offset:\s*([+-]?\d+)\]", raw.strip(), re.I)
        if m:
            offset = int(m.group(1)) / 1000
            continue
        stamps = TAG.findall(raw)
        body = TAG.sub("", raw).strip()
        for mm, ss in stamps:
            lines.append((int(mm) * 60 + float(ss.replace(":", ".")), body))
    lines.sort()
    return [(t - offset, s) for t, s in lines] or None

def local_lrc(url):
    if not url.startswith("file://"):
        return None
    audio = Path(urllib.parse.unquote(urllib.parse.urlparse(url).path))
    for cand in (audio.with_suffix(".lrc"), audio.with_suffix(".LRC")):
        if cand.is_file():
            return parse_lrc(cand.read_text(encoding="utf-8", errors="replace"))
    return None

def clean_title(t):
    t = re.sub(r"\s*[\(\[][^\)\]]*(official|video|audio|lyric|visuali[sz]er|remaster|hd|4k|mv)[^\)\]]*[\)\]]", "", t, flags=re.I)
    return re.sub(r"\s+(ft\.?|feat\.?)\s.*$", "", t, flags=re.I).strip()

def http_json(path, params):
    url = "https://lrclib.net" + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise

def lrclib(artist, title, album, dur):
    """Return synced LRC text or None. Raises on network failure (caller retries later)."""
    titles = [title] + ([clean_title(title)] if clean_title(title) != title else [])
    for t in titles:
        p = {"artist_name": artist, "track_name": t}
        if album:
            p["album_name"] = album
        if dur:
            p["duration"] = round(dur)
        d = http_json("/api/get", p)
        if d and d.get("syncedLyrics"):
            return d["syncedLyrics"]
    for t in titles:
        res = http_json("/api/search", {"artist_name": artist, "track_name": t}) or []
        best = [r for r in res if r.get("syncedLyrics") and (not dur or abs((r.get("duration") or 0) - dur) <= 3)]
        if best:
            best.sort(key=lambda r: abs((r.get("duration") or 0) - (dur or 0)))
            return best[0]["syncedLyrics"]
    return None

class Lyrics:
    def __init__(self):
        self.key = None; self.lines = None; self.state = "idle"; self.retry_at = 0; self.lock = threading.Lock()

    def want(self, p):
        key = (p["url"] if p["url"].startswith("file://") else "", p["artist"], p["title"], round(p["length"] or 0))
        with self.lock:
            if key == self.key and not (self.state == "error" and time.time() > self.retry_at):
                return
            self.key, self.lines, self.state = key, None, "searching"
        threading.Thread(target=self.load, args=(key, dict(p)), daemon=True).start()

    def load(self, key, p):
        lines, state = local_lrc(p["url"]), None
        if lines:
            state = "ok"
        elif p["artist"] and p["title"]:
            cf = LYR / (hashlib.sha1("|".join(map(str, key[1:])).encode()).hexdigest() + ".json")
            try:
                if cf.is_file():
                    c = json.loads(cf.read_text())
                    if c.get("synced") or time.time() - c.get("fetched", 0) < 7 * 86400:
                        lines = parse_lrc(c["synced"]) if c.get("synced") else None
                        state = "ok" if lines else "none"
                if state is None:
                    synced = lrclib(p["artist"], p["title"], p["album"], p["length"])
                    cf.write_text(json.dumps({"artist": p["artist"], "title": p["title"], "synced": synced,
                                              "source": "lrclib" if synced else "none", "fetched": time.time()}))
                    lines = parse_lrc(synced) if synced else None
                    state = "ok" if lines else "none"
            except Exception:
                state = "error"
        else:
            state = "none"
        with self.lock:
            if self.key == key:
                self.lines, self.state = lines, state
                if state == "error":
                    self.retry_at = time.time() + 60

    def line_at(self, pos):
        with self.lock:
            lines, state = self.lines, self.state
        if state != "ok" or not lines:
            return None, state
        cur = ""
        for t, s in lines:
            if t > pos:
                break
            cur = s
        return cur, state

# ---------------- album art ----------------
_art = {}
def art_file(url):
    if not url:
        return ""
    if url.startswith("file://"):
        return url
    if not url.startswith(("http://", "https://")):
        return ""
    if url in _art:
        return _art[url]
    f = ART / (hashlib.sha1(url.encode()).hexdigest() + ".img")
    _art[url] = ""
    def fetch():
        try:
            if not f.is_file():
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=8) as r:
                    data = r.read(5 * 1024 * 1024)
                tmp = f.with_suffix(".tmp"); tmp.write_bytes(data); tmp.replace(f)
            _art[url] = f.as_uri()
        except Exception:
            _art.pop(url, None)
    threading.Thread(target=fetch, daemon=True).start()
    return ""

# ---------------- main loop ----------------
def write(state):
    tmp = NOW.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    tmp.replace(NOW)

def main():
    lyr, cur, base_t, last_poll, shown, seq = Lyrics(), None, 0.0, 0.0, None, 0
    while True:
        now = time.monotonic()
        if now - last_poll >= 1.0:
            cur = choose(poll()); base_t = now; last_poll = now
            if cur and cur["status"] in ("Playing", "Paused"):
                lyr.want(cur)
        if not cur:
            st = dict(line="♪ no music player running", status="none", sub="", art="", player="")
        else:
            pos = cur["pos"] + (now - base_t if cur["status"] == "Playing" else 0)
            sub = " — ".join(x for x in (cur["artist"], cur["title"]) if x)
            if cur["status"] not in ("Playing", "Paused"):
                line = f"♪ {cur['kind']} ready · nothing playing"
            else:
                text, state = lyr.line_at(pos)
                line = {"ok": text or "♪", "searching": "♪ looking for synced lyrics…",
                        "none": "♪ no synced lyrics", "error": "♪ lyrics offline — retrying"}.get(state, "♪")
            st = dict(line=line, status=cur["status"], sub=sub, art=art_file(cur["art"]), player=cur["kind"])
        st["rtl"] = bool(ARABIC.search(st["line"]))
        if st != shown:
            seq += 1; shown = dict(st); write(dict(st, seq=seq))
        time.sleep(0.15)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
