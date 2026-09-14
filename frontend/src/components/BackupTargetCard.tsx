"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Server, Activity, ChevronDown, ChevronUp, AlertTriangle, ShieldCheck, ShieldAlert } from "lucide-react";
import ScoreGauge from "./ScoreGauge";

interface PortInfo {
  port: number;
  service: string;
  open: boolean;
}

interface BackupTarget {
  id: string;
  name: string;
  isBackupTarget: boolean;
  reachability: "up" | "degraded" | "down";
  dnsResolved: boolean;
  latencyMs: number;
  packetLossPct: number;
  ports: PortInfo[];
  backupReadiness: {
    score: number;
    verdict: "ready" | "at-risk" | "not-ready";
    slaWindowHours: number;
    estimatedTransferHours: number;
    willMeetSla: boolean;
  };
}

interface BackupTargetCardProps {
  target: BackupTarget;
}

export default function BackupTargetCard({ target }: BackupTargetCardProps) {
  const [expanded, setExpanded] = useState(false);
  const { backupReadiness, reachability, ports, latencyMs, packetLossPct, dnsResolved } = target;

  const isDown = reachability === "down";

  // Visual cues based on verdict
  const verdictColors = {
    ready: "border-green-500/30 shadow-[0_0_15px_rgba(34,197,94,0.1)]",
    "at-risk": "border-orange-500/50 shadow-[0_0_20px_rgba(249,115,22,0.15)]",
    "not-ready": "border-red-500/50 shadow-[0_0_20px_rgba(239,68,68,0.15)]"
  };

  const statusIcons = {
    up: <ShieldCheck className="w-5 h-5 text-green-400" />,
    degraded: <AlertTriangle className="w-5 h-5 text-orange-400" />,
    down: <ShieldAlert className="w-5 h-5 text-red-400" />
  };

  return (
    <motion.div 
      layout
      className={`glass-card p-5 ${verdictColors[backupReadiness.verdict]} border-l-4 ${
        backupReadiness.verdict === "ready" ? "border-l-green-500" :
        backupReadiness.verdict === "at-risk" ? "border-l-orange-500" : "border-l-red-500"
      }`}
    >
      <div className="flex justify-between items-start cursor-pointer" onClick={() => setExpanded(!expanded)}>
        <div className="flex items-center gap-4">
          <div className="p-3 bg-white/5 rounded-xl border border-white/10">
            <Server className="w-6 h-6 text-cyan-400" />
          </div>
          <div>
            <h3 className="text-lg font-bold text-white flex items-center gap-2">
              {target.name}
              {target.id.startsWith("demo-") && (
                <span className="text-[10px] uppercase tracking-wider bg-cyan-500/20 text-cyan-300 px-2 py-0.5 rounded-full border border-cyan-500/30">
                  Demo
                </span>
              )}
            </h3>
            <div className="flex items-center gap-3 mt-1 text-sm text-slate-400">
              <span className="flex items-center gap-1">
                {statusIcons[reachability]}
                <span className="capitalize">{reachability}</span>
              </span>
              <span className="text-white/20">|</span>
              <span className={`font-medium ${backupReadiness.willMeetSla ? 'text-green-400' : 'text-red-400'}`}>
                {backupReadiness.willMeetSla ? "Meets SLA" : "Misses SLA"}
              </span>
            </div>
          </div>
        </div>
        
        <div className="flex items-center gap-4">
          <ScoreGauge 
            score={backupReadiness.score} 
            size={60} 
            strokeWidth={4} 
          />
          <button className="p-1 text-slate-400 hover:text-white transition-colors">
            {expanded ? <ChevronUp className="w-5 h-5" /> : <ChevronDown className="w-5 h-5" />}
          </button>
        </div>
      </div>

      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="mt-6 pt-5 border-t border-white/10 overflow-hidden"
          >
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              {/* Network Stats */}
              <div className="bg-white/5 rounded-lg p-3 border border-white/5">
                <span className="text-xs text-slate-400 block mb-1">Latency</span>
                <span className="text-lg font-semibold text-white">
                  {isDown ? "N/A" : `${Math.round(latencyMs)}ms`}
                </span>
              </div>
              <div className="bg-white/5 rounded-lg p-3 border border-white/5">
                <span className="text-xs text-slate-400 block mb-1">Packet Loss</span>
                <span className="text-lg font-semibold text-white">
                  {isDown ? "N/A" : `${packetLossPct.toFixed(1)}%`}
                </span>
              </div>
              <div className="bg-white/5 rounded-lg p-3 border border-white/5">
                <span className="text-xs text-slate-400 block mb-1">DNS Resolution</span>
                <span className={`text-lg font-semibold ${dnsResolved ? 'text-green-400' : 'text-red-400'}`}>
                  {dnsResolved ? "Resolved" : "Failed"}
                </span>
              </div>
              <div className="bg-white/5 rounded-lg p-3 border border-white/5">
                <span className="text-xs text-slate-400 block mb-1">Est. Transfer Time</span>
                <span className={`text-lg font-semibold ${
                  backupReadiness.estimatedTransferHours >= 999 
                    ? "text-red-400" 
                    : backupReadiness.willMeetSla ? "text-green-400" : "text-orange-400"
                }`}>
                  {backupReadiness.estimatedTransferHours >= 999 
                    ? "Unbounded" 
                    : `${backupReadiness.estimatedTransferHours.toFixed(1)}h / ${backupReadiness.slaWindowHours}h`}
                </span>
              </div>
            </div>

            {/* Ports Array */}
            <div className="mt-4">
              <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">Required Protocols</h4>
              <div className="flex flex-wrap gap-2">
                {isDown ? (
                  <span className="text-sm text-slate-500 italic">Not scanned (host unreachable)</span>
                ) : ports.length === 0 ? (
                  <span className="text-sm text-slate-500 italic">No ports configured</span>
                ) : (
                  ports.map((p) => (
                    <div 
                      key={`${p.port}-${p.service}`} 
                      className={`flex items-center gap-2 px-3 py-1.5 rounded-md border text-sm ${
                        p.open 
                          ? "bg-green-500/10 border-green-500/20 text-green-300" 
                          : "bg-red-500/10 border-red-500/20 text-red-300"
                      }`}
                    >
                      <span className="font-mono">{p.port}</span>
                      <span className="font-semibold">{p.service}</span>
                      <span className="ml-1 opacity-75 text-xs">{p.open ? "OPEN" : "CLOSED"}</span>
                    </div>
                  ))
                )}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
