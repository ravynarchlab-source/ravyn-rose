"""The five rice wallpapers, drawn from maths and noise. No external images.

    python3 wallpapers.py            draw all five into ../wallpapers/
    python3 wallpapers.py mono neon  draw only these

mono   white halftone dots on black          rose   red marble (domain-warped noise)
neon   grid running to a striped sun         amber  radar rings behind scanlines
paper  contour lines in ink on paper

Needs python-numpy and python-pillow. Each picture is 1920 x 1080 and uses a fixed seed, so a run
reproduces the files in this repository."""
import sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageFilter

W, H = 1920, 1080
OUT = Path(__file__).resolve().parent.parent / "wallpapers"
y, x = np.mgrid[0:H, 0:W].astype(np.float32)


def hexc(h):
    return np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)], np.float32)


def noise(rng, scale):
    """Smooth value noise: a small random grid upsampled bicubically."""
    g = rng.random((max(2, H // scale), max(2, W // scale))).astype(np.float32)
    return np.asarray(Image.fromarray(g, "F").resize((W, H), Image.BICUBIC), dtype=np.float32)


def fbm(rng, base, octaves=6):
    out, amp, tot = np.zeros((H, W), np.float32), 1.0, 0.0
    for o in range(octaves):
        out += amp * noise(rng, max(2, base >> o)); tot += amp; amp *= 0.5
    return out / tot


def soften(a, k):
    """Low-pass a float field: shrink it k times, then scale it back up."""
    im = Image.fromarray(a.astype(np.float32), "F").resize((W // k, H // k), Image.BOX)
    return np.asarray(im.resize((W, H), Image.BICUBIC), dtype=np.float32)


def over(rgb, colour, alpha):
    return rgb * (1 - alpha[..., None]) + colour * alpha[..., None]


def save(name, rgb, colours=0, blur=0.0):
    img = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    if colours:                                  # few colours: a much smaller file, no visible banding here
        img = img.quantize(colours, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    OUT.mkdir(exist_ok=True)
    out = OUT / f"{name}.png"
    img.save(out, optimize=True)
    print(f"{name}: {out.stat().st_size / 1024:.0f} KiB")


def mono():
    """Halftone: a 45-degree lattice of white dots whose size follows a slow noise field."""
    rng = np.random.default_rng(2401)
    pitch = 16.0
    u, v = (x + y) / np.sqrt(2), (x - y) / np.sqrt(2)
    du, dv = (u % pitch) - pitch / 2, (v % pitch) - pitch / 2
    d = np.sqrt(du * du + dv * dv)
    cu, cv = u - du, v - dv                                   # lattice point each pixel belongs to
    cx, cy = (cu + cv) / np.sqrt(2), (cu - cv) / np.sqrt(2)
    field = fbm(rng, 640, 4)
    tone = soften(field, 8)
    tone = np.clip((tone - 0.34) * 2.4, 0, 1) * np.clip(1.25 - np.hypot((cx - W / 2) / W, (cy - H / 2) / H) * 1.2, 0.2, 1)
    r = tone * pitch * 0.46
    a = np.clip(r - d + 0.5, 0, 1)
    save("mono", np.repeat((a * 255)[..., None], 3, 2), colours=8)


def rose():
    """Red marble: veins are the zero lines of a sine bent by fractal noise."""
    rng = np.random.default_rng(1123)
    warp1, warp2 = fbm(rng, 480), fbm(rng, 320)
    t = (x * 0.9 + y * 0.55) / W * 7.0 + 6.0 * warp1 + 3.0 * warp2
    veins = np.abs(np.sin(t * np.pi))
    fine = np.abs(np.sin(((x * 0.4 - y) / W * 23.0 + 9.0 * fbm(rng, 200)) * np.pi))
    cloud = fbm(rng, 600, 5)
    stops = np.array([[0.00, 16, 4, 9], [0.45, 52, 10, 22], [0.75, 112, 22, 44], [1.00, 150, 34, 60]], np.float32)
    val = np.clip(cloud * 1.25 - 0.1, 0, 1)
    rgb = np.stack([np.interp(val, stops[:, 0], stops[:, c]) for c in (1, 2, 3)], -1)
    vein = np.clip(1 - veins / 0.06, 0, 1) ** 1.5
    hair = np.clip(1 - fine / 0.03, 0, 1) * 0.35
    pink = np.array([232, 120, 146], np.float32)
    glow = np.clip(1 - veins / 0.25, 0, 1) * 0.18
    rgb = over(rgb, pink, vein * 0.85)
    rgb = rgb * (1 - hair[..., None]) + pink * 0.8 * hair[..., None]
    rgb += np.array([90, 20, 40], np.float32) * glow[..., None]
    dist = np.sqrt(((x - W / 2) / (W / 2)) ** 2 + ((y - H / 2) / (H / 2)) ** 2)
    rgb *= np.clip(1.15 - 0.45 * dist, 0.45, 1)[..., None]
    save("rose", rgb, blur=0.6)


def neon():
    """A floor grid in perspective under a striped sun."""
    rng = np.random.default_rng(3303)
    hz = H * 0.60                                             # horizon
    sky = np.clip((hz - y) / hz, 0, 1)
    rgb = hexc("#0a0a12") + (hexc("#2a0f4a") - hexc("#0a0a12")) * (np.clip(1 - sky, 0, 1) ** 3)[..., None]
    stars = (rng.random((H, W)) > 0.9993) & (y < hz - 40)
    rgb = over(rgb, hexc("#e6f7ff"), stars.astype(np.float32) * (0.4 + 0.6 * rng.random((H, W)).astype(np.float32)))
    # sun: pink at the top to purple at the bottom, cut by dark bands that widen downwards
    cx, cy, rad = W / 2, hz - 60, 230.0
    d = np.hypot(x - cx, y - cy)
    disc = np.clip(rad - d + 0.5, 0, 1) * (y < hz)
    k = np.clip((y - (cy - rad)) / (2 * rad), 0, 1)
    band = ((y - cy) % 34) < (34 * np.clip((k - 0.45) * 0.9, 0, 0.6))
    disc = disc * ~((y > cy - 20) & band)
    sun = hexc("#ff2a6d") + (hexc("#5b1a8c") - hexc("#ff2a6d")) * k[..., None]
    halo = np.exp(-np.clip(d - rad, 0, None) / 90.0) * 0.22 * (y < hz)
    rgb = over(rgb, hexc("#ff2a6d"), halo)
    rgb = over(rgb, sun, disc.astype(np.float32))
    # floor
    floor = y >= hz
    rgb = np.where(floor[..., None], hexc("#0a0a12") + (hexc("#150a2a") - hexc("#0a0a12")) * np.clip((y - hz) / (H - hz), 0, 1)[..., None], rgb)
    depth = 1.0 / np.clip((y - hz) / (H - hz), 1e-3, 1)       # 1 at the bottom edge, large at the horizon
    zline = np.abs(((depth * 2.2) % 1.0) - 0.5) / (depth * 0.011 + 1e-3)
    xline = np.abs((((x - W / 2) * depth / 150.0) % 1.0) - 0.5) / (depth * 0.011 + 1e-3)
    line = np.clip(1.6 - np.minimum(zline, xline) * 22, 0, 1) * floor * np.clip(1.4 - depth / 9.0, 0, 1)
    rgb = over(rgb, hexc("#00f0ff"), line.astype(np.float32) * 0.9)
    glow = np.exp(-np.abs(y - hz) / 18.0) * 0.55
    rgb = over(rgb, hexc("#00f0ff"), glow.astype(np.float32))
    save("neon", rgb, colours=128, blur=0.5)


def amber():
    """Radar rings and a crosshair, dimmed by scanlines, on a dark phosphor glow."""
    rng = np.random.default_rng(4404)
    cx, cy = W * 0.5, H * 0.5
    d = np.hypot(x - cx, y - cy)
    rgb = hexc("#140a00") + (hexc("#3a2204") - hexc("#140a00")) * (np.clip(1 - d / 900.0, 0, 1) ** 2)[..., None]
    ring = np.clip(1.2 - np.abs((d % 120.0) - 60.0), 0, 1) * (d < 720)
    rgb = over(rgb, hexc("#ffb000"), ring.astype(np.float32) * 0.35)
    cross = np.clip(1.0 - np.minimum(np.abs(x - cx), np.abs(y - cy)), 0, 1) * (d < 720)
    rgb = over(rgb, hexc("#ffb000"), cross.astype(np.float32) * 0.28)
    ang = np.arctan2(y - cy, x - cx)
    sweep = np.clip(1 - ((ang - 0.6) % (2 * np.pi)) / 0.9, 0, 1) * (d < 720)     # the fading wedge behind the sweep line
    rgb = over(rgb, hexc("#ffb000"), sweep.astype(np.float32) * 0.16)
    blips = np.zeros((H, W), np.float32)
    for _ in range(9):
        a, r = rng.random() * 2 * np.pi, 120 + rng.random() * 560
        bx, by = cx + np.cos(a) * r, cy + np.sin(a) * r
        blips += np.exp(-((x - bx) ** 2 + (y - by) ** 2) / 30.0)
    rgb = over(rgb, hexc("#ffe0a0"), np.clip(blips, 0, 1) * 0.8)
    rgb *= (0.72 + 0.28 * ((y % 3) > 0))[..., None]            # scanlines
    save("amber", rgb, colours=96)


def paper():
    """Contour lines of a noise landscape, drawn in ink on paper; every fifth line is heavier."""
    rng = np.random.default_rng(5505)
    h = fbm(rng, 700, 5)
    h = soften(h, 4)
    levels = h * 34.0
    gy, gx = np.gradient(levels)
    grad = np.hypot(gx, gy) + 1e-4
    dist = np.abs((levels % 1.0) - 0.5)
    dist = (0.5 - dist) / grad                                 # pixels to the nearest contour
    thin = np.clip(1.1 - dist, 0, 1)
    idx = np.floor(levels + 0.5)
    heavy = np.clip(1.9 - dist, 0, 1) * ((idx % 5) == 0)
    cloud = (soften(rng.random((H, W)).astype(np.float32), 24) - 0.5) * 14.0     # soft unevenness, like pulp
    rgb = hexc("#f3ecdc") + cloud[..., None]
    rgb = over(rgb, hexc("#6b6458"), thin * 0.40)
    rgb = over(rgb, hexc("#1c1a17"), heavy * 0.55)
    dd = np.hypot((x - W / 2) / (W / 2), (y - H / 2) / (H / 2))
    rgb *= np.clip(1.06 - 0.10 * dd, 0.9, 1)[..., None]
    save("paper", rgb, colours=48)


ALL = dict(mono=mono, rose=rose, neon=neon, amber=amber, paper=paper)
if __name__ == "__main__":
    for n in (sys.argv[1:] or list(ALL)):
        ALL[n]()
