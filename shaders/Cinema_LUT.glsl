// Included in a MAIN hook by cinema_shader.py. The frame number refers to
// the presentation frame, including all FSRCNNX LUMA passes when enabled.
int lf_shot_index()
{
    int i = frame / LF_STRIDE - 1;
    return int(texelFetch(LF_FRAME_INDEX, ivec2(i % LF_FRAME_W, i / LF_FRAME_W), 0).r);
}

vec4 lf_parameters(int shot, int word)
{
    int i = 6 * shot + word;
    return texelFetch(LF_COEFFICIENTS, ivec2(i % LF_SHOT_W, i / LF_SHOT_W), 0);
}

vec3 shot_grade()
{
    return lf_parameters(lf_shot_index(), 0).rgb;
}

/* LF_SCENE_TRANSFORM */

float lf_mid_coordinate(float x)
{
    // Fixed middle-exposure knots: (3*pi*u - cos(3*pi*u) + 1) / (3*pi+2).
    const float knots[17] = float[17](
        0.0, 0.066310174, 0.157150871, 0.259281730, 0.355656914,
        0.431170602, 0.477748564, 0.497069668, 0.5, 0.502930332,
        0.522251436, 0.568829398, 0.644343086, 0.740718270, 0.842849129,
        0.933689826, 1.0);
    int lo = 0, hi = 16;
    for (int n = 0; n < 4; ++n) {
        int mid = (lo + hi) / 2;
        if (x <= knots[mid]) hi = mid; else lo = mid;
    }
    return float(lo) + (x - knots[lo]) / (knots[lo+1] - knots[lo]);
}

vec3 lf_power_coordinate(vec3 x, vec3 exponent)
{
    // Invert the knot function to find the cell, then interpolate in the
    // *actual* nonlinear knot interval as the published trilinear kernel does.
    vec3 base = clamp(floor(16.0 * pow(x, 1.0 / exponent)), 0.0, 15.0);
    vec3 lo = pow(base / 16.0, exponent);
    vec3 hi = pow((base + 1.0) / 16.0, exponent);
    return base + (x - lo) / (hi - lo);
}

vec3 lf_branch(int branch, vec3 grid, vec4 first_four, float fifth)
{
    vec3 pos = (clamp(grid, 0.0, 16.0) + 0.5) / vec3(17.0, 17.0, 255.0);
    vec3 out_pq = vec3(0.0);
    for (int basis = 0; basis < 5; ++basis) {
        float shift = float(branch * 5 + basis) / 15.0;
        float weight = basis == 4 ? fifth : first_four[basis];
        out_pq += weight * textureLod(LF_BASES, pos + vec3(0.0, 0.0, shift), 0.0).rgb;
    }
    return out_pq;
}

vec3 lf_display_shoulder(vec3 pq)
{
    // Maintain the app's 1000-nit mastering target. The shoulder starts at
    // 700 nits, scales linear RGB together and has unit slope at the join.
    if (max(max(pq.r, pq.g), pq.b) <= 0.71301175)
        return pq;
    const float m1 = 2610.0/16384.0, m2 = 2523.0/32.0;
    const float c1 = 3424.0/4096.0, c2 = 2413.0/128.0, c3 = 2392.0/128.0;
    vec3 p = pow(pq, vec3(1.0/m2));
    vec3 rgb = 10000.0 * pow(max(p - c1, 0.0) / (c2 - c3*p), vec3(1.0/m1));
    float peak = max(max(rgb.r, rgb.g), rgb.b);
    float excess = max(peak - 700.0, 0.0);
    return nits_to_pq(rgb * ((700.0 + 300.0 * excess / (300.0 + excess)) / max(peak, 700.0)));
}

vec4 hook()
{
    vec4 base = lf_scene_hook();
    int shot = lf_shot_index();
    float amount = lf_parameters(shot, 0).a;
    if (amount == 0.0)
        return base;
    vec3 x = clamp(HOOKED_tex(HOOKED_pos).rgb, 0.0, 1.0);
    float brightness = dot(x, vec3(0.2126, 0.7152, 0.0722));
    amount *= smoothstep(0.0, 0.02, brightness) * (0.35 + 0.65 * smoothstep(0.04, 0.35, brightness));
    vec3 means = lf_parameters(shot, 1).rgb;
    vec3 fifth = lf_parameters(shot, 5).rgb;
    vec3 bright = clamp((x - 0.55) / 0.45, 0.0, 1.0);
    vec3 dark = clamp((0.45 - x) / 0.45, 0.0, 1.0);
    vec3 middle = clamp(1.0 - bright - dark, 0.0, 1.0);
    vec3 pq = vec3(0.0);
    if (any(greaterThan(bright, vec3(0.0)))) {
        vec3 coord = lf_power_coordinate(x, 1.0 / (1.4 + 0.8 * means));
        pq += bright * lf_branch(0, coord, lf_parameters(shot, 2), fifth.r);
    }
    if (any(greaterThan(middle, vec3(0.0)))) {
        vec3 coord = vec3(lf_mid_coordinate(x.r), lf_mid_coordinate(x.g), lf_mid_coordinate(x.b));
        pq += middle * lf_branch(1, coord, lf_parameters(shot, 3), fifth.g);
    }
    if (any(greaterThan(dark, vec3(0.0)))) {
        vec3 coord = lf_power_coordinate(x, 2.2 - 0.8 * means);
        pq += dark * lf_branch(2, coord, lf_parameters(shot, 4), fifth.b);
    }
    return vec4(mix(base.rgb, lf_display_shoulder(clamp(pq, 0.0, 1.0)), amount), base.a);
}
