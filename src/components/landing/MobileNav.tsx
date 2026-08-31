import { useEffect, useState, type ReactNode } from "react";
import { ArrowRight, ChevronDown } from "lucide-react";
import { DrawerContent, DrawerRoot, DrawerTitle } from "../ui/Drawer";
import { COMMUNITY_LINK, LANDING_NAV_MENUS } from "../../data/landingNav";
import { APP_ROUTE } from "../../lib/routes";
import { Link } from "../../lib/router";
import { cn } from "../../lib/cn";
import { NavMenuItemRow } from "./NavMenuItemRow";
import { FeaturedMenuCard } from "./FeaturedMenuCard";

function MobileAccordionSection({ label, children }: { label: string; children: ReactNode }) {
  const [expanded, setExpanded] = useState(false);
  // Keep content mounted through the collapse transition, then remove it —
  // same pattern as the sidebar's project rows, so collapsed sections never
  // leave links keyboard-focusable.
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    if (expanded) setMounted(true);
  }, [expanded]);

  return (
    <div className="border-b border-subtle/40">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="flex w-full items-center justify-between rounded-lg px-1 py-3.5 text-left text-sm font-medium text-primary transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
      >
        {label}
        <ChevronDown
          aria-hidden="true"
          className={cn(
            "h-4 w-4 text-tertiary transition-transform duration-200 ease-premium",
            expanded && "rotate-180",
          )}
        />
      </button>
      <div
        className={cn(
          "grid transition-[grid-template-rows] duration-200 ease-premium",
          expanded ? "grid-rows-[1fr]" : "grid-rows-[0fr]",
        )}
        onTransitionEnd={(event) => {
          if (event.target !== event.currentTarget) return;
          if (!expanded) setMounted(false);
        }}
      >
        <div className="overflow-hidden">{mounted && <div className="pb-4">{children}</div>}</div>
      </div>
    </div>
  );
}

export function MobileNav({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const close = () => onOpenChange(false);

  return (
    <DrawerRoot open={open} onOpenChange={onOpenChange}>
      <DrawerContent className="flex flex-col overflow-y-auto p-5">
        <DrawerTitle>Menu</DrawerTitle>

        <nav aria-label="Mobile" className="mt-4 flex flex-col">
          {LANDING_NAV_MENUS.map((menu) => (
            <MobileAccordionSection key={menu.id} label={menu.label}>
              <div className="flex flex-col gap-4">
                {menu.columns.map((column) => (
                  <div key={column.heading}>
                    <p className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-wide text-tertiary">
                      {column.heading}
                    </p>
                    <div className="flex flex-col gap-0.5">
                      {column.items.map((item) => (
                        <NavMenuItemRow key={item.id} item={item} onNavigate={close} />
                      ))}
                    </div>
                  </div>
                ))}
                <div className="rounded-xl border border-subtle/50 bg-surface-hover/40 p-3">
                  <FeaturedMenuCard featured={menu.featured} onNavigate={close} />
                </div>
              </div>
            </MobileAccordionSection>
          ))}
          <Link
            to={COMMUNITY_LINK.href}
            onClick={close}
            className="flex items-center justify-between rounded-lg px-1 py-3.5 text-left text-sm font-medium text-primary transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
          >
            {COMMUNITY_LINK.label}
          </Link>
        </nav>

        <Link
          to={APP_ROUTE}
          onClick={close}
          className="mt-6 inline-flex items-center justify-center gap-1.5 rounded-lg bg-accent px-4 py-2.5 text-sm font-medium text-on-accent transition-opacity duration-150 hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
        >
          Open app
          <ArrowRight className="h-4 w-4" aria-hidden="true" />
        </Link>
      </DrawerContent>
    </DrawerRoot>
  );
}
