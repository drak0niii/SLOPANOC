import type { ReactNode } from "react";

/**
 * POST-6A — the one presentation shell for every authentication state
 * (sign in, callback failure, misconfiguration). A single component so
 * all three read the same and none of them can accidentally grow its own
 * error-rendering style.
 *
 * SAFE ERROR PRESENTATION: this renders only strings its callers chose.
 * It never receives an exception, a response body, a URL, or anything
 * derived from the authorization server's response — a failed token
 * exchange must not put protocol detail, let alone a code or token
 * fragment, on screen or in the DOM.
 */
export function AuthPanel({
  eyebrow,
  title,
  body,
  action,
}: {
  eyebrow: string;
  title: string;
  body: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex h-screen w-screen flex-col items-center justify-center gap-2 bg-bg px-6 text-center">
      <p className="text-sm font-medium text-tertiary">{eyebrow}</p>
      <h1 className="text-lg font-semibold text-primary">{title}</h1>
      <p className="max-w-sm text-sm leading-relaxed text-secondary">{body}</p>
      {action ? <div className="mt-3">{action}</div> : null}
    </div>
  );
}

export function AuthActionButton({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-lg px-3 py-2 text-sm font-medium text-accent transition-opacity duration-150 hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
    >
      {children}
    </button>
  );
}
