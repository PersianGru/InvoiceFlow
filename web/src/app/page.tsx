"use client";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useApp } from "@/lib/app-context";
import { Spinner } from "@/components/ui";

export default function Home() {
  const { ready, user, t } = useApp();
  const router = useRouter();
  useEffect(() => {
    if (!ready) return;
    router.replace(user ? (user.role === "client_admin" || user.role === "client_user" ? "/my" : "/inbox") : "/login");
  }, [ready, user, router]);
  return (
    <main className="flex flex-1 items-center justify-center p-8">
      <Spinner label={t("common.loading")} />
    </main>
  );
}
