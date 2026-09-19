"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Shield, FileText, Download, Terminal, Activity } from "lucide-react";
import Link from "next/link";
import Sidebar from "@/components/Sidebar";
import { fetchWithAuth, subscribeBackendStatus } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

function ReportSkeletons() {
  return (
    <div className="animate-pulse">
      <div className="glass-card p-6 mb-6 h-32 bg-white/5 border border-white/10 rounded-xl" />
      <div className="max-w-xl glass-card p-6 h-48 bg-white/5 border border-white/10 rounded-xl" />
    </div>
  );
}

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
        const res = await fetchWithAuth(`/api/report`);
        if (res.ok) {
          const report = await res.json();
          // Nothing to report until either a hosted scan has run or the
          // account has devices.
          setReportData(report.hosted_scan || (report.devices && report.devices.length > 0) ? report : null);
        }
      } catch (err) {
        console.error("Failed to fetch report data:", err);
      } finally {
        setLoading(false);
      }
    };
    fetchReportData();
  }, [user]);

  const toMarkdown = (r: any): string => {
    const lines: string[] = [];
    lines.push(`# ${r.title}`, "");
    lines.push(`**Generated:** ${new Date(r.generated_at).toLocaleString()}  `);
    lines.push(`**Report version:** ${r.report_version}  `);
    lines.push(`**Account:** ${r.account?.signed_in ? "signed in" : "guest"}`, "");

    lines.push("## Summary", "");
    const sm = r.summary || {};
    lines.push(`- **Hosted scan:** ${sm.hosted_scan_status}${sm.hosted_scan_health_score != null ? ` (${sm.hosted_scan_health_score}/100)` : ""}`);
    lines.push(`- **Devices:** ${sm.devices} (${sm.devices_reporting} reporting)`);
    lines.push(`- **Backup targets:** ${sm.backup_targets}${sm.backup_readiness_score != null ? ` — readiness ${sm.backup_readiness_score}/100` : ""}`);
    lines.push(`- **Open warnings:** ${sm.open_warnings}`, "");

    if (r.hosted_scan) {
      const h = r.hosted_scan;
      lines.push("## Hosted scan (NetSentinel server)", "");
      lines.push(`> ${h.note}`, "");
      lines.push(`- **Run at:** ${new Date(h.run_at).toLocaleString()}${h.is_demo ? " (demo)" : ""}`);
      lines.push(`- **Server:** ${h.server_hostname} (${h.server_ip})`);
      lines.push(`- **Health:** ${h.status} (${h.health_score}/100)`);
      lines.push(`- **Latency:** ${h.metrics.latency_ms} ms · **Packet loss:** ${h.metrics.packet_loss_pct}%`);
      lines.push(`- **Gateway:** ${h.metrics.gateway} · **Internet:** ${h.metrics.internet} · **DNS:** ${h.metrics.dns} · **TCP:** ${h.metrics.tcp}`, "");
      if (h.diagnostics?.length) {
        lines.push("### Findings", "");
        for (const d of h.diagnostics) {
          lines.push(`#### ${d.title} — ${String(d.severity).toUpperCase()}`);
          lines.push(`- **Cause:** ${d.likely_cause}`);
          if (d.evidence?.length) lines.push(`- **Evidence:** ${d.evidence.join("; ")}`);
          if (d.recommended_checks?.length) lines.push(`- **Recommended:** ${d.recommended_checks.join("; ")}`);
          lines.push("");
        }
      }
    }

    if (r.devices?.length) {
      lines.push("## Your devices", "");
      lines.push("| Device | Platform | IP | Gateway | Latency | Loss | DNS | Last report |");
      lines.push("|---|---|---|---|---|---|---|---|");
      for (const d of r.devices) {
        const t = d.latest_telemetry;
        lines.push(`| ${d.name} | ${d.platform} | ${d.ip_address} | ${t?.gateway_ip ?? "—"} | ${t ? `${t.latency_ms} ms` : "—"} | ${t ? `${t.packet_loss_pct}%` : "—"} | ${t ? (t.dns_healthy ? "OK" : "FAIL") : "—"} | ${t ? (t.stale ? `stale (${Math.round(t.age_seconds / 60)} min ago)` : "live") : "never"} |`);
      }
      lines.push("");
    }

    if (r.backup_readiness) {
      const b = r.backup_readiness;
      lines.push(`## Backup readiness — ${b.backupReadinessScore}/100`, "");
      for (const t of b.targets) {
        const br = t.backupReadiness;
        lines.push(`### ${t.name} — ${br.verdict.toUpperCase()} (${br.score}/100)`);
        lines.push(`- **Reachability:** ${t.reachability} · **DNS:** ${t.dnsResolved ? "resolved" : "failed"} · **Latency:** ${t.latencyMs} ms · **Loss:** ${t.packetLossPct}%`);
        lines.push(`- **SLA:** ${br.willMeetSla ? "meets" : "misses"} — est. ${br.estimatedTransferHours} h of ${br.slaWindowHours} h window`);
        if (t.ports?.length) lines.push(`- **Protocols:** ${t.ports.map((p: any) => `${p.service} ${p.open ? "open" : "closed"}`).join(", ")}`);
        lines.push("");
      }
      if (b.diagnostics?.length) {
        lines.push("### Findings", "");
        for (const d of b.diagnostics) {
          lines.push(`- **[${String(d.severity).toUpperCase()} · ${d.category}]** ${d.message}`);
          lines.push(`  - ${d.recommendation}`);
        }
        lines.push("");
      }
    }
    return lines.join("\n");
  };

  const generateReport = () => {
    setGenerating(true);
    setTimeout(() => {
      const dataToExport = reportData || {
        title: "NetSentinel Diagnostic & Observability Report",
        report_version: "2",
        generated_at: new Date().toISOString(),
        account: { signed_in: !!user },
        summary: { hosted_scan_status: "NOT RUN", hosted_scan_health_score: null, devices: 0, devices_reporting: 0, backup_targets: 0, backup_readiness_score: null, open_warnings: 0 },
        hosted_scan: null,
        devices: [],
        backup_readiness: null,
        note: "No diagnostic data available. Run a diagnostic from the Overview page, or register a device.",
      };

      let content = "";
      let filename = "netsentinel-report";

      if (reportType === "json") {
        content = JSON.stringify(dataToExport, null, 2);
        filename += ".json";
      } else {
        content = toMarkdown(dataToExport);
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
        <header className="mb-10">
          <h2 className="text-3xl font-bold text-white mb-2">Report Generation</h2>
          <p className="text-slate-400">Generate network health audit reports</p>
        </header>

        {loading ? (
          <ReportSkeletons />
        ) : (
          <>
            {/* Report data summary */}
            {reportData ? (
              <div className="glass-card p-6 mb-6">
                <h3 className="text-lg font-semibold text-white mb-1">What this report contains</h3>
                <p className="text-xs text-slate-500 mb-4">Assembled by the server so every number agrees with the dashboard.</p>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                  <div>
                    <p className="text-xs text-slate-500">Hosted scan</p>
                    <p className="text-slate-200 font-medium">{reportData.summary.hosted_scan_status}</p>
                    {reportData.hosted_scan && (
                      <p className="text-xs text-slate-500">
                        {reportData.hosted_scan.health_score}/100 · gateway {reportData.hosted_scan.metrics.gateway}
                      </p>
                    )}
                  </div>
                  <div>
                    <p className="text-xs text-slate-500">Your devices</p>
                    <p className="text-slate-200 font-medium">{reportData.summary.devices}</p>
                    <p className="text-xs text-slate-500">{reportData.summary.devices_reporting} reporting live</p>
                  </div>
                  <div>
                    <p className="text-xs text-slate-500">Backup targets</p>
                    <p className="text-slate-200 font-medium">{reportData.summary.backup_targets}</p>
                    {reportData.summary.backup_readiness_score != null && (
                      <p className="text-xs text-slate-500">readiness {reportData.summary.backup_readiness_score}/100</p>
                    )}
                  </div>
                  <div>
                    <p className="text-xs text-slate-500">Open warnings</p>
                    <p className={`font-bold ${reportData.summary.open_warnings === 0 ? "text-green-400" : "text-orange-400"}`}>
                      {reportData.summary.open_warnings}
                    </p>
                  </div>
                </div>
                {reportData.hosted_scan && (
                  <p className="mt-4 text-xs text-slate-500 border-t border-white/10 pt-3">
                    {reportData.hosted_scan.note}
                  </p>
                )}
              </div>
            ) : (
              <div className="glass-card p-6 mb-6 text-center">
                <p className="text-slate-400">Nothing to report yet. Run a diagnostic from the Overview page, or register a device from Getting Started.</p>
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
        </>
        )}
      </main>
    </div>
  );
}
