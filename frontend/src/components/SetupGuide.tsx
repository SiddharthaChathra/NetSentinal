"use client";

import { useEffect, useState } from "react";
import { fetchWithAuth } from "@/lib/api";
import { Check, Copy, Activity } from "lucide-react";
import Link from "next/link";

interface SetupStep {
  title: string;
  body: string;
  done?: boolean;
  commands?: string[];
}

interface SetupData {
  user_id: string;
  hosted_mode: boolean;
  hosted_mode_note: string;
  requires_sign_in: boolean;
  signed_in: boolean;
  agent_token_required: boolean;
  api_base_url: string;
  repo_url: string;
  steps: SetupStep[];
}

export default function SetupGuide() {
  const [data, setData] = useState<SetupData | null>(null);
  const [loading, setLoading] = useState(true);
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);

  useEffect(() => {
    const loadData = async () => {
      try {
        const res = await fetchWithAuth("/api/setup");
        if (res.ok) {
          setData(await res.json());
        }
      } catch (err) {
        console.error("Failed to load setup guide:", err);
      } finally {
        setLoading(false);
      }
    };
    loadData();
  }, []);

  const handleCopy = (text: string, index: number) => {
    navigator.clipboard.writeText(text);
    setCopiedIndex(index);
    setTimeout(() => setCopiedIndex(null), 2000);
  };

  if (loading) {
    return (
      <div className="flex justify-center py-12">
        <Activity className="w-8 h-8 text-cyan-500 animate-spin" />
      </div>
    );
  }

  if (!data) {
    return (
      <div className="p-4 bg-red-500/10 border border-red-500/20 rounded-xl text-red-400 text-sm">
        Failed to load setup instructions.
      </div>
    );
  }

  return (
    <div className="glass-card p-6 w-full">
      <div className="space-y-8 text-left">
        {data.steps.map((step, index) => (
          <div key={index} className="flex gap-4">
            <div className="flex flex-col items-center">
              <div className={`w-8 h-8 rounded-full border flex items-center justify-center font-bold text-sm shrink-0 transition-colors ${
                step.done 
                  ? "bg-cyan-500/20 border-cyan-500/50 text-cyan-400" 
                  : "bg-white/5 border-white/20 text-slate-400"
              }`}>
                {step.done ? <Check className="w-4 h-4" /> : index + 1}
              </div>
              {index < data.steps.length - 1 && (
                <div className="w-0.5 h-full bg-white/10 my-2"></div>
              )}
            </div>
            <div className="flex-1 pb-4">
              <h4 className="font-semibold text-white mb-2">{step.title}</h4>
              <p className="text-sm text-slate-400 mb-3">{step.body}</p>
              
              {!data.signed_in && index === 0 && step.title.toLowerCase().includes("sign in") && (
                <div className="mb-4">
                  <Link href="/auth" className="inline-block px-4 py-2 bg-cyan-500 text-slate-900 rounded-lg text-sm font-semibold hover:bg-cyan-400 transition-colors">
                    Sign In to NetSentinel
                  </Link>
                </div>
              )}

              {step.commands && step.commands.length > 0 && (
                <div className="relative group">
                  <pre className="bg-black/40 border border-white/5 rounded-lg p-4 font-mono text-xs text-slate-300 overflow-x-auto max-w-full">
                    {step.commands.join("\n")}
                  </pre>
                  <button
                    onClick={() => handleCopy(step.commands!.join("\n"), index)}
                    className="absolute top-2 right-2 p-2 bg-white/5 hover:bg-white/10 border border-white/10 rounded-md text-slate-400 hover:text-white transition-all opacity-0 group-hover:opacity-100 focus:opacity-100"
                    title="Copy commands"
                  >
                    {copiedIndex === index ? <Check className="w-4 h-4 text-green-400" /> : <Copy className="w-4 h-4" />}
                  </button>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
