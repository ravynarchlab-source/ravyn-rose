#!/usr/bin/env python3
"""Rice flavours. A flavour is one JSON file in flavours/: colours, shapes, panel settings and tile maps.

    flavour.py list                     names in menu order (the current one is marked)
    flavour.py current                  the flavour in use
    flavour.py random                   a flavour other than the current one
    flavour.py next | prev              the neighbour of the current one in menu order
    flavour.py build NAME OUTDIR        write everything `rice on` needs for NAME into OUTDIR:
                                        two KDE colour schemes, alacritty.toml, cava.conf, fastfetch.jsonc,
                                        theme.json (read by the deck, the lyrics bar and the showcase), env.sh
    flavour.py tiler NAME LAYOUT MODE   print the KWin tiling script with NAME's tile map filled in
    flavour.py pick OUTFILE             small terminal picker; writes the chosen name to OUTFILE

To add your own: copy one of the files in flavours/, change it, and run `rice flavour yourname`."""
import json, os, random, shlex, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CFG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "rice"
DEFAULT = "mono"


def load(name):
    return json.loads((HERE / "flavours" / f"{name}.json").read_text(encoding="utf-8"))


def names():
    found = []
    for p in (HERE / "flavours").glob("*.json"):
        try:
            found.append((json.loads(p.read_text(encoding="utf-8")).get("order", 99), p.stem))
        except ValueError:
            pass
    return [n for _, n in sorted(found)]


def current():
    try:
        n = (CFG / "flavour").read_text().strip()
    except OSError:
        n = ""
    return n if n in names() else DEFAULT


# ---------------------------------------------------------------- colour helpers
def rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def dec(h):
    return ",".join(map(str, rgb(h)))


def mix(a, b, t):
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(rgb(a), rgb(b)))


def scheme_name(f, border=False):
    return "Rice" + f["name"].capitalize() + ("Border" if border else "")


def scheme(f, border=False):
    """A KDE colour scheme. The plain one themes the desktop while the rice is on; the Border one is used
    only for the frames of rice windows (Breeze paints title bar and frame from the Header colours)."""
    c, light = f["colors"], f["light"]
    hover = c.get("hover", c["accent2"])
    view, header = mix(c["bg"], c["window"], 0.35), mix(c["bg"], c["window"], 0.6)
    groups = {
        "Window": (c["window"], mix(c["window"], c["fg"], 0.04)),
        "View": (view, c["window"]),
        "Button": (c["button"], mix(c["button"], c["fg"], 0.05)),
        "Header": (header, c["window"]),
        "Header][Inactive": (view, header),
        "Tooltip": (c["window"], mix(c["window"], c["fg"], 0.04)),
        "Complementary": (c["bg"], c["window"]),
        "Selection": (c["deep"], mix(c["deep"], c["bg"], 0.25)),
    }
    active, inactive = (c["accent"], c["frame_inactive"]) if border else (c["button"], c["window"])
    if border:
        groups["Header"], groups["Header][Inactive"] = (active, active), (inactive, inactive)
    fg = dict(ForegroundNormal=c["fg"], ForegroundInactive=c["dim"], ForegroundActive=hover, ForegroundLink=hover,
              ForegroundVisited=mix(hover, c["dim"], 0.5), ForegroundNegative=c["bad"], ForegroundNeutral=c["warn"],
              ForegroundPositive=c["good"], DecorationFocus=c["accent"], DecorationHover=hover)
    out = []
    for g, (bg, alt) in groups.items():
        out += [f"[Colors:{g}]", f"BackgroundAlternate={dec(alt)}", f"BackgroundNormal={dec(bg)}"]
        cur = dict(fg)
        if g == "Selection" and not light:
            cur.update(ForegroundNormal="#ffffff", ForegroundInactive=c["fg"], ForegroundActive="#ffffff")
        out += [f"{k}={dec(v)}" for k, v in sorted(cur.items())] + [""]
    name = scheme_name(f, border)
    title_fg = c["bg"] if border else c["fg"]
    out += ["[General]", f"ColorScheme={name}", f"Name={name}", "shadeSortColumn=true", "", "[KDE]", "contrast=4", "",
            "[WM]", f"activeBackground={dec(active)}", f"activeBlend={dec(active)}", f"activeForeground={dec(title_fg)}",
            f"inactiveBackground={dec(inactive)}", f"inactiveBlend={dec(inactive)}", f"inactiveForeground={dec(c['dim'])}",
            f"frame={dec(active)}", f"inactiveFrame={dec(inactive)}", ""]
    return "\n".join(out)


def alacritty(f):
    c, a, s = f["colors"], f["ansi"], f["shape"]
    names8 = ["black", "red", "green", "yellow", "blue", "magenta", "cyan", "white"]
    lines = ["# Rice terminal panels. Generated from flavours/%s.json: edit that file, not this one." % f["name"],
             "[window]", "opacity = 1.0", "blur = false", "padding = { x = 8, y = 6 }", "dynamic_padding = true",
             'decorations = "Full"', "", "[font]",
             'normal = { family = "CaskaydiaCove Nerd Font", style = "Regular" }', f"size = {s['term_font_size']}", "",
             "[cursor]", 'style = { shape = "%s", blinking = "Off" }' % s["cursor"], "",
             "[colors.primary]", f'background = "{c["bg"]}"', f'foreground = "{c["fg"]}"', "[colors.normal]"]
    lines += [f'{n} = "{a[i]}"' for i, n in enumerate(names8)] + ["[colors.bright]"]
    lines += [f'{n} = "{a[8 + i]}"' for i, n in enumerate(names8)]
    return "\n".join(lines) + "\n"


def cava(f):
    k = f["panels"]["cava"]
    g = k["gradient"]
    return ("; Rice bar visualizer (classic layout). Generated from flavours/%s.json.\n[general]\nframerate = 30\nbars = 0\n"
            "bar_width = %d\nbar_spacing = %d\nsleep_timer = 3\n\n[input]\nmethod = pulse\nsource = auto\n\n"
            "[output]\nmethod = noncurses\n\n[color]\ngradient = 1\ngradient_count = %d\n%s\n\n[smoothing]\nnoise_reduction = 77\n"
            % (f["name"], k["bar_width"], k["bar_spacing"], len(g),
               "\n".join(f"gradient_color_{i + 1} = '{c}'" for i, c in enumerate(g))))


def sgr(h):
    return "38;2;%d;%d;%d" % rgb(h)


def fastfetch(f):
    """No title module on purpose: the showcase never prints a user or host name."""
    c = f["colors"]
    mods = [dict(type=t, key=k) for t, k in (("os", "os"), ("kernel", "kernel"), ("uptime", "uptime"),
                                              ("packages", "pkgs"), ("wm", "wm"), ("memory", "memory"))]
    conf = dict(logo=dict(type="file", source=str(HERE / "showcase" / "logos" / f"{f['name']}.txt"),
                          color={"1": sgr(c["accent"]), "2": sgr(c["accent2"])},
                          padding=dict(top=0, left=1, right=2)),
                display=dict(separator="  ", color=dict(keys=sgr(c["accent"]), output=sgr(c["fg"])), key=dict(width=9)),
                modules=mods)
    return json.dumps(conf, indent=2) + "\n"


def build(name, out):
    f = load(name)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / (scheme_name(f) + ".colors")).write_text(scheme(f))
    (out / (scheme_name(f, True) + ".colors")).write_text(scheme(f, True))
    (out / "alacritty.toml").write_text(alacritty(f))
    (out / "cava.conf").write_text(cava(f))
    (out / "fastfetch.jsonc").write_text(fastfetch(f))
    theme = dict(name=f["name"], title=f["title"], light=f["light"], mono=f["mono"], shader=f["shader"],
                 wallpaper=str(HERE / "wallpapers" / f"{name}.png"), scenes=str(HERE / "showcase" / "scenes"),
                 fastfetch=str(out / "fastfetch.jsonc"), **f["colors"], **f["shape"])
    (out / "theme.json").write_text(json.dumps(theme, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    # the showcase reads this instead of theme.json: no JSON parser before its first frame
    kv = dict(name=name, hi=theme["hi"], dim=theme["dim"], faint=theme["faint"], accent=theme["accent"],
              clock_fill=theme["clock_fill"], chafa=" ".join(theme["chafa"]), tint=" ".join(theme.get("chafa_tint") or []), mono=int(f["mono"]), light=int(f["light"]),
              fastfetch=theme["fastfetch"], scenes=theme["scenes"])
    (out / "showcase.kv").write_text("".join(f"{k}\t{v}\n" for k, v in kv.items()), encoding="utf-8")
    for old in out.glob("header-*.ans"):                       # rendered again by rice for this flavour
        old.unlink()
    arr = lambda xs: "(" + " ".join(shlex.quote(str(x)) for x in xs) + ")"
    p = f["panels"]
    (out / "env.sh").write_text("\n".join([
        "# generated by flavour.py build; sourced by rice",
        f"FL_NAME={shlex.quote(name)}", f"FL_TITLE={shlex.quote(f['title'])}",
        f"FL_SCHEME={scheme_name(f)}", f"FL_BORDER_SCHEME={scheme_name(f, True)}",
        f"FL_BORDERSIZE={int(f['shape']['border'])}",
        f"FL_OPACITY_ACTIVE={int(f['shape']['player_opacity'][0])}",
        f"FL_OPACITY_INACTIVE={int(f['shape']['player_opacity'][1])}", f"FL_WALL={shlex.quote(theme['wallpaper'])}",
        f"FL_MATRIX={arr(p['matrix'])}", f"FL_LAVA={arr(p['lava'])}", f"FL_PIPES={arr(p['pipes'])}", ""]))


# Terminals-only layout (rice layout classic): the same map for every flavour, with the flavour's gap.
CLASSIC = dict(
    landscape={"lyrics": [0, 0, 1, 0.13], "player": [0, 0.13, 0.36, 0.57], "term": [0, 0.70, 0.36, 0.30],
               "cava": [0.36, 0.13, 0.38, 0.42], "pipes": [0.36, 0.55, 0.38, 0.45],
               "matrix": [0.74, 0.13, 0.10, 0.87], "lava": [0.84, 0.13, 0.16, 0.87]},
    portrait={"lyrics": [0, 0, 1, 0.08], "player": [0, 0.08, 1, 0.34], "cava": [0, 0.42, 0.55, 0.20],
              "pipes": [0.55, 0.42, 0.45, 0.20], "term": [0, 0.62, 0.55, 0.38],
              "matrix": [0.55, 0.62, 0.17, 0.38], "lava": [0.72, 0.62, 0.28, 0.38]})


def tiler(name, layout, mode):
    f = load(name)
    tiles = CLASSIC if layout == "classic" else f["tiles"]
    js = (HERE / "kwin" / "tile.js").read_text(encoding="utf-8")
    return (js.replace("@MODE@", mode).replace("@GAP@", str(int(f["shape"]["gap"])))
              .replace("@TILES@", json.dumps(tiles)))


# ---------------------------------------------------------------- picker
def pick(outfile):
    import select, termios, tty
    ns = names()
    fl = [load(n) for n in ns]
    i = ns.index(current())
    block = lambda h: "\x1b[48;2;%d;%d;%dm   \x1b[0m" % rgb(h)
    levels = ["off", "subtle", "full"]                    # how much the deck moves with the music (rice react)
    try:
        r = levels.index((CFG / "react").read_text().strip())
    except (OSError, ValueError):
        r = 2

    def draw():
        rows = ["\x1b[H\x1b[2J  rice flavour   (arrows or 1-%d, r = react, enter = apply, q = cancel)\r\n" % len(ns)]
        for k, f in enumerate(fl):
            c = f["colors"]
            sw = "".join(block(c[x]) for x in ("bg", "deep", "accent", "accent2", "fg"))
            mark = "\x1b[1m>\x1b[0m" if k == i else " "
            rows.append("  %s %d  %s  \x1b[1m%-6s\x1b[0m %s\r\n" % (mark, k + 1, sw, f["title"], f["blurb"]))
        rows.append("\r\n    react to the music:  " + "  ".join(
            ("\x1b[1;7m %s \x1b[0m" if k == r else " %s ") % name for k, name in enumerate(levels)) + "\r\n")
        sys.stdout.write("".join(rows))
        sys.stdout.flush()

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    choice = None
    try:
        tty.setraw(fd)
        sys.stdout.write("\x1b[?25l")
        while True:
            draw()
            ch = os.read(fd, 8)
            if ch in (b"q", b"\x1b", b"\x03"):
                break
            if ch in (b"\r", b"\n"):
                choice = ns[i]
                break
            if ch in (b"\x1b[A", b"k"):
                i = (i - 1) % len(ns)
            elif ch in (b"\x1b[B", b"j", b"\t"):
                i = (i + 1) % len(ns)
            elif ch in (b"r", b"R", b"\x1b[C"):
                r = (r + 1) % len(levels)
            elif ch == b"\x1b[D":
                r = (r - 1) % len(levels)
            elif ch.isdigit() and 1 <= int(ch) <= len(ns):
                i = int(ch) - 1
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\x1b[?25h\x1b[H\x1b[2J")
        sys.stdout.flush()
    Path(outfile).write_text("%s\t%s" % (choice, levels[r]) if choice else "")


def main(a):
    cmd = a[0] if a else "list"
    if cmd == "list":
        cur = current()
        for n in names():
            f = load(n)
            print("%s %-6s %s" % ("*" if n == cur else " ", n, f["blurb"]))
    elif cmd == "names":
        print(" ".join(names()))
    elif cmd == "current":
        print(current())
    elif cmd in ("next", "prev"):                      # the neighbour in menu order, wrapping round
        ns = names()
        print(ns[(ns.index(current()) + (1 if cmd == "next" else -1)) % len(ns)])
    elif cmd == "random":
        print(random.choice([n for n in names() if n != current()] or names()))
    elif cmd == "build":
        build(a[1], a[2])
    elif cmd == "tiler":
        sys.stdout.write(tiler(a[1], a[2], a[3]))
    elif cmd == "pick":
        pick(a[1])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
