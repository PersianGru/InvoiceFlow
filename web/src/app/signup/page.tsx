"use client";
// The public sign-up is gone: the first visitor sets up the platform with the setup code,
// firms are created by the platform admin. Old links land on the right page.
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { Spinner } from "@/components/ui";
import { get } from "@/lib/api";
import { useApp } from "@/lib/app-context";

export default function SignupPage() {
  const { t } = useApp();
  const router = useRouter();
  useEffect(() => {
    void get<{ setup_required: boolean }>("/api/v1/setup/status")
      .then((s) => router.replace(s.setup_required ? "/setup" : "/login"))
      .catch(() => router.replace("/login"));
  }, [router]);
  return (
    <main className="flex flex-1 items-center justify-center p-8">
      <Spinner label={t("common.loading")} />
    </main>
  );
}
