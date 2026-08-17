"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Server, Wifi, WifiOff, Activity, Shield, Clock } from "lucide-react";
import Link from "next/link";
import Sidebar from "@/components/Sidebar";
import { fetchWithAuth } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

interface DeviceData {
  id: string;
  name: string;
  hostname: string;
  platform: string;
  ip_address: string;
  status: string;
  agent_version: string;
  last_seen: string;
  architecture?: string;
}

function statusColor(status: string) {
  switch (status) {
    case "ONLINE": return "text-green-400";
    case "DEGRADED": return "text-orange-400";
    case "OFFLINE": return "text-red-400";
    default: return "text-slate-400";
  }
}

function healthColor(score: number) {
  if (score >= 80) return "text-green-400";
  if (score >= 60) return "text-orange-400";
  return "text-red-400";
}

export default function DevicesPage() {
  const { user } = useAuth();
  const [devices, setDevices] = useState<DeviceData[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Reset state immediately on user change
    setDevices([]);
    setLoading(true);
    const fetchDevices = async () => {
      try {
        const res = await fetchWithAuth(`/api/devices`);
        if (res.ok) {
          const data = await res.json();
          setDevices(data);
        }
      } catch (err) {
        console.error("Failed to fetch devices:", err);
        // Try local-device fallback
        try {
          const localRes = await fetchWithAuth(`/api/local-device`);
          if (localRes.ok) {
            const localData = await localRes.json();
            setDevices([{
              id: "local",
              name: localData.hostname,
              hostname: localData.hostname,
              platform: localData.platform,
              ip_address: localData.ip_address,
              status: "ONLINE",
              agent_version: "local",
              last_seen: localData.timestamp || new Date().toISOString(),
              architecture: localData.architecture,
            }]);
          }
        } catch (e2) {
          console.error("Failed to fetch local device:", e2);
        }
      } finally {
        setLoading(false);
      }
    };
    fetchDevices();
  }, [user]);

  const containerVariants = {
    hidden: { opacity: 0 },
    visible: { opacity: 1, transition: { staggerChildren: 0.08 } }
  };

  const itemVariants = {
    hidden: { y: 20, opacity: 0 },
    visible: { y: 0, opacity: 1, transition: { type: "spring" as const, stiffness: 100 } }
  };

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

      {/* Main */}
      <main className="flex-1 p-8 overflow-y-auto">
        <header className="mb-10">
          <h2 className="text-3xl font-bold text-white mb-2">Devices</h2>
          <p className="text-slate-400">{devices.length} device{devices.length !== 1 ? "s" : ""} registered</p>
        </header>

        {devices.length === 0 ? (
          <div className="glass-card p-8 text-center">
            <Server className="w-12 h-12 text-slate-500 mx-auto mb-4" />
            <h3 className="text-lg font-semibold text-white mb-2">No Devices Found</h3>
            <p className="text-slate-400">Run a diagnostic scan from the Overview page to detect your device.</p>
          </div>
        ) : (
          <motion.div
            variants={containerVariants}
            initial="hidden"
            animate="visible"
            className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6"
          >
            {devices.map((device) => (
              <motion.div
                key={device.id}
                variants={itemVariants}
                whileHover={{ scale: 1.02, rotateY: 3 }}
                className="glass-card p-6 cursor-pointer group"
                style={{ perspective: 800 }}
              >
                <div className="flex justify-between items-start mb-4">
                  <div className="flex items-center gap-3">
                    <div className={`p-2 rounded-lg ${device.status === "ONLINE" ? "bg-green-500/10" : device.status === "DEGRADED" ? "bg-orange-500/10" : "bg-red-500/10"}`}>
                      {device.status === "OFFLINE" ? (
                        <WifiOff className="w-5 h-5 text-red-400" />
                      ) : (
                        <Wifi className={`w-5 h-5 ${statusColor(device.status)}`} />
                      )}
                    </div>
                    <div>
                      <h3 className="font-semibold text-white group-hover:text-cyan-400 transition-colors">{device.name}</h3>
                      <p className="text-xs text-slate-500 font-mono">{device.ip_address}</p>
                    </div>
                  </div>
                  <span className={`text-xs font-bold px-2 py-1 rounded ${statusColor(device.status)} ${device.status === "ONLINE" ? "bg-green-500/10" : device.status === "DEGRADED" ? "bg-orange-500/10" : "bg-red-500/10"}`}>
                    {device.status}
                  </span>
                </div>

                <div className="grid grid-cols-3 gap-4 border-t border-white/10 pt-4">
                  <div className="text-center">
                    <p className="text-xs text-slate-500 mb-1">Platform</p>
                    <p className="text-sm text-slate-300">{device.platform}</p>
                  </div>
                  <div className="text-center">
                    <p className="text-xs text-slate-500 mb-1">Agent</p>
                    <p className="text-sm text-slate-300">{device.agent_version}</p>
                  </div>
                  <div className="text-center">
                    <p className="text-xs text-slate-500 mb-1">Last Seen</p>
                    <p className="text-xs text-slate-400 font-mono">
                      {new Date(device.last_seen).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </p>
                  </div>
                </div>
              </motion.div>
            ))}
          </motion.div>
        )}
      </main>
    </div>
  );
}
