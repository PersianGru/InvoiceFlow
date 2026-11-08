"use client";
// Registers the service worker once (production builds only; the dev server reloads too often).
import { useEffect } from "react";

import { BASE_PATH } from "@/lib/api";

export function PwaRegister() {
  useEffect(() => {
    if (process.env.NODE_ENV !== "production" || !("serviceWorker" in navigator)) return;
    const handle = setTimeout(() => {
      navigator.serviceWorker.register(`${BASE_PATH}/sw.js`, { scope: `${BASE_PATH}/` }).catch(() => undefined);
    }, 0);
    return () => clearTimeout(handle);
  }, []);
  return null;
}
