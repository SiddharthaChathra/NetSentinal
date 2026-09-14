"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Server, Wifi, WifiOff, Activity, Shield, Clock } from "lucide-react";
import Link from "next/link";
import Sidebar from "@/components/Sidebar";
import SetupGuide from "@/components/SetupGuide";
import { fetchWithAuth, subscribeBackendStatus } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

function DevicesSkeletons() {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6 animate-pulse">
      <div className="glass-card p-6 h-48 bg-white/5 rounded-xl border border-white/10" />
      <div className="glass-card p-6 h-48 bg-white/5 rounded-xl border border-white/10" />
      <div className="glass-card p-6 h-48 bg-white/5 rounded-xl border border-white/10" />
    </div>
  );
}

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
  is_backup_target?: boolean;
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
    visible: { opacity: 1, transition: { staggerChildren: 0.08 } }
  };

  const itemVariants = {
    hidden: { y: 20, opacity: 0 },
    visible: { y: 0, opacity: 1, transition: { type: "spring" as const, stiffness: 100 } }
  };

  return (
    <div className="flex flex-col md:flex-row min-h-screen">
      <Sidebar />

      <main className="flex-1 p-8 overflow-y-auto">
        <header className="mb-8 flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
          <div>
            <h2 className="text-3xl font-bold text-white mb-2">Devices</h2>
            <p className="text-slate-400">
              {devices.filter(d => d.agent_version !== "hosted-server").length > 0 
                ? `${devices.filter(d => d.agent_version !== "hosted-server").length} device${devices.filter(d => d.agent_version !== "hosted-server").length !== 1 ? "s" : ""} registered`
                : "No devices yet"}
            </p>
          </div>
        </header>

        <div className="mb-8 p-4 bg-cyan-500/10 border border-cyan-500/20 rounded-xl flex items-center justify-between text-sm text-cyan-200">
          <div className="flex items-center gap-2">
            <Server className="w-4 h-4 text-cyan-400 shrink-0" />
            <p>This scan runs from the NetSentinel server. To monitor your own machines, install the agent.</p>
          </div>
        </div>

        {loading ? (
          <DevicesSkeletons />
        ) : (
          <>
            {devices.filter(d => d.agent_version !== "hosted-server").length === 0 && (
              <div className="mb-8">
                <SetupGuide />
              </div>
            )}
            
            {devices.length > 0 && (
              <motion.div
                variants={containerVariants}
                initial="hidden"
                animate="visible"
                className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6 items-stretch"
              >
            {devices.map((device) => (
              <motion.div
                key={device.id}
                variants={itemVariants}
                className="glass-card p-6 flex flex-col justify-between h-full"
              >
                <div>
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
                        <h3 className="font-semibold text-white">{device.name}</h3>
                        <p className="text-xs text-slate-500 font-mono">{device.ip_address}</p>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`text-xs font-bold px-2 py-1 rounded ${statusColor(device.status)} ${device.status === "ONLINE" ? "bg-green-500/10" : device.status === "DEGRADED" ? "bg-orange-500/10" : "bg-red-500/10"}`}>
                        {device.status}
                      </span>
                      {device.agent_version === "hosted-server" && (
                        <span 
                          className="text-xs font-bold px-2 py-1 rounded bg-purple-500/10 text-purple-400 cursor-help"
                          title="This is the server NetSentinel runs on, not one of your machines."
                        >
                          Demo
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="grid grid-cols-3 gap-4 border-t border-white/10 pt-4 mb-4">
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
                </div>

                {/* Backup Target Toggle */}
                <div className="border-t border-white/10 pt-4 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Shield className="w-4 h-4 text-cyan-400" />
                    <span className="text-sm text-slate-300 font-medium">Backup Target</span>
                  </div>
                  <button
                    disabled={device.agent_version === "hosted-server"}
                    onClick={async () => {
                      if (!user) {
                        alert("Sign in to save backup targets.");
                        return;
                      }
                      const newValue = !device.is_backup_target;
                      // Optimistic update
                      setDevices(prev => prev.map(d => d.id === device.id ? { ...d, is_backup_target: newValue } : d));
                      try {
                        const res = await fetchWithAuth(`/api/devices/${device.id}/backup-target`, {
                          method: 'POST',
                          headers: { "Content-Type": "application/json" },
                          body: JSON.stringify({ is_backup_target: newValue })
                        });
                        if (!res.ok) throw new Error(`HTTP ${res.status}`);
                      } catch (e) {
                        console.error("Failed to toggle backup target", e);
                        // Revert on error
                        setDevices(prev => prev.map(d => d.id === device.id ? { ...d, is_backup_target: !newValue } : d));
                      }
                    }}
                    className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                      device.is_backup_target ? 'bg-cyan-500' : 'bg-slate-700'
                    } ${device.agent_version === "hosted-server" ? 'opacity-50 cursor-not-allowed' : ''}`}
                  >
                    <span
                      className={`inline-block h-3 w-3 transform rounded-full bg-white transition-transform ${
                        device.is_backup_target ? 'translate-x-5' : 'translate-x-1'
                      }`}
                    />
                  </button>
                </div>
              </motion.div>
            ))}
          </motion.div>
          )}
        </>
        )}
      </main>
    </div>
  );
}
