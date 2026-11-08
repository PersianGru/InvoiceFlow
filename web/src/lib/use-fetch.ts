"use client";
// A tiny data hook: load when the key changes, expose an awaitable reload, surface API errors as text.
// State updates happen in promise callbacks (never synchronously inside the effect body).
import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "./api";

interface FetchState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
}

export function useFetch<T>(loader: () => Promise<T>, deps: unknown[]): FetchState<T> & { reload: () => Promise<void>; setData: (d: T | null) => void } {
  const [state, setState] = useState<FetchState<T>>({ data: null, error: null, loading: true });
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const seq = useRef(0);
  const key = JSON.stringify(deps);

  const reload = useCallback(async () => {
    const id = ++seq.current;
    await Promise.resolve();
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const data = await loaderRef.current();
      if (id === seq.current) setState({ data, error: null, loading: false });
    } catch (e) {
      if (id === seq.current) setState((s) => ({ ...s, error: errorText(e, String(e)), loading: false }));
    }
  }, []);

  useEffect(() => {
    const handle = setTimeout(() => void reload(), 0);
    return () => clearTimeout(handle);
  }, [key, reload]);

  const setData = useCallback((d: T | null) => setState((s) => ({ ...s, data: d })), []);
  return { ...state, reload, setData };
}

export function errorText(e: unknown, fallback: string): string {
  return e instanceof ApiError ? e.message : fallback;
}
