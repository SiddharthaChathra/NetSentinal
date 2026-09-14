"use client";
import dynamic from "next/dynamic";
export default dynamic(() => import("@/components/AmbientBackground"), { ssr: false });
