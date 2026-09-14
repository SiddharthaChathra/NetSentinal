"use client";

import { useState, useMemo, useRef, useEffect, Suspense } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Mail, Lock, Shield, Activity, AlertCircle,
  User, Phone, Calendar, ChevronDown, Eye, EyeOff, Check, ShieldCheck, Search, X
} from "lucide-react";
import { supabase } from "@/lib/supabase";
import { useAuth } from "@/context/AuthContext";

// --- Country Codes Data ---
const COUNTRY_CODES = [
  { code: "+1", country: "US", name: "United States", flag: "🇺🇸" },
  { code: "+1", country: "CA", name: "Canada", flag: "🇨🇦" },
  { code: "+44", country: "GB", name: "United Kingdom", flag: "🇬🇧" },
  { code: "+91", country: "IN", name: "India", flag: "🇮🇳" },
  { code: "+61", country: "AU", name: "Australia", flag: "🇦🇺" },
  { code: "+49", country: "DE", name: "Germany", flag: "🇩🇪" },
  { code: "+33", country: "FR", name: "France", flag: "🇫🇷" },
  { code: "+81", country: "JP", name: "Japan", flag: "🇯🇵" },
  { code: "+86", country: "CN", name: "China", flag: "🇨🇳" },
  { code: "+55", country: "BR", name: "Brazil", flag: "🇧🇷" },
  { code: "+7", country: "RU", name: "Russia", flag: "🇷🇺" },
  { code: "+82", country: "KR", name: "South Korea", flag: "🇰🇷" },
  { code: "+39", country: "IT", name: "Italy", flag: "🇮🇹" },
  { code: "+34", country: "ES", name: "Spain", flag: "🇪🇸" },
  { code: "+52", country: "MX", name: "Mexico", flag: "🇲🇽" },
  { code: "+31", country: "NL", name: "Netherlands", flag: "🇳🇱" },
  { code: "+46", country: "SE", name: "Sweden", flag: "🇸🇪" },
  { code: "+47", country: "NO", name: "Norway", flag: "🇳🇴" },
  { code: "+45", country: "DK", name: "Denmark", flag: "🇩🇰" },
  { code: "+41", country: "CH", name: "Switzerland", flag: "🇨🇭" },
  { code: "+48", country: "PL", name: "Poland", flag: "🇵🇱" },
  { code: "+43", country: "AT", name: "Austria", flag: "🇦🇹" },
  { code: "+32", country: "BE", name: "Belgium", flag: "🇧🇪" },
  { code: "+351", country: "PT", name: "Portugal", flag: "🇵🇹" },
  { code: "+353", country: "IE", name: "Ireland", flag: "🇮🇪" },
  { code: "+358", country: "FI", name: "Finland", flag: "🇫🇮" },
  { code: "+64", country: "NZ", name: "New Zealand", flag: "🇳🇿" },
  { code: "+65", country: "SG", name: "Singapore", flag: "🇸🇬" },
  { code: "+852", country: "HK", name: "Hong Kong", flag: "🇭🇰" },
  { code: "+971", country: "AE", name: "UAE", flag: "🇦🇪" },
  { code: "+966", country: "SA", name: "Saudi Arabia", flag: "🇸🇦" },
  { code: "+972", country: "IL", name: "Israel", flag: "🇮🇱" },
  { code: "+90", country: "TR", name: "Turkey", flag: "🇹🇷" },
  { code: "+27", country: "ZA", name: "South Africa", flag: "🇿🇦" },
  { code: "+234", country: "NG", name: "Nigeria", flag: "🇳🇬" },
  { code: "+254", country: "KE", name: "Kenya", flag: "🇰🇪" },
  { code: "+20", country: "EG", name: "Egypt", flag: "🇪🇬" },
  { code: "+62", country: "ID", name: "Indonesia", flag: "🇮🇩" },
  { code: "+60", country: "MY", name: "Malaysia", flag: "🇲🇾" },
  { code: "+66", country: "TH", name: "Thailand", flag: "🇹🇭" },
  { code: "+84", country: "VN", name: "Vietnam", flag: "🇻🇳" },
  { code: "+63", country: "PH", name: "Philippines", flag: "🇵🇭" },
  { code: "+880", country: "BD", name: "Bangladesh", flag: "🇧🇩" },
  { code: "+92", country: "PK", name: "Pakistan", flag: "🇵🇰" },
  { code: "+94", country: "LK", name: "Sri Lanka", flag: "🇱🇰" },
  { code: "+977", country: "NP", name: "Nepal", flag: "🇳🇵" },
  { code: "+54", country: "AR", name: "Argentina", flag: "🇦🇷" },
  { code: "+56", country: "CL", name: "Chile", flag: "🇨🇱" },
  { code: "+57", country: "CO", name: "Colombia", flag: "🇨🇴" },
  { code: "+51", country: "PE", name: "Peru", flag: "🇵🇪" },
];

// --- Password Strength Checker ---
interface PasswordCheck {
  label: string;
  met: boolean;
}

function getPasswordStrength(password: string): { score: number; checks: PasswordCheck[]; label: string; color: string } {
  const checks: PasswordCheck[] = [
    { label: "At least 8 characters", met: password.length >= 8 },
    { label: "Contains uppercase letter", met: /[A-Z]/.test(password) },
    { label: "Contains lowercase letter", met: /[a-z]/.test(password) },
    { label: "Contains a number", met: /[0-9]/.test(password) },
    { label: "Contains special character (!@#$...)", met: /[^A-Za-z0-9]/.test(password) },
  ];
  const score = checks.filter(c => c.met).length;
  const labels = ["Very Weak", "Weak", "Fair", "Good", "Strong", "Excellent"];
  const colors = ["#ef4444", "#f97316", "#eab308", "#84cc16", "#22c55e", "#06b6d4"];
  return { score, checks, label: labels[score], color: colors[score] };
}

// --- Sanitize user input to prevent XSS ---
function sanitize(input: string): string {
  return input.replace(/[<>"'&]/g, (char) => {
    const map: Record<string, string> = { '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;', '&': '&amp;' };
    return map[char] || char;
  });
}

// --- Country Code Dropdown ---
function CountryCodeDropdown({
  selected,
  onSelect,
}: {
  selected: typeof COUNTRY_CODES[0];
  onSelect: (c: typeof COUNTRY_CODES[0]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const filtered = useMemo(() =>
    COUNTRY_CODES.filter(c =>
      c.name.toLowerCase().includes(search.toLowerCase()) ||
      c.code.includes(search) ||
      c.country.toLowerCase().includes(search.toLowerCase())
    ), [search]);

  return (
    <div ref={ref} className="relative z-50">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex items-center justify-between gap-1.5 rounded-xl border border-white/10 bg-black/20 px-3 py-2.5 text-sm text-white hover:border-cyan-500/30 transition-colors min-w-[110px]"
      >
        <span className="text-base">{selected.flag}</span>
        <span className="text-slate-300">{selected.code}</span>
        <ChevronDown className={`h-3.5 w-3.5 text-slate-500 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.15 }}
            className="absolute left-0 top-full mt-1 z-50 w-72 rounded-xl border border-white/10 bg-[#0d1424] shadow-2xl overflow-hidden"
          >
            <div className="p-2 border-b border-white/10">
              <div className="relative">
                <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" />
                <input
                  autoFocus
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search country..."
                  className="w-full rounded-lg border border-white/10 bg-black/30 py-2 pl-8 pr-3 text-xs text-white placeholder:text-slate-600 focus:border-cyan-500/50 focus:outline-none"
                />
              </div>
            </div>
            <div className="max-h-52 overflow-y-auto overscroll-contain custom-scrollbar">
              {filtered.length === 0 && (
                <p className="p-3 text-center text-xs text-slate-500">No countries found</p>
              )}
              {filtered.map((c) => (
                <button
                  key={`${c.country}-${c.code}`}
                  type="button"
                  onClick={() => { onSelect(c); setOpen(false); setSearch(""); }}
                  className={`flex w-full items-center gap-3 px-3 py-2 text-sm transition-colors hover:bg-cyan-500/10 ${
                    selected.country === c.country && selected.code === c.code
                      ? "bg-cyan-500/10 text-cyan-400"
                      : "text-slate-300"
                  }`}
                >
                  <span className="text-base">{c.flag}</span>
                  <span className="flex-1 text-left truncate">{c.name}</span>
                  <span className="text-xs text-slate-500 font-mono">{c.code}</span>
                </button>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// --- Main Component ---
function AuthContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const redirectPath = searchParams.get("redirect") || "/";
  const { user } = useAuth();
  // Arriving from a password-recovery email: Supabase creates a temporary
  // session so the user can set a new password. Don't bounce them away.
  const resetMode = searchParams.get("reset") === "1";
  const [forgotMode, setForgotMode] = useState(false);
  const [newPassword, setNewPassword] = useState("");
  const [newPasswordConfirm, setNewPasswordConfirm] = useState("");

  useEffect(() => {
    if (user && !resetMode) {
      router.replace(redirectPath);
    }
  }, [user, router, redirectPath, resetMode]);

  // Landing here from the confirmation email. supabase-js exchanges the
  // code in the URL for a session automatically; the effect above then
  // redirects. If the link was bad/expired Supabase puts the reason in the
  // URL hash instead — show it rather than a blank form.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const hash = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    const linkError = hash.get("error_description") || hash.get("error");
    if (linkError) {
      setError(decodeURIComponent(linkError.replace(/\+/g, " ")));
      return;
    }
    if (searchParams.get("confirmed") === "1") {
      setSuccess("Email confirmed — signing you in…");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [isLogin, setIsLogin] = useState(true);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [phone, setPhone] = useState("");
  const [dob, setDob] = useState("");
  const [countryCode, setCountryCode] = useState(COUNTRY_CODES[3]); // Default India +91
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [agreedToTerms, setAgreedToTerms] = useState(false);

  const strength = getPasswordStrength(password);
  const passwordsMatch = password === confirmPassword && confirmPassword.length > 0;

  // --- Validation ---
  const validateSignup = (): string | null => {
    const trimmedName = fullName.trim();
    if (trimmedName.length < 2) return "Full name must be at least 2 characters.";
    if (!/^[a-zA-Z\s'-]+$/.test(trimmedName)) return "Name can only contain letters, spaces, hyphens and apostrophes.";
    if (!email.includes("@") || !email.includes(".")) return "Please enter a valid email address.";
    
    if (phone) {
      if (phone.length < 6 || phone.length > 15) return "Phone number must be between 6 and 15 digits.";
      if (!/^\d+$/.test(phone)) return "Phone number must contain only digits.";
    }

    if (dob) {
      const birthDate = new Date(dob);
      const today = new Date();
      let age = today.getFullYear() - birthDate.getFullYear();
      const monthDiff = today.getMonth() - birthDate.getMonth();
      if (monthDiff < 0 || (monthDiff === 0 && today.getDate() < birthDate.getDate())) age--;
      if (age < 13) return "You must be at least 13 years old to create an account.";
      if (age > 120) return "Please enter a valid date of birth.";
    }

    if (strength.score < 3) return "Password is too weak. Please meet at least 3 strength criteria.";
    if (!passwordsMatch) return "Passwords do not match.";
    if (!agreedToTerms) return "You must agree to the Privacy Policy & Terms of Service.";
    return null;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setSuccess(null);

    try {
      if (isLogin) {
        const { error } = await supabase.auth.signInWithPassword({
          email: email.trim().toLowerCase(),
          password,
        });
        if (error) throw error;
        router.push(redirectPath);
      } else {
        // Validate all fields
        const validationError = validateSignup();
        if (validationError) {
          setError(validationError);
          setLoading(false);
          return;
        }

        // Sign up with Supabase
        const { data, error } = await supabase.auth.signUp({
          email: email.trim().toLowerCase(),
          password,
          options: {
            // Without this Supabase sends users to its "Site URL" (localhost
            // by default) after they click the confirmation link.
            emailRedirectTo: `${window.location.origin}/auth?confirmed=1`,
            data: {
              full_name: sanitize(fullName.trim()),
              phone: `${countryCode.code}${phone}`,
              country_code: countryCode.code,
              country: countryCode.country,
              date_of_birth: dob,
            },
          },
        });
        if (error) throw error;
        
        if (data?.session) {
          router.push(redirectPath);
        } else {
          setSuccess("Account created! Check your email for a confirmation link, then sign in.");
        }
      }
    } catch (err: any) {
      let msg = "An unexpected error occurred.";
      const raw = err.message?.toLowerCase() || "";
      if (raw.includes("failed to fetch") || raw.includes("network error")) {
        msg = "Couldn't reach the sign-in service. Check your connection.";
      } else if (raw.includes("invalid login credentials")) {
        msg = "Invalid email or password. Please try again.";
      } else if (raw.includes("already registered") || raw.includes("user already exists")) {
        msg = "An account with this email already exists.";
      } else if (raw.includes("rate limit")) {
        msg = "Too many attempts right now — please try again in a little while.";
      } else if (raw.includes("email not confirmed")) {
        msg = "Please confirm your email first — check your inbox for the link we sent, then sign in.";
      } else if (raw.includes("is invalid") && raw.includes("email")) {
        msg = "That email address was rejected. Check for typos, or try a different address.";
      } else if (raw.includes("password") && raw.includes("at least")) {
        msg = "Password must be at least 6 characters.";
      } else {
        msg = err.message || msg;
      }
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const handleForgot = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setSuccess(null);
    try {
      const { error } = await supabase.auth.resetPasswordForEmail(email.trim().toLowerCase(), {
        redirectTo: `${window.location.origin}/auth?reset=1`,
      });
      if (error) throw error;
      // Same message whether or not the address exists, so the form can't be
      // used to check who has an account.
      setSuccess("If an account exists for that email, a reset link is on its way. Check your inbox (and spam).");
    } catch (err: any) {
      const raw = err.message?.toLowerCase() || "";
      setError(raw.includes("rate limit")
        ? "Too many requests right now — please try again in a little while."
        : "Couldn't send the reset email. Check the address and try again.");
    } finally {
      setLoading(false);
    }
  };

  const handleReset = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (newPassword.length < 8) { setError("Password must be at least 8 characters."); return; }
    if (newPassword !== newPasswordConfirm) { setError("Passwords do not match."); return; }
    setLoading(true);
    try {
      const { error } = await supabase.auth.updateUser({ password: newPassword });
      if (error) throw error;
      setSuccess("Password updated — taking you to your dashboard…");
      setTimeout(() => router.replace("/"), 1200);
    } catch (err: any) {
      const raw = err.message?.toLowerCase() || "";
      setError(raw.includes("session") || raw.includes("not logged in") || raw.includes("jwt")
        ? "This reset link has expired or was already used. Request a new one."
        : err.message || "Couldn't update the password.");
    } finally {
      setLoading(false);
    }
  };

  const switchMode = () => {
    setIsLogin(!isLogin);
    setError(null);
    setSuccess(null);
    // Reset signup-specific fields
    if (!isLogin) {
      setConfirmPassword("");
      setFullName("");
      setPhone("");
      setDob("");
      setAgreedToTerms(false);
    }
  };

  // Max date for DOB: today. Min date: 120 years ago.
  const today = new Date().toISOString().split("T")[0];
  const minDob = new Date(new Date().setFullYear(new Date().getFullYear() - 120)).toISOString().split("T")[0];

  const inputCls = "w-full rounded-xl border border-white/10 bg-black/20 py-2.5 pl-10 pr-4 text-sm text-white placeholder:text-slate-600 focus:border-cyan-500/50 focus:outline-none focus:ring-1 focus:ring-cyan-500/50 transition-all";

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 20 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 20 }}
        className="w-full max-w-md overflow-hidden rounded-2xl glass-panel shadow-2xl max-h-[92vh] flex flex-col relative z-10"
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-white/10 px-6 py-5 shrink-0">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-cyan-500/20 border border-cyan-500/50 shrink-0">
              <Shield className="h-5 w-5 text-cyan-400" />
            </div>
            <h2 className="text-xl font-bold text-white tracking-tight">
              {resetMode ? "Set a New Password" : forgotMode ? "Reset Password" : isLogin ? "Welcome Back" : "Create Account"}
            </h2>
          </div>
        </div>

        {/* Scrollable Body */}
        <div className="p-6 overflow-y-auto flex-1 custom-scrollbar">
          <p className="mb-6 text-sm text-slate-400 leading-relaxed">
            {resetMode
              ? "Choose a new password for your account."
              : forgotMode
              ? "Enter your email and we'll send you a link to reset your password."
              : isLogin
              ? "Sign in to manage your monitored devices and view historical network health data."
              : "Create an account to save your network diagnostics and connect remote agents."}
          </p>

          {/* Error / Success Messages */}
          {error && (
            <div className="mb-6 flex items-start gap-3 rounded-xl border border-red-500/20 bg-red-500/10 p-3.5 text-red-400">
              <AlertCircle className="h-5 w-5 shrink-0 mt-0.5" />
              <p className="text-sm font-medium leading-relaxed">{error}</p>
            </div>
          )}
          {success && (
            <div className="mb-6 flex items-start gap-3 rounded-xl border border-green-500/20 bg-green-500/10 p-3.5 text-green-400">
              <ShieldCheck className="h-5 w-5 shrink-0 mt-0.5" />
              <p className="text-sm font-medium leading-relaxed">{success}</p>
            </div>
          )}

          {resetMode ? (
            <form onSubmit={handleReset} className="space-y-5">
              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-300">New password</label>
                <div className="relative">
                  <Lock className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
                  <input type="password" required minLength={8} value={newPassword} onChange={(e) => setNewPassword(e.target.value)} className={inputCls} placeholder="At least 8 characters" autoComplete="new-password" />
                </div>
              </div>
              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-300">Confirm new password</label>
                <div className="relative">
                  <Lock className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
                  <input type="password" required value={newPasswordConfirm} onChange={(e) => setNewPasswordConfirm(e.target.value)} className={inputCls} placeholder="••••••••" autoComplete="new-password" />
                </div>
              </div>
              {!user && (
                <p className="text-xs text-amber-400/90">Verifying your reset link… if this doesn't clear in a few seconds, the link may have expired — request a new one.</p>
              )}
              <button type="submit" disabled={loading || !user} className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl bg-cyan-500 py-3 text-sm font-semibold text-slate-900 transition-all hover:bg-cyan-400 disabled:opacity-40 disabled:cursor-not-allowed">
                {loading ? <Activity className="h-5 w-5 animate-spin" /> : "Update password"}
              </button>
            </form>
          ) : forgotMode ? (
            <form onSubmit={handleForgot} className="space-y-5">
              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-300">Email Address</label>
                <div className="relative">
                  <Mail className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
                  <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} className={inputCls} placeholder="you@example.com" autoComplete="email" />
                </div>
              </div>
              <button type="submit" disabled={loading} className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl bg-cyan-500 py-3 text-sm font-semibold text-slate-900 transition-all hover:bg-cyan-400 disabled:opacity-40 disabled:cursor-not-allowed">
                {loading ? <Activity className="h-5 w-5 animate-spin" /> : "Send reset link"}
              </button>
              <button type="button" onClick={() => { setForgotMode(false); setError(null); setSuccess(null); }} className="w-full text-sm text-slate-400 hover:text-white py-1">
                Back to sign in
              </button>
            </form>
          ) : (<>
          <form onSubmit={handleSubmit} className="space-y-5">
            {/* --- SIGNUP-ONLY FIELDS --- */}
            {!isLogin && (
              <>
                {/* Full Name */}
                <div>
                  <label className="mb-1.5 block text-xs font-medium text-slate-300">Full Name</label>
                  <div className="relative">
                    <User className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
                    <input
                      type="text"
                      required
                      value={fullName}
                      onChange={(e) => setFullName(e.target.value)}
                      className={inputCls}
                      placeholder="John Doe"
                      maxLength={100}
                      autoComplete="name"
                    />
                  </div>
                </div>

                {/* Phone with Country Code */}
                <div>
                  <label className="mb-1.5 block text-xs font-medium text-slate-300">Phone Number <span className="text-slate-500 font-normal">(Optional)</span></label>
                  <div className="flex gap-2">
                    <CountryCodeDropdown selected={countryCode} onSelect={setCountryCode} />
                    <div className="relative flex-1">
                      <Phone className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
                      <input
                        type="tel"
                        value={phone}
                        onChange={(e) => setPhone(e.target.value.replace(/\D/g, ""))}
                        className={inputCls}
                        placeholder="9876543210"
                        maxLength={15}
                        autoComplete="tel"
                      />
                    </div>
                  </div>
                </div>

                {/* Date of Birth */}
                <div>
                  <label className="mb-1.5 block text-xs font-medium text-slate-300">Date of Birth <span className="text-slate-500 font-normal">(Optional)</span></label>
                  <div className="relative">
                    <Calendar className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
                    <input
                      type="date"
                      value={dob}
                      onChange={(e) => setDob(e.target.value)}
                      max={today}
                      min={minDob}
                      className={`${inputCls} [color-scheme:dark]`}
                      autoComplete="bday"
                    />
                  </div>
                </div>
              </>
            )}

            {/* --- COMMON FIELDS --- */}
            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-300">Email Address</label>
              <div className="relative">
                <Mail className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
                <input
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className={inputCls}
                  placeholder="you@example.com"
                  autoComplete="email"
                />
              </div>
            </div>

            {/* Password */}
            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-300">Password</label>
              <div className="relative">
                <Lock className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
                <input
                  type={showPassword ? "text" : "password"}
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className={`${inputCls} !pr-10`}
                  placeholder="••••••••"
                  autoComplete={isLogin ? "current-password" : "new-password"}
                />
                <button
                  type="button"
                  tabIndex={-1}
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300 transition-colors p-1"
                >
                  {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>

              {isLogin && (
                <div className="mt-2 text-right">
                  <button type="button" onClick={() => { setForgotMode(true); setError(null); setSuccess(null); }} className="text-xs text-cyan-400 hover:text-cyan-300 hover:underline">
                    Forgot password?
                  </button>
                </div>
              )}

              {/* Password Strength Meter (Signup only) */}
              {!isLogin && password.length > 0 && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: "auto" }}
                  exit={{ opacity: 0, height: 0 }}
                  className="mt-4 space-y-2.5"
                >
                  <div className="flex items-center gap-3">
                    <div className="flex-1 h-1.5 rounded-full bg-white/10 overflow-hidden">
                      <motion.div
                        initial={{ width: 0 }}
                        animate={{ width: `${(strength.score / 5) * 100}%` }}
                        transition={{ duration: 0.3 }}
                        className="h-full rounded-full"
                        style={{ backgroundColor: strength.color }}
                      />
                    </div>
                    <span className="text-[10px] font-bold uppercase tracking-wider whitespace-nowrap" style={{ color: strength.color }}>
                      {strength.label}
                    </span>
                  </div>

                  <div className="grid grid-cols-1 gap-1.5">
                    {strength.checks.map((check) => (
                      <div key={check.label} className="flex items-center gap-2">
                        <div className={`flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full ${check.met ? "bg-green-500/20" : "bg-white/5"}`}>
                          {check.met ? (
                            <Check className="h-2.5 w-2.5 text-green-400" />
                          ) : (
                            <div className="h-1.5 w-1.5 rounded-full bg-slate-600" />
                          )}
                        </div>
                        <span className={`text-[11px] ${check.met ? "text-green-400" : "text-slate-500"}`}>
                          {check.label}
                        </span>
                      </div>
                    ))}
                  </div>
                </motion.div>
              )}
            </div>

            {/* Confirm Password (Signup only) */}
            {!isLogin && (
              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-300">Confirm Password</label>
                <div className="relative">
                  <Lock className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
                  <input
                    type={showConfirm ? "text" : "password"}
                    required
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    className={`${inputCls} !pr-10 ${
                      confirmPassword.length > 0
                        ? passwordsMatch
                          ? "!border-green-500/50 !ring-green-500/30"
                          : "!border-red-500/50 !ring-red-500/30"
                        : ""
                    }`}
                    placeholder="Re-enter password"
                    autoComplete="new-password"
                  />
                  <button
                    type="button"
                    tabIndex={-1}
                    onClick={() => setShowConfirm(!showConfirm)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300 transition-colors p-1"
                  >
                    {showConfirm ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
                {confirmPassword.length > 0 && (
                  <p className={`mt-2 text-xs font-medium flex items-center gap-1 ${passwordsMatch ? "text-green-400" : "text-red-400"}`}>
                    {passwordsMatch ? <><Check className="w-3 h-3" /> Passwords match</> : <><X className="w-3 h-3" /> Passwords do not match</>}
                  </p>
                )}
              </div>
            )}

            {/* Terms & Privacy (Signup only) */}
            {!isLogin && (
              <label className="flex items-start gap-3 cursor-pointer group pt-2">
                <div className="relative mt-0.5 shrink-0">
                  <input
                    type="checkbox"
                    checked={agreedToTerms}
                    onChange={(e) => setAgreedToTerms(e.target.checked)}
                    className="sr-only"
                  />
                  <div
                    className={`h-5 w-5 rounded-md border transition-all flex items-center justify-center ${
                      agreedToTerms
                        ? "bg-cyan-500 border-cyan-400"
                        : "border-white/20 bg-black/20 group-hover:border-cyan-500/40"
                    }`}
                  >
                    {agreedToTerms && <Check className="h-3.5 w-3.5 text-slate-900" />}
                  </div>
                </div>
                <span className="text-xs text-slate-400 leading-relaxed">
                  I agree to the <span className="text-cyan-400 hover:underline cursor-pointer">Privacy Policy</span> &{" "}
                  <span className="text-cyan-400 hover:underline cursor-pointer">Terms of Service</span>.
                  My data is encrypted and never shared with third parties.
                </span>
              </label>
            )}

            {/* Submit Button */}
            <button
              type="submit"
              disabled={loading || (!isLogin && (!agreedToTerms || strength.score < 3 || !passwordsMatch))}
              className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl bg-cyan-500 py-3 text-sm font-semibold text-slate-900 shadow-[0_0_15px_rgba(6,214,214,0.2)] transition-all hover:bg-cyan-400 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-cyan-500 disabled:shadow-none"
            >
              {loading ? (
                <Activity className="h-5 w-5 animate-spin" />
              ) : isLogin ? (
                "Sign In"
              ) : (
                "Create Account"
              )}
            </button>
          </form>

          {/* Data Protection Notice (Signup) */}
          {!isLogin && (
            <div className="mt-6 flex items-start gap-3 rounded-xl border border-cyan-500/10 bg-cyan-500/5 p-4">
              <ShieldCheck className="h-5 w-5 text-cyan-400 shrink-0 mt-0.5" />
              <p className="text-xs text-slate-400 leading-relaxed">
                <span className="text-cyan-400 font-semibold">Data Protection:</span> Your password is hashed with bcrypt and never stored in plaintext.
                Personal data is encrypted at rest (AES-256) by Supabase. We comply with GDPR standards.
                Phone/DOB are stored only in your authenticated profile metadata — never exposed to the public API.
              </p>
            </div>
          )}

          {/* Toggle Login/Signup */}
          <div className="mt-6 text-center text-sm text-slate-400 border-t border-white/10 pt-6">
            {isLogin ? "Don't have an account? " : "Already have an account? "}
            <button
              type="button"
              onClick={switchMode}
              className="font-semibold text-cyan-400 hover:text-cyan-300 hover:underline px-2 py-1 rounded-md focus:outline-none focus:ring-2 focus:ring-cyan-500/50"
            >
              {isLogin ? "Sign up" : "Sign in"}
            </button>
          </div>
          </>)}
        </div>
      </motion.div>
    </div>
  );
}

export default function AuthPage() {
  return (
    <Suspense fallback={<div className="min-h-screen flex items-center justify-center bg-[#0d1424]"><div className="animate-spin rounded-full h-8 w-8 border-b-2 border-cyan-500"></div></div>}>
      <AuthContent />
    </Suspense>
  );
}
