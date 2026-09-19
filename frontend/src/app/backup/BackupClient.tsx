"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Activity, ShieldAlert, Server, ShieldCheck, Settings2, RefreshCw } from "lucide-react";
import Sidebar from "@/components/Sidebar";
import { fetchWithAuth, subscribeBackendStatus } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import BackupTargetCard from "@/components/BackupTargetCard";
import DiagnosticsFeed, { DiagnosticItem } from "@/components/DiagnosticsFeed";
import ScoreGauge from "@/components/ScoreGauge";

function BackupSkeletons() {
  return (
    <div className="animate-pulse">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
        <div className="glass-card p-6 min-h-[160px] bg-white/5 border border-white/10 rounded-xl" />
        <div className="glass-card p-6 min-h-[160px] bg-white/5 border border-white/10 rounded-xl" />
        <div className="glass-card p-6 min-h-[160px] bg-white/5 border border-white/10 rounded-xl" />
      </div>
      <div className="space-y-4">
        <div className="glass-card h-24 bg-white/5 border border-white/10 rounded-xl" />
        <div className="glass-card h-24 bg-white/5 border border-white/10 rounded-xl" />
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        <div className="glass-card h-48 bg-white/5 border border-white/10 rounded-xl" />
        <div className="glass-card h-48 bg-white/5 border border-white/10 rounded-xl" />
        <div className="glass-card h-48 bg-white/5 border border-white/10 rounded-xl" />
      </div>
    </div>
  );
}

export default function BackupClient() {
  const { user } = useAuth();
  const [data, setData] = useState<any>(null);
  const [legacyDiagnostics, setLegacyDiagnostics] = useState<DiagnosticItem[]>([]);
  const [loading, setLoading] = useState(true);
  // Deep-linkable: /backup?demo=port-blocked preselects a scenario.
  const [demoScenario, setDemoScenario] = useState(() => {
    if (typeof window === "undefined") return "";
    const d = new URLSearchParams(window.location.search).get("demo") || "";
    return ["dns-flap", "port-blocked", "throughput-drop"].includes(d) ? d : "";
  });
  const [datasetSize, setDatasetSize] = useState(500);
  const [slaHours, setSlaHours] = useState(4);
  const [refreshing, setRefreshing] = useState(false);

  const fetchData = async () => {
    setRefreshing(true);
    try {
      // Fetch backup readiness data
      let url = `/api/backup/readiness?dataset_size_gb=${datasetSize}&sla_hours=${slaHours}`;
      if (demoScenario) {
        url += `&demo=${demoScenario}`;
      }
      
      const backupRes = await fetchWithAuth(url);
      let backupData = null;
      if (backupRes.ok) {
        backupData = await backupRes.json();
      }

      // Fetch legacy diagnostics to merge
      const legacyRes = await fetchWithAuth(`/api/diagnostic-run`);
      let lDiags: DiagnosticItem[] = [];
      if (legacyRes.ok) {
        const legacyData = await legacyRes.json();
        if (legacyData?.diagnostics) {
          lDiags = legacyData.diagnostics
            .filter((d: any) => d.severity !== "info" && d.severity !== "INFO")
            .map((d: any) => ({
              ...d,
              source: "legacy"
            }));
        }
      }

      setData(backupData);
      setLegacyDiagnostics(lDiags);
    } catch (err) {
      console.error("Failed to fetch backup data:", err);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    setLoading(true);
    fetchData();
  }, [user, demoScenario, datasetSize, slaHours]);

  useEffect(() => {
    let wasDisconnected = false;
    const unsub = subscribeBackendStatus((status) => {
      if (status === "disconnected") {
        wasDisconnected = true;
      } else if (status === "connected" && wasDisconnected) {
        wasDisconnected = false;
        window.location.reload();
      }
    });
    return unsub;
  }, []);

  const containerVariants = {
    hidden: { opacity: 0 },
    visible: { opacity: 1, transition: { staggerChildren: 0.1 } }
  };

  const itemVariants = {
    hidden: { y: 20, opacity: 0 },
    visible: { y: 0, opacity: 1, transition: { type: "spring" as const, stiffness: 100 } }
  };

  // Merge diagnostics
  const backupDiagnostics = (data?.diagnostics || []).map((d: any) => ({ 
    ...d, 
    source: "backup",
    affectedTargetName: data?.targets?.find((t: any) => t.id === d.affectedTargetId)?.name
  }));
  const allDiagnostics = [...backupDiagnostics, ...legacyDiagnostics];

  const atRiskCount = data?.targets?.filter((t: any) => t.backupReadiness.verdict === "at-risk").length || 0;
  const notReadyCount = data?.targets?.filter((t: any) => t.backupReadiness.verdict === "not-ready").length || 0;
  const readyCount = data?.targets?.filter((t: any) => t.backupReadiness.verdict === "ready").length || 0;

  return (
    <div className="flex flex-col md:flex-row min-h-screen">
      <Sidebar />

      <main className="flex-1 p-8 overflow-y-auto relative z-10">
        <header className="flex flex-col xl:flex-row justify-between items-start xl:items-center gap-6 mb-10 glass-panel p-6 rounded-2xl">
          <div className="min-w-0">
            <h2 className="text-3xl font-bold text-white mb-2 tracking-tight truncate">Backup Readiness</h2>
            <p className="text-slate-400 truncate">Evaluate backup SLA, target health, and active protocols.</p>
          </div>
          <div className="flex items-center gap-4 flex-wrap justify-end">
            <div className="flex items-center gap-2 bg-black/20 px-3 py-1.5 rounded-lg border border-white/5">
              <span className="text-xs text-slate-400 uppercase tracking-wider font-semibold">Dataset (GB)</span>
              <input
                type="number"
                value={datasetSize}
                onChange={(e) => setDatasetSize(Number(e.target.value) || 500)}
                className="w-16 bg-transparent text-slate-200 text-sm focus:outline-none text-right"
              />
            </div>
            
            <div className="flex items-center gap-2 bg-black/20 px-3 py-1.5 rounded-lg border border-white/5">
              <span className="text-xs text-slate-400 uppercase tracking-wider font-semibold">SLA (Hrs)</span>
              <input
                type="number"
                value={slaHours}
                onChange={(e) => setSlaHours(Number(e.target.value) || 4)}
                className="w-12 bg-transparent text-slate-200 text-sm focus:outline-none text-right"
              />
            </div>

            <div className="flex items-center gap-2 bg-black/20 px-3 py-1.5 rounded-lg border border-white/5 relative">
              <Settings2 className="w-4 h-4 text-slate-400" />
              <select 
                value={demoScenario}
                onChange={(e) => setDemoScenario(e.target.value)}
                className="bg-transparent text-slate-200 text-sm focus:outline-none appearance-none pr-2 cursor-pointer"
              >
                <option value="">Live Data</option>
                <option value="dns-flap">Demo: DNS Flap</option>
                <option value="port-blocked">Demo: Port Blocked</option>
                <option value="throughput-drop">Demo: Throughput Drop</option>
              </select>
              {data?.simulatedScenarios && data.simulatedScenarios.length > 0 && (
                <span className={`text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded-md font-bold ${
                  data.simulatedScenarios[0].status === "pass" 
                    ? "bg-green-500/20 text-green-400" 
                    : "bg-red-500/20 text-red-400"
                }`}>
                  {data.simulatedScenarios[0].status}
                </span>
              )}
            </div>

            <button 
              onClick={fetchData}
              disabled={refreshing}
              className="p-2.5 rounded-lg bg-white/5 hover:bg-white/10 text-cyan-400 border border-white/10 transition-colors disabled:opacity-50"
              title="Refresh Data"
            >
              <RefreshCw className={`w-5 h-5 ${refreshing ? "animate-spin" : ""}`} />
            </button>
          </div>
        </header>

        {loading ? (
          <BackupSkeletons />
        ) : (
          <>
            <motion.div variants={containerVariants} initial="hidden" animate="visible" className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8 items-stretch">
          <motion.div variants={itemVariants} className="glass-card p-6 flex flex-col h-full border-t-4 border-t-cyan-500">
            <div className="flex justify-between items-start mb-4 gap-4">
              <p className="text-slate-400 text-sm font-medium truncate">Overall Readiness</p>
              <div className="p-2.5 bg-white/5 rounded-xl shrink-0"><ShieldCheck className="w-5 h-5 text-slate-300" /></div>
            </div>
            <div className="flex-1 flex flex-col items-center justify-center">
              <ScoreGauge 
                score={data?.backupReadinessScore || 0} 
                size={90} 
                strokeWidth={6} 
                label={data?.targets?.length === 0 ? "No Targets" : undefined}
              />
            </div>
            <div className="mt-4 pt-4 border-t border-white/10 text-sm text-center">
              <span className="text-glow text-cyan-400">Score</span>
            </div>
          </motion.div>

          <motion.div variants={itemVariants} className="glass-card p-6 flex flex-col h-full">
            <div className="flex justify-between items-start mb-4 gap-4">
              <p className="text-slate-400 text-sm font-medium truncate">Target Health</p>
              <div className="p-2.5 bg-white/5 rounded-xl shrink-0"><Server className="w-5 h-5 text-slate-300" /></div>
            </div>
            <div className="flex-1 flex flex-col justify-center">
              <div className="flex gap-6">
                <div>
                  <span className="block text-3xl font-bold text-green-400">{readyCount}</span>
                  <span className="text-xs text-slate-400 mt-1 block">Ready</span>
                </div>
                <div>
                  <span className="block text-3xl font-bold text-orange-400">{atRiskCount}</span>
                  <span className="text-xs text-slate-400 mt-1 block">At Risk</span>
                </div>
                <div>
                  <span className="block text-3xl font-bold text-red-400">{notReadyCount}</span>
                  <span className="text-xs text-slate-400 mt-1 block">Not Ready</span>
                </div>
              </div>
            </div>
            <div className="mt-4 pt-4 border-t border-white/10 text-sm text-slate-400">
              Current backup targets
            </div>
          </motion.div>

          <motion.div variants={itemVariants} className="glass-card p-6 flex flex-col h-full">
             <div className="flex justify-between items-start mb-4 gap-4">
              <p className="text-slate-400 text-sm font-medium truncate">Health Score</p>
              <div className="p-2.5 bg-white/5 rounded-xl shrink-0"><Activity className="w-5 h-5 text-slate-300" /></div>
            </div>
             <div className="flex-1 flex flex-col items-center justify-center">
               <ScoreGauge 
                score={data?.healthScore || 0} 
                size={90} 
                strokeWidth={6} 
              />
             </div>
             <div className="mt-4 pt-4 border-t border-white/10 text-sm text-center text-slate-400">
               Global Network Health
             </div>
          </motion.div>
        </motion.div>

        {/* Targets take the full width; diagnostics sit below them in an
            equal-height grid so cards line up instead of stacking in a
            narrow side column next to empty space. */}
        <section className="space-y-4">
          <div>
            <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
              <Server className="w-5 h-5 text-cyan-400" /> Backup Targets
              <span className="ml-auto text-xs font-normal text-slate-500">{data?.targets?.length ?? 0} target{(data?.targets?.length ?? 0) === 1 ? "" : "s"}</span>
            </h3>
            {data?.targets?.length === 0 ? (
              <div className="glass-panel p-8 text-center rounded-2xl">
                <ShieldCheck className="w-12 h-12 text-slate-500 mx-auto mb-3" />
                <h4 className="text-slate-300 font-medium text-lg">No backup targets configured</h4>
                <p className="text-slate-400 text-sm mt-1">Tag devices as backup targets to monitor them here.</p>
              </div>
            ) : (
              <div className="space-y-4">
                {data?.targets?.map((target: any) => (
                  <BackupTargetCard key={target.id} target={target} />
                ))}
              </div>
            )}
          </div>
        </section>

        <section className="mt-10">
          <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
            <ShieldAlert className="w-5 h-5 text-cyan-400" /> Unified Diagnostics
            <span className="ml-auto text-xs font-normal text-slate-500">{allDiagnostics.length} finding{allDiagnostics.length === 1 ? "" : "s"}</span>
          </h3>
          <DiagnosticsFeed diagnostics={allDiagnostics} />
        </section>
        </>
        )}
      </main>
    </div>
  );
}
