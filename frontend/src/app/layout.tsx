import type { Metadata } from "next";
import { Suspense } from "react";
import Script from "next/script";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { AuthProvider } from "@/context/AuthContext";
import BackendStatusBanner from "@/components/BackendStatusBanner";
import AmbientBackground from "@/components/AmbientBackgroundLoader";
import OnboardingTour from "@/components/OnboardingTour";
import AuthGate from "@/components/AuthGate";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });
const jetbrains = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono" });

const BACKEND_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "https://netsentinal.onrender.com";

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
      <head>
        {/* Start the (free-tier, spin-down) backend booting before any JS is
            hydrated: preconnect opens DNS+TLS, the inline script sends the
            first real request. Both are fire-and-forget. */}
        <link rel="preconnect" href={BACKEND_URL} crossOrigin="anonymous" />
        <Script
          id="netsentinel-warm-backend"
          strategy="beforeInteractive"
          dangerouslySetInnerHTML={{
            __html: `try{fetch(${JSON.stringify(BACKEND_URL + "/api/health")},{cache:"no-store",keepalive:true}).catch(function(){})}catch(e){}`,
          }}
        />
      </head>
      <body
        className={`${inter.variable} ${jetbrains.variable} antialiased bg-[#060a13] text-slate-200 min-h-screen selection:bg-cyan-500/30`}
      >
        <AuthProvider>
          <AmbientBackground />
          {/* Outside the gate on purpose: the sign-in page is now the first
              thing every visitor loads, so the cold-start banner has to be
              able to render there too. */}
          <BackendStatusBanner />
          <OnboardingTour />
          {/* AuthGate reads the query string (?redirect=), which Next requires
              to sit under a Suspense boundary. */}
          <Suspense fallback={null}>
            <AuthGate>{children}</AuthGate>
          </Suspense>
        </AuthProvider>
      </body>
    </html>
  );
}
