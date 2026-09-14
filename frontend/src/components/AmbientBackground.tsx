"use client";

import { useRef, useMemo, useState, useEffect } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import * as THREE from "three";

function VolumetricDataWave() {
  const groupRef = useRef<THREE.Group>(null);
  const [mouse, setMouse] = useState({ x: 0, y: 0 });
  
  // Create a single plane geometry that spans the entire screen
  // 160x100 is wide enough to cover ultra-wide monitors when set back in Z
  const { geometry } = useMemo(() => {
    const geo = new THREE.PlaneGeometry(160, 100, 90, 60);
    const count = geo.attributes.position.count;
    const col = new Float32Array(count * 3);
    geo.setAttribute('color', new THREE.BufferAttribute(col, 3));
    return { geometry: geo };
  }, []);

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      setMouse({
        x: (e.clientX / window.innerWidth) * 2 - 1,
        y: -(e.clientY / window.innerHeight) * 2 + 1,
      });
    };
    window.addEventListener("mousemove", handleMouseMove, { passive: true });
    return () => window.removeEventListener("mousemove", handleMouseMove);
  }, []);

  useFrame((state) => {
    if (!groupRef.current) return;
    
    const time = state.clock.elapsedTime * 0.7;
    const posAttribute = geometry.attributes.position as THREE.BufferAttribute;
    const colAttribute = geometry.attributes.color as THREE.BufferAttribute;
    const pos = posAttribute.array;
    const col = colAttribute.array;

    for (let i = 0; i < posAttribute.count; i++) {
      const x = pos[i * 3];
      const y = pos[i * 3 + 1];
      
      const distFromCenter = Math.sqrt(x * x + y * y);
      
      // Complex undulating wave calculation
      const wave1 = Math.sin(distFromCenter * 0.12 - time * 1.5);
      const wave2 = Math.sin(x * 0.08 + time * 1.1);
      const wave3 = Math.cos(y * 0.08 + time * 1.3);
      
      let z = (wave1 + wave2 + wave3) * 2.5;
      
      // ---------------------------------------------------------
      // CRITICAL UI/UX FEATURE:
      // Flatten the waves and push them deep into the background 
      // near the center of the screen. This ensures the dashboard
      // text and cards are never obscured or unreadable.
      // ---------------------------------------------------------
      const centerDampening = Math.min(1, Math.pow(distFromCenter / 25, 2)); 
      z = z * centerDampening - (1 - centerDampening) * 6;
      
      // Mouse interaction: repulsive ripple effect on hover
      const mouseWorldX = mouse.x * 60;
      const mouseWorldY = mouse.y * 40;
      const distToMouse = Math.sqrt(Math.pow(x - mouseWorldX, 2) + Math.pow(y - mouseWorldY, 2));
      
      if (distToMouse < 18) {
        const hoverForce = (18 - distToMouse) * 0.35;
        z += hoverForce;
      }
      
      pos[i * 3 + 2] = z;
      
      // Depth-based heatmap coloring
      const depthNormalized = Math.max(0, Math.min(1, (z + 8) / 16)); 
      
      // Deep cyber-blue in troughs -> bright neon cyan at peaks
      col[i * 3] = 0.01 + depthNormalized * 0.15; // R
      col[i * 3 + 1] = 0.25 + depthNormalized * 0.75; // G
      col[i * 3 + 2] = 0.45 + depthNormalized * 0.55; // B
    }
    
    posAttribute.needsUpdate = true;
    colAttribute.needsUpdate = true;
    
    // Subtle parallax tilt across the whole environment
    groupRef.current.rotation.x = mouse.y * 0.04;
    groupRef.current.rotation.y = mouse.x * 0.04;
  });

  return (
    <group ref={groupRef} position={[0, 0, -20]}>
      {/* 1. The glowing data nodes (points) */}
      <points geometry={geometry}>
        <pointsMaterial 
          size={0.22} 
          vertexColors 
          transparent 
          opacity={0.85} 
          blending={THREE.AdditiveBlending}
          depthWrite={false}
        />
      </points>
      {/* 2. The connecting network fabric (wireframe) */}
      <mesh geometry={geometry}>
        <meshBasicMaterial 
          vertexColors 
          wireframe 
          transparent 
          opacity={0.15} 
          blending={THREE.AdditiveBlending}
          depthWrite={false}
        />
      </mesh>
    </group>
  );
}

export default function AmbientBackground() {
  const [reducedMotion, setReducedMotion] = useState(false);

  useEffect(() => {
    const mediaQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReducedMotion(mediaQuery.matches);
    
    const listener = (e: MediaQueryListEvent) => setReducedMotion(e.matches);
    mediaQuery.addEventListener("change", listener);
    return () => mediaQuery.removeEventListener("change", listener);
  }, []);

  if (reducedMotion) {
    return (
      <div className="fixed inset-0 z-[-1] bg-gradient-to-br from-[#060a13] via-[#091524] to-[#04121d]" />
    );
  }

  return (
    <div className="fixed inset-0 z-[-1] pointer-events-none">
      <Canvas camera={{ position: [0, 0, 15], fov: 60 }}>
        {/* Dark fog fades the edges of the wave perfectly into the background */}
        <fog attach="fog" args={["#060a13", 15, 50]} />
        <ambientLight intensity={0.5} />
        <VolumetricDataWave />
      </Canvas>
    </div>
  );
}
