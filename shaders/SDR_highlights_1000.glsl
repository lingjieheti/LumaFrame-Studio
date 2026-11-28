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
#ifdef LF_SHOT_GRADE
    vec3 grade = shot_grade();
#ifdef LF_EXTENDED_WHITE
    // Positive luma excursions are present in some video-range masters.
    // Make room before RGB clipping. The gradual shoulder uses one common
    // scale for R'G'B'; black and ordinary midtones keep their original values.
    float reserve = grade.b * (20.0 / 219.0);
    float signal = dot(s.rgb, vec3(0.2126, 0.7152, 0.0722));
    if (reserve > 0.0 && signal > 0.8) {
        float x = clamp((signal - 0.8) / (0.2 + reserve), 0.0, 1.0);
        s.rgb *= 1.0 - reserve * x * x / signal;
    }
#endif
#endif
    vec3 linear709 = pow(clamp(s.rgb, 0.0, 1.0), vec3(2.4));
    // D65 linear-light primary conversion, rows expressed as dot products.
    vec3 rgb = 203.0 * vec3(
        dot(linear709, vec3(0.627404, 0.329283, 0.043313)),
        dot(linear709, vec3(0.069097, 0.919540, 0.011362)),
        dot(linear709, vec3(0.016391, 0.088013, 0.895595))
    );
#ifdef LF_REFERENCE_WHITE
    // BT.2408 display-referred direct mapping: preserve relative display
    // light and chromaticity, with nominal SDR white at 203 cd/m2.
    return vec4(nits_to_pq(rgb), s.a);
#else
    float y = dot(rgb, vec3(0.2627, 0.6780, 0.0593));
    // BT.2408 SDR reference white is 203 nits. At and below the knee
    // the black, middle grey and chromaticities from the normal conversion remain intact.
    const float knee = 120.0, white = 203.0, peak = 1000.0;
    float t = clamp((y - knee) / (white - knee), 0.0, 1.0);
#ifdef LF_SHOT_GRADE
    // Broad textured whites use a softer shoulder. Sparse glints can gain a
    // little separation; the additional lift peaks at 34 nits and tapers to
    // zero at the ceiling. High-key or heavily clipped shots keep the source look.
    float sparse = smoothstep(0.82, 0.98, grade.x);
    float shoulder = mix(t, 1.0 + 0.4 * (1.0 - t), sparse);
    shoulder = mix(shoulder, 1.0, grade.y);
    // A window can have a clipped light core and a textured surround in the
    // same shot. Fade out attenuation towards the ceiling, even when the
    // surround occupies most of the frame. Sparse highlights retain their lift.
    float weight = grade.x * shoulder;
    weight = mix(weight, max(weight, 1.0), smoothstep(0.38, 0.94, t));
    float mapped = min(y, white) + weight * (peak - white) * t*t*t;
#else
    float mapped = min(y, white) + (peak - white) * t*t*t;
#endif
    float gain = mapped / max(y, 0.00001);
    // Limit the brightest linear RGB channel, preserving chromaticity.
    gain = min(gain, peak / max(max(rgb.r, rgb.g), max(rgb.b, 0.00001)));
    return vec4(nits_to_pq(rgb * gain), s.a);
#endif
}
