"use client";

import { motion } from "framer-motion";
import { AlertCircle, Info, ShieldAlert, CheckCircle2 } from "lucide-react";

export interface DiagnosticItem {
  id: string;
  severity: "info" | "warning" | "critical" | "ERROR" | "WARNING" | "INFO";
  category?: string; // from backup
  title?: string; // from legacy
  message?: string; // from backup
  likely_cause?: string; // from legacy
  recommendation?: string; // from backup
  recommended_checks?: string[]; // from legacy
  affectedTargetId?: string; // from backup
  affectedTargetName?: string; // from backup, resolved client-side
  timestamp?: string; // from backup
  source: "backup" | "legacy";
}

interface DiagnosticsFeedProps {
  diagnostics: DiagnosticItem[];
  filterTargetId?: string; // If provided, only show legacy + this target's backup diagnostics
}

export default function DiagnosticsFeed({ diagnostics, filterTargetId }: DiagnosticsFeedProps) {
  // Filter by target if requested
  const filtered = diagnostics.filter(d => {
    if (!filterTargetId) return true;
    if (d.source === "legacy") return true; // Keep general diagnostics
    return d.affectedTargetId === filterTargetId;
  });

  const severityStyles = {
    info: "border-blue-500/30 bg-blue-500/5 text-blue-400",
    warning: "border-orange-500/30 bg-orange-500/5 text-orange-400",
    critical: "border-red-500/30 bg-red-500/5 text-red-400",
    INFO: "border-blue-500/30 bg-blue-500/5 text-blue-400",
    WARNING: "border-orange-500/30 bg-orange-500/5 text-orange-400",
    ERROR: "border-red-500/30 bg-red-500/5 text-red-400",
  };

  const icons = {
    info: <Info className="w-5 h-5 text-blue-400" />,
    warning: <AlertCircle className="w-5 h-5 text-orange-400" />,
    critical: <ShieldAlert className="w-5 h-5 text-red-400" />,
    INFO: <Info className="w-5 h-5 text-blue-400" />,
    WARNING: <AlertCircle className="w-5 h-5 text-orange-400" />,
    ERROR: <ShieldAlert className="w-5 h-5 text-red-400" />,
  };

  if (filtered.length === 0) {
    return (
      <div className="glass-panel rounded-xl p-8 text-center flex flex-col items-center">
        <div className="w-12 h-12 rounded-full bg-green-500/10 flex items-center justify-center mb-3">
          <CheckCircle2 className="w-6 h-6 text-green-400" />
        </div>
        <h4 className="text-slate-200 font-medium text-lg">No active issues</h4>
        <p className="text-slate-400 text-sm mt-1">All monitored systems are operating normally.</p>
      </div>
    );
  }

  const categoryLabel = (c?: string) =>
    c ? c.replace("-", " ").replace(/\\b\\w/g, (m) => m.toUpperCase()) : "Backup";

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4 items-stretch">
      {filtered.map((diag, i) => {
        const isBackup = diag.source === "backup";
        const label = isBackup ? categoryLabel(diag.category) : "Network";
        const title = isBackup ? (diag.affectedTargetName || diag.affectedTargetId || "Backup target") : (diag.title || "Finding");
        const mainMsg = isBackup ? diag.message : diag.likely_cause;

        return (
          <motion.div
            key={diag.id || `diag-${i}`}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: Math.min(i, 8) * 0.04 }}
            className={`glass-panel p-4 rounded-xl border-l-4 flex flex-col ${severityStyles[diag.severity]}`}
          >
            {/* Header: severity icon, category chip, source/target name */}
            <div className="flex items-center gap-2 mb-2 min-w-0">
              <div className="shrink-0">{icons[diag.severity]}</div>
              <span className="shrink-0 text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full bg-white/5 border border-white/10 text-slate-300">
                {label}
              </span>
              <h4 className="font-semibold text-slate-200 text-sm truncate min-w-0" title={title}>{title}</h4>
            </div>

            <p className="text-sm text-slate-300 leading-relaxed">{mainMsg}</p>

            {(diag.recommendation || (diag.recommended_checks && diag.recommended_checks.length > 0)) && (
              <div className="mt-3 pt-3 border-t border-white/5">
                <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">Recommendation</span>
                {isBackup ? (
                  <p className="text-sm text-cyan-200/90 mt-1">{diag.recommendation}</p>
                ) : (
                  <ul className="mt-1 space-y-1">
                    {diag.recommended_checks?.map((check, idx) => (
                      <li key={idx} className="text-sm text-cyan-200/90 flex gap-2">
                        <span className="opacity-50">•</span> {check}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}

            {diag.timestamp && (
              <div className="mt-auto pt-3 text-[11px] text-slate-500 font-mono">
                {new Date(diag.timestamp).toLocaleString()}
              </div>
            )}
          </motion.div>
        );
      })}
    </div>
  );
}
