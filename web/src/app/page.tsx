"use client";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { Spinner } from "@/components/ui";
import { get } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { homeFor } from "@/lib/countries";

export default function Home() {
  const { ready, user, t } = useApp();
  const router = useRouter();
  useEffect(() => {
    if (!ready) return;
    if (user) {
      router.replace(homeFor(user));
      return;
    }
    void get<{ setup_required: boolean }>("/api/v1/setup/status")
      .then((s) => router.replace(s.setup_required ? "/setup" : "/login"))
      .catch(() => router.replace("/login"));
  }, [ready, user, router]);
  return (
    <main className="flex flex-1 items-center justify-center p-8">
      <Spinner label={t("common.loading")} />
    </main>
  );
}
