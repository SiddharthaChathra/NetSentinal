"use client";

import dynamic from "next/dynamic";

const DashboardClient = dynamic(() => import("./DashboardClient"), {
  ssr: false,
});

export default function Page() {
  return <DashboardClient />;
}
