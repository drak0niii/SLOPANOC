import { useEffect, useRef, useState } from "react";
import { useRouter } from "../../lib/router";
import { beginLogin, completeLogin, isAuthConfigured, isOidcMode } from "../../api/auth";
import { AuthActionButton, AuthPanel } from "./AuthPanel";

/**
 * POST-6A — the redirect landing page for the authorization server.
 *
 * THIS IS THE COMPONENT THAT WAS MISSING. `completeLogin()` existed but
 * no route rendered it, so the browser arrived back at `/auth/callback`
 * with a live authorization code and hit the 404 page: the code was never
 * exchanged and the app never became authenticated. Login was not a
 * partially-working feature, it was an unreachable one.
 *
 * WHAT COMPLETION ACTUALLY VALIDATES happens inside `completeLogin` →
 * `oidc-client-ts`: the `state` must match the value stored when the
 * redirect began (otherwise this response belongs to some other flow, or
 * to an attacker), the PKCE verifier must match, the issuer must match
 * the discovery document, and `nonce` is checked when an ID token is
 * returned. A failure throws, and this component STOPS — it never falls
 * through to the app as if sign-in had worked.
 *
 * RUNS EXACTLY ONCE. An authorization code is single-use, so React 18
 * StrictMode's deliberate double-invoke of effects in development would
 * otherwise fire a second exchange that the authorization server
 * correctly rejects, turning every successful dev login into a visible
 * error. The ref guard is the standard fix and is not a race workaround.
 */
export function AuthCallbackPage() {
  const { navigate } = useRouter();
  const [failed, setFailed] = useState(false);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;

    if (!isOidcMode() || !isAuthConfigured()) {
      // Nothing could have issued this redirect in this configuration;
      // treat it as an ordinary stray URL rather than an auth failure.
      navigate("/");
      return;
    }

    completeLogin()
      .then((returnTo) => {
        // `returnTo` is already constrained to a same-origin path by
        // `safeReturnPath`, so this cannot be pointed off-origin.
        navigate(returnTo);
      })
      .catch(() => {
        // Deliberately discards the error object. See AuthPanel — the
        // user gets a fixed, safe sentence, and nothing from the
        // authorization server's response reaches the DOM or the console.
        setFailed(true);
      });
  }, [navigate]);

  if (failed) {
    return (
      <AuthPanel
        eyebrow="Sign-in"
        title="Sign-in could not be completed"
        body="The sign-in response could not be verified. This usually means the attempt took too long or was started in a different browser tab. Please try again."
        action={<AuthActionButton onClick={() => void beginLogin("/")}>Try again</AuthActionButton>}
      />
    );
  }

  return <AuthPanel eyebrow="Sign-in" title="Completing sign-in" body="Verifying your sign-in response." />;
}
