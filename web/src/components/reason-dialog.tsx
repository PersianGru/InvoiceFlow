"use client";
// A modal that asks for a written justification (required for user, firm, trash and restore actions).
// `useReason()` returns the dialog element and an `ask()` that resolves to the text or null.
import { useCallback, useRef, useState, type ReactNode } from "react";

import { Button, Field, Textarea } from "@/components/ui";
import { useApp } from "@/lib/app-context";

interface Pending {
  title: string;
  body?: ReactNode;
  confirm: string;
  danger: boolean;
  resolve: (v: string | null) => void;
}

export function useReason(): [ReactNode, (title: string, opts?: { body?: ReactNode; confirm?: string; danger?: boolean }) => Promise<string | null>] {
  const { t } = useApp();
  const [pending, setPending] = useState<Pending | null>(null);
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement | null>(null);

  const ask = useCallback(
    (title: string, opts?: { body?: ReactNode; confirm?: string; danger?: boolean }) =>
      new Promise<string | null>((resolve) => {
        setText("");
        setPending({ title, body: opts?.body, confirm: opts?.confirm ?? t("reason.confirm"), danger: opts?.danger ?? false, resolve });
        setTimeout(() => ref.current?.focus(), 0);
      }),
    [t],
  );

  const close = (v: string | null) => {
    pending?.resolve(v);
    setPending(null);
  };

  const element = pending ? (
    <div role="dialog" aria-modal="true" aria-labelledby="reason-title" className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md rounded-lg border border-neutral-200 bg-white p-4 shadow-lg dark:border-neutral-800 dark:bg-neutral-900">
        <h2 id="reason-title" className="mb-2 text-base font-semibold">
          {pending.title}
        </h2>
        {pending.body ? <div className="mb-3 text-sm">{pending.body}</div> : null}
        <Field id="reason-text" label={t("reason.label")} hint={t("reason.hint")}>
          <Textarea id="reason-text" ref={ref} rows={3} value={text} onChange={(e) => setText(e.target.value)} />
        </Field>
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={() => close(null)}>
            {t("common.cancel")}
          </Button>
          <Button variant={pending.danger ? "danger" : "primary"} disabled={text.trim().length < 3} onClick={() => close(text.trim())}>
            {pending.confirm}
          </Button>
        </div>
      </div>
    </div>
  ) : null;
  return [element, ask];
}
