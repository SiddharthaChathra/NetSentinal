"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { RefreshCw, WifiOff, CheckCircle } from "lucide-react";
import { ensureBackendReady, subscribeBackendStatus, BACKEND_URL } from "@/lib/api";

export default function BackendStatusBanner() {
  // "checking" = the shared readiness probe is still retrying (up to ~90s
  // for a cold start). Nothing alarming should render in that state.
  const [status, setStatus] = useState<"checking" | "connected" | "disconnected">("checking");
  const [errorMsg, setErrorMsg] = useState("");
  const [retrying, setRetrying] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [attempt, setAttempt] = useState<{ n: number; max: number } | null>(null);
  const inFlight = useRef(false);

  useEffect(() => {
    const unsubscribe = subscribeBackendStatus((s, info) => {
      if (s === "connecting") {
        setStatus("checking");
        setAttempt(info.attempt ? { n: info.attempt, max: info.maxAttempts ?? 0 } : null);
      } else if (s === "connected") {
        setStatus("connected");
        setErrorMsg("");
        setAttempt(null);
        setTimeout(() => setDismissed(true), 2000);
      } else {
        setStatus("disconnected");
        setErrorMsg(info.health?.error || "Backend is unreachable");
        setAttempt(null);
        setDismissed(false);
      }
    });
    ensureBackendReady();
    return unsubscribe;
  }, []);

  const checkConnection = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setRetrying(true);
    try {
      await ensureBackendReady(true);
    } finally {
      setRetrying(false);
      inFlight.current = false;
    }
  }, []);

  // Only keep polling while we are actually disconnected.
  useEffect(() => {
    if (status !== "disconnected") return;
    const interval = setInterval(checkConnection, 15000);
    return () => clearInterval(interval);
  }, [status, checkConnection]);

  // Exposed for the UI layer: while `attempt` is non-null a connection
  // attempt is in progress (attempt.n of attempt.max).
  void attempt;

  // Don't render anything if connected and dismissed
  if (status === "connected" && dismissed) return null;

  return (
    <AnimatePresence>
      {status === "checking" && attempt !== null && (
        <motion.div
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: "auto", opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          transition={{ duration: 0.3 }}
          className="overflow-hidden"
        >
          <div className="bg-cyan-500/10 border-b border-cyan-500/20 px-6 py-2 flex items-center justify-center">
            <div className="flex flex-col items-center justify-center max-w-7xl mx-auto gap-1">
              <div className="flex items-center gap-3">
                <div className="w-4 h-4 border-2 border-cyan-400 border-t-transparent rounded-full animate-spin" />
                <p className="text-sm font-medium text-cyan-300 animate-pulse">
                  Connecting to NetSentinel...
                </p>
              </div>
              {attempt.n >= 2 && (
                <p className="text-xs text-cyan-400/70">
                  Waking up the server, this can take up to a minute.
                </p>
              )}
            </div>
          </div>
        </motion.div>
      )}

      {status === "disconnected" && (
        <motion.div
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: "auto", opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          transition={{ duration: 0.3 }}
          className="overflow-hidden"
        >
          <div className="bg-red-500/10 border-b border-red-500/20 px-6 py-3">
            <div className="flex items-center justify-between max-w-7xl mx-auto">
              <div className="flex items-center gap-3">
                <div className="p-1.5 bg-red-500/20 rounded-lg">
                  <WifiOff className="w-4 h-4 text-red-400" />
                </div>
                <div>
                  <p className="text-sm font-medium text-red-300">
                    Service Unreachable
                  </p>
                  <p className="text-xs text-red-400/70">
                    We couldn't reach the NetSentinel service. It may be restarting — we'll keep trying.
                  </p>
                </div>
              </div>
              <button
                onClick={checkConnection}
                disabled={retrying}
                className="flex items-center gap-2 px-3 py-1.5 bg-red-500/20 hover:bg-red-500/30 border border-red-500/30 rounded-lg text-xs text-red-300 transition-colors disabled:opacity-50"
              >
                <RefreshCw className={`w-3 h-3 ${retrying ? "animate-spin" : ""}`} />
                Retry
              </button>
            </div>
          </div>
        </motion.div>
      )}

      {status === "connected" && !dismissed && (
        <motion.div
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: "auto", opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          transition={{ duration: 0.3 }}
          className="overflow-hidden"
        >
          <div className="bg-green-500/10 border-b border-green-500/20 px-6 py-2">
            <div className="flex items-center gap-3 max-w-7xl mx-auto">
              <CheckCircle className="w-4 h-4 text-green-400" />
              <p className="text-sm text-green-300">Backend connected successfully</p>
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
