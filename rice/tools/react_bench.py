#!/usr/bin/env python3
"""Measure what the reactive deck costs and how late it is, on the machine it runs on.

    RICE_DECK_STATS=1 rice on          (so the deck writes its frame statistics)
    python3 tools/react_bench.py [seconds]     while music plays; default 20

Prints, for the measured stretch:
  * frames per second of the deck window, and how even the frames are;
  * CPU of each rice panel (systemd's own accounting per unit), as a share of ONE core;
  * GPU time of the deck and the lyrics bar (i915 / xe / amdgpu report it per process in fdinfo);
  * how late a beat reaches a finished frame after the moment it was due.
What it cannot see is the compositor's own frame and the screen's response after the frame is handed
over (up to one refresh, 16.7 ms at 60 Hz) and how exact the audio output's reported delay is."""
import glob, json, os, re, subprocess, sys, time
from pathlib import Path

RUN = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "rice"
UNITS = ["rice-react", "rice-deck", "rice-lyrics", "rice-lyricsd"]


def cpu_ns(unit):
    out = subprocess.run(["systemctl", "--user", "show", unit, "-p", "CPUUsageNSec", "--value"], capture_output=True, text=True).stdout.strip()
    return int(out) if out.isdigit() else None


def pids(unit):
    out = subprocess.run(["systemctl", "--user", "show", unit, "-p", "MainPID", "--value"], capture_output=True, text=True).stdout.strip()
    return [int(out)] if out.isdigit() and int(out) > 0 else []


def gpu_ns(pid):
    """Render-engine time of a process, counted once per DRM client."""
    seen, total = set(), 0
    for f in glob.glob(f"/proc/{pid}/fdinfo/*"):
        try:
            text = open(f).read()
        except OSError:
            continue
        cid = re.search(r"drm-client-id:\s*(\d+)", text)
        if not cid or cid.group(1) in seen:
            continue
        seen.add(cid.group(1))
        for m in re.finditer(r"drm-engine-(?:render|gfx)\S*:\s*(\d+) ns", text):
            total += int(m.group(1))
    return total if seen else None


def rss_mb(pid):
    try:
        return int(re.search(r"VmRSS:\s*(\d+)", open(f"/proc/{pid}/status").read()).group(1)) / 1024
    except Exception:
        return None


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
    stats = RUN / "deck.stats"
    start_size = stats.stat().st_size if stats.exists() else 0
    c0 = {u: cpu_ns(u) for u in UNITS}
    g0 = {u: [(p, gpu_ns(p)) for p in pids(u)] for u in ("rice-deck", "rice-lyrics")}
    t0 = time.monotonic()
    time.sleep(secs)
    span = time.monotonic() - t0
    out = {"seconds": round(span, 1), "cpu_percent_of_one_core": {}, "gpu_percent": {}, "memory_mb": {}}
    for u in UNITS:
        c1 = cpu_ns(u)
        if c0[u] is not None and c1 is not None:
            out["cpu_percent_of_one_core"][u] = round((c1 - c0[u]) / 1e9 / span * 100, 1)
        for p in pids(u):
            out["memory_mb"][u] = round(rss_mb(p) or 0)
    out["cpu_percent_of_one_core"]["all four"] = round(sum(out["cpu_percent_of_one_core"].values()), 1)
    for u, lst in g0.items():
        for p, a in lst:
            b = gpu_ns(p)
            if a is not None and b is not None:
                out["gpu_percent"][u] = round((b - a) / 1e9 / span * 100, 1)
    rows = []
    if stats.exists():
        with open(stats) as f:
            f.seek(start_size)
            rows = [json.loads(l) for l in f if l.strip()]
    rows = [r for r in rows if r.get("fps", 0) > 1]
    if rows:
        w = sum(r["seconds"] for r in rows)
        avg = lambda k: round(sum(r[k] * r["seconds"] for r in rows if r.get(k) is not None) / max(1e-9, sum(r["seconds"] for r in rows if r.get(k) is not None)), 1)
        out["deck"] = dict(fps=avg("fps"), frame_ms_p50=avg("frame_ms_p50"), frame_ms_p95_worst=max(r["frame_ms_p95"] for r in rows),
                           frame_ms_max=max(r["frame_ms_max"] for r in rows), frames_over_25ms=sum(r["over_25ms"] for r in rows),
                           beats=sum(r["beats"] for r in rows),
                           beat_due_to_frame_ms_p50=avg("beat_due_to_swap_ms_p50"),
                           beat_due_to_frame_ms_p95_worst=max((r["beat_due_to_swap_ms_p95"] for r in rows if r["beat_due_to_swap_ms_p95"] is not None), default=None),
                           beat_due_to_frame_ms_max=max((r["beat_due_to_swap_ms_max"] for r in rows if r["beat_due_to_swap_ms_max"] is not None), default=None),
                           data_age_ms_p95_worst=max((r["data_age_ms_p95"] for r in rows if r["data_age_ms_p95"] is not None), default=None),
                           build_to_swap_ms_p95_worst=max((r["build_to_swap_ms_p95"] for r in rows if r["build_to_swap_ms_p95"] is not None), default=None))
    else:
        out["deck"] = "no frame statistics: start the rice with RICE_DECK_STATS=1"
    try:
        import struct
        head = struct.unpack_from("<8sIIIIQdddd", open(RUN / "react.ring", "rb").read(64))
        out["audio"] = dict(output_delay_ms=round(head[7] * 1000), shown_later_by_ms=round(head[8] * 1000))
    except Exception:
        pass
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
