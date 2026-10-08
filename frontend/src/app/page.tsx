"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useFeatures } from "@/lib/features";

export default function Home() {
  const router = useRouter();
  const { loading, workspaceEnabled, preferences } = useFeatures();
  useEffect(() => {
    if (loading) return;
    router.replace(workspaceEnabled && preferences?.effective_experience === "workspace" ? "/workspace" : "/dashboard");
  }, [loading, workspaceEnabled, preferences, router]);
  return null;
}
