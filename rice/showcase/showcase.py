#!/usr/bin/env python3
"""Rice terminal showcase: what the terminal tile shows until you ask for a shell.

Top:    fastfetch with the flavour's RAVYN logo (no user or host name is ever printed).
Below:  a block clock, and a part that rotates between the album art of the playing track (chafa) and a
        few small ASCII scenes. A wide tile puts the clock beside it, a tall one above it.

Keys:   Enter, Space or q   leave the showcase and start a shell in the same tile
        any other key       next page
Pages can be pinned from outside by writing clock | art | scene:NAME | auto to $XDG_RUNTIME_DIR/rice/showcase.page.

Speed:  the first frame needs only os, sys and time, a small key/value file and a header that `rice`
        rendered in advance, so the tile is drawn a few dozen ms after Python starts. Everything slower
        (json, subprocess, chafa) is loaded after that. The time from launch to first frame is written
        to ~/.local/state/rice/log. Run with `python3 -S` to skip the site module as well."""
import os, sys, time

P0 = time.time_ns()                                            # this process, before anything else is loaded
HERE = os.path.dirname(os.path.abspath(__file__))
T0 = int(os.environ.get("RICE_T0") or 0)                       # set by `rice` when it launches the tile (ns)
HOME = os.path.expanduser("~")
RUN = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "rice")
CACHE = os.path.join(os.environ.get("XDG_CACHE_HOME", HOME + "/.cache"), "rice")
STATE = os.path.join(os.environ.get("XDG_STATE_HOME", HOME + "/.local/state"), "rice")
PAGE_SECONDS = 9
RESET = "\x1b[0m"


def load_theme():
    """showcase.kv (tab-separated, written by flavour.py build) from the folder given on the command line."""
    d = os.path.dirname(sys.argv[1]) if len(sys.argv) > 1 else ""
    if not os.path.isfile(os.path.join(d, "showcase.kv")):     # started by hand: build the files now
        sys.path.insert(0, os.path.dirname(HERE))
        import flavour
        d = os.path.join(RUN, "flavour")
        flavour.build(flavour.current(), d)
    th = {"dir": d}
    with open(os.path.join(d, "showcase.kv"), encoding="utf-8") as f:
        for line in f:
            k, _, v = line.rstrip("\n").partition("\t")
            th[k] = v
    return th


TH = load_theme()


def col(h):
    return "\x1b[38;2;%d;%d;%dm" % tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


C_HI, C_DIM, C_FAINT, C_ACC = (col(TH[k]) for k in ("hi", "dim", "faint", "accent"))


def which(name):
    for p in os.environ.get("PATH", "/usr/bin").split(":"):
        if os.access(os.path.join(p, name), os.X_OK):
            return os.path.join(p, name)
    return ""


def run(cmd, timeout=4):
    import subprocess
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


# ---------------------------------------------------------------- header: fastfetch + logo
def header(cols, fresh):
    """fresh=False: use the copy `rice` rendered when the flavour was applied (first frame).
    fresh=True: ask fastfetch again, so uptime and memory stay current."""
    small = cols < 64                                         # narrow tile: short logo on top of the facts
    pos = "top" if small else "left"
    cached = os.path.join(TH["dir"], "header-%s.ans" % pos)
    logo = os.path.join(HERE, "logos", TH["name"] + ("-small" if small else "") + ".txt")
    out = ""
    if not fresh and os.path.isfile(cached):
        with open(cached, encoding="utf-8") as f:
            out = f.read()
    elif which("fastfetch"):
        out = run(["fastfetch", "-c", TH["fastfetch"], "--pipe", "false", "--logo", logo, "--logo-position", pos])
    if not out.strip():                                       # fastfetch missing: the logo alone
        with open(logo, encoding="utf-8") as f:
            out = C_ACC + f.read().replace("$1", "") + C_DIM + " (install fastfetch for system facts)\n"
    lines = out.rstrip("\n").split("\n")
    while lines and not lines[-1].replace("\x1b[m", "").replace("\x1b[0m", "").strip():
        lines.pop()
    return lines


# ---------------------------------------------------------------- clock
DIGITS = {"0": "111101101101111", "1": "001001001001001", "2": "111001111100111", "3": "111001111001111",
          "4": "101101111001001", "5": "111100111001111", "6": "111100111101111", "7": "111001001001001",
          "8": "111101111101111", "9": "111101111001111", ":": "000010000010000"}


def clock():
    """Five rows of block digits (each dot two cells wide) and a date line."""
    now = time.localtime()
    text = time.strftime("%H:%M", now)
    fill = (TH.get("clock_fill") or "█") * 2
    rows = []
    for r in range(5):
        parts = []
        for ch in text:
            bits = DIGITS[ch][r * 3:r * 3 + 3]
            if ch == ":":
                parts.append(fill if bits[1] == "1" else "  ")
            else:
                parts.append("".join(fill if b == "1" else "  " for b in bits))
        rows.append((C_HI, "  ".join(parts)))
    rows.append((C_DIM, ""))
    rows.append((C_DIM, time.strftime("%A %d %B", now).lower()))
    return rows


# ---------------------------------------------------------------- scenes
SCENES = {}


def load_scenes():
    d = TH["scenes"]
    for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not fn.endswith(".txt"):
            continue
        with open(os.path.join(d, fn), encoding="utf-8") as f:
            raw = f.read().split("\n")
        anim = raw[0].split(":", 1)[1].strip() if raw and raw[0].startswith("# anim:") else ""
        body = raw[1:] if anim else raw
        while body and not body[-1].strip():
            body.pop()
        SCENES[fn[:-4]] = (anim, body)


def chance(tick, a, b):
    """Small repeatable pseudo-random number in 0..1 (keeps the random module out of the start-up path)."""
    x = (tick * 7919 + a * 104729 + b * 1299709) & 0xFFFFFFFF
    x = (x ^ (x >> 15)) * 2246822519 & 0xFFFFFFFF
    x = (x ^ (x >> 13)) * 3266489917 & 0xFFFFFFFF
    return ((x ^ (x >> 16)) & 0xFFFF) / 65535.0


def scene_frame(name, tick):
    """The scene's lines for this tick: stars twinkle, water drifts, rain falls."""
    anim, body = SCENES[name]
    lines, step = list(body), tick // 2
    if anim == "twinkle":
        out = []
        for y, l in enumerate(lines):
            chars = list(l)
            for i, c in enumerate(chars):
                if c in "*." and (i == 0 or chars[i - 1] == " ") and (i + 1 == len(chars) or chars[i + 1] == " "):
                    if chance(step, y, i) < 0.18:
                        chars[i] = "." if c == "*" else "*"
            out.append("".join(chars))
        lines = out
    elif anim == "waves":
        out = []
        for k, l in enumerate(lines):
            if l.count("~") > len(l) // 3 and "_" not in l:
                s = step % len(l) * (1 if k % 2 else -1)
                l = l[s:] + l[:s]
            out.append(l)
        lines = out
    elif anim == "rain":
        idx = [k for k, l in enumerate(lines) if l.strip().startswith("|") and l.rstrip().endswith("|")]
        if idx:
            inner = [lines[k] for k in idx]
            s = step % len(inner)
            inner = inner[-s:] + inner[:-s] if s else inner
            for k, l in zip(idx, inner):
                lines[k] = l
    return [(C_DIM, l) for l in lines] + [(C_FAINT, ""), (C_FAINT, name)]


# ---------------------------------------------------------------- album art
_art_cache = {}


def now_playing():
    """(cover file, 'artist — title') of the track the lyrics daemon is following."""
    try:
        import json
        with open(os.path.join(CACHE, "now.json"), encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        return "", ""
    art = st.get("art") or ""
    if not art.startswith("file://"):
        return "", st.get("sub") or ""
    from urllib.parse import unquote
    return unquote(art[7:]), st.get("sub") or ""


def art_lines(path, w, h):
    """The cover as text. chafa draws it; without chafa a plain half-block version is drawn with Pillow."""
    key = (path, w, h)
    if key in _art_cache:
        return _art_cache[key]
    lines = []
    if which("chafa"):
        out = run(["chafa", "--format", "symbols", "--animate", "off", "--polite", "on", "--size", "%dx%d" % (w, h),
                   *TH.get("chafa", "").split(), path])
        lines = [l for l in out.replace("\x1b[?25l", "").replace("\x1b[?25h", "").split("\n") if l.strip()]
    if not lines:
        try:
            import site
            site.main()                                       # Pillow lives in site-packages (-S skipped it)
            from PIL import Image
            grey = TH.get("mono") == "1" or TH.get("light") == "1"
            im = Image.open(path).convert("L" if grey else "RGB")
            side = max(2, min(w // 2, h))
            im = im.resize((side * 2, side * 2))
            px = im.load()
            for y in range(0, side * 2, 2):
                row = ""
                for x in range(side * 2):
                    a, b = px[x, y], px[x, y + 1]
                    if isinstance(a, int):
                        a, b = (a,) * 3, (b,) * 3
                    row += "\x1b[38;2;%d;%d;%dm\x1b[48;2;%d;%d;%dm▀" % (*a, *b)
                lines.append(row + RESET)
        except Exception:
            lines = []
    _art_cache.clear()
    _art_cache[key] = lines
    return lines


def visible_len(s):
    n, i = 0, 0
    while i < len(s):
        if s[i] == "\x1b":
            while i < len(s) and not s[i].isalpha():
                i += 1
            i += 1
        else:
            n += 1
            i += 1
    return n


# ---------------------------------------------------------------- composing a frame
def block(rows, width, height):
    """Centre (colour, text) rows in a width x height box; returns plain strings with colour codes."""
    rows = rows[:height]
    w = max((visible_len(t) for _, t in rows), default=0)
    left = max(0, (width - w) // 2)
    top = max(0, (height - len(rows)) // 2)
    out = [""] * top
    for c, t in rows:
        out.append(" " * left + c + t + RESET)
    return out + [""] * (height - len(out))


def stage(page, cols, rows, tick, quick=False):
    """quick = the very first frame: nothing slow (no cover art yet), so the tile appears at once."""
    if rows < 3:
        return []
    wide = cols >= 84
    tall = not wide and rows >= 18                            # room for the clock above the rotating part
    if tall:
        return block(clock(), cols, 8) + stage_body(page, cols, rows - 8, tick, quick)
    if page == "clock" and not wide:
        return block(clock(), cols, rows)
    if not wide:
        return stage_body(page, cols, rows, tick, quick)
    left, right = block(clock(), 40, rows), stage_body(page, cols - 40, rows, tick, quick)
    return [l + " " * (40 - visible_len(l)) + r for l, r in zip(left, right)]


def stage_body(page, width, rows, tick, quick):
    if page == "art":
        path, sub = ("", "") if quick else now_playing()
        h = max(3, rows - 2)
        art = art_lines(path, min(width - 2, h * 2), h) if path and os.path.isfile(path) else []
        body = [("", l) for l in art] + [(C_DIM, ""), (C_DIM, sub[:width - 2])] if art else \
               [(C_DIM, "no cover art right now")]
    elif page.startswith("scene:") and page[6:] in SCENES:
        body = scene_frame(page[6:], tick)
    else:
        body = scene_frame(sorted(SCENES)[0], tick) if SCENES else [(C_DIM, "")]
    return block(body, width, rows)


def pages():
    order = ["clock"]
    for n in sorted(SCENES):
        order.append("scene:" + n)
        if n == sorted(SCENES)[0]:
            order.append("art")
    return order


def pinned():
    try:
        with open(os.path.join(RUN, "showcase.page")) as f:
            p = f.read().strip()
    except OSError:
        return ""
    return "" if p in ("", "auto") else p


def main():
    import select, termios, tty
    if not sys.stdin.isatty():
        sys.exit("showcase needs a terminal")
    load_scenes()
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    order, k, tick, first = pages(), 0, 0, True
    head, head_at, head_cols = [], 0.0, 0
    page_at = time.monotonic()
    leave = False
    try:
        tty.setraw(fd)
        sys.stdout.write("\x1b[?25l\x1b[2J")
        while not leave:
            try:
                cols, rows = os.get_terminal_size()
            except OSError:
                cols, rows = 80, 24
            now = time.monotonic()
            if not head or cols != head_cols or now - head_at > 60:
                head, head_cols = header(cols, fresh=not first), cols
                head_at = now if not first else now - 58       # the advance copy is replaced a moment later
            page = pinned() or order[k % len(order)]
            if page == "art" and not pinned() and not first and not os.path.isfile(now_playing()[0] or "/nonexistent"):
                k += 1                                         # nothing playing with a cover: skip that page
                continue
            body = stage(page, cols, rows - len(head) - 2, tick, quick=first)
            hint = C_FAINT + " enter: shell   any key: next" + RESET
            frame = ["\x1b[H"] + [l + "\x1b[K\r\n" for l in head] + ["\x1b[K\r\n"]
            frame += [l + "\x1b[K\r\n" for l in body]
            frame += [hint + "\x1b[K\x1b[J"]
            sys.stdout.write("".join(frame))
            sys.stdout.flush()
            if first:
                first = False
                if T0:
                    ms = (time.time_ns() - T0) / 1e6
                    try:
                        os.makedirs(STATE, exist_ok=True)
                        with open(os.path.join(STATE, "log"), "a") as f:
                            f.write("%s [rice] showcase: first frame %d ms after the tile was launched, "
                                    "%d ms after the showcase itself started (%s)\n"
                                    % (time.strftime("%F %T"), ms, (time.time_ns() - P0) / 1e6, TH["name"]))
                    except OSError:
                        pass
            r, _, _ = select.select([fd], [], [], 0.05 if tick == 0 else 0.5)
            tick += 1
            if r:
                ch = os.read(fd, 16)
                if ch in (b"\r", b"\n", b" ", b"q", b"\x03", b"\x04"):
                    leave = True
                else:
                    k += 1
                    page_at = time.monotonic()
            elif time.monotonic() - page_at > PAGE_SECONDS:
                k += 1
                page_at = time.monotonic()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write(RESET + "\x1b[?25h\x1b[2J\x1b[H")
        sys.stdout.flush()
    shell = which("fish") or os.environ.get("SHELL") or "/bin/bash"
    os.execv(shell, [shell])


if __name__ == "__main__":
    main()
