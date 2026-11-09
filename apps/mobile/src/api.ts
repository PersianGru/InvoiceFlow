// The InvoiceFlow API client for the native app: plain TypeScript over fetch, no React Native
// imports, so it is unit-tested with vitest. Bearer session tokens (the API returns the token in
// the `x-session-token` header on login); nothing here logs bodies or tokens.

export type Fetch = typeof fetch;

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
  get needsTotp(): boolean {
    return this.status === 401 && this.code === "totp_required";
  }
}

export interface User {
  id: string;
  email: string;
  display_name: string;
  role: string;
  client_company_id: string | null;
  locale: string;
  mfa_setup_required?: boolean;
  totp_enabled?: boolean;
}

export interface DocumentRow {
  id: string;
  status: string;
  review_status: string;
  original_filename: string;
  received_at: string;
  invoice_number: string | null;
  supplier_name: string | null;
  issue_date: string | null;
  currency: string | null;
  gross_total: string | null;
  error: string | null;
  payment_status?: "unpaid" | "partial" | "paid";
}

export interface DocumentPage {
  items: DocumentRow[];
  total: number;
}

export interface Comment {
  id: string;
  kind: "question" | "answer" | "note";
  body: string;
  created_at: string;
  author_name?: string | null;
  resolved_at?: string | null;
}

export interface Summary {
  by_status: Record<string, number>;
  total: number;
  open_questions: number;
}

export interface Picked {
  uri: string;
  name: string;
  mimeType: string;
}

type Query = Record<string, string | number | boolean | null | undefined>;

export function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const parts: string[] = [];
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== "") parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  }
  return parts.length ? `${path}?${parts.join("&")}` : path;
}

export async function parseError(res: Response): Promise<ApiError> {
  let body: { error?: string; message?: string } = {};
  try {
    body = (await res.json()) as typeof body;
  } catch {
    /* non-JSON body */
  }
  return new ApiError(res.status, body.error ?? `http_${res.status}`, body.message ?? res.statusText, body);
}

/** Guess a file name and MIME type from a picked asset (the pickers do not always fill them). */
export function describeAsset(uri: string, fileName?: string | null, mimeType?: string | null): Picked {
  const fromUri = uri.split(/[?#]/)[0].split("/").pop() ?? "";
  const name = fileName || fromUri || `capture-${Date.now()}.jpg`;
  const ext = name.includes(".") ? name.split(".").pop()!.toLowerCase() : "";
  const byExt: Record<string, string> = { jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", heic: "image/heic", pdf: "application/pdf", xml: "application/xml" };
  return { uri, name, mimeType: mimeType || byExt[ext] || "application/octet-stream" };
}

export class InvoiceFlowClient {
  constructor(
    readonly baseUrl: string,
    private token: string | null = null,
    private readonly fetchImpl: Fetch = fetch,
  ) {}

  setToken(token: string | null): void {
    this.token = token;
  }

  get hasToken(): boolean {
    return this.token !== null;
  }

  private headers(extra: Record<string, string> = {}): Record<string, string> {
    const h: Record<string, string> = { accept: "application/json", ...extra };
    if (this.token) h.authorization = `Bearer ${this.token}`;
    return h;
  }

  private async request<T>(method: string, path: string, body?: unknown, query?: Query): Promise<T> {
    const res = await this.fetchImpl(`${this.baseUrl}${withQuery(path, query)}`, {
      method,
      headers: this.headers(body instanceof FormData ? {} : body !== undefined ? { "content-type": "application/json" } : {}),
      body: body instanceof FormData ? body : body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) throw await parseError(res);
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }

  /** Signs in; the session token comes from the response header and is stored on the client. */
  async login(email: string, password: string, totpCode?: string, recoveryCode?: string): Promise<User> {
    const body: Record<string, string> = { email, password };
    if (totpCode) body.totp_code = totpCode;
    if (recoveryCode) body.recovery_code = recoveryCode;
    const res = await this.fetchImpl(`${this.baseUrl}/api/v1/auth/login`, {
      method: "POST",
      headers: { accept: "application/json", "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw await parseError(res);
    const token = res.headers.get("x-session-token");
    if (!token) throw new ApiError(500, "no_token", "The server returned no session token.");
    this.token = token;
    return (await res.json()) as User;
  }

  async logout(): Promise<void> {
    try {
      await this.request<void>("POST", "/api/v1/auth/logout");
    } finally {
      this.token = null;
    }
  }

  me(): Promise<User> {
    return this.request<User>("GET", "/api/v1/auth/me");
  }

  /** Mandatory 2FA (0.11): the otpauth URI opens the authenticator app on the same phone. */
  totpSetup(): Promise<{ otpauth_uri: string; secret: string }> {
    return this.request("POST", "/api/v1/users/me/totp/setup");
  }

  totpConfirm(code: string): Promise<{ recovery_codes: string[] }> {
    return this.request("POST", "/api/v1/users/me/totp/confirm", { code });
  }

  summary(): Promise<Summary> {
    return this.request<Summary>("GET", "/api/v1/my/summary");
  }

  documents(q?: string, limit = 100): Promise<DocumentPage> {
    return this.request<DocumentPage>("GET", "/api/v1/documents", undefined, { q, limit });
  }

  document(id: string): Promise<DocumentRow> {
    return this.request<DocumentRow>("GET", `/api/v1/documents/${id}`);
  }

  comments(id: string): Promise<Comment[]> {
    return this.request<Comment[]>("GET", `/api/v1/documents/${id}/comments`);
  }

  answer(id: string, body: string): Promise<Comment> {
    return this.request<Comment>("POST", `/api/v1/documents/${id}/comments`, { kind: "answer", body, visible_to_client: true });
  }

  /** Uploads one picked file as multipart/form-data (React Native's FormData takes {uri, name, type}). */
  upload(picked: Picked): Promise<DocumentRow> {
    const form = new FormData();
    form.append("file", { uri: picked.uri, name: picked.name, type: picked.mimeType } as unknown as Blob);
    return this.request<DocumentRow>("POST", "/api/v1/documents", form);
  }
}
