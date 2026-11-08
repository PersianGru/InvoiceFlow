import type { Metadata } from "next";
import type React from "react";
import "./globals.css";

import { PwaRegister } from "@/components/pwa-register";
import { BASE_PATH } from "@/lib/api";
import { AppProvider } from "@/lib/app-context";

export const metadata: Metadata = {
  title: "InvoiceFlow",
  description: "Automated invoice intake and processing for accounting firms.",
  manifest: `${BASE_PATH}/manifest.webmanifest`,
  appleWebApp: { capable: true, statusBarStyle: "default", title: "InvoiceFlow" },
  icons: { icon: `${BASE_PATH}/icons/icon-192.png`, apple: `${BASE_PATH}/icons/apple-touch-icon.png` },
};

export const viewport = { themeColor: "#2563eb", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="h-full antialiased" suppressHydrationWarning>
      <body className="min-h-full flex flex-col bg-neutral-50 text-neutral-900 dark:bg-neutral-950 dark:text-neutral-100">
        <AppProvider>{children}</AppProvider>
        <PwaRegister />
      </body>
    </html>
  );
}
