"use client";

import { useMemo, useRef, useState, useSyncExternalStore } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import * as THREE from "three";

export type AiAgentState = "idle" | "hover" | "thinking" | "responding";

interface AiAgentIconProps {
  state?: AiAgentState;
  className?: string;
}

/*
 * The "Ask NetSentinel" orb: a Siri-like flowing core with NetSentinel's
 * network nodes orbiting it.
 *
 * Why it is a shader. The first version rotated a 5-node web slowly and pulsed
 * the core by ±5% in a single cyan. At its real size (32px) that measured as
 * 3% of pixels changing per frame and two hue bands - technically animating,
 * practically invisible. It also used a transmission material, which makes
 * three.js render the scene a second time every frame.
 *
 * Here everything is drawn by one fragment shader on one quad: one draw call,
 * no geometry, no lights. Colour flow, the rippling outline and the orbiting
 * nodes are all computed per pixel, which is cheap and stays crisp when small.
 */

// Per-state targets. The live values glide towards these, so a change of state
// visibly speeds up and then calms back down instead of snapping.
const TARGETS: Record<AiAgentState, { flow: number; wobble: number; energy: number; orbit: number }> = {
  idle:       { flow: 0.9, wobble: 0.050, energy: 0.95, orbit: 0.55 },
  hover:      { flow: 1.8, wobble: 0.085, energy: 1.10, orbit: 1.20 },
  thinking:   { flow: 4.2, wobble: 0.130, energy: 1.30, orbit: 3.20 },
  responding: { flow: 2.4, wobble: 0.095, energy: 1.18, orbit: 1.70 },
};

const VERTEX = /* glsl */ `
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = vec4(position.xy, 0.0, 1.0); // full-canvas quad; no camera maths
  }
`;

const FRAGMENT = /* glsl */ `
  precision mediump float;
  varying vec2 vUv;
  uniform float uTime;    // flow phase - advanced by delta * flow, so speed changes never jump
  uniform float uOrbitT;  // node orbit phase
  uniform float uWobble;  // outline ripple amplitude
  uniform float uEnergy;  // brightness / saturation

  // NetSentinel cyan -> blue -> violet -> pink, looping.
  vec3 palette(float t) {
    vec3 c0 = vec3(0.02, 0.84, 0.84);
    vec3 c1 = vec3(0.23, 0.51, 0.98);
    vec3 c2 = vec3(0.56, 0.36, 0.98);
    vec3 c3 = vec3(0.86, 0.30, 0.90);
    t = fract(t) * 4.0;
    if (t < 1.0) return mix(c0, c1, smoothstep(0.0, 1.0, t));
    if (t < 2.0) return mix(c1, c2, smoothstep(0.0, 1.0, t - 1.0));
    if (t < 3.0) return mix(c2, c3, smoothstep(0.0, 1.0, t - 2.0));
    return mix(c3, c0, smoothstep(0.0, 1.0, t - 3.0));
  }

  void main() {
    vec2 p = vUv * 2.0 - 1.0;
    float r = length(p);
    float a = atan(p.y, p.x);
    float t = uTime;

    // A living outline: two travelling ripples around the rim.
    float edge = 0.60 + uWobble * (0.6 * sin(3.0 * a + t * 1.7) + 0.4 * sin(5.0 * a - t * 2.3));

    // Flowing interior: domain-warped waves, so colour bands drift and fold.
    vec2 q = p * 1.7;
    q += 0.38 * vec2(sin(q.y * 2.1 + t * 1.3), cos(q.x * 1.7 - t * 1.1));
    q += 0.26 * vec2(sin(q.y * 3.3 - t * 1.9), cos(q.x * 2.9 + t * 1.5));
    float flow = 0.5 + 0.5 * sin(q.x * 1.3 + q.y * 1.1 + t * 0.8);
    float swirl = 0.5 + 0.5 * sin(q.y * 2.0 - q.x * 0.7 - t);
    vec3 col = mix(palette(flow * 0.7 + t * 0.06 + a / 6.2832 * 0.5),
                   palette(flow + 0.35 - t * 0.05), swirl);

    float body = smoothstep(edge, edge - 0.05, r);           // antialiased disc
    float core = exp(-r * r * 5.0);
    col = col * (0.70 + 0.55 * core) * uEnergy + vec3(0.85, 0.97, 1.0) * core * 0.30 * uEnergy;

    // Soft halo just outside the rim, tinted by the colour passing it.
    vec3 haloCol = palette(t * 0.06 + a / 6.2832);
    float halo = exp(-max(r - edge, 0.0) * 11.0) * (1.0 - body) * 0.60 * uEnergy;

    // NetSentinel's network: five nodes on tilted orbits, linked to the core.
    float nodes = 0.0;
    float links = 0.0;
    for (int i = 0; i < 5; i++) {
      float fi = float(i);
      float ang = fi * 1.2566 + uOrbitT * (0.75 + 0.12 * fi);
      float rad = 0.84 + 0.05 * sin(uOrbitT * 0.7 + fi * 2.1);
      vec2 np = vec2(cos(ang), sin(ang) * 0.78) * rad;
      nodes += smoothstep(0.085, 0.045, length(p - np));
      float h = clamp(dot(p, np) / dot(np, np), 0.0, 1.0);
      float dl = length(p - np * h);
      links += smoothstep(0.035, 0.0, dl) * smoothstep(edge - 0.02, edge + 0.06, r);
    }
    nodes = min(nodes, 1.0);
    links = min(links, 1.0) * 0.55;

    vec3 rgb = col * body + haloCol * halo + vec3(0.80, 0.96, 1.0) * nodes + haloCol * links;
    float alpha = clamp(body + halo + nodes + links, 0.0, 1.0);
    gl_FragColor = vec4(rgb, alpha);
  }
`;

function Orb({ state, onFirstFrame }: { state: AiAgentState; onFirstFrame: () => void }) {
  const material = useRef<THREE.ShaderMaterial>(null);
  const live = useRef({ ...TARGETS[state] });
  const firstFrame = useRef(true);
  const uniforms = useMemo(
    () => ({
      uTime: { value: 0 },
      uOrbitT: { value: 0 },
      uWobble: { value: TARGETS.idle.wobble },
      uEnergy: { value: TARGETS.idle.energy },
    }),
    [],
  );

  useFrame((_, delta) => {
    const m = material.current;
    if (!m) return;
    const dt = Math.min(delta, 0.05); // a backgrounded tab must not jump on return
    const target = TARGETS[state];
    const v = live.current;
    const k = 1 - Math.exp(-dt * 3.5); // ~0.3s to settle on a new state
    v.flow += (target.flow - v.flow) * k;
    v.wobble += (target.wobble - v.wobble) * k;
    v.energy += (target.energy - v.energy) * k;
    v.orbit += (target.orbit - v.orbit) * k;
    m.uniforms.uTime.value += dt * v.flow;
    m.uniforms.uOrbitT.value += dt * v.orbit;
    m.uniforms.uWobble.value = v.wobble;
    m.uniforms.uEnergy.value = v.energy;
    if (firstFrame.current) {
      firstFrame.current = false;
      onFirstFrame();
    }
  });

  return (
    <mesh frustumCulled={false}>
      <planeGeometry args={[2, 2]} />
      <shaderMaterial
        ref={material}
        vertexShader={VERTEX}
        fragmentShader={FRAGMENT}
        uniforms={uniforms}
        transparent
        depthWrite={false}
      />
    </mesh>
  );
}

// ----------------------------------------------------------------------
// CSS version: the instant placeholder, the small-screen version, and - with
// motion-safe - a still orb for people who asked for reduced motion.
// ----------------------------------------------------------------------
const FALLBACK_SPEED: Record<AiAgentState, string> = {
  idle: "motion-safe:animate-[spin_6s_linear_infinite]",
  hover: "motion-safe:animate-[spin_3s_linear_infinite]",
  thinking: "motion-safe:animate-[spin_1.2s_linear_infinite]",
  responding: "motion-safe:animate-[spin_2.2s_linear_infinite]",
};

function FallbackIcon({ state = "idle", className = "" }: AiAgentIconProps) {
  return (
    <div className={`relative flex items-center justify-center ${className}`} aria-hidden>
      <div
        className={`absolute inset-[20%] rounded-full shadow-[0_0_10px_rgba(6,214,214,0.55)] ${FALLBACK_SPEED[state]}`}
        style={{ background: "conic-gradient(from 0deg, #06d6d6, #3b82f6, #8e5cf9, #db4ce6, #06d6d6)" }}
      />
      <div className="absolute inset-[30%] rounded-full bg-white/25 blur-[2px]" />
      <div className={`absolute inset-[6%] rounded-full border border-cyan-300/40 border-t-cyan-200 ${FALLBACK_SPEED[state]}`} />
    </div>
  );
}

// Whether to draw the shader: not during server render or hydration, not for
// reduced motion, and not on small screens. Read through useSyncExternalStore
// so it follows the media query live.
function subscribe(onChange: () => void) {
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  reduced.addEventListener("change", onChange);
  window.addEventListener("resize", onChange);
  return () => {
    reduced.removeEventListener("change", onChange);
    window.removeEventListener("resize", onChange);
  };
}
const can3D = () => !window.matchMedia("(prefers-reduced-motion: reduce)").matches && window.innerWidth >= 768;
function useCan3D() {
  return useSyncExternalStore(subscribe, can3D, () => false);
}

export default function AiAgentIcon({ state = "idle", className = "w-6 h-6" }: AiAgentIconProps) {
  const enabled = useCan3D();
  const [drawn, setDrawn] = useState(false);

  // The CSS orb is in place from the first paint, in the same box, so there is
  // no layout shift; the shader fades in over it once it has drawn a frame.
  return (
    <div className={`relative ${className}`} data-agent-state={state} aria-hidden>
      <FallbackIcon
        state={state}
        className={`absolute inset-0 transition-opacity duration-500 ${enabled && drawn ? "opacity-0" : "opacity-100"}`}
      />
      {enabled && (
        <div className={`absolute inset-0 transition-opacity duration-500 ${drawn ? "opacity-100" : "opacity-0"}`}>
          <Canvas
            dpr={[1, 2]}
            flat
            gl={{ alpha: true, antialias: false, powerPreference: "low-power" }}
            style={{ pointerEvents: "none" }}
          >
            <Orb state={state} onFirstFrame={() => setDrawn(true)} />
          </Canvas>
        </div>
      )}
    </div>
  );
}
