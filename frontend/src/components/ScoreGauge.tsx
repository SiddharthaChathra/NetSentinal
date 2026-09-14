"use client";

import { motion } from "framer-motion";

interface ScoreGaugeProps {
  score: number;
  label?: string;
  size?: number;
  strokeWidth?: number;
  getColor?: (score: number) => string;
}

export default function ScoreGauge({ 
  score, 
  label, 
  size = 120, 
  strokeWidth = 8,
  getColor = (s) => s >= 85 ? "text-green-500" : s >= 55 ? "text-orange-500" : "text-red-500"
}: ScoreGaugeProps) {
  const radius = (size - strokeWidth) / 2;
  const circumference = radius * 2 * Math.PI;
  const strokeDashoffset = circumference - (circumference * score) / 100;
  
  const colorClass = getColor(score);

  return (
    <div className="relative flex flex-col items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="transform -rotate-90">
        <circle 
          cx={size / 2} cy={size / 2} r={radius} 
          fill="none" 
          stroke="rgba(255,255,255,0.1)" 
          strokeWidth={strokeWidth} 
        />
        <motion.circle 
          cx={size / 2} cy={size / 2} r={radius} 
          fill="none" 
          stroke="currentColor" 
          strokeWidth={strokeWidth} 
          className={colorClass}
          strokeDasharray={circumference}
          initial={{ strokeDashoffset: circumference }}
          animate={{ strokeDashoffset }}
          transition={{ duration: 1.5, ease: "easeOut" }}
          strokeLinecap="round"
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className={`text-3xl font-bold ${colorClass}`}>{score}</span>
        {label && <span className="text-xs text-slate-400 mt-1">{label}</span>}
      </div>
    </div>
  );
}
