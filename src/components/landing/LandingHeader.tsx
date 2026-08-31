import { useState } from "react";
import { ArrowRight, Menu as MenuIcon } from "lucide-react";
import { BrandMark } from "../ui/BrandMark";
import { Link } from "../../lib/router";
import { APP_ROUTE } from "../../lib/routes";
import { DesktopNav } from "./DesktopNav";
import { MobileNav } from "./MobileNav";

interface LandingHeaderProps {
  activeMenu: string;
  onActiveMenuChange: (value: string) => void;
}

export function LandingHeader({ activeMenu, onActiveMenuChange }: LandingHeaderProps) {
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <header className="fixed inset-x-0 top-0 z-40 border-b border-subtle/40 bg-surface/70 backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between gap-4 px-4 sm:px-6 lg:px-8">
        <Link
          to="/"
          aria-label="Home"
          className="flex shrink-0 items-center gap-2 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
        >
          <BrandMark />
        </Link>

        {/* This slot gets real flex-computed width (unlike the nav's own
            shrink-to-fit content box), so the mega-menu panel positioned
            relative to it can be centered without risking viewport overflow. */}
        <div className="hidden flex-1 lg:block">
          <DesktopNav openMenu={activeMenu} onOpenMenuChange={onActiveMenuChange} />
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <Link
            to={APP_ROUTE}
            className="hidden items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium text-secondary transition-colors duration-150 hover:bg-surface-hover hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 sm:inline-flex"
          >
            Open app
            <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
          </Link>

          <button
            type="button"
            aria-label="Open menu"
            aria-haspopup="dialog"
            aria-expanded={mobileOpen}
            onClick={() => setMobileOpen(true)}
            className="inline-flex h-9 w-9 items-center justify-center rounded-lg text-secondary transition-colors duration-150 hover:bg-surface-hover hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 lg:hidden"
          >
            <MenuIcon className="h-5 w-5" aria-hidden="true" />
          </button>
        </div>
      </div>

      <MobileNav open={mobileOpen} onOpenChange={setMobileOpen} />
    </header>
  );
}
