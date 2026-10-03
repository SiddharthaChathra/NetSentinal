"use client";

import { useCallback, useEffect, useState } from "react";
import { MessageSquare, TrendingUp, Send } from "lucide-react";
import Sidebar from "@/components/Sidebar";
import AiResult, { AiLoading } from "@/components/AiResult";
import DigestPanel from "@/components/DigestPanel";
import { useAuth } from "@/context/AuthContext";
import { fetchWithAuth } from "@/lib/api";
import * as ai from "@/lib/ai";

type Slot = { loading: boolean; result: ai.AiResult | null; error: string | null };
const empty: Slot = { loading: false, result: null, error: null };

function useAiSlot() {
  const [slot, setSlot] = useState<Slot>(empty);
  const run = useCallback(async (fn: () => Promise<ai.AiResult>) => {
    setSlot({ loading: true, result: null, error: null });
    try {
      setSlot({ loading: false, result: await fn(), error: null });
    } catch (e) {
      setSlot({ loading: false, result: null, error: e instanceof Error ? e.message : String(e) });
    }
  }, []);
  return [slot, run] as const;
}

function SlotBody({ slot, idle }: { slot: Slot; idle?: string }) {
  if (slot.loading) return <AiLoading />;
  if (slot.error) return <p className="text-sm text-slate-400">{slot.error}</p>;
  if (slot.result) return <AiResult result={slot.result} />;
  return idle ? <p className="text-sm text-slate-500">{idle}</p> : null;
}

const EXAMPLES = ["Any DNS problems this week?", "What happened today?", "Is anything still open?"];

export default function AssistantClient() {
  const { user } = useAuth();
  const [question, setQuestion] = useState("");
  const [answer, runAsk] = useAiSlot();
  const [devices, setDevices] = useState<{ id: string; name: string }[]>([]);
  const [deviceId, setDeviceId] = useState("");
  const [trendSlot, runTrend] = useAiSlot();

  useEffect(() => {
    if (!user) return;
    fetchWithAuth("/api/devices")
      .then((r) => (r.ok ? r.json() : []))
      .then((list: { id: string; name?: string; hostname?: string }[]) => {
        const ds = list.map((d) => ({ id: d.id, name: d.name || d.hostname || d.id }));
        setDevices(ds);
        if (ds.length) setDeviceId(ds[0].id);
      })
      .catch(() => setDevices([]));
  }, [user]);

  useEffect(() => {
    if (deviceId) runTrend(() => ai.trends(deviceId));
  }, [deviceId, runTrend]);

  const submit = (q: string) => {
    const trimmed = q.trim();
    if (!trimmed) return;
    setQuestion(trimmed);
    runAsk(() => ai.ask(trimmed));
  };

  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <main className="flex-1 p-4 md:p-8 overflow-y-auto">
        <header className="mb-8">
          <h2 className="text-3xl font-bold text-white mb-2">Assistant</h2>
          <p className="text-slate-400 max-w-2xl">
            Answers come only from the findings NetSentinel&apos;s diagnostic engine has recorded on your account.
            If a question isn&apos;t covered by them, it says so.
          </p>
        </header>

        <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
          <section className="glass-card p-6 xl:col-span-2">
            <h3 className="flex items-center gap-2 font-semibold text-white mb-4">
              <MessageSquare className="w-4 h-4 text-cyan-400" /> Ask NetSentinel
            </h3>
            <form
              onSubmit={(e) => { e.preventDefault(); submit(question); }}
              className="flex gap-2 mb-3"
            >
              <input
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                maxLength={500}
                placeholder="e.g. Any packet loss on my NAS this week?"
                className="flex-1 min-w-0 bg-white/5 border border-white/10 rounded-lg px-4 py-2 text-white placeholder:text-slate-500 focus:outline-none focus:border-cyan-500/50"
              />
              <button
                type="submit"
                disabled={answer.loading || !question.trim()}
                className="px-4 py-2 rounded-lg bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 disabled:opacity-40 flex items-center gap-2"
              >
                <Send className="w-4 h-4" /> Ask
              </button>
            </form>
            <div className="flex flex-wrap gap-2 mb-5">
              {EXAMPLES.map((ex) => (
                <button key={ex} onClick={() => submit(ex)}
                  className="text-xs px-3 py-1 rounded-full bg-white/5 border border-white/10 text-slate-400 hover:text-white">
                  {ex}
                </button>
              ))}
            </div>
            <SlotBody slot={answer} />
          </section>

          {/* Digest panel — new standalone component with loading/degradation states */}
          <DigestPanel autoFetch={!!user} />

          <section className="glass-card p-6">
            <div className="flex items-center justify-between gap-3 mb-4">
              <h3 className="flex items-center gap-2 font-semibold text-white">
                <TrendingUp className="w-4 h-4 text-cyan-400" /> Trends (30 days)
              </h3>
              {devices.length > 0 && (
                <select value={deviceId} onChange={(e) => setDeviceId(e.target.value)}
                  className="bg-white/5 border border-white/10 rounded-md px-2 py-1 text-sm text-white max-w-[50%]">
                  {devices.map((d) => <option key={d.id} value={d.id} className="bg-slate-900">{d.name}</option>)}
                </select>
              )}
            </div>
            <SlotBody slot={trendSlot} idle={devices.length ? undefined : "No devices on this account yet."} />
          </section>
        </div>
      </main>
    </div>
  );
}

