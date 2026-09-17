/**
 * POST-6A — browser-side authentication for the SLOPANOC API.
 *
 * BUILT ON `oidc-client-ts`, NOT HAND-ROLLED PROTOCOL CODE. The previous
 * cut implemented the authorization-code exchange by hand; that meant
 * owning state validation, nonce validation, issuer checking, discovery
 * caching and token lifetime handling — all of which are exactly the
 * places a bespoke OIDC implementation goes quietly wrong.
 * `oidc-client-ts` is the maintained successor to `oidc-client-js` and
 * performs all of them:
 *
 *   - `state` is generated and verified on callback (CSRF / cross-session
 *     response injection),
 *   - `nonce` is generated and verified when an ID token is present,
 *   - the issuer is validated against the discovery document,
 *   - PKCE S256 is used, with the verifier held in the library's own
 *     state store and consumed once.
 *
 * WE ASK FOR AN ACCESS TOKEN FOR **THIS API**, NOT AN ID TOKEN. `scope`
 * must include this API's own scope so the authorization server issues a
 * token whose `aud` is our API. An ID token is proof of authentication
 * for the *client*, not an authorization credential for a resource
 * server; sending one as a bearer token is a real and common mistake,
 * and our backend would reject it on audience anyway.
 *
 * NO SECRET, EVER. This is a public client: authorization code + PKCE,
 * no `client_secret` anywhere in configuration, code, or logs. A browser
 * cannot keep a secret — anything shipped to it is public.
 *
 * TOKENS ARE HELD IN MEMORY. `userStore` is deliberately left at the
 * default in-memory store rather than `localStorage`: a token in
 * `localStorage` is readable by any script that reaches the page. The
 * cost is a redirect after a hard reload, which is the right trade for an
 * operational tool.
 *
 * REDIRECT DESTINATIONS ARE CONSTRAINED. The post-login return path is
 * only ever a same-origin PATH taken from our own router; an absolute or
 * protocol-relative value is discarded. An open redirector here would
 * hand an attacker a trusted-origin bounce.
 */

import { UserManager, WebStorageStateStore } from "oidc-client-ts";

const AUTH_MODE = (import.meta.env.VITE_SLOPANOC_AUTH_MODE ?? "development").trim();
const ISSUER = (import.meta.env.VITE_SLOPANOC_AUTH_ISSUER ?? "").trim();
const CLIENT_ID = (import.meta.env.VITE_SLOPANOC_AUTH_CLIENT_ID ?? "").trim();
/** MUST include this API's own scope (e.g. `api://slopanoc/user_impersonation`)
 * so the issued ACCESS token carries our API as its audience. */
const SCOPE = (import.meta.env.VITE_SLOPANOC_AUTH_SCOPE ?? "openid profile").trim();
const DEV_USER = (import.meta.env.VITE_SLOPANOC_DEV_USER ?? "").trim();

export const CALLBACK_PATH = "/auth/callback";
const RETURN_TO_KEY = "slopanoc.returnTo";

let manager: UserManager | null = null;
let accessToken: string | null = null;
let expiresAtMs = 0;

/** Components render differently signed in and signed out, and the token
 * changes outside React (callback completion, a 401, an explicit sign
 * out). One tiny subscription is what connects those to the tree --
 * without it `AuthGate` would read the token once and never learn that it
 * arrived, which is precisely the "helper nothing calls" failure. */
type AuthListener = () => void;
const listeners = new Set<AuthListener>();

export function subscribeAuth(listener: AuthListener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function notifyAuthChanged(): void {
  listeners.forEach((listener) => listener());
}

export function isOidcMode(): boolean {
  return AUTH_MODE === "oidc";
}

export function isAuthConfigured(): boolean {
  return Boolean(ISSUER && CLIENT_ID);
}

function getManager(): UserManager {
  if (!isAuthConfigured()) {
    throw new Error("Authentication is not configured for this deployment.");
  }
  if (!manager) {
    manager = new UserManager({
      authority: ISSUER,
      client_id: CLIENT_ID,
      redirect_uri: `${window.location.origin}${CALLBACK_PATH}`,
      post_logout_redirect_uri: window.location.origin,
      response_type: "code",
      scope: SCOPE,
      // The library validates `state`, `nonce` and the issuer itself; the
      // transient PKCE/state record lives in sessionStorage (cleared with
      // the tab), while the USER — and therefore the token — stays in the
      // default in-memory store.
      stateStore: new WebStorageStateStore({ store: window.sessionStorage }),
      automaticSilentRenew: false,
      loadUserInfo: false,
    });
  }
  return manager;
}

/** Only a same-origin PATH is ever returned to. Anything absolute,
 * protocol-relative, or otherwise not starting with a single `/` is
 * discarded in favour of the app root — this is what stops the callback
 * becoming an open redirector. */
function safeReturnPath(candidate: string | null): string {
  if (!candidate) return "/";
  if (!candidate.startsWith("/") || candidate.startsWith("//")) return "/";
  return candidate;
}

/** Begin login. Remembers where the user was so they land back there. */
export async function beginLogin(returnTo?: string): Promise<void> {
  if (!isOidcMode()) return;
  const path = safeReturnPath(returnTo ?? window.location.pathname + window.location.search);
  sessionStorage.setItem(RETURN_TO_KEY, path);
  await getManager().signinRedirect();
}

/**
 * Complete the redirect. `signinRedirectCallback` performs the state and
 * nonce checks and the code exchange; a mismatch throws, and the caller
 * shows a safe message rather than proceeding.
 */
export async function completeLogin(): Promise<string> {
  const user = await getManager().signinRedirectCallback();
  if (!user.access_token) {
    // An ID token alone is not an API credential — see the module note.
    throw new Error("Sign-in did not return an access token for this application.");
  }
  accessToken = user.access_token;
  expiresAtMs = (user.expires_at ?? 0) * 1000;
  notifyAuthChanged();

  const returnTo = safeReturnPath(sessionStorage.getItem(RETURN_TO_KEY));
  sessionStorage.removeItem(RETURN_TO_KEY);
  return returnTo;
}

export async function logout(): Promise<void> {
  clearToken();
  if (!isOidcMode() || !isAuthConfigured()) return;
  try {
    await getManager().signoutRedirect();
  } catch {
    // An issuer without an end-session endpoint is normal; the local
    // token is already gone, which is the part we control.
    window.location.assign("/");
  }
}

export function hasValidToken(): boolean {
  return Boolean(accessToken) && Date.now() < expiresAtMs - 30_000;
}

export function clearToken(): void {
  const had = accessToken !== null;
  accessToken = null;
  expiresAtMs = 0;
  if (had) notifyAuthChanged();
}

/** THE one place request auth headers are produced. Every transport
 * calls this, so a new call site cannot silently omit authentication.
 * In `oidc` mode the dev header is never sent — the backend ignores it
 * there, and sending it anyway would imply it still matters. */
export function getAuthHeaders(): Record<string, string> {
  if (isOidcMode()) {
    return hasValidToken() ? { Authorization: `Bearer ${accessToken}` } : {};
  }
  return DEV_USER ? { "X-SLOPANOC-DEV-USER": DEV_USER } : {};
}

/** Merge auth headers into an existing init, preserving whatever the
 * caller already set — notably `Content-Type` for JSON, and its ABSENCE
 * for multipart, where the browser must set its own boundary. */
export function withAuth(init?: RequestInit): RequestInit {
  const headers = new Headers(init?.headers ?? {});
  Object.entries(getAuthHeaders()).forEach(([key, value]) => headers.set(key, value));
  return { ...init, headers };
}

/**
 * Called when the API answers 401. Clears the dead token and restarts
 * login from the current page, so an expired session is a redirect
 * rather than a wall of failures.
 */
export async function handleUnauthenticated(): Promise<void> {
  clearToken();
  if (isOidcMode() && isAuthConfigured()) {
    await beginLogin();
  }
}
