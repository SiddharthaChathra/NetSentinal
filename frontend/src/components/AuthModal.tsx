"use client";

import { useState, useMemo, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  X, Mail, Lock, Shield, Activity, AlertCircle,
  User, Phone, Calendar, ChevronDown, Eye, EyeOff,
  Check, ShieldCheck, Search
} from "lucide-react";
import { supabase } from "@/lib/supabase";

interface AuthModalProps {
  isOpen: boolean;
  onClose: () => void;
}

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
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 rounded-lg border border-white/10 bg-black/20 px-3 py-2.5 text-sm text-white hover:border-cyan-500/30 transition-colors min-w-[110px]"
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
export default function AuthModal({ isOpen, onClose }: AuthModalProps) {
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

  if (!isOpen) return null;

  const strength = getPasswordStrength(password);
  const passwordsMatch = password === confirmPassword && confirmPassword.length > 0;

  // --- Validation ---
  const validateSignup = (): string | null => {
    const trimmedName = fullName.trim();
    if (trimmedName.length < 2) return "Full name must be at least 2 characters.";
    if (!/^[a-zA-Z\s'-]+$/.test(trimmedName)) return "Name can only contain letters, spaces, hyphens and apostrophes.";
    if (!email.includes("@") || !email.includes(".")) return "Please enter a valid email address.";
    if (phone.length < 6 || phone.length > 15) return "Phone number must be between 6 and 15 digits.";
    if (!/^\d+$/.test(phone)) return "Phone number must contain only digits.";
    if (!dob) return "Date of birth is required.";

    // Age check: must be at least 13
    const birthDate = new Date(dob);
    const today = new Date();
    let age = today.getFullYear() - birthDate.getFullYear();
    const monthDiff = today.getMonth() - birthDate.getMonth();
    if (monthDiff < 0 || (monthDiff === 0 && today.getDate() < birthDate.getDate())) age--;
    if (age < 13) return "You must be at least 13 years old to create an account.";
    if (age > 120) return "Please enter a valid date of birth.";

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
          email: sanitize(email.trim()),
          password,
        });
        if (error) throw error;
        onClose();
      } else {
        // Validate all fields
        const validationError = validateSignup();
        if (validationError) {
          setError(validationError);
          setLoading(false);
          return;
        }

        // Sign up with Supabase — profile metadata stored in user_metadata (encrypted at rest by Supabase)
        const { data, error } = await supabase.auth.signUp({
          email: sanitize(email.trim()),
          password,
          options: {
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
          // If auto-confirm is enabled in Supabase, user gets logged in instantly
          onClose();
        } else {
          setSuccess("Account created! Check your email for a confirmation link, then sign in.");
        }
      }
    } catch (err: any) {
      setError(err.message || "An unexpected error occurred.");
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

  const inputCls = "w-full rounded-lg border border-white/10 bg-black/20 py-2.5 pl-10 pr-4 text-sm text-white placeholder:text-slate-600 focus:border-cyan-500/50 focus:outline-none focus:ring-1 focus:ring-cyan-500/50 transition-all";

  return (
    <AnimatePresence>
      {isOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 20 }}
            className="w-full max-w-md overflow-hidden rounded-2xl border border-white/10 bg-[#0a0f1c] shadow-2xl max-h-[92vh] flex flex-col"
          >
            {/* Header */}
            <div className="flex items-center justify-between border-b border-white/10 px-6 py-4 shrink-0">
              <div className="flex items-center gap-2">
                <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-cyan-500/20 border border-cyan-500/50">
                  <Shield className="h-4 w-4 text-cyan-400" />
                </div>
                <h2 className="text-lg font-bold text-white">
                  {isLogin ? "Welcome Back" : "Create Account"}
                </h2>
              </div>
              <button
                onClick={onClose}
                className="rounded-lg p-2 text-slate-400 transition-colors hover:bg-white/5 hover:text-white"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            {/* Scrollable Body */}
            <div className="p-6 overflow-y-auto flex-1 custom-scrollbar">
              <p className="mb-5 text-sm text-slate-400">
                {isLogin
                  ? "Sign in to manage your monitored devices and view historical network health data."
                  : "Create an account to save your network diagnostics and connect remote agents."}
              </p>

              {/* Error / Success Messages */}
              {error && (
                <div className="mb-4 flex items-start gap-3 rounded-lg border border-red-500/20 bg-red-500/10 p-3 text-red-400">
                  <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
                  <p className="text-xs font-medium">{error}</p>
                </div>
              )}
              {success && (
                <div className="mb-4 flex items-start gap-3 rounded-lg border border-green-500/20 bg-green-500/10 p-3 text-green-400">
                  <ShieldCheck className="h-4 w-4 shrink-0 mt-0.5" />
                  <p className="text-xs font-medium">{success}</p>
                </div>
              )}

              <form onSubmit={handleSubmit} className="space-y-4">

                {/* --- SIGNUP-ONLY FIELDS --- */}
                {!isLogin && (
                  <>
                    {/* Full Name */}
                    <div>
                      <label className="mb-1 block text-xs font-medium text-slate-300">Full Name</label>
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
                      <label className="mb-1 block text-xs font-medium text-slate-300">Phone Number</label>
                      <div className="flex gap-2">
                        <CountryCodeDropdown selected={countryCode} onSelect={setCountryCode} />
                        <div className="relative flex-1">
                          <Phone className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
                          <input
                            type="tel"
                            required
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
                      <label className="mb-1 block text-xs font-medium text-slate-300">Date of Birth</label>
                      <div className="relative">
                        <Calendar className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
                        <input
                          type="date"
                          required
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

                {/* Email */}
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-300">Email Address</label>
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
                  <label className="mb-1 block text-xs font-medium text-slate-300">Password</label>
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
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300 transition-colors"
                    >
                      {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </button>
                  </div>

                  {/* Password Strength Meter (Signup only) */}
                  {!isLogin && password.length > 0 && (
                    <motion.div
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: "auto" }}
                      exit={{ opacity: 0, height: 0 }}
                      className="mt-3 space-y-2"
                    >
                      {/* Strength Bar */}
                      <div className="flex items-center gap-2">
                        <div className="flex-1 h-1.5 rounded-full bg-white/10 overflow-hidden">
                          <motion.div
                            initial={{ width: 0 }}
                            animate={{ width: `${(strength.score / 5) * 100}%` }}
                            transition={{ duration: 0.3 }}
                            className="h-full rounded-full"
                            style={{ backgroundColor: strength.color }}
                          />
                        </div>
                        <span className="text-[10px] font-bold uppercase tracking-wider" style={{ color: strength.color }}>
                          {strength.label}
                        </span>
                      </div>

                      {/* Checklist */}
                      <div className="grid grid-cols-1 gap-1">
                        {strength.checks.map((check) => (
                          <div key={check.label} className="flex items-center gap-2">
                            <div className={`flex h-3.5 w-3.5 items-center justify-center rounded-full ${check.met ? "bg-green-500/20" : "bg-white/5"}`}>
                              {check.met ? (
                                <Check className="h-2.5 w-2.5 text-green-400" />
                              ) : (
                                <div className="h-1.5 w-1.5 rounded-full bg-slate-600" />
                              )}
                            </div>
                            <span className={`text-[10px] ${check.met ? "text-green-400" : "text-slate-500"}`}>
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
                    <label className="mb-1 block text-xs font-medium text-slate-300">Confirm Password</label>
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
                        className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300 transition-colors"
                      >
                        {showConfirm ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                      </button>
                    </div>
                    {confirmPassword.length > 0 && (
                      <p className={`mt-1 text-[10px] font-medium ${passwordsMatch ? "text-green-400" : "text-red-400"}`}>
                        {passwordsMatch ? "✓ Passwords match" : "✗ Passwords do not match"}
                      </p>
                    )}
                  </div>
                )}

                {/* Terms & Privacy (Signup only) */}
                {!isLogin && (
                  <label className="flex items-start gap-3 cursor-pointer group mt-2">
                    <div className="relative mt-0.5">
                      <input
                        type="checkbox"
                        checked={agreedToTerms}
                        onChange={(e) => setAgreedToTerms(e.target.checked)}
                        className="sr-only"
                      />
                      <div
                        className={`h-4 w-4 rounded border transition-all flex items-center justify-center ${
                          agreedToTerms
                            ? "bg-cyan-500 border-cyan-400"
                            : "border-white/20 bg-black/20 group-hover:border-cyan-500/40"
                        }`}
                      >
                        {agreedToTerms && <Check className="h-3 w-3 text-slate-900" />}
                      </div>
                    </div>
                    <span className="text-[11px] text-slate-400 leading-relaxed">
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
                  className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg bg-cyan-500 py-2.5 text-sm font-semibold text-slate-900 shadow-[0_0_15px_rgba(6,214,214,0.2)] transition-all hover:bg-cyan-400 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-cyan-500"
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
                <div className="mt-4 flex items-start gap-2 rounded-lg border border-cyan-500/10 bg-cyan-500/5 p-3">
                  <ShieldCheck className="h-4 w-4 text-cyan-400 shrink-0 mt-0.5" />
                  <p className="text-[10px] text-slate-400 leading-relaxed">
                    <span className="text-cyan-400 font-semibold">Data Protection:</span> Your password is hashed with bcrypt and never stored in plaintext.
                    Personal data is encrypted at rest (AES-256) by Supabase. We comply with GDPR standards.
                    Phone/DOB are stored only in your authenticated profile metadata — never exposed to the public API.
                  </p>
                </div>
              )}

              {/* Toggle Login/Signup */}
              <div className="mt-5 text-center text-sm text-slate-400">
                {isLogin ? "Don't have an account? " : "Already have an account? "}
                <button
                  onClick={switchMode}
                  className="font-semibold text-cyan-400 hover:text-cyan-300 hover:underline"
                >
                  {isLogin ? "Sign up" : "Sign in"}
                </button>
              </div>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
}
