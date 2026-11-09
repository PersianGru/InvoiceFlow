import { describe, expect, it, vi } from "vitest";

import { ApiError, InvoiceFlowClient, describeAsset, parseError, withQuery } from "./api";

function response(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });
}

describe("api client", () => {
  it("builds query strings and skips empty values", () => {
    expect(withQuery("/x", { q: "a b", limit: 10, empty: "", none: null })).toBe("/x?q=a%20b&limit=10");
    expect(withQuery("/x")).toBe("/x");
  });

  it("turns error bodies into ApiError with the license and TOTP flags", async () => {
    const e = await parseError(response(402, { error: "license_required", message: "No plan." }));
    expect(e).toBeInstanceOf(ApiError);
    expect(e.isLicenseError).toBe(true);
    expect(e.message).toBe("No plan.");
    const t = await parseError(response(401, { error: "totp_required", message: "code" }));
    expect(t.needsTotp).toBe(true);
    const plain = await parseError(new Response("oops", { status: 500, statusText: "Server Error" }));
    expect(plain.code).toBe("http_500");
  });

  it("keeps the session token from the login header and sends it afterwards", async () => {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    const fetchImpl = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
      calls.push({ url: String(url), init: init ?? {} });
      if (String(url).endsWith("/auth/login")) return response(200, { id: "u1", email: "a@b.c" }, { "x-session-token": "tok-1" });
      return response(200, { items: [], total: 0 });
    });
    const client = new InvoiceFlowClient("https://api.example.test", null, fetchImpl as unknown as typeof fetch);
    const user = await client.login("a@b.c", "pw", "123456");
    expect(user.id).toBe("u1");
    expect(JSON.parse(String(calls[0].init.body))).toEqual({ email: "a@b.c", password: "pw", totp_code: "123456" });
    await client.documents("inv", 20);
    expect(calls[1].url).toBe("https://api.example.test/api/v1/documents?q=inv&limit=20");
    expect((calls[1].init.headers as Record<string, string>).authorization).toBe("Bearer tok-1");
    await client.logout();
    expect(client.hasToken).toBe(false);
  });

  it("describes picked assets with a name and a MIME type", () => {
    expect(describeAsset("file:///tmp/abc/IMG_1.jpg")).toEqual({ uri: "file:///tmp/abc/IMG_1.jpg", name: "IMG_1.jpg", mimeType: "image/jpeg" });
    expect(describeAsset("content://x/y?z=1", "scan.pdf", null).mimeType).toBe("application/pdf");
    expect(describeAsset("content://x/y", null, "image/heic").name).toBe("y");
  });

  it("surfaces license errors from uploads", async () => {
    const fetchImpl = vi.fn(async () => response(402, { error: "license_required", message: "paused" }));
    const client = new InvoiceFlowClient("https://api.example.test", "tok", fetchImpl as unknown as typeof fetch);
    await expect(client.upload({ uri: "file:///a.jpg", name: "a.jpg", mimeType: "image/jpeg" })).rejects.toMatchObject({ status: 402, isLicenseError: true });
  });
});

describe("two-factor setup (0.11)", () => {
  it("sends the recovery code and calls the setup endpoints", async () => {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    const fetchImpl = (async (url: string, init: RequestInit) => {
      calls.push({ url: String(url), init });
      if (String(url).endsWith("/auth/login")) return new Response(JSON.stringify({ id: "u", email: "a@b.c", mfa_setup_required: true }), { status: 200, headers: { "x-session-token": "tok" } });
      if (String(url).endsWith("/totp/setup")) return new Response(JSON.stringify({ otpauth_uri: "otpauth://totp/x", secret: "ABC" }), { status: 200 });
      return new Response(JSON.stringify({ recovery_codes: ["a1b2c-3d4e5"] }), { status: 200 });
    }) as unknown as typeof fetch;
    const c = new InvoiceFlowClient("https://x", null, fetchImpl);
    const u = await c.login("a@b.c", "pw", undefined, "abcde-fghjk");
    expect(u.mfa_setup_required).toBe(true);
    expect(JSON.parse(String(calls[0].init.body))).toEqual({ email: "a@b.c", password: "pw", recovery_code: "abcde-fghjk" });
    expect((await c.totpSetup()).otpauth_uri).toBe("otpauth://totp/x");
    expect((await c.totpConfirm("123456")).recovery_codes).toHaveLength(1);
    expect(calls[2].init.headers).toMatchObject({ authorization: "Bearer tok" });
  });
});
