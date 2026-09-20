"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Server, Wifi, WifiOff, Activity, Shield, Clock, Trash2, Plus, X, AlertCircle } from "lucide-react";
import Link from "next/link";
import Sidebar from "@/components/Sidebar";
import SetupGuide from "@/components/SetupGuide";
import { fetchWithAuth, subscribeBackendStatus } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

/** "11h ago" rather than "12:08 am", which is ambiguous the moment a device
 *  has been quiet for more than a day. */
function relativeAge(iso: string | null | undefined): string {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "unknown";
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (seconds < 90) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 90) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 36) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

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
  backup_protocols?: string[] | null;
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
  const [removingId, setRemovingId] = useState<string | null>(null);
  const [savingProtocolsId, setSavingProtocolsId] = useState<string | null>(null);
  const ALL_PROTOCOLS = ["NFS", "SMB", "iSCSI", "Replication"];

  // null/undefined from the API means "all four" (the default).
  const protocolsOf = (d: DeviceData) => d.backup_protocols && d.backup_protocols.length > 0 ? d.backup_protocols : ALL_PROTOCOLS;

  const saveProtocols = async (device: DeviceData, next: string[]) => {
    if (next.length === 0) return; // must serve at least one protocol
    const previous = device.backup_protocols ?? null;
    const payload = next.length === ALL_PROTOCOLS.length ? null : next;
    setDevices(prev => prev.map(d => d.id === device.id ? { ...d, backup_protocols: payload } : d));
    setSavingProtocolsId(device.id);
    try {
      const res = await fetchWithAuth(`/api/devices/${device.id}/backup-target`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_backup_target: true, backup_protocols: next }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
    } catch (e) {
      console.error("Failed to save backup protocols", e);
      setDevices(prev => prev.map(d => d.id === device.id ? { ...d, backup_protocols: previous } : d));
    } finally {
      setSavingProtocolsId(null);
    }
  };
  const [loading, setLoading] = useState(true);
  // Whether the "add a device" setup guide is expanded. Opened from the
  // header button, and from the "set it to start automatically" link on an
  // offline device.
  const [showSetup, setShowSetup] = useState(false);

  // The hosted demo device is this server, not one of the user's machines, so
  // it never counts towards "how many devices have you registered".
  const agentDeviceCount = devices.filter(d => d.agent_version !== "hosted-server").length;

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
              {agentDeviceCount > 0
                ? `${agentDeviceCount} device${agentDeviceCount !== 1 ? "s" : ""} registered`
                : "No devices yet"}
            </p>
          </div>

          {/* The only route to adding a SECOND machine. The setup guide used
              to appear solely in the empty state, so once you had one device
              there was no way in at all. */}
          {agentDeviceCount > 0 && (
            <button
              onClick={() => setShowSetup(v => !v)}
              aria-expanded={showSetup}
              className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-cyan-500/20 border border-cyan-500/40 text-cyan-300 text-sm font-medium hover:bg-cyan-500/30 transition-colors shrink-0"
            >
              {showSetup ? <X className="w-4 h-4" /> : <Plus className="w-4 h-4" />}
              {showSetup ? "Close" : "Add a device"}
            </button>
          )}
        </header>

        {/* Only meaningful before the first agent exists. Once the user has
            their own machines registered it is stale advice sitting above the
            very devices it tells them to install. */}
        {agentDeviceCount === 0 && (
          <div className="mb-8 p-4 bg-cyan-500/10 border border-cyan-500/20 rounded-xl flex items-center justify-between text-sm text-cyan-200">
            <div className="flex items-center gap-2">
              <Server className="w-4 h-4 text-cyan-400 shrink-0" />
              <p>This scan runs from the NetSentinel server. To monitor your own machines, install the agent.</p>
            </div>
          </div>
        )}

        {loading ? (
          <DevicesSkeletons />
        ) : (
          <>
            {(agentDeviceCount === 0 || showSetup) && (
              <div className="mb-8">
                {showSetup && agentDeviceCount > 0 && (
                  <p className="mb-3 text-sm text-slate-400">
                    Run these steps on the machine you want to add. The same token works on every
                    machine on your account — each one registers under its own hostname, so give
                    them distinct names.
                  </p>
                )}
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
                      <p className="text-xs text-slate-500 mb-1">Last Report</p>
                      <p className={`text-xs font-mono ${device.status === "OFFLINE" ? "text-red-400" : "text-slate-400"}`}>
                        {relativeAge(device.last_seen)}
                      </p>
                    </div>
                  </div>

                  {/* "Offline" means the AGENT stopped reporting, which is not
                      the same as the machine being down. Someone sitting in
                      front of that very laptop will read a bare "OFFLINE" as
                      simply wrong unless we say which we mean. */}
                  {device.status === "OFFLINE" && device.agent_version !== "hosted-server" && (
                    <div className="mt-4 p-3 rounded-lg bg-red-500/10 border border-red-500/20">
                      <div className="flex items-start gap-2">
                        <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
                        <div className="min-w-0">
                          <p className="text-xs text-red-300 font-medium">
                            No agent report for {relativeAge(device.last_seen).replace(" ago", "")}
                          </p>
                          <p className="text-xs text-slate-400 mt-1">
                            The machine itself may be fine — this means the NetSentinel agent is not
                            running on it. Start it again on that machine:
                          </p>
                          <code className="mt-2 block text-[11px] font-mono text-cyan-300 bg-black/30 rounded px-2 py-1.5 break-all">
                            python agent/agent.py --start
                          </code>
                          <p className="text-xs text-slate-500 mt-2">
                            Tired of restarting it?{" "}
                            <button
                              onClick={() => setShowSetup(true)}
                              className="text-cyan-400 hover:text-cyan-300 underline underline-offset-2"
                            >
                              Set it to start automatically
                            </button>
                            .
                          </p>
                        </div>
                      </div>
                    </div>
                  )}
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
                      // Defensive only: this page is behind AuthGate, so a
                      // missing user means the session ended mid-click, and
                      // the request below would 401 anyway.
                      if (!user) return;
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

                {/* Which backup protocols this target serves — only these are checked and scored */}
                {device.is_backup_target && device.agent_version !== "hosted-server" && (
                  <div className="pt-3">
                    <p className="text-xs text-slate-500 mb-2">Serves backups over</p>
                    <div className="flex flex-wrap gap-2">
                      {ALL_PROTOCOLS.map((proto) => {
                        const active = protocolsOf(device).includes(proto);
                        const onlyOne = active && protocolsOf(device).length === 1;
                        return (
                          <button
                            key={proto}
                            type="button"
                            disabled={!user || savingProtocolsId === device.id || onlyOne}
                            title={onlyOne ? "A backup target must serve at least one protocol" : undefined}
                            onClick={() => {
                              const current = protocolsOf(device);
                              saveProtocols(device, active ? current.filter(p => p !== proto) : [...current, proto]);
                            }}
                            className={`px-2.5 py-1 rounded-md text-xs font-medium border transition-colors disabled:cursor-not-allowed ${
                              active
                                ? "bg-cyan-500/15 border-cyan-500/40 text-cyan-300"
                                : "bg-white/5 border-white/10 text-slate-500 hover:text-slate-300 hover:border-white/20"
                            }`}
                          >
                            {proto}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* Remove device (owner only; the hosted demo server cannot be removed) */}
                {user && device.agent_version !== "hosted-server" && (
                  <div className="pt-3 flex justify-end">
                    <button
                      disabled={removingId === device.id}
                      onClick={async () => {
                        if (!confirm(`Remove "${device.name}" and all of its telemetry? If its agent is still running, stop it or re-register it afterwards.`)) return;
                        setRemovingId(device.id);
                        try {
                          const res = await fetchWithAuth(`/api/devices/${device.id}`, { method: "DELETE" });
                          if (!res.ok) throw new Error(`HTTP ${res.status}`);
                          setDevices(prev => prev.filter(d => d.id !== device.id));
                        } catch (e) {
                          console.error("Failed to remove device", e);
                          alert("Couldn't remove this device. Please try again.");
                        } finally {
                          setRemovingId(null);
                        }
                      }}
                      className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-red-400 transition-colors disabled:opacity-50"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                      {removingId === device.id ? "Removing…" : "Remove"}
                    </button>
                  </div>
                )}
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
