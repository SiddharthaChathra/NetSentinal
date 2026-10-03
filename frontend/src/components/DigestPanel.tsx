"use client";

import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { CalendarDays, Sparkles, RefreshCw, Loader2, Info, ChevronDown, ChevronUp, Clock } from "lucide-react";
import AiResult from "@/components/AiResult";
import { digest, type AiResult as AiResultData } from "@/lib/ai";

type Period = "daily" | "weekly";

type DigestState =
  | { status: "loading" }
  | { status: "done"; result: AiResultData; fetchedAt: Date }
  | { status: "error"; message: string };

interface DigestPanelProps {
  /** Fetch on mount (and on period change)? Defaults to true. */
  autoFetch?: boolean;
}

function load(period: Period): Promise<DigestState> {
  return digest(period).then(
    (result): DigestState => ({ status: "done", result, fetchedAt: new Date() }),
    (e): DigestState => ({
      status: "error",
      message: e instanceof Error ? e.message : "Could not load the digest right now.",
    }),
  );
}

export default function DigestPanel({ autoFetch = true }: DigestPanelProps) {
  const [period, setPeriod] = useState<Period>("daily");
  const [state, setState] = useState<DigestState>({ status: "loading" });
  const [collapsed, setCollapsed] = useState(false);

  // Loading is set by whoever asks for a fetch (initial state, the period
  // toggle, Refresh); the effect only applies the answer, and drops it if the
  // period changed again before it arrived.
  useEffect(() => {
    if (!autoFetch) return;
    let current = true;
    load(period).then((next) => { if (current) setState(next); });
    return () => { current = false; };
  }, [period, autoFetch]);

  const choosePeriod = (p: Period) => {
    if (p === period) return;
    setState({ status: "loading" });
    setPeriod(p);
  };

  const refresh = () => {
    setState({ status: "loading" });
    load(period).then(setState);
  };

  // The badge says "AI Generated" only when a model actually wrote the text.
  // A quiet period, or the model being down, is the backend's own digest.
  const aiWritten = state.status === "done" && state.result.ai.used;

  return (
    <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="glass-card overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 px-6 py-4 border-b border-white/5">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-cyan-500/10 border border-cyan-500/20 flex items-center justify-center">
            <CalendarDays className="w-4 h-4 text-cyan-400" />
          </div>
          <div>
            <h3 className="font-semibold text-white text-sm flex items-center gap-2">
              {period === "daily" ? "Daily" : "Weekly"} Digest
              {aiWritten && (
                <span className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-cyan-500/70 font-medium bg-cyan-500/5 border border-cyan-500/15 rounded-full px-2 py-0.5">
                  <Sparkles className="w-2.5 h-2.5" /> AI Generated
                </span>
              )}
            </h3>
            {state.status === "done" && (
              <p className="text-[10px] text-slate-600 flex items-center gap-1 mt-0.5">
                <Clock className="w-2.5 h-2.5" />
                Updated {state.fetchedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
              </p>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2">
          <div className="flex gap-1 bg-white/[0.03] rounded-lg p-0.5 border border-white/5">
            {(["daily", "weekly"] as const).map((p) => (
              <button
                key={p}
                onClick={() => choosePeriod(p)}
                className={`px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
                  period === p
                    ? "bg-cyan-500/15 text-cyan-400 border border-cyan-500/25 shadow-[0_0_8px_rgba(6,214,214,0.1)]"
                    : "text-slate-500 border border-transparent hover:text-slate-300"
                }`}
              >
                {p === "daily" ? "24h" : "7d"}
              </button>
            ))}
          </div>
          <button
            onClick={refresh}
            disabled={state.status === "loading"}
            className="w-8 h-8 rounded-lg bg-white/5 border border-white/10 flex items-center justify-center text-slate-500 hover:text-white disabled:opacity-40 transition-colors"
            aria-label="Refresh digest"
          >
            {state.status === "loading" ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
          </button>
          <button
            onClick={() => setCollapsed(!collapsed)}
            className="w-8 h-8 rounded-lg bg-white/5 border border-white/10 flex items-center justify-center text-slate-500 hover:text-white transition-colors"
            aria-label={collapsed ? "Expand digest" : "Collapse digest"}
          >
            {collapsed ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronUp className="w-3.5 h-3.5" />}
          </button>
        </div>
      </div>

      <AnimatePresence>
        {!collapsed && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
          >
            <div className="px-6 py-5">
              {state.status === "loading" && (
                <div className="space-y-3">
                  <div className="flex items-center gap-2 text-sm text-slate-400">
                    <Loader2 className="w-4 h-4 animate-spin text-cyan-400" />
                    <span>Building the {period} digest…</span>
                  </div>
                  <div className="space-y-2 animate-pulse">
                    <div className="h-3 bg-white/5 rounded w-3/4" />
                    <div className="h-3 bg-white/5 rounded w-1/2" />
                    <div className="h-3 bg-white/5 rounded w-5/6" />
                  </div>
                </div>
              )}

              {/* Always the backend's text: the model's when it wrote one, the
                  diagnostic engine's own digest otherwise, with a note saying so. */}
              {state.status === "done" && <AiResult result={state.result} />}

              {state.status === "error" && (
                <div className="flex items-start gap-2">
                  <Info className="w-4 h-4 text-slate-500 mt-0.5 shrink-0" />
                  <div>
                    <p className="text-sm text-slate-400">{state.message}</p>
                    <p className="text-[10px] text-slate-600 mt-1">
                      Your diagnostic data is still on the Overview and Incidents pages.
                    </p>
                    <button onClick={refresh} className="text-xs text-cyan-500 hover:text-cyan-400 mt-2 transition-colors">
                      Retry
                    </button>
                  </div>
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
