//!HOOK MAIN
//!BIND HOOKED
//!DESC Expand SDR highlights to 1000 cd/m2 in display-linear BT.2020

// MAIN gets normalized SDR R'G'B' after the source Y'CbCr matrix and range
// are decoded. The host marks the output of this custom hook as BT.2020/PQ.
vec3 nits_to_pq(vec3 v)
{
    const float m1 = 2610.0 / 16384.0, m2 = 2523.0 / 32.0;
    const float c1 = 3424.0 / 4096.0, c2 = 2413.0 / 128.0, c3 = 2392.0 / 128.0;
    vec3 t = pow(clamp(v / 10000.0, 0.0, 1.0), vec3(m1));
    return pow((c1 + c2*t) / (1.0 + c3*t), vec3(m2));
}

vec4 hook()
{
    vec4 s = HOOKED_tex(HOOKED_pos);
    vec3 linear709 = pow(clamp(s.rgb, 0.0, 1.0), vec3(2.4));
    // D65 linear-light primary conversion, rows expressed as dot products.
    vec3 rgb = 203.0 * vec3(
        dot(linear709, vec3(0.627404, 0.329283, 0.043313)),
        dot(linear709, vec3(0.069097, 0.919540, 0.011362)),
        dot(linear709, vec3(0.016391, 0.088013, 0.895595))
    );
    float y = dot(rgb, vec3(0.2627, 0.6780, 0.0593));
    // BT.2408 SDR reference white is 203 nits. At and below the knee
    // the black, middle grey and chromaticities from the normal conversion remain intact.
    const float knee = 120.0, white = 203.0, peak = 1000.0;
    float t = clamp((y - knee) / (white - knee), 0.0, 1.0);
    float mapped = min(y, white) + (peak - white) * t*t*t;
    float gain = mapped / max(y, 0.00001);
    // Limit the brightest linear RGB channel, preserving chromaticity.
    gain = min(gain, peak / max(max(rgb.r, rgb.g), max(rgb.b, 0.00001)));
    return vec4(nits_to_pq(rgb * gain), s.a);
}
