import { useEffect, useState, type ReactNode } from "react";
import { beginLogin, hasValidToken, isAuthConfigured, isOidcMode, subscribeAuth } from "../../api/auth";
import { AuthActionButton, AuthPanel } from "./AuthPanel";

/**
 * POST-6A — nothing in the product renders until there is a usable
 * credential.
 *
 * WHY A GATE RATHER THAN PER-REQUEST HANDLING: the app issues requests on
 * mount (session list, hydration). Without a gate, an unauthenticated
 * load produces a screenful of failures and no explanation of the actual
 * cause. Gating renders one honest state instead: you are not signed in,
 * here is the button.
 *
 * IT DOES NOT PRETEND TO BE A SECURITY BOUNDARY. The real boundary is the
 * backend's token verification; a client-side gate is a usability
 * affordance and nothing more. It is written as one because a reader
 * should never mistake it for authorization.
 *
 * FAILS CLOSED ON MISCONFIGURATION. `auth_mode=oidc` with no issuer or
 * client id is a deployment error, not a reason to quietly fall back to
 * an unauthenticated app — the backend also refuses to start in that
 * state, and these two must agree.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const [authed, setAuthed] = useState(() => hasValidToken());

  useEffect(() => subscribeAuth(() => setAuthed(hasValidToken())), []);

  // Development mode: identity comes from the configured dev header and
  // there is no interactive sign-in to perform.
  if (!isOidcMode()) return <>{children}</>;

  if (!isAuthConfigured()) {
    return (
      <AuthPanel
        eyebrow="Configuration"
        title="Sign-in is not configured"
        body="This deployment requires authentication, but no identity provider has been configured for it. Contact the team that deployed this application."
      />
    );
  }

  if (!authed) {
    return (
      <AuthPanel
        eyebrow="Sign-in required"
        title="Sign in to continue"
        body="You need to sign in with your organization account to use this assistant."
        action={<AuthActionButton onClick={() => void beginLogin()}>Sign in</AuthActionButton>}
      />
    );
  }

  return <>{children}</>;
}
