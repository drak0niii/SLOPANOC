import { lazy, Suspense } from "react";
import { RouterProvider, useRouter } from "./lib/router";
import { APP_ROUTE } from "./lib/routes";
import { NotFoundPage } from "./components/NotFoundPage";
import { AppStateProvider } from "./state/AppState";
import { AuthGate } from "./components/auth/AuthGate";
import { AuthCallbackPage } from "./components/auth/AuthCallbackPage";
import { CALLBACK_PATH } from "./api/auth";

const LandingPage = lazy(() =>
  import("./components/landing/LandingPage").then((module) => ({ default: module.LandingPage })),
);
const ProductApp = lazy(() => import("./ProductApp"));

function RouteFallback() {
  return (
    <div className="flex h-screen w-screen items-center justify-center bg-bg">
      <span className="anim-breathe-dot h-1.5 w-1.5 rounded-full bg-tertiary" />
    </div>
  );
}

function Routes() {
  const { pathname } = useRouter();

  // POST-6A — checked BEFORE the product route. This is the redirect URI
  // registered with the identity provider; without it the browser came
  // back from a successful sign-in holding a single-use authorization
  // code and landed on the 404 page, which is why login never completed.
  // Deliberately NOT wrapped in AuthGate: this route is how a session is
  // obtained, so gating it on already having one would deadlock.
  if (pathname === CALLBACK_PATH) {
    return <AuthCallbackPage />;
  }
  if (pathname === APP_ROUTE || pathname.startsWith(`${APP_ROUTE}/`)) {
    // The landing page stays public; only the product application, which
    // issues authenticated API calls on mount, requires a credential.
    return (
      <AuthGate>
        <ProductApp />
      </AuthGate>
    );
  }
  if (pathname === "/") {
    return <LandingPage />;
  }
  return <NotFoundPage />;
}

export default function App() {
  return (
    <RouterProvider>
      {/* Lives above the route switch, not inside ProductApp — Routes()
          renders <LandingPage/> and <ProductApp/> as different component
          types at the same tree position, so if the state lived inside
          ProductApp, navigating to "/" and back to "/app" (e.g. the
          sidebar's "Back to site" link) would unmount it and silently
          wipe every chat, project, and scheduled task. */}
      <AppStateProvider>
        <Suspense fallback={<RouteFallback />}>
          <Routes />
        </Suspense>
      </AppStateProvider>
    </RouterProvider>
  );
}
