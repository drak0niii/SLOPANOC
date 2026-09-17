import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

/**
 * POST-6A — the login flow that previously existed but was unreachable.
 *
 * These are integration-shaped checks over the REAL module: they drive
 * `completeLogin` / `getAuthHeaders` / `withAuth` as the app does, with
 * only `oidc-client-ts`'s `UserManager` replaced (it performs real
 * network redirects and a real token exchange, which cannot run here).
 *
 * WHAT IS DELIBERATELY NOT MOCKED: `state`, `nonce`, PKCE and issuer
 * validation all live inside `signinRedirectCallback`. Reimplementing
 * them in a test double would assert that our double works, which proves
 * nothing. What IS asserted here is the part this codebase owns: that a
 * failed callback NEVER yields a credential, that an ID-token-only
 * response is rejected, and that the return path cannot be pointed
 * off-origin.
 */

const signinRedirect = vi.fn();
const signinRedirectCallback = vi.fn();
const signoutRedirect = vi.fn();

vi.mock("oidc-client-ts", () => ({
  UserManager: class {
    signinRedirect = signinRedirect;
    signinRedirectCallback = signinRedirectCallback;
    signoutRedirect = signoutRedirect;
  },
  WebStorageStateStore: class {},
}));

vi.stubEnv("VITE_SLOPANOC_AUTH_MODE", "oidc");
vi.stubEnv("VITE_SLOPANOC_AUTH_ISSUER", "https://issuer.example/v2.0");
vi.stubEnv("VITE_SLOPANOC_AUTH_CLIENT_ID", "spa-client-id");
vi.stubEnv("VITE_SLOPANOC_AUTH_SCOPE", "openid profile api://slopanoc/user_impersonation");

async function freshAuth() {
  vi.resetModules();
  return import("./auth");
}

beforeEach(() => {
  signinRedirect.mockReset();
  signinRedirectCallback.mockReset();
  signoutRedirect.mockReset();
  sessionStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("login → callback → authenticated request", () => {
  it("attaches the access token to requests only after the callback completes", async () => {
    const auth = await freshAuth();

    // Before sign-in there is no credential, and no header is invented.
    expect(auth.hasValidToken()).toBe(false);
    expect(auth.getAuthHeaders()).toEqual({});

    signinRedirectCallback.mockResolvedValue({
      access_token: "access-token-for-this-api",
      expires_at: Math.floor(Date.now() / 1000) + 3600,
    });

    const returnTo = await auth.completeLogin();

    expect(auth.hasValidToken()).toBe(true);
    expect(auth.getAuthHeaders()).toEqual({ Authorization: "Bearer access-token-for-this-api" });
    expect(returnTo).toBe("/");

    // `withAuth` is the choke point every transport uses, and it must
    // merge rather than replace — notably preserving a caller's
    // Content-Type, and its deliberate ABSENCE for multipart uploads.
    const init = auth.withAuth({ method: "POST", headers: { "Content-Type": "application/json" } });
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBe("Bearer access-token-for-this-api");
    expect(headers.get("Content-Type")).toBe("application/json");
  });

  it("returns the user to the page they started from, and only to a same-origin path", async () => {
    const auth = await freshAuth();
    signinRedirect.mockResolvedValue(undefined);
    signinRedirectCallback.mockResolvedValue({
      access_token: "t",
      expires_at: Math.floor(Date.now() / 1000) + 3600,
    });

    await auth.beginLogin("/app/chat-123");
    expect(await auth.completeLogin()).toBe("/app/chat-123");
  });

  it("refuses an off-origin return destination", async () => {
    const auth = await freshAuth();
    signinRedirectCallback.mockResolvedValue({
      access_token: "t",
      expires_at: Math.floor(Date.now() / 1000) + 3600,
    });

    // An attacker-planted absolute or protocol-relative value must never
    // become the post-login destination — that would make the callback an
    // open redirector on a trusted origin.
    for (const hostile of ["https://evil.example/steal", "//evil.example/steal"]) {
      sessionStorage.setItem("slopanoc.returnTo", hostile);
      expect(await auth.completeLogin()).toBe("/");
    }
  });
});

describe("rejected OAuth state", () => {
  it("yields no credential at all when the callback cannot be verified", async () => {
    const auth = await freshAuth();
    // This is what `oidc-client-ts` does on a state/nonce/PKCE mismatch:
    // it throws rather than returning a user.
    signinRedirectCallback.mockRejectedValue(new Error("State mismatch"));

    await expect(auth.completeLogin()).rejects.toThrow();
    expect(auth.hasValidToken()).toBe(false);
    expect(auth.getAuthHeaders()).toEqual({});
  });

  it("rejects a response carrying only an ID token", async () => {
    const auth = await freshAuth();
    // An ID token authenticates the CLIENT; it is not an authorization
    // credential for this resource server, and the backend would reject
    // it on audience. Failing here makes the misconfiguration obvious
    // instead of producing a token that fails every later request.
    signinRedirectCallback.mockResolvedValue({
      id_token: "id-token-only",
      expires_at: Math.floor(Date.now() / 1000) + 3600,
    });

    await expect(auth.completeLogin()).rejects.toThrow(/access token/i);
    expect(auth.hasValidToken()).toBe(false);
  });
});

describe("expired session", () => {
  it("stops presenting an expired token", async () => {
    const auth = await freshAuth();
    signinRedirectCallback.mockResolvedValue({
      access_token: "stale",
      expires_at: Math.floor(Date.now() / 1000) - 10,
    });

    await auth.completeLogin();
    expect(auth.hasValidToken()).toBe(false);
    expect(auth.getAuthHeaders()).toEqual({});
  });

  it("clears the token and restarts sign-in on 401", async () => {
    const auth = await freshAuth();
    signinRedirect.mockResolvedValue(undefined);
    signinRedirectCallback.mockResolvedValue({
      access_token: "t",
      expires_at: Math.floor(Date.now() / 1000) + 3600,
    });
    await auth.completeLogin();
    expect(auth.hasValidToken()).toBe(true);

    await auth.handleUnauthenticated();

    expect(auth.hasValidToken()).toBe(false);
    expect(signinRedirect).toHaveBeenCalledTimes(1);
  });

  it("notifies subscribers so the UI can re-render on sign-out", async () => {
    const auth = await freshAuth();
    signinRedirectCallback.mockResolvedValue({
      access_token: "t",
      expires_at: Math.floor(Date.now() / 1000) + 3600,
    });

    const seen: boolean[] = [];
    const unsubscribe = auth.subscribeAuth(() => seen.push(auth.hasValidToken()));

    await auth.completeLogin();
    auth.clearToken();
    unsubscribe();
    auth.clearToken(); // after unsubscribe — must not notify again

    expect(seen).toEqual([true, false]);
  });
});
