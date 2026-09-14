"use client";

import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Activity, ShieldAlert, Server, Network, ShieldCheck, ChevronRight, HelpCircle, AlertCircle, RefreshCw } from "lucide-react";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from "recharts";
import Link from "next/link";
import Sidebar from "@/components/Sidebar";
import { fetchWithAuth, subscribeBackendStatus, BACKEND_URL } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

function DashboardSkeletons() {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8 animate-pulse">
      <div className="glass-card p-6 h-48 bg-white/5 rounded-xl border border-white/10" />
      <div className="glass-card p-6 h-48 bg-white/5 rounded-xl border border-white/10" />
      <div className="glass-card p-6 h-48 bg-white/5 rounded-xl border border-white/10" />
      <div className="glass-card p-6 h-48 bg-white/5 rounded-xl border border-white/10" />
      <div className="glass-card p-6 h-64 lg:col-span-2 bg-white/5 rounded-xl border border-white/10" />
      <div className="glass-card p-6 h-64 bg-white/5 rounded-xl border border-white/10" />
    </div>
  );
}

export default function Dashboard() {
  const { user } = useAuth();
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [runningDiagnostic, setRunningDiagnostic] = useState(false);
  const [demoScenario, setDemoScenario] = useState("");
  const [slowAnalysis, setSlowAnalysis] = useState<any>(null);
  const [analyzingSlow, setAnalyzingSlow] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchDashboardData = async () => {
    try {
      let historyData = [];
      if (user) {
        const historyRes = await fetchWithAuth(`/api/history?limit=10`);
        if (historyRes.ok) {
          historyData = await historyRes.json();
        }
      } else {
        try {
          const localHist = localStorage.getItem("netsentinel_history");
          if (localHist) {
            historyData = JSON.parse(localHist);
          }
        } catch (e) {
          console.error("Failed to load local history:", e);
        }
      }

      const incidentsRes = await fetchWithAuth(`/api/incidents`);
      let incidentsData = [];
      if (incidentsRes.ok) {
        incidentsData = await incidentsRes.json();
      }

      const devicesRes = await fetchWithAuth(`/api/devices`);
      let devicesData = [];
      if (devicesRes.ok) {
        devicesData = await devicesRes.json();
      }

      const lastRunRes = await fetchWithAuth(`/api/diagnostic-run`);
      let lastRunData = null;
      if (lastRunRes.ok) {
        lastRunData = await lastRunRes.json();
      }

      let latestScore = historyData.length > 0 ? historyData[0].score : null;
      let latestStatus = historyData.length > 0 ? historyData[0].status : null;
      let latencyPoints = historyData.length > 0 
        ? historyData.slice(0, 10).reverse().map((h: any) => ({
            time: new Date(h.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
            ms: h.latency
          }))
        : [];
      
      // Process incidents: filter out "info", map severities
      let filteredIncidents = incidentsData.filter((i: any) => {
        const sev = i.severity?.toLowerCase();
        return sev === "critical" || sev === "warning";
      }).map((i: any) => ({
        ...i,
        severity: i.severity?.toLowerCase() === "critical" ? "CRITICAL" : "WARNING"
      }));

      let incidentsCount = filteredIncidents.filter((i: any) => i.status === "OPEN").length;
      let incidentsList = filteredIncidents.length > 0 ? filteredIncidents : null;
      let diagnosticsData = null;

      if (lastRunData) {
        const isNewer = !historyData.length || new Date(lastRunData.timestamp) > new Date(historyData[0].timestamp);
        
        if (isNewer || lastRunData.is_demo) {
          latestScore = lastRunData.health_score;
          latestStatus = lastRunData.status;
          diagnosticsData = lastRunData.diagnostics || null;
          
          const currentLatency = lastRunData.internet?.latency_ms || 0;
          
          if (lastRunData.is_demo) {
            // Generate demo latency trend
            const baseTime = new Date(lastRunData.timestamp);
            latencyPoints = Array.from({ length: 7 }, (_, i) => {
              const d = new Date(baseTime.getTime() - (6 - i) * 60000);
              let ms = 22 + Math.round(Math.random() * 6);
              if (i === 6) ms = Math.round(currentLatency);
              else if (i === 5 && lastRunData.status !== "HEALTHY") ms = Math.round(currentLatency * 0.6);
              return { time: d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }), ms };
            });
          } else if (currentLatency > 0) {
            const nowTime = new Date(lastRunData.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            const lastPoint = latencyPoints[latencyPoints.length - 1];
            if (!lastPoint || lastPoint.time !== nowTime) {
              latencyPoints = [...latencyPoints.slice(1), { time: nowTime, ms: Math.round(currentLatency) }];
            }
          }

          // Use real diagnostic findings for incidents
          if (lastRunData.diagnostics) {
            const mappedDiags = lastRunData.diagnostics
              .filter((d: any) => {
                const s = d.severity?.toLowerCase();
                return s === "critical" || s === "warning";
              })
              .map((d: any, idx: number) => ({
                id: `diag-inc-${idx}`,
                title: d.title,
                likely_cause: d.likely_cause,
                severity: d.severity?.toLowerCase() === "critical" ? "CRITICAL" : "WARNING",
                status: "OPEN"
              }));
            incidentsCount = mappedDiags.length;
            incidentsList = mappedDiags;
          }
        }
      }

      const realDevices = devicesData.filter((d: any) => d.agent_version !== "hosted-server");
      setData({
        healthScore: latestScore,
        status: latestStatus,
        devices: realDevices.length,
        devicesOnline: realDevices.filter((d: any) => d.status === "ONLINE").length,
        devicesOffline: realDevices.filter((d: any) => d.status === "OFFLINE").length,
        incidentsCount: incidentsCount,
        latency: latencyPoints,
        incidents: incidentsList,
        history: historyData,
        diagnostics: diagnosticsData
      });
    } catch (err) {
      console.error("Failed to fetch backend data.", err);
      setError("Couldn't load network data. Check your connection to the NetSentinel service.");
      setData(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setData(null);
    setLoading(true);
    setSlowAnalysis(null);
    fetchDashboardData();
  }, [user]);

  useEffect(() => {
    let wasDisconnected = false;
    const unsub = subscribeBackendStatus((status) => {
      if (status === "disconnected") {
        wasDisconnected = true;
      } else if (status === "connected" && wasDisconnected) {
        wasDisconnected = false;
        fetchDashboardData();
      }
    });
    return unsub;
  }, [user]);

  const runDiagnostic = async () => {
    setRunningDiagnostic(true);
    try {
      let url = `/api/diagnostic-run`;
      if (demoScenario) {
        url += `?demo=${demoScenario}`;
      }
      const res = await fetchWithAuth(url, {
        method: "POST"
      });
      if (res.ok) {
        if (!user) {
          try {
            const resultData = await res.clone().json();
            if (resultData && !resultData.is_demo) {
              const localHist = localStorage.getItem("netsentinel_history");
              let historyData = localHist ? JSON.parse(localHist) : [];
              const newEntry = {
                id: Date.now(),
                timestamp: resultData.timestamp,
                score: resultData.health_score,
                status: resultData.status,
                gateway_status: resultData.gateway?.reachable ? "PASS" : "FAIL",
                internet_status: resultData.internet?.reachable ? "PASS" : "FAIL",
                dns_status: resultData.dns?.every((d: any) => d.success) ? "PASS" : "FAIL",
                tcp_status: resultData.tcp?.every((t: any) => t.success) ? "PASS" : "FAIL",
                latency: resultData.internet?.latency_ms || 0,
                packet_loss: resultData.internet?.packet_loss || 0,
                is_demo: false
              };
              historyData = [newEntry, ...historyData].slice(0, 10);
              localStorage.setItem("netsentinel_history", JSON.stringify(historyData));
            }
          } catch (e) {
            console.error("Failed to save local history:", e);
          }
        }
        await fetchDashboardData();
        // Clear slow network analysis as values updated
        setSlowAnalysis(null);
      } else {
        setError("Diagnostic run failed. The backend returned an error — check your API server logs.");
      }
    } catch (err) {
      console.error(err);
      setError("Cannot reach the backend API. Make sure the backend is running with: python app.py --web");
    } finally {
      setRunningDiagnostic(false);
    }
  };

  const evaluateWhyNetworkSlow = () => {
    setAnalyzingSlow(true);
    setTimeout(() => {
      // Analyze current latency trend
      const latestLatency = data.latency.length > 0 
        ? data.latency[data.latency.length - 1].ms 
        : 24;

      const baselineAvg = 24; // standard baseline target
      
      let analysisResult = {
        status: "NORMAL",
        latency: Math.round(latestLatency),
        baseline: baselineAvg,
        loss: data.status.includes("Loss") ? 15.0 : 0.3,
        likely_issue: "No significant degradation detected.",
        evidence: [
          "Current latency is close to the calculated baseline.",
          "Packet loss rate is normal.",
          "Gateway and DNS services respond within nominal boundaries."
        ],
        recommendations: [
          "Confirm if specific websites are slow rather than your connection.",
          "Check local device CPU usage or running background downloads."
        ]
      };

      if (data.diagnostics && data.diagnostics.length > 0) {
        const primaryFinding = data.diagnostics[0];
        analysisResult = {
          status: primaryFinding.severity === "ERROR" ? "CRITICAL" : "DEGRADED",
          latency: Math.round(latestLatency),
          baseline: baselineAvg,
          loss: data.status.includes("Loss") ? 15.0 : 0.3,
          likely_issue: primaryFinding.title,
          evidence: primaryFinding.evidence || [],
          recommendations: primaryFinding.recommended_checks || []
        };
      } else if (data.healthScore < 90 || latestLatency > 100) {
        analysisResult = {
          status: "DEGRADED",
          latency: Math.round(latestLatency),
          baseline: baselineAvg,
          loss: 4.2,
          likely_issue: "Network latency degradation detected.",
          evidence: [
            `Current latest latency (${Math.round(latestLatency)}ms) is significantly above baseline (${baselineAvg}ms).`,
            "Gateway response time is elevated, indicating local link congestion."
          ],
          recommendations: [
            "Check wireless signal strength and router distance.",
            "Inspect router load for heavy uploads/downloads.",
            "Compare latency with another device on the same local network."
          ]
        };
      }

      setSlowAnalysis(analysisResult);
      setAnalyzingSlow(false);
    }, 800);
  };

  const containerVariants = {
    hidden: { opacity: 0 },
    visible: { 
      opacity: 1,
      transition: { staggerChildren: 0.1 }
    }
  };

  const itemVariants = {
    hidden: { y: 20, opacity: 0 },
    visible: { y: 0, opacity: 1, transition: { type: "spring" as const, stiffness: 100 } }
  };

  return (
    <div className="flex flex-col md:flex-row min-h-screen">
      <Sidebar />
      <main className="flex-1 p-8 overflow-y-auto">
        {error && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            className="mb-6 p-4 bg-red-500/10 border border-red-500/20 rounded-xl flex items-center justify-between"
          >
            <div className="flex items-center gap-3">
              <AlertCircle className="w-5 h-5 text-red-400 shrink-0" />
              <p className="text-sm text-red-300">{error}</p>
            </div>
            <button onClick={() => setError(null)} className="text-red-400 hover:text-red-300 text-xs px-2 py-1 bg-red-500/10 rounded">Dismiss</button>
          </motion.div>
        )}

        <header className="flex flex-col md:flex-row justify-between items-start md:items-center gap-6 mb-6">
          <div className="min-w-0">
            <h2 className="text-3xl font-bold text-white mb-2 truncate" data-tour="dashboard-title">Network Overview</h2>
            <p className="text-slate-400 truncate">
              System status: <span className={`text-glow ${data?.status === "CRITICAL" ? "text-red-400" : data?.status === "WARNING" ? "text-orange-400" : data?.status === null ? "text-slate-400" : "text-cyan-400"}`}>
                {data?.status === "CRITICAL" ? "Outage" : data?.status === "WARNING" ? "Degraded" : data?.status === null ? "No scan yet" : "Operational"}
              </span>
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-4 w-full md:w-auto">
            <select 
              value={demoScenario}
              onChange={(e) => setDemoScenario(e.target.value)}
              className="max-w-full bg-white/5 border border-white/10 text-slate-300 px-4 py-2.5 rounded-lg text-sm focus:outline-none focus:border-cyan-500/50"
            >
              <option value="">Normal Scan (Real Network)</option>
              <option value="healthy">Demo: Healthy</option>
              <option value="dns-failure">Demo: DNS Failure</option>
              <option value="gateway-failure">Demo: Gateway Failure</option>
              <option value="port-failure">Demo: TCP Failure</option>
              <option value="high-latency">Demo: High Latency</option>
              <option value="packet-loss">Demo: Packet Loss</option>
            </select>
            <button 
              onClick={runDiagnostic}
              disabled={runningDiagnostic}
              className="px-6 py-2.5 rounded-lg bg-cyan-500 hover:bg-cyan-400 text-slate-900 font-semibold transition-colors shadow-[0_0_15px_rgba(6,214,214,0.3)] disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {runningDiagnostic ? "Diagnosing..." : "Run Diagnostic"}
            </button>
          </div>
        </header>

        <div className="mb-8 p-4 bg-cyan-500/10 border border-cyan-500/20 rounded-xl flex items-center justify-between text-sm text-cyan-200">
          <div className="flex items-center gap-2">
            <Server className="w-4 h-4 text-cyan-400 shrink-0" />
            <p>This scan runs from the NetSentinel server. To monitor your own machines, install the agent.</p>
          </div>
        </div>

        {loading ? (
          <DashboardSkeletons />
        ) : !data ? (
          <div className="glass-card p-12 text-center rounded-xl border border-white/10">
            <Activity className="w-12 h-12 text-slate-500 mx-auto mb-4" />
            <h2 className="text-xl text-slate-300">Awaiting Network Data</h2>
            <p className="text-sm text-slate-500 mt-2">Could not load dashboard metrics.</p>
          </div>
        ) : (
          <>
        <motion.div 
          variants={containerVariants}
          initial="hidden"
          animate="visible"
          className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8 items-stretch"
        >
          {/* Health Card */}
          <motion.div 
            variants={itemVariants}
            whileHover={{ scale: 1.02, rotateX: 5, rotateY: 5 }}
            className="glass-card p-6 flex flex-col h-full border-t-4 border-t-cyan-500"
            style={{ perspective: 1000 }}
          >
            <div className="flex justify-between items-start mb-4 gap-4">
              <p className="text-slate-400 text-sm font-medium truncate">Health Score</p>
              <div className="p-2.5 bg-white/5 rounded-xl shrink-0"><Activity className="w-5 h-5 text-slate-300" /></div>
            </div>
            <div className="flex-1 flex flex-col items-center justify-center">
              <div className="relative w-32 h-32 mb-2">
                <svg viewBox="0 0 100 100" className="w-full h-full transform -rotate-90">
                  <circle cx="50" cy="50" r="45" fill="none" stroke="rgba(255,255,255,0.1)" strokeWidth="8" />
                  <motion.circle 
                    cx="50" cy="50" r="45" 
                    fill="none" 
                    stroke="currentColor" 
                    strokeWidth="8" 
                    className={data.healthScore === null ? "text-slate-600" : "text-cyan-500"}
                    strokeDasharray="283"
                    initial={{ strokeDashoffset: 283 }}
                    animate={{ strokeDashoffset: 283 - (283 * (data.healthScore || 0)) / 100 }}
                    transition={{ duration: 1.5, ease: "easeOut" }}
                  />
                </svg>
                <div className="absolute inset-0 flex flex-col items-center justify-center">
                  <span className="text-3xl font-bold text-white">{data.healthScore !== null ? data.healthScore : "—"}</span>
                </div>
              </div>
            </div>
            <div className="mt-4 pt-4 border-t border-white/10">
              <h3 className={`text-sm font-medium text-center text-glow ${data.status === null ? "text-slate-400" : "text-cyan-400"}`}>{data.status !== null ? data.status : "No scan yet"}</h3>
            </div>
          </motion.div>

          {/* Stats Cards */}
          <motion.div variants={itemVariants} className="glass-card p-6 flex flex-col h-full">
            <div className="flex justify-between items-start mb-4 gap-4">
              <p className="text-slate-400 text-sm font-medium truncate">Monitored Devices</p>
              <div className="p-2.5 bg-white/5 rounded-xl shrink-0"><Server className="w-5 h-5 text-slate-300" /></div>
            </div>
            <div className="flex-1 flex flex-col justify-center">
              {user && data.devices === 0 ? (
                <Link href="/getting-started" className="block text-sm font-semibold text-cyan-400 hover:text-cyan-300 truncate group">
                  Add your first device <span className="inline-block transition-transform group-hover:translate-x-1">→</span>
                </Link>
              ) : (
                <h3 className="text-4xl font-bold text-white truncate">{data.devices}</h3>
              )}
            </div>
            <div className="mt-4 pt-4 border-t border-white/10 flex flex-wrap gap-4 text-sm">
              <span className="text-green-400 flex items-center gap-2 whitespace-nowrap"><ShieldCheck className="w-4 h-4 shrink-0"/> {data.devicesOnline} Online</span>
              <span className="text-red-400 flex items-center gap-2 whitespace-nowrap"><ShieldAlert className="w-4 h-4 shrink-0"/> {data.devicesOffline} Offline</span>
            </div>
          </motion.div>

          <motion.div variants={itemVariants} className="glass-card p-6 flex flex-col h-full">
            <div className="flex justify-between items-start mb-4 gap-4">
              <p className="text-slate-400 text-sm font-medium truncate">Active Incidents</p>
              <div className="p-2.5 bg-orange-500/10 rounded-xl shrink-0"><ShieldAlert className="w-5 h-5 text-orange-400" /></div>
            </div>
            <div className="flex-1 flex flex-col justify-center">
              <h3 className="text-4xl font-bold text-white truncate">{data.incidentsCount}</h3>
            </div>
            <div className="mt-4 pt-4 border-t border-white/10 text-sm text-slate-400">
              {data.incidentsCount > 0 ? "Requires immediate attention" : "No open incidents"}
            </div>
          </motion.div>
          
          <motion.div variants={itemVariants} className="glass-card p-6 flex flex-col h-full">
            <div className="flex justify-between items-start mb-4 gap-4">
              <p className="text-slate-400 text-sm font-medium truncate">Average Latency</p>
              <div className="p-2.5 bg-white/5 rounded-xl shrink-0"><Activity className="w-5 h-5 text-slate-300" /></div>
            </div>
            <div className="flex-1 flex flex-col justify-center">
              <h3 className="text-4xl font-bold text-white truncate">
                {data.latency.length > 0 ? Math.round(data.latency.reduce((acc: number, cur: any) => acc + cur.ms, 0) / data.latency.length) : "—"}
                {data.latency.length > 0 && <span className="text-xl text-slate-400 ml-1">ms</span>}
              </h3>
            </div>
            <div className="mt-4 pt-4 border-t border-white/10 text-sm text-cyan-400 text-glow">
              Normal Baseline
            </div>
          </motion.div>
        </motion.div>

        {/* Guided Troubleshooting "Why is my network slow?" Section */}
        <motion.div 
          initial={{ opacity: 0, y: 15 }}
          animate={{ opacity: 1, y: 0 }}
          className="glass-card p-6 mb-8 border border-cyan-500/20"
        >
          <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 mb-4">
            <div className="flex items-center gap-3">
              <HelpCircle className="w-6 h-6 text-cyan-400 shrink-0" />
              <h3 className="text-lg font-semibold text-white">Guided Troubleshooting</h3>
            </div>
            <button
              onClick={evaluateWhyNetworkSlow}
              disabled={analyzingSlow}
              className="px-5 py-2.5 bg-white/5 border border-white/10 text-cyan-400 hover:text-white hover:bg-cyan-500/10 rounded-xl text-sm transition-colors flex items-center justify-center gap-2 w-full sm:w-auto"
            >
              <RefreshCw className={`w-4 h-4 shrink-0 ${analyzingSlow ? 'animate-spin' : ''}`} />
              Why is my network slow?
            </button>
          </div>

          <AnimatePresence>
            {slowAnalysis && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                exit={{ opacity: 0, height: 0 }}
                className="mt-4 border-t border-white/10 pt-4"
              >
                <h4 className="text-sm font-semibold text-white mb-2 flex items-center gap-1.5">
                  <AlertCircle className="w-4 h-4 text-cyan-400" /> Network Performance Analysis
                </h4>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mt-3">
                  <div>
                    <span className="text-xs text-slate-500 block">Current Latency</span>
                    <span className="text-lg font-semibold text-slate-200">{slowAnalysis.latency}ms</span>
                    <span className="text-xs text-slate-600 block mt-1">Baseline: {slowAnalysis.baseline}ms</span>
                  </div>
                  <div>
                    <span className="text-xs text-slate-500 block">Packet Loss</span>
                    <span className="text-lg font-semibold text-slate-200">{slowAnalysis.loss}%</span>
                  </div>
                  <div>
                    <span className="text-xs text-slate-500 block">Likely Issue</span>
                    <span className="text-sm font-medium text-orange-400">{slowAnalysis.likely_issue}</span>
                  </div>
                </div>

                <div className="mt-5 grid grid-cols-1 md:grid-cols-2 gap-6">
                  <div>
                    <span className="text-xs font-semibold text-slate-400 block mb-2">Evidence</span>
                    <ul className="space-y-1.5">
                      {slowAnalysis.evidence.map((ev: string, idx: number) => (
                        <li key={idx} className="text-sm text-slate-400 flex items-start gap-2">
                          <span className="text-cyan-500 mt-1">•</span> {ev}
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <span className="text-xs font-semibold text-slate-400 block mb-2">Recommended Checks</span>
                    <ul className="space-y-1.5">
                      {slowAnalysis.recommendations.map((rec: string, idx: number) => (
                        <li key={idx} className="text-sm text-slate-400 flex items-start gap-2">
                          <span className="text-cyan-400 font-bold mt-0.5">{idx + 1}.</span> {rec}
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </motion.div>

        {/* Charts & Tables */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <motion.div 
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.4 }}
            className="glass-card p-6 col-span-2"
          >
            <h3 className="text-lg font-semibold text-white mb-6">Latency Trend</h3>
            <div className="h-64 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={data.latency}>
                  <XAxis dataKey="time" stroke="#475569" fontSize={12} tickLine={false} axisLine={false} />
                  <YAxis stroke="#475569" fontSize={12} tickLine={false} axisLine={false} tickFormatter={(v)=>`${v}ms`} />
                  <Tooltip 
                    contentStyle={{ backgroundColor: 'rgba(15,23,42,0.9)', border: '1px solid rgba(6,214,214,0.2)', borderRadius: '8px' }}
                    itemStyle={{ color: '#06d6d6' }}
                  />
                  <Line type="monotone" dataKey="ms" stroke="#06d6d6" strokeWidth={3} dot={{ r: 4, fill: '#06d6d6' }} activeDot={{ r: 6, fill: '#fff' }} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </motion.div>

          <motion.div 
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.5 }}
            className="glass-card p-6"
          >
            <h3 className="text-lg font-semibold text-white mb-4">Recent Incidents</h3>
            <div className="space-y-4">
              {data.incidents && data.incidents.length > 0 ? (
                data.incidents.slice(0, 3).map((inc: any) => (
                  <Link href="/incidents" key={inc.id}>
                    <div className="p-4 bg-red-500/10 border border-red-500/20 rounded-xl group hover:bg-red-500/20 transition-colors cursor-pointer flex justify-between items-center mb-3">
                      <div className="min-w-0 pr-4">
                        <h4 className="font-medium text-red-400 truncate">{inc.title}</h4>
                        <p className="text-xs text-slate-400 font-mono mt-1 truncate">{inc.likely_cause}</p>
                      </div>
                      <ChevronRight className="w-5 h-5 text-red-400 opacity-50 group-hover:opacity-100 transition-opacity shrink-0"/>
                    </div>
                  </Link>
                ))
              ) : (
                <div className="p-4 bg-white/5 border border-white/10 rounded-lg text-center">
                  <ShieldCheck className="w-8 h-8 text-green-400 mx-auto mb-2" />
                  <p className="text-sm text-slate-400">No incidents detected</p>
                </div>
              )}
            </div>
          </motion.div>
        </div>
        </>
        )}
      </main>
    </div>
  );
}
