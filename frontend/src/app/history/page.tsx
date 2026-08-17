"use client";

import { useState, useEffect, useMemo } from "react";
import { motion } from "framer-motion";
import { Shield, Clock, Activity } from "lucide-react";
import Link from "next/link";
import Sidebar from "@/components/Sidebar";
import { LineChart, Line, AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";
import { useAuth } from "@/context/AuthContext";
import { fetchWithAuth } from "@/lib/api";

const mockLatency = Array.from({ length: 48 }, (_, i) => ({
  time: `${String(Math.floor(i / 2)).padStart(2, '0')}:${i % 2 === 0 ? '00' : '30'}`,
  latency: 20 + Math.random() * 15 + (i > 30 && i < 35 ? 120 : 0),
  packetLoss: Math.random() * 0.5 + (i > 30 && i < 35 ? 4 : 0),
  health: Math.max(40, 95 - (i > 30 && i < 35 ? 30 : 0) - Math.random() * 5),
}));

export default function HistoryPage() {
  const { user } = useAuth();
  const [range, setRange] = useState("24h");
  const [historyData, setHistoryData] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Reset state immediately on user change
    setHistoryData([]);
    setLoading(true);
    const fetchHistory = async () => {
      let data = [];
      if (user) {
        try {
          const res = await fetchWithAuth(`/api/history?limit=48`);
          if (res.ok) {
            data = await res.json();
          }
        } catch (e) {
          console.error("Failed to fetch Supabase history:", e);
        }
      } else {
        try {
          const localHist = localStorage.getItem("netsentinel_history");
          if (localHist) {
            data = JSON.parse(localHist);
          }
        } catch (e) {
          console.error("Failed to load local history:", e);
        }
      }
      setHistoryData(data);
      setLoading(false);
    };
    fetchHistory();
  }, [user]);

  const chartData = useMemo(() => {
    if (!historyData || historyData.length === 0) return mockLatency;
    return historyData.map((h: any) => ({
      time: new Date(h.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      latency: h.latency,
      packetLoss: h.packet_loss,
      health: h.score,
    })).reverse();
  }, [historyData]);

  const baselines = useMemo(() => {
    if (!historyData || historyData.length === 0) {
      return [
        { label: "Latency", avg: "24 ms", median: "22 ms", p95: "41 ms", stddev: "8.2 ms" },
        { label: "Packet Loss", avg: "0.3%", median: "0.1%", p95: "1.2%", stddev: "0.4%" },
      ];
    }
    
    const latencies = historyData.map(h => h.latency || 0).sort((a, b) => a - b);
    const losses = historyData.map(h => h.packet_loss || 0).sort((a, b) => a - b);
    const n = latencies.length;
    
    const avgLat = latencies.reduce((a, b) => a + b, 0) / n;
    const medLat = latencies[Math.floor(n / 2)];
    const p95Lat = latencies[Math.floor(n * 0.95)];
    const stdLat = Math.sqrt(latencies.map(x => Math.pow(x - avgLat, 2)).reduce((a, b) => a + b, 0) / n);

    const avgLoss = losses.reduce((a, b) => a + b, 0) / n;
    const medLoss = losses[Math.floor(n / 2)];
    const p95Loss = losses[Math.floor(n * 0.95)];
    const stdLoss = Math.sqrt(losses.map(x => Math.pow(x - avgLoss, 2)).reduce((a, b) => a + b, 0) / n);

    return [
      { 
        label: "Latency", 
        avg: `${Math.round(avgLat)} ms`, 
        median: `${Math.round(medLat)} ms`, 
        p95: `${Math.round(p95Lat)} ms`, 
        stddev: `${stdLat.toFixed(1)} ms` 
      },
      { 
        label: "Packet Loss", 
        avg: `${avgLoss.toFixed(1)}%`, 
        median: `${medLoss.toFixed(1)}%`, 
        p95: `${p95Loss.toFixed(1)}%`, 
        stddev: `${stdLoss.toFixed(1)}%` 
      },
    ];
  }, [historyData]);

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
            <h2 className="text-3xl font-bold text-white mb-2">Historical Metrics</h2>
            <p className="text-slate-400 flex items-center gap-2"><Clock className="w-4 h-4" /> Showing last {range}</p>
          </div>
          <div className="flex gap-2">
            {["1h", "6h", "24h", "7d", "30d"].map(r => (
              <button key={r} onClick={() => setRange(r)}
                className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${range === r ? "bg-cyan-500/20 text-cyan-400 border border-cyan-500/30" : "bg-white/5 text-slate-400 border border-white/10 hover:text-white"}`}
              >{r}</button>
            ))}
          </div>
        </header>

        <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
          {/* Health Score Chart */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1 }}
            className="glass-card p-6"
          >
            <h3 className="text-lg font-semibold text-white mb-6">Health Score</h3>
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={chartData}>
                  <defs>
                    <linearGradient id="healthGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#06d6d6" stopOpacity={0.3}/>
                      <stop offset="95%" stopColor="#06d6d6" stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                  <XAxis dataKey="time" stroke="#475569" fontSize={11} tickLine={false} axisLine={false} />
                  <YAxis stroke="#475569" fontSize={11} tickLine={false} axisLine={false} domain={[0, 100]} />
                  <Tooltip contentStyle={{ backgroundColor: 'rgba(15,23,42,0.95)', border: '1px solid rgba(6,214,214,0.2)', borderRadius: '8px' }} itemStyle={{ color: '#06d6d6' }} />
                  <Area type="monotone" dataKey="health" stroke="#06d6d6" fill="url(#healthGrad)" strokeWidth={2} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </motion.div>

          {/* Latency Chart */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.2 }}
            className="glass-card p-6"
          >
            <h3 className="text-lg font-semibold text-white mb-6">Latency</h3>
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                  <XAxis dataKey="time" stroke="#475569" fontSize={11} tickLine={false} axisLine={false} />
                  <YAxis stroke="#475569" fontSize={11} tickLine={false} axisLine={false} tickFormatter={(v)=>`${v}ms`} />
                  <Tooltip contentStyle={{ backgroundColor: 'rgba(15,23,42,0.95)', border: '1px solid rgba(6,214,214,0.2)', borderRadius: '8px' }} itemStyle={{ color: '#06d6d6' }} />
                  <Line type="monotone" dataKey="latency" stroke="#06d6d6" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </motion.div>

          {/* Packet Loss Chart */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.3 }}
            className="glass-card p-6"
          >
            <h3 className="text-lg font-semibold text-white mb-6">Packet Loss</h3>
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={chartData}>
                  <defs>
                    <linearGradient id="lossGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#f97316" stopOpacity={0.3}/>
                      <stop offset="95%" stopColor="#f97316" stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                  <XAxis dataKey="time" stroke="#475569" fontSize={11} tickLine={false} axisLine={false} />
                  <YAxis stroke="#475569" fontSize={11} tickLine={false} axisLine={false} tickFormatter={(v)=>`${v}%`} />
                  <Tooltip contentStyle={{ backgroundColor: 'rgba(15,23,42,0.95)', border: '1px solid rgba(249,115,22,0.2)', borderRadius: '8px' }} itemStyle={{ color: '#f97316' }} />
                  <Area type="monotone" dataKey="packetLoss" stroke="#f97316" fill="url(#lossGrad)" strokeWidth={2} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </motion.div>

          {/* Baseline Summary */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.4 }}
            className="glass-card p-6"
          >
            <h3 className="text-lg font-semibold text-white mb-6">Baseline Summary</h3>
            <div className="space-y-5">
              {baselines.map(b => (
                <div key={b.label} className="p-4 bg-white/5 rounded-lg border border-white/10">
                  <h4 className="text-sm font-semibold text-cyan-400 mb-3">{b.label}</h4>
                  <div className="grid grid-cols-4 gap-4 text-center">
                    <div><p className="text-xs text-slate-500">Average</p><p className="font-mono text-sm text-white">{b.avg}</p></div>
                    <div><p className="text-xs text-slate-500">Median</p><p className="font-mono text-sm text-white">{b.median}</p></div>
                    <div><p className="text-xs text-slate-500">P95</p><p className="font-mono text-sm text-white">{b.p95}</p></div>
                    <div><p className="text-xs text-slate-500">Stddev</p><p className="font-mono text-sm text-white">{b.stddev}</p></div>
                  </div>
                </div>
              ))}
            </div>
          </motion.div>
        </div>
      </main>
    </div>
  );
}
