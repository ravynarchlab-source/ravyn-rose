#version 440
// Rice ASCII DECK: draws the album art as a grid of character cells, entirely on the GPU.
//
// Inputs
//   artNew / artOld  the art, already cropped, contrast-corrected and sharpened by deck.py and scaled
//                    to exactly 2 x 4 texels per character cell (so braille dots and half blocks have
//                    real detail to show). Alpha 0 = no picture in that cell (letterbox in "fit").
//   atlas            one row of Monocraft glyphs, darkest to brightest, one cell each.
//   mode             0 classic ASCII ramp, true colour   1 half blocks   2 braille dots   3 theme tint
//   progress         0..1 while a track change is animated: the old art falls apart into digital rain,
//                    then the new art "decodes" (random glyphs settle into the picture). 1 = settled.
//   beat             0..1 envelope from cava (bass onsets); shimmer = strength of the idle shimmer.
//   c0..c3           the flavour's four colours, from the panel background to the brightest ink.
//   mono, invert     1 = draw in grey only; 1 = dark ink on a light panel (the PAPER flavour).
//   scan             1 = dim every third pixel row, like a CRT (the AMBER flavour).
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec2 grid;        // columns, rows
    float nGlyphs;
    float mode;
    float time;       // seconds
    float frame;      // tick counter (15 per second)
    float progress;
    float beat;
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
};
layout(binding = 1) uniform sampler2D artNew;
layout(binding = 2) uniform sampler2D artOld;
layout(binding = 3) uniform sampler2D atlas;

const float BAYER[16] = float[16](0.0, 8.0, 2.0, 10.0, 12.0, 4.0, 14.0, 6.0, 3.0, 11.0, 1.0, 9.0, 15.0, 7.0, 13.0, 5.0);

float hash1(float n) { return fract(sin(n * 127.1) * 43758.5453); }
float hash2(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float luma(vec3 c) { return dot(c, vec3(0.299, 0.587, 0.114)); }

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
vec4 cellAvg(sampler2D t, vec2 cell) {
    vec4 s = vec4(0.0);
    for (int j = 0; j < 4; j++)
        for (int i = 0; i < 2; i++)
            s += sub(t, cell, vec2(float(i), float(j)));
    return s / 8.0;
}
float glyph(float gi, vec2 f) {
    return textureLod(atlas, vec2((gi + clamp(f.x, 0.0, 0.999)) / nGlyphs, f.y), 0.0).r;
}

// One cell of an art layer. scr = share of cells shown scrambled (0 = the picture, 1 = all noise).
vec4 drawArt(sampler2D t, vec2 cell, vec2 f, float scr, float seed) {
    vec4 a = cellAvg(t, cell);
    if (a.a < 0.5) return vec4(0.0);
    vec3 avg = a.rgb;
    float L = luma(avg);
    float ink = invert > 0.5 ? 1.0 - L : L;      // light panel: dark parts of the picture get the ink
    bool scrambled = hash2(cell + seed) < scr;
    vec3 col;
    if (mode < 0.5 || mode > 2.5) {                       // classic ramp / theme tint
        float gi = floor(clamp(ink, 0.0, 0.999) * nGlyphs);
        if (scrambled) gi = floor(hash2(cell * 1.7 + seed * 3.1) * nGlyphs);
        float m = glyph(gi, f);
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
        if (scrambled) col = mix(col, c2.rgb * (0.25 + 0.75 * hash2(cell * 2.3 + seed + j0)), 0.85);
    } else {                                              // braille: 2 x 4 dots per cell, ordered dither
        vec2 ij = floor(f * vec2(2.0, 4.0));
        float l = luma(sub(t, cell, ij).rgb);
        vec2 g = cell * vec2(2.0, 4.0) + ij;
        float th = (BAYER[int(mod(g.y, 4.0)) * 4 + int(mod(g.x, 4.0))] + 0.5) / 16.0;
        float on = step(0.08 + 0.84 * th, (invert > 0.5 ? 1.0 - l : l) + 0.05 * beat);
        if (scrambled) on = step(0.5, hash2(g + seed));
        vec2 d = fract(f * vec2(2.0, 4.0)) - 0.5;
        float dotMask = 1.0 - smoothstep(0.30, 0.48, length(d));
        vec3 fg = (scrambled || invert > 0.5) ? c3.rgb : mix(vivid(avg), vec3(1.0), 0.12);
        col = mix(invert > 0.5 ? c0.rgb : avg * 0.32, fg, on * dotMask);
    }
    if (mono > 0.5) col = vec3(luma(col));
    return vec4(col, 1.0);
}

void main() {
    vec2 p = qt_TexCoord0 * grid;
    vec2 cell = floor(p);
    vec2 f = fract(p);
    float seed = floor(frame * 0.5);                      // scrambled glyphs change 7-8 times a second
    vec3 col = vec3(0.0);                                 // premultiplied
    float alpha = 0.0;
    bool moving = progress < 0.999;

    // 1. old art dissolves into digital rain (first 60 % of the transition)
    if (hasOld > 0.5 && moving && progress < 0.62) {
        float a = clamp(progress / 0.60, 0.0, 1.0);
        float fall = a * a * grid.y * (0.35 + 1.10 * hash1(cell.x + 17.0));
        vec2 src = vec2(cell.x, cell.y - floor(fall));
        if (src.y >= 0.0) {
            vec4 o = drawArt(artOld, src, f, smoothstep(0.0, 0.5, a), seed);
            float fade = 1.0 - smoothstep(0.55, 1.0, a);
            o.rgb = mix(o.rgb, c2.rgb * (0.25 + 1.5 * luma(o.rgb)), smoothstep(0.10, 0.70, a));
            col = o.rgb * o.a * fade;
            alpha = o.a * fade;
        } else {                                          // the trail left above the falling picture
            float tail = exp(src.y * 0.22) * (1.0 - 0.7 * a);
            float m = glyph(floor(hash2(cell + seed) * nGlyphs), f) * tail;
            col = c3.rgb * m;
            alpha = m;
        }
    }

    // 2. new art decodes in (from 35 % on); when settled it is simply the picture
    if (hasNew > 0.5) {
        float b = moving ? clamp((progress - 0.35) / 0.65, 0.0, 1.0) : 1.0;
        float rv = 0.12 + 0.58 * hash2(cell * 0.73 + 5.0) + 0.12 * (cell.y / grid.y);
        float started = moving ? step(rv - 0.20, b) : 1.0;
        float done = moving ? smoothstep(rv, rv + 0.12, b) : 1.0;
        if (started > 0.5) {
            vec4 n = drawArt(artNew, cell, f, 1.0 - done, seed);
            float k = n.a * mix(0.70, 1.0, done);
            col = n.rgb * k + col * (1.0 - k);
            alpha = k + alpha * (1.0 - k);
        }
    }

    // 3. idle shimmer and beat pulse on whatever picture is showing
    float wave = sin(time * 0.9 + cell.x * 0.11 + cell.y * 0.19);
    float sweepPos = fract(time * 0.07) * 1.7 - 0.35;     // a soft highlight crosses every ~14 s
    float sweep = exp(-pow((cell.x / grid.x + 0.35 * cell.y / grid.y - sweepPos) * 9.0, 2.0));
    vec2 c0 = cell / grid - 0.5;
    float centre = 1.0 - 0.9 * dot(c0, c0) * 2.0;         // the pulse is strongest in the middle
    col *= 1.0 + shimmer * (0.045 * wave + 0.20 * sweep) + 0.30 * beat * centre;

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

    col *= 1.0 - scan * 0.34 * (1.0 - step(1.0, mod(gl_FragCoord.y, 3.0)));
    fragColor = vec4(col, alpha) * qt_Opacity;
}
