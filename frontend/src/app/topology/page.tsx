"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Shield, Network, Globe, Laptop, HardDrive, Activity } from "lucide-react";
import Link from "next/link";
import Sidebar from "@/components/Sidebar";
import { fetchWithAuth, subscribeBackendStatus } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

function TopologySkeletons() {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] py-10 animate-pulse">
      <div className="glass-card p-6 w-48 h-32 bg-white/5 border border-white/10 rounded-xl" />
      <div className="w-0.5 h-12 bg-white/10 my-2" />
      <div className="glass-card p-6 w-48 h-32 bg-white/5 border border-white/10 rounded-xl" />
      <div className="w-0.5 h-12 bg-white/10 my-2" />
      <div className="flex gap-4">
        <div className="glass-card p-6 w-40 h-32 bg-white/5 border border-white/10 rounded-xl" />
        <div className="glass-card p-6 w-40 h-32 bg-white/5 border border-white/10 rounded-xl" />
      </div>
    </div>
  );
}

function nodeIcon(type: string, status: string) {
  const color = status === "ONLINE" ? "text-cyan-400" : status === "DEGRADED" ? "text-orange-400" : "text-red-400";
  switch (type) {
    case "globe": return <Globe className={`w-8 h-8 ${color}`} />;
    case "router": return <Network className={`w-8 h-8 ${color}`} />;
    case "laptop": return <Laptop className={`w-8 h-8 ${color}`} />;
    default: return <HardDrive className={`w-8 h-8 ${color}`} />;
  }
}

function statusBorder(status: string) {
  if (status === "ONLINE") return "border-cyan-500/30 hover:border-cyan-500 shadow-[0_0_15px_rgba(6,214,214,0.05)]";
  if (status === "DEGRADED") return "border-orange-500/30 hover:border-orange-500 shadow-[0_0_15px_rgba(249,115,22,0.05)]";
  return "border-red-500/30 hover:border-red-500 shadow-[0_0_15px_rgba(239,68,68,0.05)]";
}

export default function TopologyPage() {
  const { user } = useAuth();
  const [loading, setLoading] = useState(true);
  const [nodes, setNodes] = useState<any>(null);

  const fetchTopologyData = async () => {
    try {
      // 1. Fetch devices list
      const devicesRes = await fetchWithAuth(`/api/devices`);
      let devicesList: any[] = [];
      if (devicesRes.ok) {
        devicesList = await devicesRes.json();
      }

      // 2. Fetch actual gateway info from last diagnostic run
      let gatewayIp = "Unknown";
      let internetStatus = "ONLINE";
      let gatewayStatus = "ONLINE";

      try {
        const gwRes = await fetchWithAuth(`/api/gateway`);
        if (gwRes.ok) {
          const gwData = await gwRes.json();
          if (gwData && gwData.address) {
            gatewayIp = gwData.address;
            gatewayStatus = gwData.reachable ? "ONLINE" : "OFFLINE";
          }
        }
      } catch {}

      try {
        const intRes = await fetchWithAuth(`/api/internet`);
        if (intRes.ok) {
          const intData = await intRes.json();
          if (intData) {
            internetStatus = intData.reachable ? "ONLINE" : "OFFLINE";
          }
        }
      } catch {}

      // 2b. No diagnostic run in this session? Use what the agents report
      // about their own hosts — the newest report wins.
      if (gatewayIp === "Unknown") {
        try {
          const telRes = await fetchWithAuth(`/api/telemetry/latest`);
          if (telRes.ok) {
            const latest: Record<string, any> = await telRes.json();
            const newest = Object.values(latest)
              .filter((t: any) => t?.gateway_ip)
              .sort((a: any, b: any) => String(b.timestamp).localeCompare(String(a.timestamp)))[0];
            if (newest) {
              gatewayIp = newest.gateway_ip;
              gatewayStatus = newest.gateway_reachable === false ? "OFFLINE" : "ONLINE";
              if (newest.internet_reachable === false) internetStatus = "OFFLINE";
            }
          }
        } catch {}
      }

      // 3. Map devices dynamically — always use real data, never hardcoded IPs
      let mappedDevices: any[];
      if (devicesList.length > 0) {
        mappedDevices = devicesList.map((d: any) => ({
          name: d.name || d.hostname,
          type: d.platform?.toLowerCase() === "windows" ? "laptop" : "server",
          status: d.status || "ONLINE",
          ip: d.ip_address || "127.0.0.1",
          health: d.health_score ?? 100
        }));
      } else {
        // Fallback: fetch local device info (real IP, not mock)
        try {
          const localRes = await fetchWithAuth(`/api/local-device`);
          if (localRes.ok) {
            const localData = await localRes.json();
            mappedDevices = [{
              name: localData.hostname,
              type: localData.platform?.toLowerCase() === "windows" ? "laptop" : "server",
              status: "ONLINE",
              ip: localData.ip_address,
              health: 100
            }];
          } else {
            mappedDevices = [];
          }
        } catch {
          mappedDevices = [];
        }
      }

      setNodes({
        internet: { name: "Internet Gateway", type: "globe", status: internetStatus, ip: "8.8.8.8" },
        gateway: { name: "Default Gateway", type: "router", status: gatewayStatus, ip: gatewayIp },
        devices: mappedDevices
      });
    } catch (err) {
      console.error("Failed to load topology from api:", err);
      // Even in error fallback, try to get real local device info
      let fallbackDevices: any[] = [];
      try {
        const localRes = await fetchWithAuth(`/api/local-device`);
        if (localRes.ok) {
          const localData = await localRes.json();
          fallbackDevices = [{
            name: localData.hostname,
            type: localData.platform?.toLowerCase() === "windows" ? "laptop" : "server",
            status: "ONLINE",
            ip: localData.ip_address,
            health: 100
          }];
        }
      } catch {}
      
      setNodes({
        internet: { name: "Internet Gateway", type: "globe", status: "ONLINE", ip: "8.8.8.8" },
        gateway: { name: "Default Gateway", type: "router", status: "ONLINE", ip: "Unknown" },
        devices: fallbackDevices.length > 0 ? fallbackDevices : [{ name: "This Device", type: "laptop", status: "ONLINE", ip: "Detecting...", health: 100 }]
      });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    // Reset state immediately on user change
    setNodes(null);
    setLoading(true);
    fetchTopologyData();
  }, [user]);

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

  return (
    <div className="flex flex-col md:flex-row min-h-screen">
      <Sidebar />

      {/* Main Content */}
      <main className="flex-1 p-8 overflow-y-auto">
        <header className="mb-10 flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
          <div>
            <h2 className="text-3xl font-bold text-white mb-2">Logical Diagnostic Topology</h2>
            <p className="text-slate-400">Shows current network dependency path routing structure</p>
          </div>
          <button 
            onClick={fetchTopologyData}
            className="px-4 py-2 border border-cyan-500/30 bg-cyan-500/10 hover:bg-cyan-500/20 text-cyan-400 rounded-lg text-sm font-semibold transition-colors w-full md:w-auto"
          >
            Refresh Layout
          </button>
        </header>

        {loading ? (
          <TopologySkeletons />
        ) : (

        <div className="flex flex-col items-center justify-center min-h-[60vh] py-10 relative">
          {/* Internet Node */}
          <motion.div
            initial={{ opacity: 0, y: -20 }}
            animate={{ opacity: 1, y: 0 }}
            className={`glass-card p-6 flex flex-col items-center justify-center text-center w-48 border ${statusBorder(nodes.internet.status)}`}
          >
            {nodeIcon(nodes.internet.type, nodes.internet.status)}
            <h3 className="font-bold text-white mt-3">{nodes.internet.name}</h3>
            <p className="text-xs text-slate-500 font-mono mt-1">{nodes.internet.ip}</p>
          </motion.div>

          {/* Connection Line */}
          <div className="w-0.5 h-12 bg-gradient-to-b from-cyan-500 to-cyan-500/50"></div>

          {/* Gateway Node */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.1 }}
            className={`glass-card p-6 flex flex-col items-center justify-center text-center w-48 border ${statusBorder(nodes.gateway.status)}`}
          >
            {nodeIcon(nodes.gateway.type, nodes.gateway.status)}
            <h3 className="font-bold text-white mt-3">{nodes.gateway.name}</h3>
            <p className="text-xs text-slate-500 font-mono mt-1">{nodes.gateway.ip}</p>
          </motion.div>

          {/* Multi-branch Line Container */}
          <div className="w-full max-w-4xl flex justify-between px-[10%] mt-0">
            {nodes.devices.map((_: any, i: number) => (
              <div key={i} className="flex flex-col items-center w-full">
                <div className="h-8 w-0.5 bg-cyan-500/30"></div>
              </div>
            ))}
          </div>
          <div className="w-full max-w-4xl h-0.5 bg-cyan-500/30 -mt-8 flex justify-between px-[10%]"></div>

          {/* Device Nodes */}
          <div className="grid grid-cols-1 md:grid-cols-4 gap-6 w-full max-w-5xl mt-8">
            {nodes.devices.map((device: any, idx: number) => (
              <motion.div
                key={device.name}
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.2 + idx * 0.05 }}
                className={`glass-card p-5 flex flex-col items-center justify-center text-center border ${statusBorder(device.status)}`}
              >
                {nodeIcon(device.type, device.status)}
                <h3 className="font-semibold text-white mt-3">{device.name}</h3>
                <p className="text-xs text-slate-500 font-mono mt-1">{device.ip}</p>
                <div className="mt-3 flex items-center gap-1.5">
                  <span className="text-xs text-slate-400">Health:</span>
                  <span className={`text-xs font-bold ${device.health >= 80 ? 'text-green-400' : device.health >= 60 ? 'text-orange-400' : 'text-red-400'}`}>
                    {device.health}%
                  </span>
                </div>
              </motion.div>
            ))}
          </div>
        </div>
        )}
      </main>
    </div>
  );
}
