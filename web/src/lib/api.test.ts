import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, withQuery } from "./api";

describe("api client", () => {
  afterEach(() => vi.restoreAllMocks());

  it("builds query strings and drops empty values", () => {
    expect(withQuery("/x", { a: 1, b: "", c: undefined, d: "y z" })).toBe("/x?a=1&d=y+z");
    expect(withQuery("/x")).toBe("/x");
  });

  it("sends credentials and surfaces the API error shape", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ error: "license_required", message: "paused", state: "unlicensed" }), { status: 402, headers: { "content-type": "application/json" } }));
    await expect(api("/api/v1/documents", { method: "POST", body: "{}" })).rejects.toMatchObject({ status: 402, code: "license_required", message: "paused" });
    const [, init] = fetchMock.mock.calls[0];
    expect(init?.credentials).toBe("include");
    try {
      await api("/api/v1/documents", { method: "POST", body: "{}" });
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      expect((e as ApiError).isLicenseError).toBe(true);
    }
  });

  it("tolerates non-JSON error bodies", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("Bad Gateway", { status: 502, statusText: "Bad Gateway" }));
    await expect(api("/x")).rejects.toMatchObject({ status: 502, code: "http_502" });
  });
});
