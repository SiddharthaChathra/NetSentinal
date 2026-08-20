import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { AuthProvider } from "@/context/AuthContext";
import BackendStatusBanner from "@/components/BackendStatusBanner";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });
const jetbrains = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono" });

export const metadata: Metadata = {
  title: "NetSentinel | Network Observability",
  description: "Advanced Network Observability Platform",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body
        className={`${inter.variable} ${jetbrains.variable} antialiased bg-[#060a13] text-slate-200 min-h-screen selection:bg-cyan-500/30`}
      >
        <AuthProvider>
          <BackendStatusBanner />
          {children}
        </AuthProvider>
      </body>
    </html>
  );
}
