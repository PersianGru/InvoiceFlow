"use client";
// Small accessible primitives (no UI-kit dependency): button, input, select, badge, notice, table shell, spinner.
import { forwardRef, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

type Variant = "primary" | "secondary" | "danger" | "ghost";
const VARIANTS: Record<Variant, string> = {
  primary: "bg-blue-700 text-white hover:bg-blue-800 disabled:bg-blue-300 dark:bg-blue-500 dark:hover:bg-blue-400 dark:text-neutral-950",
  secondary: "border border-neutral-300 bg-white text-neutral-900 hover:bg-neutral-100 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-100 dark:hover:bg-neutral-800",
  danger: "bg-red-700 text-white hover:bg-red-800 dark:bg-red-500 dark:text-neutral-950",
  ghost: "text-neutral-700 hover:bg-neutral-100 dark:text-neutral-200 dark:hover:bg-neutral-800",
};

export const Button = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md" }>(function Button({ variant = "primary", size = "md", className, type = "button", ...rest }, ref) {
  return (
    <button
      ref={ref}
      type={type}
      className={cx("inline-flex items-center gap-1.5 rounded-md font-medium focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 disabled:cursor-not-allowed", size === "sm" ? "px-2.5 py-1 text-sm" : "px-3.5 py-2 text-sm", VARIANTS[variant], className)}
      {...rest}
    />
  );
});

const FIELD = "w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm text-neutral-900 shadow-sm focus:border-blue-600 focus:outline-none focus:ring-2 focus:ring-blue-600/30 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-100";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...rest }, ref) {
  return <input ref={ref} className={cx(FIELD, className)} {...rest} />;
});

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select({ className, ...rest }, ref) {
  return <select ref={ref} className={cx(FIELD, className)} {...rest} />;
});

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea({ className, ...rest }, ref) {
  return <textarea ref={ref} className={cx(FIELD, "min-h-24", className)} {...rest} />;
});

export function Label({ htmlFor, children, className }: { htmlFor?: string; children: ReactNode; className?: string }) {
  return (
    <label htmlFor={htmlFor} className={cx("mb-1 block text-sm font-medium text-neutral-800 dark:text-neutral-200", className)}>
      {children}
    </label>
  );
}

export function Field({ id, label, children, hint }: { id: string; label: ReactNode; children: ReactNode; hint?: ReactNode }) {
  return (
    <div className="mb-3">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint ? (
        <p id={`${id}-hint`} className="mt-1 text-xs text-neutral-600 dark:text-neutral-400">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

type Tone = "neutral" | "info" | "success" | "warning" | "danger";
const TONES: Record<Tone, string> = {
  neutral: "bg-neutral-100 text-neutral-800 dark:bg-neutral-800 dark:text-neutral-200",
  info: "bg-blue-100 text-blue-900 dark:bg-blue-900/50 dark:text-blue-100",
  success: "bg-green-100 text-green-900 dark:bg-green-900/50 dark:text-green-100",
  warning: "bg-amber-100 text-amber-900 dark:bg-amber-900/50 dark:text-amber-100",
  danger: "bg-red-100 text-red-900 dark:bg-red-900/50 dark:text-red-100",
};

export function Badge({ tone = "neutral", children, className, title }: { tone?: Tone; children: ReactNode; className?: string; title?: string }) {
  return (
    <span title={title} className={cx("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium", TONES[tone], className)}>
      {children}
    </span>
  );
}

export function Notice({ tone = "info", children, role = "status", className }: { tone?: Tone; children: ReactNode; role?: "status" | "alert"; className?: string }) {
  return (
    <div role={role} className={cx("rounded-md border px-3 py-2 text-sm", tone === "danger" ? "border-red-300 bg-red-50 text-red-900 dark:border-red-800 dark:bg-red-950/40 dark:text-red-100" : tone === "warning" ? "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-100" : tone === "success" ? "border-green-300 bg-green-50 text-green-900 dark:border-green-800 dark:bg-green-950/40 dark:text-green-100" : "border-blue-300 bg-blue-50 text-blue-900 dark:border-blue-800 dark:bg-blue-950/40 dark:text-blue-100", className)}>
      {children}
    </div>
  );
}

export function Spinner({ label }: { label: string }) {
  return (
    <div role="status" className="flex items-center gap-2 text-sm text-neutral-600 dark:text-neutral-400">
      <span aria-hidden="true" className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-neutral-300 border-t-blue-600" />
      {label}
    </div>
  );
}

export function Card({ title, children, className, actions }: { title?: ReactNode; children: ReactNode; className?: string; actions?: ReactNode }) {
  return (
    <section className={cx("rounded-lg border border-neutral-200 bg-white p-4 shadow-sm dark:border-neutral-800 dark:bg-neutral-900", className)}>
      {title ? (
        <div className="mb-3 flex items-center justify-between gap-2">
          <h2 className="text-base font-semibold">{title}</h2>
          {actions}
        </div>
      ) : null}
      {children}
    </section>
  );
}

export function statusTone(status: string): Tone {
  switch (status) {
    case "approved":
      return "success";
    case "needs_review":
    case "needs_info":
      return "warning";
    case "rejected":
    case "failed":
    case "quarantined":
      return "danger";
    case "processing":
      return "info";
    default:
      return "neutral";
  }
}

export function confidenceTone(c: number | null): Tone {
  if (c === null) return "neutral";
  if (c >= 0.9) return "success";
  if (c >= 0.7) return "warning";
  return "danger";
}
