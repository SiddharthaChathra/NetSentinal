"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { Sparkles, AlertCircle, FileText, BookOpen } from "lucide-react";
import AiResult, { AiLoading } from "@/components/AiResult";
import { summarizeIncidents, kbArticle, type AiResult as AiResultData } from "@/lib/ai";

interface AiIncidentSummaryProps {
  /** A stored incident id. Rows synthesised in the browser have none. */
  incidentId: string;
}

type Kind = "summary" | "kb";

type State =
  | { status: "idle" }
  | { status: "loading"; kind: Kind }
  | { status: "done"; kind: Kind; result: AiResultData }
  | { status: "error"; kind: Kind; message: string };

const LABELS: Record<Kind, string> = { summary: "Ticket write-up", kb: "KB article" };

/**
 * Write-ups for one incident, shown below its raw evidence, never in place of
 * it. Whatever the backend returns is shown: the model's text when it wrote
 * one, otherwise the diagnostic engine's own wording plus a note saying why.
 */
export default function AiIncidentSummary({ incidentId }: AiIncidentSummaryProps) {
  const [state, setState] = useState<State>({ status: "idle" });

  const run = async (kind: Kind) => {
    setState({ status: "loading", kind });
    try {
      const result = kind === "summary" ? await summarizeIncidents([incidentId]) : await kbArticle(incidentId);
      setState({ status: "done", kind, result });
    } catch (e) {
      setState({
        status: "error",
        kind,
        message: e instanceof Error ? e.message : "Could not write that up. The incident data above is unaffected.",
      });
    }
  };

  const busy = state.status === "loading";
  const aiWritten = state.status === "done" && state.result.ai.used;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        {(["summary", "kb"] as const).map((kind) => (
          <button
            key={kind}
            onClick={() => run(kind)}
            disabled={busy}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/5 text-slate-300 border border-white/10 text-xs font-medium hover:text-white hover:border-cyan-500/30 disabled:opacity-40 transition-colors"
          >
            {kind === "summary" ? <FileText className="w-3.5 h-3.5" /> : <BookOpen className="w-3.5 h-3.5" />}
            {kind === "summary" ? "Write up for a ticket" : "KB article"}
          </button>
        ))}
      </div>

      {state.status !== "idle" && (
        <motion.div
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          className={`rounded-xl border p-4 ${aiWritten ? "border-cyan-500/15 bg-cyan-500/[0.03]" : "border-white/10 bg-white/[0.02]"}`}
        >
          <div className="flex items-center gap-2 mb-3">
            {aiWritten && <Sparkles className="w-3.5 h-3.5 text-cyan-500" />}
            <span className={`text-[10px] uppercase tracking-wider font-semibold ${aiWritten ? "text-cyan-500/80" : "text-slate-500"}`}>
              {LABELS[state.kind]}
              {aiWritten ? " · AI written" : ""}
            </span>
          </div>

          {state.status === "loading" && <AiLoading label="Writing it up from this incident's findings…" />}
          {state.status === "done" && <AiResult result={state.result} />}
          {state.status === "error" && (
            <div className="flex items-start gap-2">
              <AlertCircle className="w-4 h-4 text-slate-500 mt-0.5 shrink-0" />
              <div>
                <p className="text-xs text-slate-400">{state.message}</p>
                <button onClick={() => run(state.kind)} className="text-[10px] text-cyan-500 hover:text-cyan-400 mt-2 transition-colors">
                  Retry
                </button>
              </div>
            </div>
          )}
        </motion.div>
      )}
    </div>
  );
}
