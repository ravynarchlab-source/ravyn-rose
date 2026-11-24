"""Generate a 64 s synthetic test track (arpeggiated chords + bass + soft kick) and a matching .lrc.
Output: ~/Music/rice-test/Rice Test Tone.flac and Rice Test Tone.lrc  (tags: artist "Rice Test", title "Test Tone")."""
import subprocess, wave
from pathlib import Path
import numpy as np

SR, BPM, BARS = 44100, 120, 32
beat = 60 / BPM; dur = BARS * 4 * beat
t = np.arange(int(SR * dur)) / SR
out = np.zeros_like(t)
f = lambda n: 440 * 2 ** ((n - 69) / 12)
chords = [(57, 60, 64), (53, 57, 60), (48, 52, 55), (55, 59, 62)]   # Am F C G
for bar in range(BARS):
    ch = chords[bar % 4]; b0 = bar * 4 * beat
    for i in range(8):                                   # eighth-note arpeggio
        n = ch[i % 3] + 12 * (i // 3 % 2)
        s, e = int(SR * (b0 + i * beat / 2)), int(SR * (b0 + (i + 1) * beat / 2))
        tt = t[s:e] - t[s]
        out[s:e] += 0.18 * np.sin(2 * np.pi * f(n) * tt) * np.exp(-tt * 5)
    s, e = int(SR * b0), int(SR * (b0 + 4 * beat))           # bass note per bar
    tt = t[s:e] - t[s]
    out[s:e] += 0.22 * np.sin(2 * np.pi * f(ch[0] - 24) * tt) * np.exp(-tt * 0.8)
    for k in range(4):                                   # kick on every beat
        s = int(SR * (b0 + k * beat)); e = s + int(SR * 0.15); tt = t[s:e] - t[s]
        out[s:e] += 0.35 * np.sin(2 * np.pi * (60 + 90 * np.exp(-tt * 40)) * tt) * np.exp(-tt * 25)
out = np.int16(np.clip(out / np.abs(out).max() * 0.8, -1, 1) * 32767)

d = Path.home() / "Music/rice-test"; d.mkdir(parents=True, exist_ok=True)
wav, flac = d / "tmp.wav", d / "Rice Test Tone.flac"
with wave.open(str(wav), "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(out.tobytes())
subprocess.run(["flac", "-s", "-f", "--best", "-T", "ARTIST=Rice Test", "-T", "TITLE=Test Tone",
                "-T", "ALBUM=Rice Test", "-o", str(flac), str(wav)], check=True)
wav.unlink()

lines = ["(intro)", "neon rain falls on the marble floor", "pipes wind slowly through the dark",
         "the lava rises, then it sinks again", "bars of sound in rose and red",
         "this is only a test tone", "every line arrives on time", "the visualizer keeps the beat",
         "lyrics read from an .lrc file", "next to the track that's playing now",
         "playerctl tells us where we are", "rice off puts it all away", "(outro)"]
step = dur / len(lines)
lrc = ["[ar:Rice Test]", "[ti:Test Tone]"] + [
    f"[{int(i*step)//60:02d}:{i*step%60:05.2f}]{l}" for i, l in enumerate(lines)]
(d / "Rice Test Tone.lrc").write_text("\n".join(lrc) + "\n", encoding="utf-8")
print(flac, f"{dur:.0f}s")
