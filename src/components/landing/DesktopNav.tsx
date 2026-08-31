import { COMMUNITY_LINK, LANDING_NAV_MENUS } from "../../data/landingNav";
import { Link } from "../../lib/router";
import {
  NavigationMenuContent,
  NavigationMenuItem,
  NavigationMenuList,
  NavigationMenuRoot,
  NavigationMenuTrigger,
  NavigationMenuViewportWrapper,
} from "../ui/NavigationMenu";
import { MegaMenuPanel } from "./MegaMenuPanel";

interface DesktopNavProps {
  openMenu: string;
  onOpenMenuChange: (value: string) => void;
}

/**
 * Controlled from LandingHeader/LandingPage (rather than owning local state)
 * so `openMenu` is the single source of truth for which trigger is active.
 */
export function DesktopNav({ openMenu, onOpenMenuChange }: DesktopNavProps) {
  return (
    // Root fills its flex-1 parent slot (see LandingHeader) rather than
    // shrinking to the trigger cluster's own content width, so the panel
    // positioned relative to it can be centered without risking viewport
    // overflow on narrower `lg:` widths.
    <NavigationMenuRoot
      value={openMenu}
      onValueChange={onOpenMenuChange}
      className="relative flex w-full justify-center"
    >
      <NavigationMenuList className="flex items-center gap-1">
        {LANDING_NAV_MENUS.map((menu) => (
          <NavigationMenuItem key={menu.id} value={menu.id}>
            <NavigationMenuTrigger>{menu.label}</NavigationMenuTrigger>
            <NavigationMenuContent>
              <MegaMenuPanel config={menu} onNavigate={() => onOpenMenuChange("")} />
            </NavigationMenuContent>
          </NavigationMenuItem>
        ))}
        <Link
          to={COMMUNITY_LINK.href}
          className="inline-flex items-center gap-1 rounded-lg px-3 py-2 text-sm font-medium text-secondary outline-none transition-colors duration-150 hover:bg-surface-hover hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
        >
          {COMMUNITY_LINK.label}
        </Link>
      </NavigationMenuList>
      <NavigationMenuViewportWrapper />
    </NavigationMenuRoot>
  );
}
