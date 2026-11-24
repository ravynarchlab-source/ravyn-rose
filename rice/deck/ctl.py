#!/usr/bin/env python3
"""Rice ASCII DECK remote: used by the global shortcuts (Meta+Alt+PgUp/PgDn, Meta+Alt+, / .).

    ctl.py vol+ | vol- | mute | next | prev | playpause | mode [0-3] | zoom | fill

If the deck is running, the command goes to it over its local socket, so the volume OSD and the
track banner appear. If it is not (rice off), volume and prev/next still work directly:
volume through PipeWire (wpctl, clamped to 0-100 %), tracks through MPRIS on the music players
only (Pear, fooyin; never a browser)."""
import os, re, socket, subprocess, sys
from pathlib import Path

SOCK = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "rice" / "deck.sock"
SINK = "@DEFAULT_AUDIO_SINK@"
MUSIC = re.compile(r"youtube.?music|pear|^fooyin", re.I)


def out(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout.strip()
    except Exception:
        return ""


def to_deck(text):
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.sendto(text.encode(), str(SOCK))
        return True
    except OSError:
        return False


def music_player():
    rows = [r.split("\t") for r in out(["playerctl", "-a", "metadata", "--format", "{{playerInstance}}\t{{status}}"]).splitlines()]
    rows = [r for r in rows if len(r) == 2 and MUSIC.search(r[0])]
    for want in ("Playing", "Paused"):
        for name, status in rows:
            if status == want:
                return name
    return rows[0][0] if rows else None


def direct(cmd):
    if cmd in ("vol+", "vol-"):
        m = re.search(r"Volume:\s*([0-9.]+)", out(["wpctl", "get-volume", SINK]))
        if m:
            v = max(0, min(100, round(float(m.group(1)) * 100) + (5 if cmd == "vol+" else -5)))
            out(["wpctl", "set-volume", SINK, f"{v}%"])
    elif cmd == "mute":
        out(["wpctl", "set-mute", SINK, "toggle"])
    elif cmd in ("next", "prev", "playpause"):
        p = music_player()
        if p:
            out(["playerctl", "-p", p, {"next": "next", "prev": "previous", "playpause": "play-pause"}[cmd]])


if __name__ == "__main__":
    text = " ".join(sys.argv[1:]).strip()
    if not text:
        sys.exit(__doc__)
    if not to_deck(text):
        direct(text.split()[0])
