import { lazy, Suspense } from "react";
import { RouterProvider, useRouter } from "./lib/router";
import { APP_ROUTE } from "./lib/routes";
import { NotFoundPage } from "./components/NotFoundPage";
import { AppStateProvider } from "./state/AppState";

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

  if (pathname === APP_ROUTE || pathname.startsWith(`${APP_ROUTE}/`)) {
    return <ProductApp />;
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
