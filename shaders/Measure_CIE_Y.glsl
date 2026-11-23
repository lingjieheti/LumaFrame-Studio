//!HOOK MAIN
//!BIND HOOKED
//!DESC Convert BT.2020 PQ RGB to neutral PQ luminance for scene statistics

vec4 hook()
{
    vec4 s = HOOKED_tex(HOOKED_pos);
    const float m1 = 2610.0/16384.0, m2 = 2523.0/32.0;
    const float c1 = 3424.0/4096.0, c2 = 2413.0/128.0, c3 = 2392.0/128.0;
    vec3 t = pow(clamp(s.rgb, 0.0, 1.0), vec3(1.0/m2));
    vec3 rgb = pow(max(t - c1, 0.0) / (c2 - c3*t), vec3(1.0/m1));
    float y = max(dot(rgb, vec3(0.2627, 0.6780, 0.0593)), 0.0);
    float p = pow(y, m1);
    float pq = pow((c1 + c2*p) / (1.0 + c3*p), m2);
    return vec4(vec3(pq), s.a);
}
