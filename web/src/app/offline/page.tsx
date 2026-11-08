"use client";
import { useApp } from "@/lib/app-context";

export default function OfflinePage() {
  const { t } = useApp();
  return (
    <main className="mx-auto max-w-md p-6 text-center">
      <h1 className="mb-2 text-xl font-semibold">{t("offline.title")}</h1>
      <p className="text-sm text-neutral-600">{t("offline.body")}</p>
    </main>
  );
}
