//!HOOK MAIN
//!BIND HOOKED
//!WIDTH OUTPUT.w
//!HEIGHT OUTPUT.h
//!DESC Reduce native PQ MaxRGB to per-tile maximum, minimum and mean

vec4 hook()
{
    // Do the nonlinear max over RGB before any spatial averaging. Each tile
    // has equal area, even for raster dimensions that do not divide by eight:
    // boundary pixels contribute to the mean in proportion to their overlap.
    vec2 extent = HOOKED_size / target_size;
    ivec2 tile = ivec2(floor(HOOKED_pos * target_size));
    vec2 lo = vec2(tile) * extent;
    vec2 hi = min(vec2(tile + 1) * extent, HOOKED_size);
    float minimum = 1.0, maximum = 0.0, sum = 0.0;
    for (int y = int(floor(lo.y)); y < int(ceil(hi.y)); y++) {
        for (int x = int(floor(lo.x)); x < int(ceil(hi.x)); x++) {
            vec2 cell = vec2(x, y);
            vec2 overlap = min(hi, cell + 1.0) - max(lo, cell);
            vec3 rgb = HOOKED_tex((cell + 0.5) * HOOKED_pt).rgb;
            float value = clamp(max(rgb.r, max(rgb.g, rgb.b)), 0.0, 1.0);
            minimum = min(minimum, value);
            maximum = max(maximum, value);
            sum += value * overlap.x * overlap.y;
        }
    }
    return vec4(maximum, minimum, sum / (extent.x * extent.y), 1.0);
}
