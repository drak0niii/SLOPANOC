import { Link } from "../lib/router";

/** Minimal fallback for any path that isn't the landing page or the product app. */
export function NotFoundPage() {
  return (
    <div className="flex h-screen w-screen flex-col items-center justify-center gap-2 bg-bg px-6 text-center">
      <p className="text-sm font-medium text-tertiary">404</p>
      <h1 className="text-lg font-semibold text-primary">Page not found</h1>
      <p className="max-w-sm text-sm leading-relaxed text-secondary">
        The page you're looking for doesn't exist yet.
      </p>
      <Link
        to="/"
        className="mt-3 rounded-lg px-3 py-2 text-sm font-medium text-accent transition-opacity duration-150 hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
      >
        Back to home
      </Link>
    </div>
  );
}
