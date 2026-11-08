// Small pure helpers for the exports / API keys / webhooks pages (unit-tested).

export const EXPORT_FORMATS = ["json", "csv", "xlsx", "ubl", "xrechnung", "cii", "facturx", "pdf_summary", "datev"] as const;
export type ExportFormat = (typeof EXPORT_FORMATS)[number];
export const TEMPLATE_FORMATS = ["csv", "xlsx", "datev"] as const;
export const API_SCOPES = ["documents:read", "documents:write", "exports:read", "exports:write", "webhooks:manage"] as const;
export const WEBHOOK_EVENTS = ["document.received", "invoice.extracted", "invoice.needs_review", "invoice.approved", "export.completed"] as const;

export interface TemplateColumn {
  key: string;
  header: string;
}

/** Move the column at `index` by `delta` positions (clamped); returns a new array. */
export function moveColumn(columns: TemplateColumn[], index: number, delta: number): TemplateColumn[] {
  const target = index + delta;
  if (index < 0 || index >= columns.length || target < 0 || target >= columns.length) return columns;
  const out = columns.slice();
  const [item] = out.splice(index, 1);
  out.splice(target, 0, item);
  return out;
}

/** Add a column once (by key) with the registry label as the default header. */
export function addColumn(columns: TemplateColumn[], key: string, label: string): TemplateColumn[] {
  if (columns.some((c) => c.key === key)) return columns;
  return [...columns, { key, header: label }];
}

export function removeColumn(columns: TemplateColumn[], key: string): TemplateColumn[] {
  return columns.filter((c) => c.key !== key);
}

/** Templates apply only to tabular formats; a template bound to a client applies to that client's exports. */
export function templatesFor<T extends { format: string; client_company_id: string | null }>(templates: T[], format: string, clientId: string): T[] {
  if (!(TEMPLATE_FORMATS as readonly string[]).includes(format)) return [];
  return templates.filter((t) => t.format === format && (t.client_company_id === null || t.client_company_id === clientId || clientId === ""));
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** The hub-compatible signature header shape, for the "how to verify" hint on the webhooks page. */
export function signatureExample(): string {
  return "t=1762250400,v1=<hex HMAC-SHA256 over \"<t>.<raw body>\">";
}
