import { render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { t as translate, type MessageKey } from "@/i18n";

const post = vi.fn();
vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {},
  post: (...args: unknown[]) => post(...args),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn() }) }));
// a new t on every render, like the app context: the effect must not depend on its identity
vi.mock("@/lib/app-context", () => ({
  useApp: () => ({ t: (k: MessageKey, v?: Record<string, string | number>) => translate("en", k, v), refreshSession: vi.fn() }),
}));

import { LinkPassword } from "./link-password";

// a made-up link token (low entropy on purpose)
const FAKE = "x".repeat(32);

describe("link password page", () => {
  beforeEach(() => {
    post.mockReset();
    post.mockResolvedValue({ kind: "invite", email: "owner@example.com", display_name: "Owner", expires_at: "2026-11-10T00:00:00Z" });
    window.history.replaceState(null, "", `/set-password#t=${FAKE}`);
  });

  it("keeps the token after clearing the address bar, also when the effect runs twice", async () => {
    const { rerender } = render(
      <StrictMode>
        <LinkPassword kind="invite" />
      </StrictMode>,
    );
    rerender(
      <StrictMode>
        <LinkPassword kind="invite" />
      </StrictMode>,
    );
    expect(await screen.findByText("For owner@example.com")).toBeInTheDocument();
    expect(screen.queryByText(/expired or was already used/)).not.toBeInTheDocument();
    expect(window.location.hash).toBe("");
    for (const call of post.mock.calls) expect(call[1]).toEqual({ token: FAKE });
  });

  it("says the link is invalid when there is no token", async () => {
    window.history.replaceState(null, "", "/set-password");
    render(<LinkPassword kind="invite" />);
    await waitFor(() => expect(screen.getByText(/expired or was already used/)).toBeInTheDocument());
    expect(post).not.toHaveBeenCalled();
  });
});
