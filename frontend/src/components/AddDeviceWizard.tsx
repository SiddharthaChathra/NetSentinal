"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  X, Download, Check, Copy, RefreshCw, Loader2, MonitorSmartphone, Terminal,
  ChevronLeft, AlertCircle, Server,
} from "lucide-react";
import { fetchWithAuth } from "@/lib/api";
import SetupGuide from "@/components/SetupGuide";

/**
 * Adding a machine, as a guided sequence.
 *
 * The design is built around one fact: the person doing this is standing at a
 * SECOND computer, reading a code off the first one. So the code is the hero
 * of the screen — oversized, grouped, monospaced, in the one accent colour —
 * and everything around it stays deliberately quiet. The step rail encodes
 * where they are; nothing else competes.
 */

type OsKey = "windows" | "linux";

interface DownloadInfo {
  label: string;
  filename: string;
  url: string;
}

interface EnrollCode {
  code: string;
  formatted_code: string;
  expires_at: string;
  expires_in_seconds: number;
  downloads: Record<OsKey, DownloadInfo>;
  agent_version: string;
}

interface DeviceSummary {
  id: string;
  name: string;
  hostname: string;
  agent_version: string;
}

const STEPS = ["Machine", "Download", "Link", "Done"] as const;

const OS_CHOICES: { key: OsKey; name: string; detail: string }[] = [
  { key: "windows", name: "Windows", detail: "Windows 10 or 11, 64-bit" },
  { key: "linux", name: "Linux", detail: "x86-64, most distributions" },
];

function StepRail({ current }: { current: number }) {
  return (
    <ol className="flex items-center gap-2" aria-label="Progress">
      {STEPS.map((label, i) => {
        const done = i < current;
        const active = i === current;
        return (
          <li key={label} className="flex items-center gap-2">
            <span
              className={`flex items-center gap-2 text-xs transition-colors ${
                active ? "text-cyan-300" : done ? "text-slate-400" : "text-slate-600"
              }`}
              aria-current={active ? "step" : undefined}
            >
              <span
                className={`grid h-5 w-5 place-items-center rounded-full border text-[10px] ${
                  active
                    ? "border-cyan-400 bg-cyan-400/15 text-cyan-300"
                    : done
                    ? "border-slate-600 bg-slate-700/40 text-slate-300"
                    : "border-white/10 text-slate-600"
                }`}
              >
                {done ? <Check className="h-3 w-3" aria-hidden /> : i + 1}
              </span>
              {label}
            </span>
            {i < STEPS.length - 1 && (
              <span className={`h-px w-6 ${done ? "bg-slate-600" : "bg-white/10"}`} aria-hidden />
            )}
          </li>
        );
      })}
    </ol>
  );
}

function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={() => {
        navigator.clipboard?.writeText(value);
        setCopied(true);
        setTimeout(() => setCopied(false), 1800);
      }}
      className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-slate-300 transition-colors hover:border-cyan-500/40 hover:text-cyan-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400"
    >
      {copied ? <Check className="h-3.5 w-3.5 text-green-400" aria-hidden /> : <Copy className="h-3.5 w-3.5" aria-hidden />}
      {copied ? "Copied" : label}
    </button>
  );
}

export default function AddDeviceWizard({
  open,
  onClose,
  knownDeviceIds,
  onDeviceAdded,
}: {
  open: boolean;
  onClose: () => void;
  knownDeviceIds: string[];
  onDeviceAdded?: () => void;
}) {
  const reduceMotion = useReducedMotion();
  const [step, setStep] = useState(0);
  const [os, setOs] = useState<OsKey | null>(null);
  const [enrollment, setEnrollment] = useState<EnrollCode | null>(null);
  const [codeError, setCodeError] = useState<string | null>(null);
  const [requesting, setRequesting] = useState(false);
  const [secondsLeft, setSecondsLeft] = useState(0);
  const [newDevice, setNewDevice] = useState<DeviceSummary | null>(null);
  const [showSource, setShowSource] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);

  // Captured when the wizard opens so a device appearing afterwards is
  // recognisably the new one, however it was named.
  const baseline = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (!open) return;
    baseline.current = new Set(knownDeviceIds);
    setStep(0);
    setOs(null);
    setEnrollment(null);
    setCodeError(null);
    setNewDevice(null);
    setShowSource(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const previouslyFocused = document.activeElement as HTMLElement | null;
    dialogRef.current?.focus();
    // The page behind must not scroll while a modal owns the screen.
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
      previouslyFocused?.focus?.();
    };
  }, [open, onClose]);

  const requestCode = useCallback(async () => {
    setRequesting(true);
    setCodeError(null);
    try {
      const res = await fetchWithAuth("/api/devices/enroll-code", { method: "POST", retry: false });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setCodeError(body.detail || "Could not create a code. Try again in a moment.");
        return;
      }
      const data: EnrollCode = await res.json();
      setEnrollment(data);
      setSecondsLeft(data.expires_in_seconds);
    } catch {
      setCodeError("Could not reach NetSentinel. Check your connection and try again.");
    } finally {
      setRequesting(false);
    }
  }, []);

  // A code is only fetched when the user reaches the step that shows one, so
  // opening the wizard to read the instructions does not burn a code.
  useEffect(() => {
    if (open && step === 2 && !enrollment && !requesting) void requestCode();
  }, [open, step, enrollment, requesting, requestCode]);

  useEffect(() => {
    if (!enrollment || secondsLeft <= 0) return;
    const t = setInterval(() => setSecondsLeft(s => Math.max(0, s - 1)), 1000);
    return () => clearInterval(t);
  }, [enrollment, secondsLeft]);

  // Watch for the machine checking in. This is the moment the whole flow is
  // for, so it is detected rather than asserted: no "click here when done".
  useEffect(() => {
    if (!open || step !== 3 || newDevice) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const res = await fetchWithAuth("/api/devices");
        if (!res.ok || cancelled) return;
        const devices: DeviceSummary[] = await res.json();
        const found = devices.find(
          d => d.agent_version !== "hosted-server" && !baseline.current.has(d.id),
        );
        if (found) {
          setNewDevice(found);
          onDeviceAdded?.();
        }
      } catch {
        /* keep waiting; the banner reports connectivity */
      }
    };
    void poll();
    const t = setInterval(poll, 4000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [open, step, newDevice, onDeviceAdded]);

  const download = useMemo(
    () => (os && enrollment ? enrollment.downloads[os] : null),
    [os, enrollment],
  );
  // Step 1 needs the download link before a code exists, so fall back to the
  // well-known release URL rather than forcing a code to be created early.
  const fallbackDownload = useMemo(() => {
    if (!os) return null;
    const file = os === "windows" ? "netsentinel-agent-windows.exe" : "netsentinel-agent-linux";
    return {
      label: os === "windows" ? "Windows 10/11 (64-bit)" : "Linux (x86-64)",
      filename: file,
      url: `https://github.com/SiddharthaChathra/NetSentinal/releases/latest/download/${file}`,
    };
  }, [os]);
  const activeDownload = download || fallbackDownload;

  const expired = !!enrollment && secondsLeft <= 0;
  const mmss = `${Math.floor(secondsLeft / 60)}:${String(secondsLeft % 60).padStart(2, "0")}`;

  if (!open) return null;

  const runCommand =
    os === "windows"
      ? `.\\${activeDownload?.filename ?? "netsentinel-agent-windows.exe"} --enroll`
      : `chmod +x ${activeDownload?.filename ?? "netsentinel-agent-linux"} && ./${activeDownload?.filename ?? "netsentinel-agent-linux"} --enroll`;

  return (
    <AnimatePresence>
      <motion.div
        className="fixed inset-0 z-[100] flex items-start justify-center overflow-y-auto p-4 sm:items-center"
        onMouseDown={e => {
          if (e.target === e.currentTarget) onClose();
        }}
      >
        <motion.div
          ref={dialogRef}
          role="dialog"
          aria-modal="true"
          aria-labelledby="add-device-title"
          tabIndex={-1}
          initial={reduceMotion ? false : { opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ type: "spring", stiffness: 260, damping: 26 }}
          className="my-auto w-full max-w-2xl rounded-2xl border border-white/15 bg-[#0a0f1c] shadow-[0_32px_90px_-16px_rgba(0,0,0,0.95)] focus:outline-none"
        >
          {/* Header */}
          <div className="flex items-start justify-between gap-4 border-b border-white/10 px-6 py-5">
            <div>
              <h2 id="add-device-title" className="text-xl font-semibold text-white">
                Add a device
              </h2>
              <p className="mt-1 text-sm text-slate-400">
                Monitor another machine on this account. You can add as many as you like.
              </p>
            </div>
            <button
              onClick={onClose}
              aria-label="Close"
              className="rounded-lg p-1.5 text-slate-500 transition-colors hover:bg-white/5 hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400"
            >
              <X className="h-5 w-5" />
            </button>
          </div>

          <div className="border-b border-white/10 px-6 py-3">
            <StepRail current={step} />
          </div>

          <div className="px-6 py-6">
            {/* ---------------------------------------------- 1. machine */}
            {step === 0 && (
              <div>
                <h3 className="text-base font-medium text-white">
                  What kind of machine are you adding?
                </h3>
                <p className="mt-1 text-sm text-slate-400">
                  Pick the operating system of the machine you want to monitor — not the one
                  you are reading this on.
                </p>
                <div className="mt-5 grid gap-3 sm:grid-cols-2">
                  {OS_CHOICES.map(choice => (
                    <button
                      key={choice.key}
                      onClick={() => {
                        setOs(choice.key);
                        setStep(1);
                      }}
                      className={`group rounded-xl border p-4 text-left transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400 ${
                        os === choice.key
                          ? "border-cyan-500/50 bg-cyan-500/10"
                          : "border-white/10 bg-white/[0.02] hover:border-cyan-500/30 hover:bg-white/5"
                      }`}
                    >
                      <MonitorSmartphone className="h-5 w-5 text-cyan-400" aria-hidden />
                      <p className="mt-3 font-medium text-white">{choice.name}</p>
                      <p className="mt-0.5 text-xs text-slate-500">{choice.detail}</p>
                    </button>
                  ))}
                </div>
                <p className="mt-5 text-xs text-slate-500">
                  Each machine registers under its own hostname, so give them distinct names —
                  two machines sharing a hostname are treated as one device.
                </p>
              </div>
            )}

            {/* --------------------------------------------- 2. download */}
            {step === 1 && activeDownload && (
              <div>
                <h3 className="text-base font-medium text-white">Download the agent</h3>
                <p className="mt-1 text-sm text-slate-400">
                  One file, nothing to install. Python, pip and git are not needed on that machine.
                </p>

                <a
                  href={activeDownload.url}
                  className="mt-5 flex items-center gap-3 rounded-xl border border-cyan-500/40 bg-cyan-500/15 px-4 py-3.5 font-medium text-cyan-200 transition-colors hover:bg-cyan-500/25 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400"
                >
                  <Download className="h-5 w-5 shrink-0" aria-hidden />
                  <span className="min-w-0">
                    <span className="block">Download for {activeDownload.label}</span>
                    <span className="block truncate font-mono text-xs font-normal text-cyan-300/70">
                      {activeDownload.filename}
                    </span>
                  </span>
                </a>

                <p className="mt-3 text-xs text-slate-500">
                  Downloading on this machine? Move the file to the machine you are adding — it
                  carries no account details, so copying it is safe.
                </p>

                <button
                  onClick={() => setShowSource(v => !v)}
                  className="mt-5 inline-flex items-center gap-2 text-xs text-slate-400 underline underline-offset-4 transition-colors hover:text-cyan-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400"
                >
                  <Terminal className="h-3.5 w-3.5" aria-hidden />
                  {showSource ? "Hide the source install" : "Rather run it from source?"}
                </button>
                {showSource && (
                  <div className="mt-4 rounded-xl border border-white/10 bg-black/20 p-4">
                    <SetupGuide />
                  </div>
                )}
              </div>
            )}

            {/* ------------------------------------------------- 3. link */}
            {step === 2 && (
              <div>
                <h3 className="text-base font-medium text-white">Run it, and type in this code</h3>
                <p className="mt-1 text-sm text-slate-400">
                  On the machine you are adding, run the file you downloaded. It asks for a code.
                </p>

                {codeError && (
                  <div className="mt-4 flex items-start gap-2 rounded-xl border border-red-500/20 bg-red-500/10 p-3">
                    <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-400" aria-hidden />
                    <p className="text-sm text-red-300">{codeError}</p>
                  </div>
                )}

                {/* The hero. Everything else on this screen is support. */}
                <div className="mt-5 rounded-2xl border border-cyan-500/25 bg-cyan-500/[0.07] px-6 py-7 text-center">
                  {requesting && !enrollment ? (
                    <div className="flex items-center justify-center gap-2 py-3 text-slate-400">
                      <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                      <span className="text-sm">Creating a code…</span>
                    </div>
                  ) : enrollment ? (
                    <>
                      <p
                        className={`font-mono text-[2.1rem] leading-none tracking-[0.28em] sm:text-[2.75rem] ${
                          expired ? "text-slate-600 line-through" : "text-cyan-300"
                        }`}
                        aria-label={`Enrolment code ${enrollment.formatted_code.split("").join(" ")}`}
                      >
                        {enrollment.formatted_code}
                      </p>
                      <div className="mt-4 flex flex-wrap items-center justify-center gap-3">
                        {expired ? (
                          <span className="text-sm text-slate-400">This code has expired.</span>
                        ) : (
                          <span
                            className={`font-mono text-xs ${
                              secondsLeft < 120 ? "text-amber-400" : "text-slate-500"
                            }`}
                          >
                            expires in {mmss}
                          </span>
                        )}
                        <CopyButton value={enrollment.code} label="Copy code" />
                        <button
                          onClick={() => {
                            setEnrollment(null);
                            void requestCode();
                          }}
                          className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-slate-300 transition-colors hover:border-cyan-500/40 hover:text-cyan-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400"
                        >
                          <RefreshCw className="h-3.5 w-3.5" aria-hidden />
                          New code
                        </button>
                      </div>
                    </>
                  ) : null}
                </div>

                <div className="mt-5">
                  <p className="text-xs text-slate-400">
                    Double-click the file and it will ask for the code. From a terminal:
                  </p>
                  <div className="mt-2 flex items-center gap-2 rounded-lg border border-white/10 bg-black/30 px-3 py-2">
                    <code className="min-w-0 flex-1 truncate font-mono text-xs text-slate-300">
                      {runCommand}
                    </code>
                    <CopyButton value={runCommand} label="Copy" />
                  </div>
                </div>
              </div>
            )}

            {/* ------------------------------------------------- 4. done */}
            {step === 3 && (
              <div className="py-2 text-center" aria-live="polite">
                {newDevice ? (
                  <motion.div
                    initial={reduceMotion ? false : { scale: 0.94, opacity: 0 }}
                    animate={{ scale: 1, opacity: 1 }}
                    transition={{ type: "spring", stiffness: 300, damping: 20 }}
                  >
                    <span className="mx-auto grid h-14 w-14 place-items-center rounded-full border border-green-500/30 bg-green-500/15">
                      <Check className="h-7 w-7 text-green-400" aria-hidden />
                    </span>
                    <h3 className="mt-4 text-lg font-medium text-white">
                      {newDevice.hostname} is reporting
                    </h3>
                    <p className="mt-1 text-sm text-slate-400">
                      It is on your Devices page now. Telemetry arrives every minute.
                    </p>
                    <div className="mt-6 flex flex-wrap justify-center gap-3">
                      <button
                        onClick={() => {
                          setStep(0);
                          setOs(null);
                          setEnrollment(null);
                          setNewDevice(null);
                          baseline.current.add(newDevice.id);
                        }}
                        className="rounded-xl border border-white/10 px-4 py-2 text-sm text-slate-300 transition-colors hover:border-cyan-500/40 hover:text-cyan-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400"
                      >
                        Add another
                      </button>
                      <button
                        onClick={onClose}
                        className="rounded-xl bg-cyan-500/20 px-4 py-2 text-sm font-medium text-cyan-300 ring-1 ring-cyan-500/40 transition-colors hover:bg-cyan-500/30 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400"
                      >
                        Done
                      </button>
                    </div>
                  </motion.div>
                ) : (
                  <>
                    <span className="relative mx-auto grid h-14 w-14 place-items-center">
                      <span className="absolute inset-0 rounded-full border border-cyan-500/20 motion-safe:animate-ping" aria-hidden />
                      <Server className="h-7 w-7 text-cyan-400" aria-hidden />
                    </span>
                    <h3 className="mt-4 text-lg font-medium text-white">
                      Waiting for the machine to check in
                    </h3>
                    <p className="mx-auto mt-1 max-w-sm text-sm text-slate-400">
                      Once the agent accepts the code it registers itself, usually within a few
                      seconds. Leave this open — it updates on its own.
                    </p>
                    <p className="mt-6 text-xs text-slate-500">
                      Nothing yet? Check the agent window for a message, and that the code has
                      not expired.
                    </p>
                  </>
                )}
              </div>
            )}
          </div>

          {/* Footer */}
          {step < 3 && (
            <div className="flex items-center justify-between gap-3 border-t border-white/10 px-6 py-4">
              <button
                onClick={() => (step === 0 ? onClose() : setStep(s => s - 1))}
                className="inline-flex items-center gap-1.5 rounded-xl px-3 py-2 text-sm text-slate-400 transition-colors hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400"
              >
                {step > 0 && <ChevronLeft className="h-4 w-4" aria-hidden />}
                {step === 0 ? "Cancel" : "Back"}
              </button>
              <button
                onClick={() => setStep(s => s + 1)}
                disabled={step === 0 ? !os : step === 2 ? !enrollment || expired : false}
                className="rounded-xl bg-cyan-500/20 px-4 py-2 text-sm font-medium text-cyan-300 ring-1 ring-cyan-500/40 transition-colors hover:bg-cyan-500/30 disabled:cursor-not-allowed disabled:opacity-40 focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400"
              >
                {step === 2 ? "I have entered the code" : "Continue"}
              </button>
            </div>
          )}
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
}
