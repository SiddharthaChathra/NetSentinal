"use client";

import React, { useRef, useSyncExternalStore } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import * as THREE from "three";

export type AiAgentState = "idle" | "hover" | "thinking" | "responding";

interface AiAgentIconProps {
  state?: AiAgentState;
  className?: string;
}

// ----------------------------------------------------------------------
// 2D Fallback / Placeholder (CSS Animated)
// ----------------------------------------------------------------------
function FallbackIcon({ state, className = "" }: AiAgentIconProps) {
  const isThinking = state === "thinking";
  const isHover = state === "hover";

  return (
    <div className={`relative flex items-center justify-center ${className}`}>
      {/* Core */}
      <div
        className={`rounded-full bg-cyan-400/80 shadow-[0_0_10px_rgba(6,214,214,0.5)] transition-all duration-300 ${
          isThinking
            ? "w-1/2 h-1/2 animate-pulse"
            : isHover
            ? "w-[45%] h-[45%] shadow-[0_0_15px_rgba(6,214,214,0.8)]"
            : "w-2/5 h-2/5"
        }`}
      />
      {/* Orbiting ring 1 */}
      <div
        className={`absolute inset-[15%] border border-cyan-500/30 rounded-full border-t-cyan-400/80 transition-all ${
          isThinking ? "animate-spin" : "animate-[spin_4s_linear_infinite]"
        }`}
        style={{ animationDuration: isThinking ? "1s" : isHover ? "2s" : "4s" }}
      />
      {/* Orbiting ring 2 */}
      <div
        className={`absolute inset-[25%] border border-cyan-500/20 rounded-full border-b-cyan-400/60 transition-all ${
          isThinking ? "animate-[spin_1.5s_linear_infinite_reverse]" : "animate-[spin_5s_linear_infinite_reverse]"
        }`}
        style={{ animationDuration: isThinking ? "1.5s" : isHover ? "2.5s" : "5s" }}
      />
    </div>
  );
}

// ----------------------------------------------------------------------
// 3D Scene Components
// ----------------------------------------------------------------------

// The orbiting nodes, computed once. Evenly spread on a sphere (golden-angle
// spiral) with slightly varied radii - the look of a random web, but the same
// shape every time, and no Math.random() during render, which React requires
// render to be free of.
const NODE_COUNT = 5;
const WEB = (() => {
  const pos = new Float32Array(NODE_COUNT * 3 + 3); // index 0 is the core at (0,0,0)
  const indices: number[] = [];
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < NODE_COUNT; i++) {
    const y = 1 - (2 * (i + 0.5)) / NODE_COUNT;
    const r = Math.sqrt(1 - y * y);
    const radius = 1.2 + 0.5 * (((i * 7) % 5) / 4);
    const idx = (i + 1) * 3;
    pos[idx] = radius * r * Math.cos(golden * i);
    pos[idx + 1] = radius * y;
    pos[idx + 2] = radius * r * Math.sin(golden * i);
    indices.push(0, i + 1);              // core to node
    if (i > 0) indices.push(i, i + 1);   // node to the previous one, forming a web
  }
  indices.push(NODE_COUNT, 1);           // close the ring
  return { positions: pos, lineIndices: new Uint16Array(indices) };
})();

// Whether to draw the 3D version: not during server render or hydration, not
// for people who asked for reduced motion, and not on small screens. Read
// through useSyncExternalStore so it follows the media query live and needs
// no setState-in-effect.
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

function AgentCore({ state }: { state: AiAgentState }) {
  const coreRef = useRef<THREE.Mesh>(null);
  const groupRef = useRef<THREE.Group>(null);
  const linesRef = useRef<THREE.LineSegments>(null);

  const { positions, lineIndices } = WEB;

  useFrame((stateCtx, delta) => {
    if (!coreRef.current || !groupRef.current) return;

    const isThinking = state === "thinking";
    const isHover = state === "hover";
    const isResponding = state === "responding";

    // Speeds
    const orbitSpeed = isThinking ? 2.5 : isHover ? 1.0 : isResponding ? 0.7 : 0.3;
    const pulseSpeed = isThinking ? 5.0 : isResponding ? 2.0 : 1.0;

    // Rotate the whole web
    groupRef.current.rotation.y += delta * orbitSpeed;
    groupRef.current.rotation.x += delta * orbitSpeed * 0.5;

    // Pulse the core
    const time = stateCtx.clock.elapsedTime;
    const scale = 1.0 + Math.sin(time * pulseSpeed) * (isThinking ? 0.15 : 0.05);
    coreRef.current.scale.set(scale, scale, scale);

    // Dynamic emissive intensity
    const material = coreRef.current.material as THREE.MeshStandardMaterial;
    material.emissiveIntensity = isThinking
      ? 1.5 + Math.sin(time * pulseSpeed) * 0.5
      : isHover
      ? 1.2
      : 0.8;
  });

  return (
    <group ref={groupRef}>
      {/* Central Core */}
      <mesh ref={coreRef}>
        <sphereGeometry args={[0.5, 32, 32]} />
        <meshPhysicalMaterial
          color="#06d6d6"
          emissive="#06d6d6"
          emissiveIntensity={0.8}
          transparent={true}
          opacity={0.9}
          roughness={0.1}
          metalness={0.1}
          transmission={0.5}
          thickness={0.5}
        />
      </mesh>

      {/* Lines connecting nodes */}
      <lineSegments ref={linesRef}>
        <bufferGeometry>
          <bufferAttribute
            attach="attributes-position"
            args={[positions, 3]}
            count={positions.length / 3}
          />
          <bufferAttribute
            attach="index"
            args={[lineIndices, 1]}
            count={lineIndices.length}
          />
        </bufferGeometry>
        <lineBasicMaterial color="#06d6d6" transparent opacity={0.3} />
      </lineSegments>

      {/* Orbiting Node Points */}
      {Array.from({ length: 5 }).map((_, i) => {
        const idx = (i + 1) * 3;
        return (
          <mesh
            key={i}
            position={[positions[idx], positions[idx + 1], positions[idx + 2]]}
          >
            <sphereGeometry args={[0.06, 16, 16]} />
            <meshBasicMaterial color="#00ffff" transparent opacity={0.8} />
          </mesh>
        );
      })}
    </group>
  );
}

// ----------------------------------------------------------------------
// Main Wrapper Component
// ----------------------------------------------------------------------
export default function AiAgentIcon({ state = "idle", className = "w-6 h-6" }: AiAgentIconProps) {
  // The CSS fallback during server render and hydration, for reduced motion,
  // and on small screens.
  if (!useCan3D()) {
    return <FallbackIcon state={state} className={className} />;
  }

  return (
    <div className={`relative ${className}`}>
      {/* Provide an absolute fallback behind the canvas just in case context takes a moment to load */}
      <div className="absolute inset-0 z-0 pointer-events-none opacity-50 blur-sm">
        <FallbackIcon state={state} className="w-full h-full" />
      </div>
      
      <div className="absolute inset-0 z-10 pointer-events-none">
        <Canvas
          camera={{ position: [0, 0, 3.5], fov: 45 }}
          // Transparent background
          gl={{ alpha: true, antialias: true }}
        >
          <ambientLight intensity={0.5} />
          <pointLight position={[10, 10, 10]} intensity={1} color="#ffffff" />
          <pointLight position={[-10, -10, -10]} intensity={0.5} color="#06d6d6" />
          
          <React.Suspense fallback={null}>
            <AgentCore state={state} />
          </React.Suspense>
        </Canvas>
      </div>
    </div>
  );
}
