"use client";

import dynamic from "next/dynamic";

const AssistantClient = dynamic(() => import("./AssistantClient"), {
  ssr: false,
});

export default function Page() {
  return <AssistantClient />;
}
