"use client";

import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { AlertTriangle, RefreshCw, Wifi, WifiOff, CheckCircle } from "lucide-react";
import { checkBackendHealth, BACKEND_URL } from "@/lib/api";

export default function BackendStatusBanner() {
  const [status, setStatus] = useState<"checking" | "connected" | "disconnected">("checking");
  const [errorMsg, setErrorMsg] = useState("");
  const [retrying, setRetrying] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  const checkConnection = useCallback(async () => {
    setRetrying(true);
    const result = await checkBackendHealth();
    if (result.ok) {
      setStatus("connected");
      setErrorMsg("");
      // Auto-dismiss after connection is restored
      setTimeout(() => setDismissed(true), 2000);
    } else {
      setStatus("disconnected");
      setErrorMsg(result.error || "Backend is unreachable");
      setDismissed(false);
    }
    setRetrying(false);
  }, []);

  useEffect(() => {
    checkConnection();

    // Poll every 15 seconds if disconnected
    const interval = setInterval(() => {
      checkConnection();
    }, 15000);

    return () => clearInterval(interval);
  }, [checkConnection]);

  // Don't render anything if connected and dismissed
  if (status === "checking") return null;
  if (status === "connected" && dismissed) return null;

  return (
    <AnimatePresence>
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
                    Backend API Unreachable
                  </p>
                  <p className="text-xs text-red-400/70">
                    Cannot connect to <code className="bg-red-500/10 px-1 py-0.5 rounded text-red-300">{BACKEND_URL}</code>. 
                    Start the backend with: <code className="bg-red-500/10 px-1 py-0.5 rounded text-red-300">python app.py --web</code>
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
