import type { MetadataRoute } from "next";

import { BASE_PATH } from "@/lib/api";

// Served at <base path>/manifest.webmanifest; every path below carries the base path.
export default function manifest(): MetadataRoute.Manifest {
  const b = BASE_PATH;
  return {
    name: "InvoiceFlow",
    short_name: "InvoiceFlow",
    description: "Send invoices to your accountant and follow their status.",
    id: `${b}/my`,
    start_url: `${b}/my`,
    scope: `${b}/`,
    display: "standalone",
    background_color: "#fafafa",
    theme_color: "#2563eb",
    lang: "en",
    icons: [
      { src: `${b}/icons/icon-192.png`, sizes: "192x192", type: "image/png" },
      { src: `${b}/icons/icon-512.png`, sizes: "512x512", type: "image/png" },
      { src: `${b}/icons/icon-512-maskable.png`, sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
    shortcuts: [{ name: "Capture an invoice", url: `${b}/my/capture`, icons: [{ src: `${b}/icons/icon-192.png`, sizes: "192x192" }] }],
  };
}
