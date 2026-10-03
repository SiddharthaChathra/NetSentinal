"use client";

import { Sparkles, Info, Loader2 } from "lucide-react";
import type { AiResult as AiResultData } from "@/lib/ai";

// Models emit **bold** and *emphasis* even when asked for plain prose; show
// the bold and drop the stray asterisks rather than printing them.
function inline(text: string): React.ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") && part.length > 4
      ? <strong key={i} className="text-slate-100 font-semibold">{part.slice(2, -2)}</strong>
      : part.replace(/(^|\s)\*([^*\s][^*]*)\*(?=[\s.,;:!?]|$)/g, "$1$2")
  );
}

// Just enough Markdown for the KB article (headings, bullets, numbered
// steps); everything else renders as plain paragraphs.
function renderText(text: string) {
  const blocks: React.ReactNode[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  const flush = () => {
    if (!list) return;
    const Tag = list.ordered ? "ol" : "ul";
    blocks.push(
      <Tag key={blocks.length} className={`${list.ordered ? "list-decimal" : "list-disc"} list-inside space-y-1 text-slate-300`}>
        {list.items.map((item, i) => <li key={i}>{inline(item)}</li>)}
      </Tag>
    );
    list = null;
  };

  for (const line of text.split("\n")) {
    const ordered = line.match(/^\d+\.\s+(.*)$/);
    const bullet = line.match(/^[-*]\s+(.*)$/);
    if (ordered || bullet) {
      const isOrdered = Boolean(ordered);
      if (!list || list.ordered !== isOrdered) { flush(); list = { ordered: isOrdered, items: [] }; }
      list.items.push((ordered || bullet)![1]);
      continue;
    }
    flush();
    if (/^#{1,6}\s/.test(line) && !line.startsWith("# ") && !line.startsWith("## ")) {
      blocks.push(<h4 key={blocks.length} className="text-sm font-semibold text-slate-200 pt-2">{inline(line.replace(/^#+\s/, ""))}</h4>);
    } else if (line.startsWith("# ")) {
      blocks.push(<h3 key={blocks.length} className="text-lg font-semibold text-white">{line.slice(2)}</h3>);
    } else if (line.startsWith("## ")) {
      blocks.push(<h4 key={blocks.length} className="text-sm font-semibold text-slate-200 pt-2">{line.slice(3)}</h4>);
    } else if (line.trim()) {
      blocks.push(<p key={blocks.length} className="text-slate-300 leading-relaxed">{inline(line)}</p>);
    }
  }
  flush();
  return blocks;
}

// The same rendering for chat bubbles, which otherwise show **raw** markdown.
export function RichText({ text }: { text: string }) {
  return <div className="space-y-2">{renderText(text)}</div>;
}

export function AiLoading({ label = "Writing…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-slate-400">
      <Loader2 className="w-4 h-4 animate-spin text-cyan-400" />
      {label}
    </div>
  );
}

export default function AiResult({ result }: { result: AiResultData }) {
  const { ai } = result;
  return (
    <div className="space-y-3">
      {ai.note && (
        <div className="flex items-start gap-2 text-xs text-amber-300/90 bg-amber-500/5 border border-amber-500/20 rounded-lg px-3 py-2">
          <Info className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span>{ai.note}</span>
        </div>
      )}
      <div className="space-y-2 text-sm">{renderText(result.text)}</div>
      {ai.used && (
        <p className="flex items-center gap-1.5 text-[11px] text-slate-500">
          <Sparkles className="w-3 h-3" />
          Written by {ai.model} from your diagnostic findings only
        </p>
      )}
    </div>
  );
}
