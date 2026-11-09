"use client";
import { BackupsPanel } from "@/components/backups-panel";
import type { SectionView } from "@/components/settings-section";
import { Spinner } from "@/components/ui";
import { get } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { useFetch } from "@/lib/use-fetch";

export default function FirmBackupsPage() {
  const { t } = useApp();
  const settings = useFetch<{ sections: Record<string, SectionView> }>(() => get("/api/v1/firm-settings"), []);
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">{t("nav.settings.backups")}</h1>
      {settings.data ? <BackupsPanel scope="firm" section={settings.data.sections.backup} onSectionSaved={settings.reload} /> : <Spinner label={t("common.loading")} />}
    </div>
  );
}
