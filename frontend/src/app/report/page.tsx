"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Shield, FileText, Download, Terminal, Activity } from "lucide-react";
import Link from "next/link";
import Sidebar from "@/components/Sidebar";
import { fetchWithAuth } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

export default function ReportsPage() {
  const { user } = useAuth();
  const [reportType, setReportType] = useState("json");
  const [generating, setGenerating] = useState(false);
  const [reportData, setReportData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Reset state immediately on user change
    setReportData(null);
    setLoading(true);
    const fetchReportData = async () => {
      try {
        // Fetch the last diagnostic run
        const diagRes = await fetchWithAuth(`/api/diagnostic-run`);
        if (diagRes.ok) {
          const diagData = await diagRes.json();
          if (diagData) {
            // Also fetch local device info for context
            let localInfo: any = {};
            try {
              const localRes = await fetchWithAuth(`/api/local-device`);
              if (localRes.ok) localInfo = await localRes.json();
            } catch {}

            setReportData({
              title: "NetSentinel Diagnostic & Observability Report",
              generated_at: diagData.timestamp || new Date().toISOString(),
              system_status: diagData.status || "UNKNOWN",
              health_score: diagData.health_score ?? 0,
              hostname: localInfo.hostname || diagData.system?.hostname || "Unknown",
              ip_address: localInfo.ip_address || diagData.system?.local_ip || "Unknown",
              metrics: {
                latency_avg: `${diagData.internet?.latency_ms ?? "N/A"} ms`,
                packet_loss_avg: `${diagData.internet?.packet_loss ?? "N/A"}%`,
                dns_status: diagData.dns?.every((d: any) => d.success) ? "PASS" : "FAIL",
                gateway_status: diagData.gateway?.reachable ? "PASS" : "FAIL"
              },
              diagnostics: diagData.diagnostics || [],
              duration_ms: diagData.duration_ms || 0,
              is_demo: diagData.is_demo || false,
            });
          }
        }
      } catch (err) {
        console.error("Failed to fetch report data:", err);
      } finally {
        setLoading(false);
      }
    };
    fetchReportData();
  }, [user]);

  const generateReport = () => {
    setGenerating(true);
    setTimeout(() => {
      const dataToExport = reportData || {
        title: "NetSentinel Diagnostic & Observability Report",
        generated_at: new Date().toISOString(),
        system_status: "NO DATA",
        health_score: 0,
        metrics: { latency_avg: "N/A", packet_loss_avg: "N/A", dns_status: "N/A", gateway_status: "N/A" },
        diagnostics: [],
        note: "No diagnostic data available. Run a diagnostic scan first."
      };

      let content = "";
      let filename = "netsentinel-report";
      
      if (reportType === "json") {
        content = JSON.stringify(dataToExport, null, 2);
        filename += ".json";
      } else {
        content = `# NetSentinel Diagnostic & Observability Report\n\n` +
                  `**Generated At:** ${new Date(dataToExport.generated_at).toLocaleString()}\n` +
                  `**System Health:** ${dataToExport.system_status} (${dataToExport.health_score}/100)\n` +
                  (dataToExport.hostname ? `**Device:** ${dataToExport.hostname} (${dataToExport.ip_address})\n` : ``) +
                  `\n## Telemetry Baselines\n` +
                  `- **Latency Avg:** ${dataToExport.metrics.latency_avg}\n` +
                  `- **Packet Loss Avg:** ${dataToExport.metrics.packet_loss_avg}\n` +
                  `- **DNS Verification:** ${dataToExport.metrics.dns_status}\n` +
                  `- **Gateway Verification:** ${dataToExport.metrics.gateway_status}\n\n` +
                  (dataToExport.diagnostics && dataToExport.diagnostics.length > 0
                    ? `## Diagnostics\n` +
                      dataToExport.diagnostics.map((d: any) => `### ${d.title} (${d.severity})\n- **Cause:** ${d.likely_cause}\n- **Confidence:** ${d.confidence}\n`).join("\n")
                    : `## Diagnostics\nNo issues found.\n`);
        filename += ".md";
      }

      const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.setAttribute("download", filename);
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      setGenerating(false);
    }, 800);
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

      {/* Main Content */}
      <main className="flex-1 p-8 overflow-y-auto">
        <header className="mb-10">
          <h2 className="text-3xl font-bold text-white mb-2">Report Generation</h2>
          <p className="text-slate-400">Generate network health audit reports</p>
        </header>

        {/* Report data summary */}
        {reportData ? (
          <div className="glass-card p-6 mb-6">
            <h3 className="text-lg font-semibold text-white mb-4">Latest Diagnostic Summary</h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
              <div>
                <p className="text-xs text-slate-500">Device</p>
                <p className="text-slate-200 font-mono">{reportData.hostname}</p>
                <p className="text-xs text-slate-500 font-mono">{reportData.ip_address}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500">Health</p>
                <p className={`font-bold ${reportData.health_score >= 80 ? 'text-green-400' : reportData.health_score >= 60 ? 'text-orange-400' : 'text-red-400'}`}>
                  {reportData.health_score}/100
                </p>
              </div>
              <div>
                <p className="text-xs text-slate-500">Gateway</p>
                <p className={`font-medium ${reportData.metrics.gateway_status === 'PASS' ? 'text-green-400' : 'text-red-400'}`}>
                  {reportData.metrics.gateway_status}
                </p>
              </div>
              <div>
                <p className="text-xs text-slate-500">DNS</p>
                <p className={`font-medium ${reportData.metrics.dns_status === 'PASS' ? 'text-green-400' : 'text-red-400'}`}>
                  {reportData.metrics.dns_status}
                </p>
              </div>
            </div>
          </div>
        ) : (
          <div className="glass-card p-6 mb-6 text-center">
            <p className="text-slate-400">No diagnostic data available. Run a diagnostic from the Overview page first.</p>
          </div>
        )}

        <div className="max-w-xl glass-card p-6">
          <h3 className="text-lg font-semibold text-white mb-4">Export Options</h3>
          
          <div className="space-y-4 mb-6">
            <div>
              <label className="text-sm text-slate-400 block mb-2">Format</label>
              <div className="flex gap-4">
                <button
                  onClick={() => setReportType("json")}
                  className={`px-4 py-2.5 rounded-lg border text-sm font-medium transition-colors flex items-center gap-2 ${reportType === "json" ? "bg-cyan-500/20 text-cyan-400 border-cyan-500/30" : "bg-white/5 text-slate-400 border-white/10 hover:text-white"}`}
                >
                  <Terminal className="w-4 h-4" /> JSON Export
                </button>
                <button
                  onClick={() => setReportType("markdown")}
                  className={`px-4 py-2.5 rounded-lg border text-sm font-medium transition-colors flex items-center gap-2 ${reportType === "markdown" ? "bg-cyan-500/20 text-cyan-400 border-cyan-500/30" : "bg-white/5 text-slate-400 border-white/10 hover:text-white"}`}
                >
                  <FileText className="w-4 h-4" /> Markdown Export
                </button>
              </div>
            </div>
          </div>

          <button
            onClick={generateReport}
            disabled={generating}
            className="w-full py-3 rounded-lg bg-cyan-500 hover:bg-cyan-400 text-slate-900 font-semibold transition-colors flex items-center justify-center gap-2 shadow-[0_0_15px_rgba(6,214,214,0.2)] disabled:opacity-50"
          >
            <Download className="w-5 h-5" />
            {generating ? "Generating..." : "Download Report"}
          </button>
        </div>
      </main>
    </div>
  );
}
