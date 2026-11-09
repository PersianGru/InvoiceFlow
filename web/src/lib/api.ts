// Typed fetch client for the InvoiceFlow API. Errors always have the shape {error, message}.
// The session cookie is sent with every request (same site in development, same origin behind
// the reverse proxy in production). Nothing here logs request bodies or tokens.

// The app may live under a base path (NEXT_BASE_PATH at build time, e.g. "/dashboard").
export const BASE_PATH: string = process.env.NEXT_PUBLIC_BASE_PATH ?? "";
// NEXT_PUBLIC_API_URL unset: the development API; "": same origin (the web server proxies
// <base path>/api to the API); anything else: that origin.
const configured = process.env.NEXT_PUBLIC_API_URL;
export const API_URL: string = configured === undefined ? "http://localhost:8000" : configured === "" ? BASE_PATH : configured;

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;
  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
  get isLicenseError(): boolean {
    return this.status === 402;
  }
}

type Query = Record<string, string | number | boolean | null | undefined>;

export function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

async function parseError(res: Response): Promise<ApiError> {
  let body: { error?: string; message?: string; [k: string]: unknown } = {};
  try {
    body = (await res.json()) as typeof body;
  } catch {
    /* non-JSON error body */
  }
  return new ApiError(res.status, body.error ?? `http_${res.status}`, body.message ?? res.statusText, body);
}

export async function api<T>(path: string, init: RequestInit & { query?: Query } = {}): Promise<T> {
  const { query, headers, ...rest } = init;
  const res = await fetch(`${API_URL}${withQuery(path, query)}`, {
    credentials: "include",
    headers: { accept: "application/json", ...(rest.body && !(rest.body instanceof FormData) ? { "content-type": "application/json" } : {}), ...(headers ?? {}) },
    ...rest,
  });
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const get = <T>(path: string, query?: Query) => api<T>(path, { method: "GET", query });
export const post = <T>(path: string, body?: unknown) => api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const patch = <T>(path: string, body: unknown) => api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const upload = <T>(path: string, form: FormData) => api<T>(path, { method: "POST", body: form });
export const del = <T>(path: string) => api<T>(path, { method: "DELETE" });
export const put = <T>(path: string, body: unknown) => api<T>(path, { method: "PUT", body: JSON.stringify(body) });

export function imageUrl(path: string): string {
  return `${API_URL}${path}`;
}

// ---- API types (mirror backend/app/api/v1) -----------------------------------------------------
export interface User {
  id: string;
  email: string;
  display_name: string;
  role: "super_admin" | "firm_owner" | "accountant" | "junior_accountant" | "client_admin" | "client_user";
  firm_id: string | null;
  client_company_id: string | null;
  locale: string;
  totp_enabled: boolean;
  status?: "active" | "invited" | "deactivated" | "deleted";
  mfa_setup_required?: boolean;
  acting_firm_id?: string | null;
  recovery_codes_left?: number;
  created_at?: string | null;
  last_login_at?: string | null;
}

export interface InviteOut {
  user: User;
  link: string;
  email_sent: boolean;
  expires_at: string;
}

export interface Tombstone {
  id: string;
  user_id: string;
  label: string;
  reason: string;
  deleted_by_user_id: string | null;
  deleted_at: string;
  erase_after: string;
}

export interface PlatformFirmRow {
  id: string;
  name: string;
  country: string;
  is_active: boolean;
  deleted_at: string | null;
  license_state: string;
  key_hint: string | null;
  hub_error: string | null;
  users: number;
  client_companies: number;
  created_at: string;
  installation_id_grouped: string;
  last_backup_at: string | null;
  inbound_domain: string | null;
}

export interface PlatformOverview {
  firms: PlatformFirmRow[];
  firm_count: number;
  max_firms: number;
  platform_license_state: string;
  platform_firm_id: string;
  health: Record<string, string | boolean>;
}

export interface FirmCreated {
  firm: PlatformFirmRow;
  owner: User;
  invite_link: string;
  email_sent: boolean;
}

export interface Firm {
  id: string;
  name: string;
  country: string;
  locale: string;
  installation_id: string;
  installation_id_grouped: string;
  license_state: string;
  created_at: string;
}

export interface LicenseMessage {
  id: string;
  level: "info" | "warning";
  text: string;
  seen: boolean;
}

export interface LicenseView {
  state: "valid" | "grace" | "expired" | "unlicensed" | "invalid";
  reason: string | null;
  core_on: boolean;
  trial: boolean;
  trial_days_left: number | null;
  expires_at: number | null;
  grace_days: number;
  kh: string | null;
  plan: string | null;
  plan_name: string | null;
  licensed_to: string | null;
  items: Record<string, { expires: number | null; trial: boolean }>;
  features: Record<string, unknown>;
  use: string | null;
  next_refresh_at: number | null;
  verified_at: number;
  hub_error: string | null;
  hub_message: string | null;
  installation_id: string;
  installation_id_grouped: string;
  has_key: boolean;
  messages: LicenseMessage[];
  store_links: { buy: string; trial: string; renew: string; offline: string; addons?: string };
  banner: { level: "info" | "warning" | "error"; code: string; text: string } | null;
}

/** What "Check now" did: `retry_at` (unix seconds) is set while the hub cannot be asked yet. */
export interface RefreshOutcome {
  ok: boolean;
  code: string;
  message: string;
  retry_at: number | null;
}

export type RefreshView = LicenseView & { outcome: RefreshOutcome };

export interface DocumentRow {
  id: string;
  sender_identity?: string | null;
  client_company_id: string | null;
  channel: string;
  status: string;
  review_status: string;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  sha256: string;
  received_at: string;
  retention_until: string | null;
  document_type: string | null;
  invoice_number: string | null;
  supplier_name: string | null;
  issue_date: string | null;
  currency: string | null;
  gross_total: string | null;
  min_confidence: number | null;
  structured_format: string | null;
  error: string | null;
  assigned_to_user_id: string | null;
  page_count: number | null;
  payment_status?: "unpaid" | "partial" | "paid";
  paid_amount?: string | null;
  paid_at?: string | null;
}

export interface DocumentPage {
  items: DocumentRow[];
  total: number;
  offset: number;
  limit: number;
}

export interface FieldMeta {
  confidence: number;
  source: string;
  location: { page: number; x0: number; y0: number; x1: number; y1: number } | null;
  ai_generated: boolean;
  confirmed_by?: string | null;
  model?: string | null;
}

export interface ValidationResult {
  check: string;
  level: "pass" | "warning" | "error";
  reason: string;
  field: string | null;
  details: Record<string, unknown>;
}

export interface Invoice {
  document_type: string;
  direction: string | null;
  number: string | null;
  issue_date: string | null;
  delivery_date: string | null;
  currency: string | null;
  language: string | null;
  supplier: Record<string, unknown>;
  buyer: Record<string, unknown>;
  lines: Array<Record<string, unknown>>;
  vat_breakdown: Array<Record<string, unknown>>;
  totals: Record<string, string | null>;
  payment: Record<string, string | null>;
  references: Record<string, string | null>;
  notes: string[];
  meta: Record<string, FieldMeta>;
  validation: ValidationResult[];
  review_status: string;
  suggested_gl_account?: string | null;
  suggested_cost_category?: string | null;
  suggested_vat_code?: string | null;
  converted_currency?: string | null;
  converted_gross_total?: string | null;
}

export interface InvoiceEnvelope {
  version: number;
  provider: string;
  model: string | null;
  prompt_version: string | null;
  invoice: Invoice;
}

export interface TimelineEvent {
  step: string;
  status: string;
  detail: Record<string, unknown> | null;
  duration_ms: number | null;
  at: string | null;
}

export interface Comment {
  id: number;
  author_user_id: string | null;
  kind: "note" | "question" | "answer";
  body: string;
  visible_to_client: boolean;
  resolved_at: string | null;
  created_at: string;
}

export interface ClientCompany {
  id: string;
  name: string;
  country: string;
  inbound_email_local_part: string | null;
  inbound_email_address?: string | null;
  email_accept_any_sender?: boolean;
  peppol_id?: string | null;
  tax_number?: string | null;
  is_active: boolean;
  intake_paused?: Record<string, boolean>;
  retired_addresses?: Array<{ address: string; retired_at: string | null; grace_until: string | null; reason: string }>;
}

export interface Supplier {
  id: string;
  name: string;
  vat_id: string | null;
  country: string | null;
  ibans: string[];
  document_count: number;
  correction_count: number;
  accuracy: number | null;
  default_gl_account: string | null;
  default_cost_category: string | null;
  default_vat_code: string | null;
  field_rules: Record<string, string>;
  stats: Record<string, unknown>;
}

export interface Stats {
  days: number;
  documents: { total: number; by_status: Record<string, number>; by_day: Array<{ day: string | null; count: number }>; by_channel: Record<string, number>; structured: number };
  auto_approval_rate: number | null;
  processing_ms: { avg: number | null; p95: number | null };
  ai_calls: Array<{ provider: string; calls: number; tokens_in: number; tokens_out: number; duration_ms: number }>;
  suppliers: Array<{ id: string; name: string; documents: number; corrections: number; accuracy: number | null }>;
}

export interface AuditRow {
  id: number;
  actor_user_id: string | null;
  actor_role: string | null;
  action: string;
  target_type: string;
  target_id: string | null;
  result: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  created_at: string;
}

export interface FirmSettings {
  confidence_threshold: number;
  auto_approve: boolean;
  ai_routing_policy: string;
  cloud_ai_opt_in: boolean;
  cloud_ai_monthly_cap: number;
  retention_days: number;
  base_currency: string;
}

// ---- exports, API keys, webhooks --------------------------------------------------------
export interface ExportColumn {
  key: string;
  label: string;
  kind: "text" | "date" | "money" | "number" | "int" | "datetime";
  group: string;
}

export interface ExportColumnsInfo {
  columns: ExportColumn[];
  default: Array<{ key: string; header: string }>;
  formats: string[];
}

export interface ExportTemplate {
  id: string;
  name: string;
  format: "csv" | "xlsx" | "datev";
  client_company_id: string | null;
  columns: Array<{ key: string; header: string }>;
  options: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ExportRow {
  id: string;
  format: string;
  status: string;
  file_name: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
  document_count: number;
  document_ids: string[];
  client_company_id: string | null;
  template_id: string | null;
  error: string | null;
  created_at: string;
  download_url: string;
}

export interface ApiKeyRow {
  id: string;
  name: string;
  key_hint: string;
  scopes: string[];
  rate_limit_per_minute: number;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  client_company_ids?: string[];
  allowed_cidrs?: string[];
  description?: string;
  last_used_ip?: string | null;
}

export interface WebhookEndpoint {
  id: string;
  url: string;
  description: string;
  events: string[];
  active: boolean;
  consecutive_failures: number;
  disabled_at: string | null;
  disabled_reason: string | null;
  created_at: string;
  client_company_ids?: string[];
}

export interface WebhookDelivery {
  id: string;
  endpoint_id: string;
  event_id: string;
  event_type: string;
  status: "pending" | "delivered" | "failed";
  attempts: number;
  next_attempt_at: string | null;
  last_attempt_at: string | null;
  last_status_code: number | null;
  last_error: string | null;
  delivered_at: string | null;
  created_at: string;
  payload: Record<string, unknown>;
}

// ---- channels ---------------------------------------------------------------------------
export interface ChannelStatus {
  email: { configured: boolean; domain: string; webhook_path: string; imap: boolean; replies: boolean };
  telegram: { configured: boolean; webhook_path: string };
  whatsapp: { configured: boolean; webhook_path: string };
}

export interface SenderMapping {
  id: string;
  client_company_id: string;
  channel: "email" | "telegram" | "whatsapp";
  identity: string | null;
  display_name: string;
  pairing_code: string | null;
  pairing_expires_at: string | null;
  paired_at: string | null;
  last_seen_at: string | null;
  created_at: string;
  scope?: "person" | "chat";
  accept_from?: "anyone" | "paired";
  bot_id?: string | null;
  active?: boolean;
}

export interface ChannelMessage {
  id: number;
  channel: string;
  direction: "in" | "out";
  sender_identity: string;
  document_id: string | null;
  kind: string;
  body: string;
  created_at: string;
}

// ---- client portal -----------------------------------------------------------------------
export interface PortalSummary {
  by_status: Record<string, number>;
  total: number;
  open_questions: number;
}

// ---- SSO -----------------------------------------------------------------------------------
export interface SsoConfig {
  enabled: boolean;
  issuer: string;
  client_id: string;
  has_client_secret: boolean;
  allowed_domains: string[];
  provisioning: string;
  default_role: string;
  callback_url: string;
}

// ---- connectors ----------------------------------------------------------------------------
export interface ConnectorInfo {
  key: string;
  name: string;
  description: string;
  docs_url: string;
  fields: Array<{ key: string; label: string; secret: boolean; required: boolean; hint: string; default: string }>;
  enabled: boolean;
  auto_push: boolean;
  receive: boolean;
  capabilities: string[];
  receive_scope: "firm" | "client" | null;
  client_fields: Array<{ key: string; label: string; secret: boolean; required: boolean; hint: string; default: string }>;
  routing_field: string | null;
  config: Record<string, string>;
  last_test_at: string | null;
  last_test_ok: boolean | null;
  last_test_message: string | null;
}

export interface ConnectorRun {
  id: number;
  key: string;
  document_id: string | null;
  trigger: string;
  status: "ok" | "error" | "skipped";
  external_id: string | null;
  message: string;
  created_at: string;
}

// ---- networks, bank reconciliation, reporting ------------------------------------------
export interface ClientCredential {
  client_company_id: string;
  client_name: string;
  enabled: boolean;
  config: Record<string, string>;
  cursor: string | null;
  last_pull_at: string | null;
  last_error: string | null;
}

export interface BankAccount {
  id: string;
  iban: string;
  bic: string | null;
  name: string;
  currency: string;
  source: string;
  client_company_id: string | null;
  last_sync_at: string | null;
  transactions: number;
  unmatched: number;
}

export interface PaymentMatch {
  id: number;
  transaction_id: string;
  document_id: string;
  amount: string;
  score: number;
  method: "auto" | "manual";
  status: "suggested" | "confirmed" | "rejected";
  reasons: string[];
  invoice_number: string | null;
  supplier_name: string | null;
  gross_total: string | null;
  created_at: string;
}

export interface BankTransaction {
  id: string;
  account_id: string;
  booking_date: string;
  value_date: string | null;
  amount: string;
  currency: string;
  counterparty_name: string | null;
  counterparty_iban: string | null;
  remittance_info: string | null;
  end_to_end_id: string | null;
  match_status: "unmatched" | "suggested" | "matched" | "ignored";
  matches: PaymentMatch[];
}

export interface BankSummary {
  accounts: number;
  unmatched: number;
  suggested: number;
  matched: number;
  ignored: number;
}

export interface StatementImport {
  statements: Array<{ id: string; format: string; filename: string; transactions_count: number }>;
  new_transactions: number;
  duplicates: number;
  matched: number;
  suggested: number;
}

export interface BankFeed {
  provider: string;
  name: string;
  enabled: boolean;
  config: Record<string, string>;
  fields: string[];
  last_sync_at: string | null;
  last_error: string | null;
}

export interface VidaReport {
  id: string;
  client_company_id: string | null;
  period_start: string;
  period_end: string;
  status: "draft" | "submitted";
  format_version: string;
  document_count: number;
  totals: { invoices?: number; by_currency?: Record<string, { net: string; vat: string; gross: string }> };
  submitted_at: string | null;
  submitted_ref: string | null;
  created_at: string;
  transport: string;
}

// ---- engine tiers --------------------------------------------------------------------------
export interface EngineRow {
  key: string;
  available: boolean;
  active: boolean;
  external: boolean;
}

export interface EngineCatalogue {
  local_ocr: EngineRow[];
  cloud_ocr: EngineRow[];
  local_llm: { key: string; model: string | null; vision: boolean } | null;
  cloud_llm: { key: string; model: string | null; vision: boolean } | null;
  settings: { ocr_engine: string; ocr_cloud: string; local_llm: string; cloud_provider: string; ocr_languages: string };
  builtin_model: {
    key: string;
    file: string;
    ready: boolean;
    auto_download: boolean;
    size_mb: number | null;
    ram_hint: string | null;
    choices: Array<{ key: string; size_mb: number; ram_hint: string }>;
  };
  renderer: boolean;
}

export interface EngineTestResult {
  local_ocr: { key: string; ok: boolean; recognised?: boolean; words?: number; ms?: number; error?: string };
  local_llm: { key?: string; model?: string | null; ok: boolean; ms?: number; error?: string };
  cloud_llm: { key?: string; model?: string | null; ok: boolean; ms?: number; error?: string };
  cloud_ocr: { key?: string; ok: boolean; error?: string };
}
