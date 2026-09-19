"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Shield, LogOut, User, Menu, X } from "lucide-react";
import { useAuth } from "@/context/AuthContext";

export default function Sidebar() {
  const pathname = usePathname();
  const { user, signOut, loading } = useAuth();

  const navItems = [
    { label: "Overview", href: "/" },
    { label: "Getting Started", href: "/getting-started" },
    { label: "Backup Readiness", href: "/backup" },
    { label: "Devices", href: "/devices" },
    { label: "Topology", href: "/topology" },
    { label: "Incidents", href: "/incidents" },
    { label: "History", href: "/history" },
    { label: "Reports", href: "/report" },
  ];

  const [isOpen, setIsOpen] = useState(false);
  const router = useRouter();

  const handleSignOut = () => {
    signOut();
    router.push("/");
  };

  return (
    <>
      {/* Mobile Top Bar */}
      <div className="md:hidden flex items-center justify-between p-4 bg-slate-900/95 border-b border-white/10 sticky top-0 z-40">
        <Link href="/" className="flex items-center gap-2">
          <Shield className="w-5 h-5 text-cyan-400" />
          <h1 className="font-bold text-white">NetSentinel</h1>
        </Link>
        <button onClick={() => setIsOpen(true)} className="text-white p-2">
          <Menu className="w-6 h-6" />
        </button>
      </div>

      {/* Mobile Overlay */}
      {isOpen && (
        <div 
          className="md:hidden fixed inset-0 bg-black/60 z-40 backdrop-blur-sm" 
          onClick={() => setIsOpen(false)} 
        />
      )}

      {/* Sidebar */}
      <aside className={`fixed inset-y-0 left-0 z-50 w-64 border-r border-white/10 bg-slate-900/95 md:bg-white/5 backdrop-blur-xl flex flex-col p-6 h-screen shrink-0 transform transition-transform duration-300 md:relative md:translate-x-0 ${isOpen ? "translate-x-0" : "-translate-x-full"}`}>
        <div className="flex items-center justify-between mb-12">
          <Link href="/" className="flex items-center gap-3" onClick={() => setIsOpen(false)}>
            <div className="w-8 h-8 rounded bg-cyan-500/20 border border-cyan-500/50 flex items-center justify-center">
              <Shield className="w-5 h-5 text-cyan-400" />
            </div>
            <h1 className="text-xl font-bold tracking-tight text-white">NetSentinel</h1>
          </Link>
          <button onClick={() => setIsOpen(false)} className="md:hidden text-slate-400 hover:text-white">
            <X className="w-6 h-6" />
          </button>
        </div>
      <nav className="flex-1 space-y-2">
        {navItems.map((item) => {
          const active = pathname === item.href;
          return (
            <Link
              key={item.label}
              href={item.href}
              onClick={() => setIsOpen(false)}
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
              </div>
            </div>
            <button
              onClick={handleSignOut}
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
            <Link
              href="/auth"
              className="flex items-center justify-center w-full py-2 rounded-lg bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 text-sm font-medium hover:bg-cyan-500/30 transition-colors"
            >
              Sign In / Sign Up
            </Link>
          </div>
        )}
      </div>
    </aside>
    </>
  );
}
