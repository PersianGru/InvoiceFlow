"use client";
// Session, locale and theme for the app shell. Locale and theme are per-browser conveniences
// (localStorage); the session lives in the API's HttpOnly cookie.
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { DEFAULT_LOCALE, isLocale, t as translate, type Locale, type MessageKey } from "@/i18n";
import { ApiError, get, type Firm, type LicenseView, type User } from "./api";

type Theme = "light" | "dark";

interface AppState {
  user: User | null;
  firm: Firm | null;
  license: LicenseView | null;
  /** Whether intake is on: from the license view (staff) or the firm's license state (clients). */
  intakeOn: boolean | null;
  locale: Locale;
  theme: Theme;
  ready: boolean;
  setLocale: (l: Locale) => void;
  toggleTheme: () => void;
  refreshSession: () => Promise<void>;
  refreshLicense: () => Promise<void>;
  t: (key: MessageKey, vars?: Record<string, string | number>) => string;
}

const AppContext = createContext<AppState | null>(null);

function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* private mode */
  }
}

function detectLocale(): Locale {
  const stored = readStorage("ivf.locale");
  const fromBrowser = navigator.language.slice(0, 2);
  return isLocale(stored) ? stored : isLocale(fromBrowser) ? fromBrowser : DEFAULT_LOCALE;
}

function detectTheme(): Theme {
  const stored = readStorage("ivf.theme");
  if (stored === "dark" || stored === "light") return stored;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

async function loadSession(): Promise<{ user: User; firm: Firm | null } | null> {
  try {
    const user = await get<User>("/api/v1/auth/me");
    const firm = user.firm_id ? await get<Firm>("/api/v1/firms/me/current") : null;
    return { user, firm };
  } catch {
    return null;
  }
}

async function loadLicense(firmId: string, previous: LicenseView | null): Promise<LicenseView | null> {
  try {
    return await get<LicenseView>(`/api/v1/firms/${firmId}/license`);
  } catch (e) {
    return e instanceof ApiError && e.status === 403 ? previous : null;
  }
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [firm, setFirm] = useState<Firm | null>(null);
  const [license, setLicense] = useState<LicenseView | null>(null);
  const [locale, setLocaleState] = useState<Locale>(DEFAULT_LOCALE);
  const [theme, setTheme] = useState<Theme>("light");
  const [ready, setReady] = useState(false);
  const firmId = user?.firm_id ?? null;

  // Browser preferences and the session are external state: read them after mount, asynchronously.
  useEffect(() => {
    const handle = setTimeout(() => {
      setLocaleState(detectLocale());
      setTheme(detectTheme());
      void loadSession().then((s) => {
        setUser(s?.user ?? null);
        setFirm(s?.firm ?? null);
        setReady(true);
      });
    }, 0);
    return () => clearTimeout(handle);
  }, []);

  const clientRole = user?.role === "client_admin" || user?.role === "client_user";
  useEffect(() => {
    if (!firmId || clientRole) return; // clients never read the License page API
    const handle = setTimeout(() => void loadLicense(firmId, null).then(setLicense), 0);
    return () => clearTimeout(handle);
  }, [firmId, clientRole]);

  useEffect(() => {
    document.documentElement.lang = locale;
    document.documentElement.classList.toggle("dark", theme === "dark");
  }, [locale, theme]);

  const value: AppState = {
    user,
    firm,
    license,
    intakeOn: license ? license.core_on : firm ? firm.license_state === "valid" || firm.license_state === "grace" : null,
    locale,
    theme,
    ready,
    setLocale: (l) => {
      setLocaleState(l);
      writeStorage("ivf.locale", l);
    },
    toggleTheme: () => {
      const next = theme === "dark" ? "light" : "dark";
      setTheme(next);
      writeStorage("ivf.theme", next);
    },
    refreshSession: async () => {
      const s = await loadSession();
      setUser(s?.user ?? null);
      setFirm(s?.firm ?? null);
      if (!s) setLicense(null);
      setReady(true);
    },
    refreshLicense: async () => {
      if (clientRole) {
        const s = await loadSession();
        setFirm(s?.firm ?? null);
        return;
      }
      if (firmId) setLicense(await loadLicense(firmId, license));
    },
    t: (key, vars) => translate(locale, key, vars),
  };
  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp(): AppState {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp outside AppProvider");
  return ctx;
}
