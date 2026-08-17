"use client";

import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ShieldAlert, ShieldCheck, CheckCircle, AlertTriangle, XCircle, ChevronDown, Shield, Activity } from "lucide-react";
import Link from "next/link";
import Sidebar from "@/components/Sidebar";
import { fetchWithAuth } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

function severityIcon(severity: string) {
  switch (severity) {
    case "CRITICAL": return <XCircle className="w-5 h-5 text-red-400" />;
    case "WARNING": return <AlertTriangle className="w-5 h-5 text-orange-400" />;
    default: return <ShieldCheck className="w-5 h-5 text-cyan-400" />;
  }
}

function severityBadge(severity: string) {
  const colors = severity === "CRITICAL" ? "bg-red-500/10 text-red-400 border-red-500/20" : "bg-orange-500/10 text-orange-400 border-orange-500/20";
  return <span className={`text-xs font-bold px-2 py-1 rounded border ${colors}`}>{severity}</span>;
}

function statusBadge(status: string) {
  if (status === "RESOLVED") return <span className="text-xs font-bold px-2 py-1 rounded bg-green-500/10 text-green-400 border border-green-500/20">RESOLVED</span>;
  if (status === "ACKNOWLEDGED") return <span className="text-xs font-bold px-2 py-1 rounded bg-blue-500/10 text-blue-400 border border-blue-500/20">ACKNOWLEDGED</span>;
  return <span className="text-xs font-bold px-2 py-1 rounded bg-white/10 text-white border border-white/10">OPEN</span>;
}

export default function IncidentsPage() {
  const { user } = useAuth();
  const [expanded, setExpanded] = useState<string | null>(null);
  const [filter, setFilter] = useState("all");
  const [incidents, setIncidents] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  const handleAcknowledge = async (id: string) => {
    if (id.startsWith("demo-inc-")) {
      setIncidents(prev => prev.map(inc => 
        inc.id === id ? { ...inc, status: "ACKNOWLEDGED" } : inc
      ));
      return;
    }
    try {
      const res = await fetchWithAuth(`/api/incidents/${id}/acknowledge`, {
        method: "POST"
      });
      if (res.ok) {
        setIncidents(prev => prev.map(inc => 
          inc.id === id ? { ...inc, status: "ACKNOWLEDGED" } : inc
        ));
      } else {
        alert("Failed to acknowledge incident. Make sure you are signed in.");
      }
    } catch (err) {
      console.error(err);
      alert("Error reaching the backend api.");
    }
  };

  const handleResolve = async (id: string) => {
    if (id.startsWith("demo-inc-")) {
      setIncidents(prev => prev.map(inc => 
        inc.id === id ? { ...inc, status: "RESOLVED" } : inc
      ));
      return;
    }
    try {
      const res = await fetchWithAuth(`/api/incidents/${id}/resolve`, {
        method: "POST"
      });
      if (res.ok) {
        setIncidents(prev => prev.map(inc => 
          inc.id === id ? { ...inc, status: "RESOLVED" } : inc
        ));
      } else {
        alert("Failed to resolve incident. Make sure you are signed in.");
      }
    } catch (err) {
      console.error(err);
      alert("Error reaching the backend api.");
    }
  };

  useEffect(() => {
    // Reset state immediately on user change
    setIncidents([]);
    setLoading(true);
    const fetchIncidents = async () => {
      try {
        const res = await fetchWithAuth(`/api/incidents`);
        let incidentsList = [];
        if (res.ok) {
          incidentsList = await res.json();
        }

        const diagRes = await fetchWithAuth(`/api/diagnostic-run`);
        if (diagRes.ok) {
          const diagData = await diagRes.json();
          if (diagData && diagData.is_demo) {
            const demoIncidents = (diagData.diagnostics || []).map((d: any, idx: number) => ({
              id: `demo-inc-${idx}`,
              title: d.title,
              likely_cause: d.likely_cause,
              severity: d.severity === "ERROR" ? "CRITICAL" : "WARNING",
              status: "OPEN",
              started_at: diagData.timestamp,
              evidence: d.evidence || [],
              recommended_actions: d.recommended_checks || [],
              confidence: d.confidence || "HIGH",
              device_id: "DEMO-PC"
            }));
            incidentsList = [...demoIncidents, ...incidentsList];
          }
        }

        setIncidents(incidentsList);
      } catch (err) {
        console.error("Failed to fetch incidents:", err);
      } finally {
        setLoading(false);
      }
    };
    fetchIncidents();
  }, [user]);

  const filtered = incidents.filter(inc => {
    if (filter === "all") return true;
    if (filter === "open") return inc.status === "OPEN";
    if (filter === "resolved") return inc.status === "RESOLVED";
    if (filter === "critical") return inc.severity === "CRITICAL";
    return true;
  });

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <motion.div
          animate={{ rotate: 360 }}
          transition={{ repeat: Infinity, duration: 1, ease: "linear" }}
        >
          <Activity className="w-12 h-12 text-cyan-500" />
        </motion.div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen">
      <Sidebar />

      <main className="flex-1 p-8 overflow-y-auto">
        <header className="flex justify-between items-center mb-10">
          <div>
            <h2 className="text-3xl font-bold text-white mb-2">Incidents</h2>
            <p className="text-slate-400">{filtered.length} incident{filtered.length !== 1 ? "s" : ""}</p>
          </div>
          <div className="flex gap-2">
            {["all", "open", "critical", "resolved"].map(f => (
              <button key={f} onClick={() => setFilter(f)}
                className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${filter === f ? "bg-cyan-500/20 text-cyan-400 border border-cyan-500/30" : "bg-white/5 text-slate-400 border border-white/10 hover:text-white"}`}
              >{f.charAt(0).toUpperCase() + f.slice(1)}</button>
            ))}
          </div>
        </header>

        {filtered.length === 0 ? (
          <div className="glass-card p-8 text-center">
            <ShieldCheck className="w-12 h-12 text-green-400 mx-auto mb-4" />
            <h3 className="text-lg font-semibold text-white mb-2">No Incidents</h3>
            <p className="text-slate-400">
              {incidents.length === 0
                ? "No incidents have been recorded. Run a diagnostic from the Overview page to start monitoring."
                : "No incidents match the current filter."}
            </p>
          </div>
        ) : (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="space-y-4"
          >
            {filtered.map((inc) => (
              <motion.div
                key={inc.id}
                layout
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                className={`glass-card overflow-hidden ${inc.status === "RESOLVED" ? "opacity-60" : ""}`}
              >
                <button
                  onClick={() => setExpanded(expanded === inc.id ? null : inc.id)}
                  className="w-full p-5 flex items-center justify-between text-left"
                >
                  <div className="flex items-center gap-4">
                    {severityIcon(inc.severity)}
                    <div>
                      <h3 className={`font-semibold ${inc.status === "RESOLVED" ? "line-through text-slate-400" : "text-white"}`}>{inc.title}</h3>
                      <p className="text-xs text-slate-500 font-mono mt-1">{inc.device_id || "—"} • {new Date(inc.started_at).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    {severityBadge(inc.severity)}
                    {statusBadge(inc.status)}
                    <motion.div animate={{ rotate: expanded === inc.id ? 180 : 0 }}>
                      <ChevronDown className="w-4 h-4 text-slate-400" />
                    </motion.div>
                  </div>
                </button>

                <AnimatePresence>
                  {expanded === inc.id && (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: "auto", opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      transition={{ duration: 0.3 }}
                      className="border-t border-white/10"
                    >
                      <div className="p-6 space-y-5">
                        <div>
                          <h4 className="text-sm font-semibold text-slate-300 mb-2">Likely Cause</h4>
                          <p className="text-slate-400">{inc.likely_cause}</p>
                          <span className="inline-block mt-2 text-xs font-bold px-2 py-0.5 rounded bg-white/5 text-slate-400">
                            Confidence: {inc.confidence}
                          </span>
                        </div>
                        <div>
                          <h4 className="text-sm font-semibold text-slate-300 mb-2">Evidence</h4>
                          <ul className="space-y-1.5">
                            {(inc.evidence || []).map((e: string, i: number) => (
                              <li key={i} className="flex items-start gap-2 text-sm text-slate-400">
                                <span className="text-cyan-500 mt-1">•</span>
                                {e}
                              </li>
                            ))}
                          </ul>
                        </div>
                        {(inc.recommended_actions || []).length > 0 && (
                          <div>
                            <h4 className="text-sm font-semibold text-slate-300 mb-2">Recommended Checks</h4>
                            <ol className="list-decimal list-inside space-y-1 text-sm text-slate-400">
                              {inc.recommended_actions.map((a: string, i: number) => <li key={i}>{a}</li>)}
                            </ol>
                          </div>
                        )}
                        {inc.status === "OPEN" && (
                          <div className="flex gap-3 pt-2">
                            <button 
                              onClick={() => handleAcknowledge(inc.id)}
                              className="px-4 py-2 rounded-lg bg-blue-500/20 text-blue-400 border border-blue-500/30 text-sm font-medium hover:bg-blue-500/30 transition-colors"
                            >
                              Acknowledge
                            </button>
                            <button 
                              onClick={() => handleResolve(inc.id)}
                              className="px-4 py-2 rounded-lg bg-green-500/20 text-green-400 border border-green-500/30 text-sm font-medium hover:bg-green-500/30 transition-colors"
                            >
                              Resolve
                            </button>
                          </div>
                        )}
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </motion.div>
            ))}
          </motion.div>
        )}
      </main>
    </div>
  );
}
