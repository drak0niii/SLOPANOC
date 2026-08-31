import type { MegaMenuConfig } from "../../data/landingNav";
import { NavMenuItemRow } from "./NavMenuItemRow";
import { FeaturedMenuCard } from "./FeaturedMenuCard";

export function MegaMenuPanel({ config, onNavigate }: { config: MegaMenuConfig; onNavigate?: () => void }) {
  return (
    <div className="grid w-[860px] max-w-[calc(100vw-2rem)] grid-cols-[1fr_1fr_0.85fr]">
      {config.columns.map((column) => (
        <div key={column.heading} className="p-5">
          <p className="px-3 pb-2 text-[11px] font-semibold uppercase tracking-wide text-tertiary">
            {column.heading}
          </p>
          <div className="flex flex-col gap-0.5">
            {column.items.map((item) => (
              <NavMenuItemRow key={item.id} item={item} onNavigate={onNavigate} />
            ))}
          </div>
        </div>
      ))}
      <div className="border-l border-subtle/40 bg-gradient-to-br from-accent/[0.06] via-surface-hover/50 to-surface-hover/50 p-5">
        <FeaturedMenuCard featured={config.featured} onNavigate={onNavigate} />
      </div>
    </div>
  );
}
