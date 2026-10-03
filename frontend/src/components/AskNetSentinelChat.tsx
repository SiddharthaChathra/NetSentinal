"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  MessageSquare,
  X,
  Send,
  Sparkles,
  Bot,
  User,
  Loader2,
  AlertCircle,
} from "lucide-react";
import { ask, type AiResult } from "@/lib/ai";
import { useAuth } from "@/context/AuthContext";

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
  aiMeta?: AiResult["ai"];
  error?: boolean;
}

// Questions retrieval answers well. Avoid ones that make the model count or
// rank ("which device has the most…"): a miscount is not something the
// grounding check can catch.
const SUGGESTIONS = [
  "Any DNS issues this week?",
  "What happened today?",
  "Is anything still open?",
];

function TypingIndicator() {
  return (
    <div className="flex items-center gap-3 p-4">
      <div className="w-7 h-7 rounded-full bg-cyan-500/10 border border-cyan-500/30 flex items-center justify-center shrink-0">
        <Bot className="w-3.5 h-3.5 text-cyan-400" />
      </div>
      <div className="flex items-center gap-1.5">
        <motion.div
          className="w-2 h-2 rounded-full bg-cyan-400"
          animate={{ opacity: [0.3, 1, 0.3], scale: [0.8, 1, 0.8] }}
          transition={{ duration: 1.2, repeat: Infinity, delay: 0 }}
        />
        <motion.div
          className="w-2 h-2 rounded-full bg-cyan-400"
          animate={{ opacity: [0.3, 1, 0.3], scale: [0.8, 1, 0.8] }}
          transition={{ duration: 1.2, repeat: Infinity, delay: 0.2 }}
        />
        <motion.div
          className="w-2 h-2 rounded-full bg-cyan-400"
          animate={{ opacity: [0.3, 1, 0.3], scale: [0.8, 1, 0.8] }}
          transition={{ duration: 1.2, repeat: Infinity, delay: 0.4 }}
        />
        <span className="text-xs text-slate-500 ml-2">NetSentinel AI is thinking…</span>
      </div>
    </div>
  );
}

function MessageBubble({ msg }: { msg: ChatMessage }) {
  const isUser = msg.role === "user";

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className={`flex gap-2.5 ${isUser ? "flex-row-reverse" : ""}`}
    >
      <div
        className={`w-7 h-7 rounded-full flex items-center justify-center shrink-0 mt-0.5 ${
          isUser
            ? "bg-white/10 border border-white/20"
            : "bg-cyan-500/10 border border-cyan-500/30"
        }`}
      >
        {isUser ? (
          <User className="w-3.5 h-3.5 text-slate-300" />
        ) : (
          <Bot className="w-3.5 h-3.5 text-cyan-400" />
        )}
      </div>
      <div className={`max-w-[80%] ${isUser ? "text-right" : ""}`}>
        {/* "AI Response" only when a model actually wrote it. Answers such as
            "no device by that name" come from the backend's own rules. */}
        {!isUser && !msg.error && (
          <div className="flex items-center gap-1.5 mb-1">
            {msg.aiMeta?.used ? (
              <Sparkles className="w-3 h-3 text-cyan-500" />
            ) : (
              <Bot className="w-3 h-3 text-slate-500" />
            )}
            <span className={`text-[10px] uppercase tracking-wider font-medium ${msg.aiMeta?.used ? "text-cyan-500/80" : "text-slate-500"}`}>
              {msg.aiMeta?.used ? "AI Response" : "From your diagnostic data"}
            </span>
          </div>
        )}
        <div
          className={`rounded-xl px-3.5 py-2.5 text-sm leading-relaxed ${
            isUser
              ? "bg-cyan-500/15 border border-cyan-500/25 text-white"
              : msg.error
              ? "bg-red-500/5 border border-red-500/20 text-red-300"
              : "bg-white/5 border border-white/10 text-slate-200"
          }`}
        >
          {msg.error && (
            <div className="flex items-center gap-1.5 mb-1 text-red-400">
              <AlertCircle className="w-3 h-3" />
              <span className="text-[10px] uppercase tracking-wider font-medium">Error</span>
            </div>
          )}
          <p className="whitespace-pre-wrap">{msg.content}</p>
        </div>
        {!isUser && msg.aiMeta?.used && (
          <p className="text-[10px] text-slate-600 mt-1 flex items-center gap-1">
            <Sparkles className="w-2.5 h-2.5" />
            {msg.aiMeta.model} · grounded in your diagnostic data only
          </p>
        )}
        {!isUser && msg.aiMeta && !msg.aiMeta.used && msg.aiMeta.note && (
          <p className="text-[10px] text-amber-500/80 mt-1 flex items-center gap-1">
            <AlertCircle className="w-2.5 h-2.5" />
            {msg.aiMeta.note}
          </p>
        )}
        <p className="text-[10px] text-slate-600 mt-0.5">
          {msg.timestamp.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
        </p>
      </div>
    </motion.div>
  );
}

// Mounted once in the root layout, so it outlives sign-out. Keyed by account:
// a different user signing in on the same browser gets a fresh instance, never
// the previous account's conversation. AuthGate also renders the public
// sign-in page, so a signed-out visitor gets no chat at all - every question
// would be a 401, which the app reports as "your session has ended".
export default function AskNetSentinelChat() {
  const { user } = useAuth();
  if (!user) return null;
  return <ChatPanel key={user.id} />;
}

function ChatPanel() {
  const [isOpen, setIsOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Auto-scroll to bottom
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, isLoading]);

  // Focus input when chat opens
  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 200);
    }
  }, [isOpen]);

  const sendMessage = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || isLoading) return;

      const userMsg: ChatMessage = {
        id: `user-${Date.now()}`,
        role: "user",
        content: trimmed,
        timestamp: new Date(),
      };

      setMessages((prev) => [...prev, userMsg]);
      setInput("");
      setIsLoading(true);

      try {
        const result = await ask(trimmed);
        const assistantMsg: ChatMessage = {
          id: `ai-${Date.now()}`,
          role: "assistant",
          content: result.text,
          timestamp: new Date(),
          aiMeta: result.ai,
        };
        setMessages((prev) => [...prev, assistantMsg]);
      } catch (e) {
        const errorMsg: ChatMessage = {
          id: `err-${Date.now()}`,
          role: "assistant",
          content:
            e instanceof Error
              ? e.message
              : "Something went wrong. The rest of NetSentinel is unaffected.",
          timestamp: new Date(),
          error: true,
        };
        setMessages((prev) => [...prev, errorMsg]);
      } finally {
        setIsLoading(false);
      }
    },
    [isLoading]
  );

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    sendMessage(input);
  };

  return (
    <>
      {/* Floating trigger button */}
      <AnimatePresence>
        {!isOpen && (
          <motion.button
            initial={{ scale: 0, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0, opacity: 0 }}
            whileHover={{ scale: 1.1 }}
            whileTap={{ scale: 0.95 }}
            onClick={() => setIsOpen(true)}
            className="fixed bottom-6 right-6 z-50 w-14 h-14 rounded-full bg-cyan-500 shadow-[0_0_30px_rgba(6,214,214,0.4)] flex items-center justify-center text-slate-900 hover:bg-cyan-400 transition-colors"
            aria-label="Open Ask NetSentinel chat"
          >
            <MessageSquare className="w-6 h-6" />
          </motion.button>
        )}
      </AnimatePresence>

      {/* Chat panel */}
      <AnimatePresence>
        {isOpen && (
          <motion.div
            initial={{ opacity: 0, y: 20, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 20, scale: 0.95 }}
            transition={{ type: "spring", stiffness: 300, damping: 25 }}
            className="fixed bottom-6 right-6 z-50 w-[min(420px,calc(100vw-3rem))] h-[min(600px,calc(100vh-6rem))] flex flex-col rounded-2xl overflow-hidden"
            style={{
              background: "rgba(10, 16, 28, 0.85)",
              backdropFilter: "blur(24px) saturate(150%)",
              WebkitBackdropFilter: "blur(24px) saturate(150%)",
              border: "1px solid rgba(255, 255, 255, 0.12)",
              boxShadow:
                "inset 0 1px 0 0 rgba(255, 255, 255, 0.1), 0 20px 60px rgba(0, 0, 0, 0.5), 0 0 40px rgba(6, 214, 214, 0.08)",
            }}
          >
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-white/10">
              <div className="flex items-center gap-2.5">
                <div className="w-8 h-8 rounded-lg bg-cyan-500/15 border border-cyan-500/30 flex items-center justify-center">
                  <Sparkles className="w-4 h-4 text-cyan-400" />
                </div>
                <div>
                  <h3 className="text-sm font-semibold text-white">Ask NetSentinel</h3>
                  <p className="text-[10px] text-slate-500 flex items-center gap-1">
                    <Bot className="w-2.5 h-2.5" />
                    AI-powered · answers from your data only
                  </p>
                </div>
              </div>
              <button
                onClick={() => setIsOpen(false)}
                className="w-7 h-7 rounded-lg bg-white/5 border border-white/10 flex items-center justify-center text-slate-400 hover:text-white hover:bg-white/10 transition-colors"
                aria-label="Close chat"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Messages area */}
            <div
              ref={scrollRef}
              className="flex-1 overflow-y-auto custom-scrollbar p-4 space-y-4"
            >
              {messages.length === 0 && !isLoading && (
                <div className="flex flex-col items-center justify-center h-full text-center px-4">
                  <div className="w-14 h-14 rounded-2xl bg-cyan-500/10 border border-cyan-500/20 flex items-center justify-center mb-4">
                    <MessageSquare className="w-7 h-7 text-cyan-400" />
                  </div>
                  <h4 className="text-sm font-semibold text-white mb-1">
                    Ask about your network
                  </h4>
                  <p className="text-xs text-slate-500 mb-5 max-w-[260px]">
                    Answers come only from your diagnostic findings. If they don&apos;t cover
                    a question, it says so.
                  </p>
                  <div className="space-y-2 w-full">
                    {SUGGESTIONS.map((s) => (
                      <button
                        key={s}
                        onClick={() => sendMessage(s)}
                        className="w-full text-left text-xs px-3 py-2.5 rounded-xl bg-white/[0.03] border border-white/10 text-slate-400 hover:text-white hover:bg-white/[0.06] hover:border-cyan-500/20 transition-all"
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {messages.map((msg) => (
                <MessageBubble key={msg.id} msg={msg} />
              ))}
              {isLoading && <TypingIndicator />}
            </div>

            {/* Input area */}
            <form
              onSubmit={handleSubmit}
              className="px-3 py-3 border-t border-white/10 flex gap-2"
            >
              <input
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                maxLength={500}
                placeholder="Ask a question…"
                disabled={isLoading}
                className="flex-1 min-w-0 bg-white/5 border border-white/10 rounded-xl px-3.5 py-2.5 text-sm text-white placeholder:text-slate-600 focus:outline-none focus:border-cyan-500/40 disabled:opacity-50 transition-colors"
              />
              <button
                type="submit"
                disabled={isLoading || !input.trim()}
                className="w-10 h-10 rounded-xl bg-cyan-500/20 border border-cyan-500/30 text-cyan-400 flex items-center justify-center disabled:opacity-30 hover:bg-cyan-500/30 transition-colors shrink-0"
                aria-label="Send message"
              >
                {isLoading ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Send className="w-4 h-4" />
                )}
              </button>
            </form>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
