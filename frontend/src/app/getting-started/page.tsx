"use client";

import { useEffect, useState } from "react";
import Sidebar from "@/components/Sidebar";
import SetupGuide from "@/components/SetupGuide";
import AddDeviceWizard from "@/components/AddDeviceWizard";
import { fetchWithAuth } from "@/lib/api";
import { Info, LayoutDashboard, Server, Network, AlertTriangle, History, FileText, ShieldAlert, Plus } from "lucide-react";

export default function GettingStartedPage() {
  const [hostedModeNote, setHostedModeNote] = useState<string | null>(null);
  const [showWizard, setShowWizard] = useState(false);

  useEffect(() => {
    const loadData = async () => {
      try {
        const res = await fetchWithAuth("/api/setup");
        if (res.ok) {
          const data = await res.json();
          setHostedModeNote(data.hosted_mode_note);
        }
      } catch (err) {
        console.error("Failed to load setup info:", err);
      }
    };
    loadData();
  }, []);

  return (
    <div className="flex flex-col md:flex-row min-h-screen">
      <Sidebar />
      <main className="flex-1 p-8 overflow-y-auto">
          <header className="mb-8">
            <h2 className="text-3xl font-bold text-white mb-2">Getting Started</h2>
            <p className="text-slate-400">Connect your devices to NetSentinel and start monitoring.</p>
          </header>

          <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
            <div className="xl:col-span-2 space-y-8">
              {hostedModeNote && (
                <section>
                  <h3 className="text-xl font-bold text-white mb-4 flex items-center gap-2">
                    <Info className="w-5 h-5 text-cyan-400" />
                    How NetSentinel works
                  </h3>
                  <div className="glass-card p-6 border-l-4 border-l-cyan-500 bg-cyan-500/5">
                    <p className="text-slate-300 leading-relaxed">{hostedModeNote}</p>
                  </div>
                </section>
              )}

              <section>
                <h3 className="text-xl font-bold text-white mb-4">Add your first machine</h3>
                <div className="glass-card p-6">
                  <p className="text-slate-300">
                    Download the agent, run it, and type in the code we show you. Nothing to
                    install first — Python, pip and git are not needed on the machine you are
                    adding.
                  </p>
                  <button
                    onClick={() => setShowWizard(true)}
                    className="mt-5 inline-flex items-center gap-2 rounded-xl bg-cyan-500/20 px-4 py-2.5 text-sm font-medium text-cyan-300 ring-1 ring-cyan-500/40 transition-colors hover:bg-cyan-500/30 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400"
                  >
                    <Plus className="h-4 w-4" aria-hidden />
                    Add a device
                  </button>
                  <p className="mt-4 text-xs text-slate-500">
                    Adding several machines? Repeat this for each one — every machine gets its own
                    code and appears separately on your Devices page.
                  </p>
                </div>
              </section>

              <section>
                <h3 className="text-xl font-bold text-white mb-4">Running it from source</h3>
                <SetupGuide />
              </section>
            </div>

            <div className="xl:col-span-1">
              <section className="sticky top-8">
                <h3 className="text-xl font-bold text-white mb-4">What you'll see</h3>
                <div className="glass-card p-6 space-y-6">
                  <div className="flex gap-4">
                    <div className="mt-1 p-2 bg-white/5 rounded-lg shrink-0">
                      <LayoutDashboard className="w-4 h-4 text-cyan-400" />
                    </div>
                    <div>
                      <h4 className="font-semibold text-white">Overview</h4>
                      <p className="text-sm text-slate-400 mt-1">High-level summary of your fleet's health, latency, and active incidents.</p>
                    </div>
                  </div>
                  
                  <div className="flex gap-4">
                    <div className="mt-1 p-2 bg-white/5 rounded-lg shrink-0">
                      <Server className="w-4 h-4 text-cyan-400" />
                    </div>
                    <div>
                      <h4 className="font-semibold text-white">Devices</h4>
                      <p className="text-sm text-slate-400 mt-1">Live status, IPs, and agent versions for every machine.</p>
                    </div>
                  </div>

                  <div className="flex gap-4">
                    <div className="mt-1 p-2 bg-white/5 rounded-lg shrink-0">
                      <Network className="w-4 h-4 text-cyan-400" />
                    </div>
                    <div>
                      <h4 className="font-semibold text-white">Topology</h4>
                      <p className="text-sm text-slate-400 mt-1">Visual map showing how your agents are geographically distributed.</p>
                    </div>
                  </div>

                  <div className="flex gap-4">
                    <div className="mt-1 p-2 bg-white/5 rounded-lg shrink-0">
                      <AlertTriangle className="w-4 h-4 text-cyan-400" />
                    </div>
                    <div>
                      <h4 className="font-semibold text-white">Incidents</h4>
                      <p className="text-sm text-slate-400 mt-1">Active alerts, likely causes, and remediation steps.</p>
                    </div>
                  </div>

                  <div className="flex gap-4">
                    <div className="mt-1 p-2 bg-white/5 rounded-lg shrink-0">
                      <History className="w-4 h-4 text-cyan-400" />
                    </div>
                    <div>
                      <h4 className="font-semibold text-white">History</h4>
                      <p className="text-sm text-slate-400 mt-1">Historical latency and packet loss metrics to track long-term trends.</p>
                    </div>
                  </div>

                  <div className="flex gap-4">
                    <div className="mt-1 p-2 bg-white/5 rounded-lg shrink-0">
                      <FileText className="w-4 h-4 text-cyan-400" />
                    </div>
                    <div>
                      <h4 className="font-semibold text-white">Reports</h4>
                      <p className="text-sm text-slate-400 mt-1">Downloadable PDF reports summarizing network performance.</p>
                    </div>
                  </div>

                  <div className="flex gap-4">
                    <div className="mt-1 p-2 bg-white/5 rounded-lg shrink-0">
                      <ShieldAlert className="w-4 h-4 text-cyan-400" />
                    </div>
                    <div>
                      <h4 className="font-semibold text-white">Backup Readiness</h4>
                      <p className="text-sm text-slate-400 mt-1">SLA monitoring and disaster scenario simulations for backup targets.</p>
                    </div>
                  </div>
                </div>
              </section>
            </div>
          </div>
        <AddDeviceWizard
          open={showWizard}
          onClose={() => setShowWizard(false)}
          knownDeviceIds={[]}
        />
      </main>
    </div>
  );
}
