//!HOOK MAIN
//!BIND HOOKED
//!DESC Measure the largest PQ-encoded BT.2020 RGB component for L1 statistics

vec4 hook()
{
    vec4 pixel = HOOKED_tex(HOOKED_pos);
    float max_pq = clamp(max(pixel.r, max(pixel.g, pixel.b)), 0.0, 1.0);
    return vec4(vec3(max_pq), pixel.a);
}
