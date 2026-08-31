import { useState } from "react";
import { LandingHeader } from "./LandingHeader";
import { PixelHero } from "./PixelHero";

export function LandingPage() {
  // Single source of truth for which desktop mega menu is open (empty
  // string = none, matching Radix NavigationMenu's own convention).
  const [activeMenu, setActiveMenu] = useState("");

  return (
    <div className="relative w-full">
      <LandingHeader activeMenu={activeMenu} onActiveMenuChange={setActiveMenu} />
      {/* The header is fixed/translucent and overlays the hero rather than
          pushing it down, so the hero's own min-h-[100dvh] composition is
          left untouched. */}
      <PixelHero />
    </div>
  );
}
