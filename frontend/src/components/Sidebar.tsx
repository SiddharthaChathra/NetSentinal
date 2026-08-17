"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Shield, LogOut, User } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { useState } from "react";
import AuthModal from "./AuthModal";

export default function Sidebar() {
  const pathname = usePathname();
  const { user, signOut, loading } = useAuth();
  const [isAuthModalOpen, setIsAuthModalOpen] = useState(false);

  const navItems = [
    { label: "Overview", href: "/" },
    { label: "Devices", href: "/devices" },
    { label: "Topology", href: "/topology" },
    { label: "Incidents", href: "/incidents" },
    { label: "History", href: "/history" },
    { label: "Reports", href: "/report" },
  ];

  return (
    <>
      <aside className="w-64 border-r border-white/10 bg-white/5 backdrop-blur-xl flex flex-col p-6 h-screen sticky top-0">
        <Link href="/" className="flex items-center gap-3 mb-12">
          <div className="w-8 h-8 rounded bg-cyan-500/20 border border-cyan-500/50 flex items-center justify-center">
            <Shield className="w-5 h-5 text-cyan-400" />
          </div>
          <h1 className="text-xl font-bold tracking-tight text-white">NetSentinel</h1>
        </Link>
        <nav className="flex-1 space-y-2">
          {navItems.map((item) => {
            const active = pathname === item.href;
            return (
              <Link
                key={item.label}
                href={item.href}
                className={`block px-4 py-3 rounded-lg text-sm font-medium transition-colors ${
                  active
                    ? "bg-cyan-500/10 text-cyan-400"
                    : "text-slate-400 hover:text-white hover:bg-white/5"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        {/* Auth Section */}
        <div className="mt-auto pt-6 border-t border-white/10">
          {!loading && user ? (
            <div className="flex flex-col gap-3">
              <div className="flex items-center gap-3 px-2">
                <div className="w-8 h-8 rounded-full bg-slate-800 flex items-center justify-center">
                  <User className="w-4 h-4 text-slate-400" />
                </div>
                <div className="overflow-hidden">
                  <p className="text-sm font-medium text-white truncate">
                    {user.email}
                  </p>
                  <p className="text-xs text-slate-500">Pro Plan</p>
                </div>
              </div>
              <button
                onClick={() => signOut()}
                className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-slate-400 hover:text-white hover:bg-white/5 transition-colors w-full"
              >
                <LogOut className="w-4 h-4" />
                Sign Out
              </button>
            </div>
          ) : (
            <div className="bg-white/5 p-4 rounded-xl border border-white/10">
              <h4 className="text-sm font-semibold text-white mb-1">Save your fleet</h4>
              <p className="text-xs text-slate-400 mb-3">
                Sign in to save devices and 30-day history.
              </p>
              <button
                onClick={() => setIsAuthModalOpen(true)}
                className="w-full py-2 rounded-lg bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 text-sm font-medium hover:bg-cyan-500/30 transition-colors"
              >
                Sign In / Sign Up
              </button>
            </div>
          )}
        </div>
      </aside>
      
      <AuthModal isOpen={isAuthModalOpen} onClose={() => setIsAuthModalOpen(false)} />
    </>
  );
}
