#version 440
// Rice ASCII DECK: the spectrum strip. 32 bars from the shared analyser (react/analyser.py), drawn as
// stacked block segments in the flavour's colours. style: 0 stacked segments, 1 solid bars, 2 round dots.
// beat brightens the lit segments; volRip is the ripple of a volume change passing through (-1 = none).
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec4 b0; vec4 b1; vec4 b2; vec4 b3; vec4 b4; vec4 b5; vec4 b6; vec4 b7;   // 32 bar heights, 0..1
    vec2 cells;       // bars across, segments up
    float beat;
    float volRip;
    float fade;
    vec4 c1;
    vec4 c2;
    vec4 c3;
    float style;
    vec2 size;        // the strip in pixels (round dots need the cell's real shape)
};


void main() {
    vec4 B[8] = vec4[8](b0, b1, b2, b3, b4, b5, b6, b7);
    vec2 p = vec2(qt_TexCoord0.x, 1.0 - qt_TexCoord0.y) * cells;
    vec2 cell = floor(p);
    vec2 f = fract(p);
    int bar = int(clamp(cell.x * 32.0 / cells.x, 0.0, 31.0));
    float v = B[bar / 4][bar % 4];
    float dx = (qt_TexCoord0.x - volRip) * 6.0;
    float crest = volRip > -0.5 ? exp(-dx * dx) : 0.0;
    v = min(1.0, v + 0.45 * crest);                      // the ripple lifts the bars as it passes
    float level = v * cells.y;                           // lit segments, fractional
    float lit = clamp(level - cell.y, 0.0, 1.0);         // the top segment is partly lit
    float box;
    if (style < 0.5) box = step(0.12, f.x) * step(f.x, 0.88) * step(0.14, f.y) * step(f.y, 0.86);
    else if (style < 1.5) box = step(0.10, f.x) * step(f.x, 0.90);
    else {
        vec2 cellPx = size / cells;
        float r = 0.38 * min(cellPx.x, cellPx.y);
        box = 1.0 - smoothstep(r - 1.0, r, length((f - 0.5) * cellPx));
    }
    float h = cell.y / cells.y;
    vec3 c = mix(c1.rgb, c2.rgb, smoothstep(0.0, 0.55, h));
    c = mix(c, c3.rgb, smoothstep(0.5, 1.0, h));
    float ghost = 0.07 * step(cell.y, 0.5);              // a faint base line so the strip is never empty
    float a = box * max(lit * (0.85 + 0.25 * beat + 0.3 * crest) * fade, ghost);
    fragColor = vec4(c * a, a) * qt_Opacity;
}
