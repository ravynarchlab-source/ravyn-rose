#version 440
// Rice ASCII DECK: draws the album art as a grid of character cells, entirely on the GPU, and lets it
// move with the music. Every flavour reacts in its own way (fx below).
//
// Inputs
//   artNew / artOld  the art, already cropped, contrast-corrected and sharpened by deck.py and scaled
//                    to exactly 2 x 4 texels per character cell (so braille dots and half blocks have
//                    real detail to show). Alpha 0 = no picture in that cell (letterbox in "fit").
//   lowNew / lowOld  the same art at one texel per cell: the cell's average colour in one read, and,
//                    read between texels, a cheap blur for glow and edges.
//   atlas            one row of Monocraft glyphs, darkest to brightest, one cell each.
//   mode             0 classic ASCII ramp, true colour   1 half blocks   2 braille dots   3 theme tint
//   progress         0..1 while a track change is animated (0.8 s): the old art falls apart into
//                    characters, then the new art is written line by line. 1 = settled.
//   c0..c3           the flavour's four colours, from the panel background to the brightest ink.
//   mono, invert     1 = draw in grey only; 1 = dark ink on a light panel (the PAPER flavour).
//   scan             1 = dim every third pixel row, like a CRT (the AMBER flavour).
//
// From the shared analyser (react/client.py), all eased, all 0..1 unless noted
//   fx               0 MONO halftone swell + invert ring   1 ROSE bloom + scattering petals
//                    2 NEON RGB split, scanline glitch, edge pulse   3 AMBER flare, afterglow, bounce
//                    4 PAPER ink that spreads and dries
//   amt              how much of it: 0 off, 0.45 subtle, 1 full
//   bass, loud       band envelopes        kick   pulse of the last beat      glow   slow tail of kick
//   ring             radius of the ring a strong beat sends out from the centre (-1 = none)
//   glitch, seed     short burst after a strong beat, and a number that changes with every strong beat
//   volRip, volDir   position (-0.2..1.2, -1 = none) and direction of the ripple a volume change sends across
//   still            0..1: the art fades to a static dither while nothing plays
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec2 grid;        // columns, rows
    float nGlyphs;
    float mode;
    float time;       // seconds
    float frame;      // advances 15 times a second: scrambled glyphs change with it
    float progress;
    float hasNew;
    float hasOld;
    float shimmer;
    vec4 c0;
    vec4 c1;
    vec4 c2;
    vec4 c3;
    float mono;
    float invert;
    float scan;
    float fx;
    float amt;
    float bass;
    float loud;
    float kick;
    float glow;
    float ring;
    float glitch;
    float seed;
    float volRip;
    float volDir;
    float still;
};
layout(binding = 1) uniform sampler2D artNew;
layout(binding = 2) uniform sampler2D artOld;
layout(binding = 3) uniform sampler2D atlas;
layout(binding = 4) uniform sampler2D lowNew;
layout(binding = 5) uniform sampler2D lowOld;

const float BAYER[16] = float[16](0.0, 8.0, 2.0, 10.0, 12.0, 4.0, 14.0, 6.0, 3.0, 11.0, 1.0, 9.0, 15.0, 7.0, 13.0, 5.0);

float hash1(float n) { return fract(sin(n * 127.1) * 43758.5453); }
float hash2(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float luma(vec3 c) { return dot(c, vec3(0.299, 0.587, 0.114)); }
bool is(float a, float b) { return abs(a - b) < 0.5; }

vec3 palette(float l) {                       // the flavour's ramp, for the theme-tinted mode (l = amount of ink)
    vec3 c = mix(c0.rgb, c1.rgb, smoothstep(0.0, 0.35, l));
    c = mix(c, c2.rgb, smoothstep(0.30, 0.70, l));
    c = mix(c, c3.rgb, smoothstep(0.65, 0.92, l));
    return mix(c, mix(c3.rgb, vec3(1.0), 0.6 * (1.0 - invert)), smoothstep(0.90, 1.0, l));
}
vec3 vivid(vec3 c) {                          // keep the hue, lift the value: glyphs cover little area
    float m = max(max(c.r, c.g), c.b);
    return c / max(m, 0.30) * mix(0.78, 1.0, m);
}
vec4 sub(sampler2D t, vec2 cell, vec2 ij) {   // texel (i, j) of a cell: i 0..1, j 0..3
    vec2 uv = (cell * vec2(2.0, 4.0) + ij + 0.5) / (grid * vec2(2.0, 4.0));
    return textureLod(t, uv, 0.0);
}
vec4 low(sampler2D t, vec2 cell) {            // average of a cell; fractional cells blend neighbours
    return textureLod(t, (cell + 0.5) / grid, 0.0);
}
float glyph(float gi, vec2 f) {               // outside the cell there is no glyph (a nudged character is cut there)
    if (f.x < 0.0 || f.x > 1.0 || f.y < 0.0 || f.y > 1.0) return 0.0;
    return textureLod(atlas, vec2((gi + clamp(f.x, 0.0, 0.999)) / nGlyphs, f.y), 0.0).r;
}
bool inside(vec2 cell) { return cell.x >= 0.0 && cell.y >= 0.0 && cell.x < grid.x && cell.y < grid.y; }

// One cell of an art layer. scr = share of cells shown scrambled (0 = the picture, 1 = all noise).
// nudge moves the glyph inside its cell (ROSE); react = 0 switches the per-flavour reaction off (transitions).
vec4 drawArt(sampler2D t, sampler2D tl, vec2 cell, vec2 f, float scr, float sd, vec2 nudge, float react) {
    if (!inside(cell)) return vec4(0.0);
    vec4 a = low(tl, cell);
    if (a.a < 0.5) return vec4(0.0);
    vec3 avg = a.rgb;
    float L = luma(avg);
    float ink = invert > 0.5 ? 1.0 - L : L;      // light panel: dark parts of the picture get the ink
    bool scrambled = hash2(cell + sd) < scr;
    float wet = (is(fx, 4.0) ? bass : 0.0) * amt * react;        // PAPER: how wet the ink is
    vec3 col;
    if (mode < 0.5 || mode > 2.5) {                       // classic ramp / theme tint
        float gi = floor(clamp(ink, 0.0, 0.999) * nGlyphs);
        if (scrambled) gi = floor(hash2(cell * 1.7 + sd * 3.1) * nGlyphs);
        float m = glyph(gi, f - nudge);
        if (is(fx, 0.0) && react > 0.5) {                 // MONO: a halftone dot swells under the character
            float r = 0.60 * sqrt(ink) * bass * amt;
            float d = length((f - 0.5) * vec2(1.0, 1.5));
            m = max(m, (1.0 - smoothstep(r - 0.07, r + 0.07, d)) * step(0.035, r));
        }
        if (wet > 0.01) {                                 // PAPER: wet ink bleeds sideways into the paper
            float o = 0.16 * wet;
            m = max(m, 0.85 * wet * max(max(glyph(gi, f + vec2(o, 0.0)), glyph(gi, f - vec2(o, 0.0))),
                                         max(glyph(gi, f + vec2(0.0, o * 0.66)), glyph(gi, f - vec2(0.0, o * 0.66)))));
        }
        vec3 fg, bg;
        if (mode > 2.5) { fg = palette(0.25 + 0.75 * ink); bg = mix(c0.rgb, palette(ink), 0.36); }
        else if (invert > 0.5) { fg = avg * 0.55; bg = mix(c0.rgb, avg, 0.22); }
        else { fg = vivid(avg); bg = avg * 0.46; }
        if (scrambled) { fg = mix(fg, c3.rgb, 0.75); bg = mix(bg, c0.rgb, 0.55); }
        col = mix(bg, fg, m);
    } else if (mode < 1.5) {                              // half blocks: two colours per cell
        float j0 = (f.y < 0.5) ? 0.0 : 2.0;
        col = (sub(t, cell, vec2(0.0, j0)) + sub(t, cell, vec2(1.0, j0))
             + sub(t, cell, vec2(0.0, j0 + 1.0)) + sub(t, cell, vec2(1.0, j0 + 1.0))).rgb * 0.25;
        if (scrambled) col = mix(col, c2.rgb * (0.25 + 0.75 * hash2(cell * 2.3 + sd + j0)), 0.85);
    } else {                                              // braille: 2 x 4 dots per cell, ordered dither
        vec2 ij = floor(f * vec2(2.0, 4.0));
        float l = luma(sub(t, cell, ij).rgb);
        vec2 g = cell * vec2(2.0, 4.0) + ij;
        float th = (BAYER[int(mod(g.y, 4.0)) * 4 + int(mod(g.x, 4.0))] + 0.5) / 16.0;
        float swell = (is(fx, 0.0) ? bass : 0.0) * amt * react;              // MONO in braille: the dots themselves swell
        // wet ink reaches a little further into the lighter parts, then dries back
        float on = step(0.08 + 0.84 * th - 0.13 * wet, (invert > 0.5 ? 1.0 - l : l) + 0.04 * kick * amt * react);
        if (scrambled) on = step(0.5, hash2(g + sd));
        vec2 d = fract(f * vec2(2.0, 4.0)) - 0.5;
        float r0 = 0.30 + 0.17 * wet * (0.55 + 0.9 * hash2(g * 0.37)) + 0.14 * swell;   // uneven: paper fibres
        float dotMask = 1.0 - smoothstep(r0, r0 + 0.18, length(d));
        vec3 fg = (scrambled || invert > 0.5) ? c3.rgb : mix(vivid(avg), vec3(1.0), 0.12);
        if (is(fx, 4.0)) fg = mix(c2.rgb, c3.rgb, 0.62 + 0.38 * max(wet, glow * amt * react));   // dry ink is a shade lighter
        col = mix(invert > 0.5 ? c0.rgb : avg * 0.32, fg, on * dotMask);
    }
    if (mono > 0.5) col = vec3(luma(col));
    return vec4(col, 1.0);
}

void main() {
    float live = 1.0 - still;                             // reactions fade with the picture when it goes still
    float A = amt * live;
    vec2 uv = qt_TexCoord0;
    if (is(fx, 3.0)) {                                    // AMBER: the tube bulges a little on every kick
        vec2 q = uv - 0.5;
        float k = 0.30 * kick * A;
        uv = 0.5 + q * (1.0 + k * dot(q, q)) / (1.0 + k * 0.5);
    }
    vec2 p = uv * grid;
    float crest = 0.0;
    if (volRip > -0.5) {                                  // a volume change: one wave crosses the deck
        float dx = (uv.x - volRip) * 6.0;
        crest = exp(-dx * dx);
        p.y += crest * 0.9 * sin(dx * 2.2) * volDir;
    }
    float burst = glitch * A;
    if (is(fx, 2.0) && burst > 0.02) {                    // NEON: a few bands of rows slip sideways for a moment
        float band = floor(p.y / 2.0);
        if (hash2(vec2(band, seed)) < 0.20)
            p.x += (hash1(band * 1.3 + seed * 3.7) - 0.5) * 9.0 * burst;
    }
    vec2 cell = floor(p);
    vec2 f = fract(p);
    float sd = floor(frame * 0.5);                        // scrambled glyphs change 7-8 times a second
    vec3 col = vec3(0.0);                                 // premultiplied
    float alpha = 0.0;
    bool moving = progress < 0.999;
    vec2 cc = (cell + 0.5) / grid - 0.5;                  // cell centre, -0.5..0.5

    // 1. track change, first half: the old art comes apart and its characters fall
    if (hasOld > 0.5 && moving && progress < 0.56) {
        float a = clamp(progress / 0.55, 0.0, 1.0);
        float lag = 0.35 * hash1(cell.x * 1.37 + 3.0);                  // columns let go one after another
        float af = clamp((a - lag) / (1.0 - lag), 0.0, 1.0);
        float fall = af * af * grid.y * (0.55 + 0.75 * hash1(cell.x + 17.0));
        vec2 src = vec2(cell.x, cell.y - floor(fall));
        if (src.y >= 0.0) {
            vec4 o = drawArt(artOld, lowOld, src, f, smoothstep(0.0, 0.45, af), sd, vec2(0.0), 0.0);
            float fade = 1.0 - smoothstep(0.50, 1.0, af);
            o.rgb = mix(o.rgb, c2.rgb * (0.25 + 1.5 * luma(o.rgb)), smoothstep(0.10, 0.70, af));
            col = o.rgb * o.a * fade;
            alpha = o.a * fade;
        } else {                                          // the trail left above the falling characters
            float tail = exp(src.y * 0.30) * (1.0 - a);
            float m = glyph(floor(hash2(cell + sd) * nGlyphs), f) * tail;
            col = c3.rgb * m;
            alpha = m;
        }
    }

    // 2. the picture. During a track change it is written line by line from the top (second half);
    //    settled, it is drawn with the flavour's reaction.
    if (hasNew > 0.5) {
        float shown = 1.0, fresh = 0.0, scr = 0.0;
        if (moving) {
            float b = clamp((progress - 0.34) / 0.66, 0.0, 1.0);
            float head = b * (grid.y + 3.0) - 1.5;                       // the row being written
            float within = head - cell.y;                                 // rows since this one was written
            float sweep = clamp(within, 0.0, 1.0) * (grid.x + 6.0) - cell.x;   // inside the row: left to right
            shown = step(0.0, within) * step(0.0, sweep);
            fresh = shown * exp(-max(within, 0.0) * 0.55);                // freshly written rows glow, then settle
            scr = shown * (1.0 - smoothstep(0.0, 2.5, within));           // and are still half-scrambled
        }
        if (shown > 0.5) {
            vec2 nudge = vec2(0.0);
            if (is(fx, 1.0) && !moving) {                 // ROSE: on a beat some characters are blown outward, then settle
                float pick = hash2(cell * 1.31 + 3.0);
                if (pick < 0.30) {
                    float ang = atan(cc.y, cc.x) + (hash2(cell + 9.0) - 0.5) * 1.7;
                    nudge = vec2(cos(ang), sin(ang) * 0.66) * kick * A * (0.25 + 0.45 * hash2(cell + 4.0));
                }
            }
            float react = moving ? 0.0 : live;
            vec4 n = drawArt(artNew, lowNew, cell, f, scr, sd, nudge, react);
            if (is(fx, 2.0) && burst > 0.02 && !moving) { // NEON: red and blue slide apart on a strong beat
                vec2 off = vec2(1.5 * burst, 0.0);
                vec2 pr = p + off, pb = p - off;
                n.r = mix(n.r, drawArt(artNew, lowNew, floor(pr), fract(pr), 0.0, sd, vec2(0.0), react).r, 0.9);
                n.b = mix(n.b, drawArt(artNew, lowNew, floor(pb), fract(pb), 0.0, sd, vec2(0.0), react).b, 0.9);
            }
            n.rgb = mix(n.rgb, mix(n.rgb, c3.rgb, 0.55) * 1.25, fresh * 0.8);
            float k = n.a;
            col = n.rgb * k + col * (1.0 - k);
            alpha = k + alpha * (1.0 - k);

            if (!moving && k > 0.5 && A > 0.001) {        // what the music adds on top of the settled picture
                if (is(fx, 1.0)) {                        // ROSE: a soft bloom that breathes with the loudness
                    vec3 g = (low(lowNew, cell + vec2(1.6, 1.1)).rgb + low(lowNew, cell + vec2(-1.6, 1.1)).rgb
                            + low(lowNew, cell + vec2(1.6, -1.1)).rgb + low(lowNew, cell + vec2(-1.6, -1.1)).rgb) * 0.25;
                    float gl = luma(g);
                    col += mix(g, c3.rgb * gl, 0.55) * gl * (0.10 + 0.62 * loud) * A;
                } else if (is(fx, 2.0)) {                 // NEON: edges light up cyan on one side, magenta on the other
                    float gx = luma(low(lowNew, cell + vec2(1.0, 0.0)).rgb) - luma(low(lowNew, cell - vec2(1.0, 0.0)).rgb);
                    float gy = luma(low(lowNew, cell + vec2(0.0, 1.0)).rgb) - luma(low(lowNew, cell - vec2(0.0, 1.0)).rgb);
                    float e = smoothstep(0.05, 0.40, abs(gx) + abs(gy));
                    col += mix(c2.rgb, c3.rgb, step(0.0, gx + gy)) * e * (0.05 + 1.05 * bass * bass) * A;
                    col *= 1.0 - 0.10 * burst * step(0.5, fract(gl_FragCoord.y * 0.5));   // scanlines flash with the burst
                } else if (is(fx, 3.0)) {                 // AMBER: phosphor flare sideways, afterglow trailing down
                    float hz = (luma(low(lowNew, cell + vec2(2.0, 0.0)).rgb) + luma(low(lowNew, cell - vec2(2.0, 0.0)).rgb)
                              + luma(low(lowNew, cell + vec2(4.5, 0.0)).rgb) + luma(low(lowNew, cell - vec2(4.5, 0.0)).rgb)) * 0.25;
                    float up = luma(low(lowNew, cell - vec2(0.0, 1.0)).rgb) * 0.5 + luma(low(lowNew, cell - vec2(0.0, 2.2)).rgb) * 0.3
                             + luma(low(lowNew, cell - vec2(0.0, 3.6)).rgb) * 0.2;
                    col += c3.rgb * hz * hz * (0.75 * kick + 0.22 * loud) * A;
                    col += c2.rgb * up * 0.55 * max(glow - kick, 0.0) * A;
                }
            }
        }
    }

    // 3. idle shimmer and the beat pulse on whatever picture is showing
    float wave = sin(time * 0.9 + cell.x * 0.11 + cell.y * 0.19);
    float sweepPos = fract(time * 0.07) * 1.7 - 0.35;     // a soft highlight crosses every ~14 s
    float sweepL = exp(-pow((cell.x / grid.x + 0.35 * cell.y / grid.y - sweepPos) * 9.0, 2.0));
    float centre = 1.0 - 0.9 * dot(cc, cc) * 2.0;         // the pulse is strongest in the middle
    float pulse = (is(fx, 0.0) || is(fx, 4.0)) ? 0.06 : 0.20;      // MONO and PAPER move their dots instead
    col *= 1.0 + shimmer * live * (0.045 * wave + 0.20 * sweepL) + pulse * kick * A * centre + 0.30 * crest;

    // MONO: a strong beat sends one clean ring of inverted cells out from the centre; it thins as it goes
    if (is(fx, 0.0) && ring >= 0.0 && A > 0.001 && alpha > 0.5) {
        vec2 rc = cc * vec2(grid.x * 6.0 / (grid.y * 9.0), 1.0);
        float w = 0.058 * A * smoothstep(0.0, 0.22, ring) * (1.0 - smoothstep(0.55, 1.55, ring));
        if (abs(length(rc) * 2.0 - ring) < w) col = (c0.rgb + c3.rgb) - col;
    }

    // 4. quiet digital rain wherever there is no picture (no art, letterbox, between tracks)
    if (alpha < 0.98) {
        float sp = 0.6 + 1.4 * hash1(cell.x * 3.3);
        float head = fract(time * 0.10 * sp + hash1(cell.x * 7.7));
        float dist = fract(head - cell.y / grid.y);       // how far behind the falling head
        float tail = exp(-dist * 10.0);
        float gi = floor(hash2(cell + floor(time * 2.0 + hash1(cell.x) * 10.0)) * nGlyphs);
        float m = glyph(gi, f) * tail * 0.75;
        vec3 rc = mix(c1.rgb, c3.rgb, tail * tail);
        col += rc * m * (1.0 - alpha);
        alpha += m * (1.0 - alpha);
    }

    // 5. nothing playing: the picture settles into a still one-bit dither in the flavour's quiet colour
    if (still > 0.001 && hasNew > 0.5 && !moving && inside(cell)) {
        vec4 a = low(lowNew, cell);
        if (a.a > 0.5) {
            vec2 ij = floor(f * vec2(2.0, 4.0));
            float l = luma(sub(artNew, cell, ij).rgb);
            vec2 g = cell * vec2(2.0, 4.0) + ij;
            float th = (BAYER[int(mod(g.y, 4.0)) * 4 + int(mod(g.x, 4.0))] + 0.5) / 16.0;
            float on = step(0.10 + 0.80 * th, invert > 0.5 ? 1.0 - l : l);
            vec2 d = abs(fract(f * vec2(2.0, 4.0)) - 0.5);
            float sq = step(d.x, 0.36) * step(d.y, 0.36);
            vec3 quiet = mix(c0.rgb, mix(c1.rgb, c2.rgb, 0.45), on * sq);
            col = mix(col, quiet, still);
        }
    }

    col *= 1.0 - scan * 0.34 * (1.0 - step(1.0, mod(gl_FragCoord.y, 3.0)));
    fragColor = vec4(col, alpha) * qt_Opacity;
}
