import type { NavLinkItem } from "../../data/landingNav";
import { Link } from "../../lib/router";

export function NavMenuItemRow({ item, onNavigate }: { item: NavLinkItem; onNavigate?: () => void }) {
  const Icon = item.icon;

  return (
    <Link
      to={item.href}
      onClick={onNavigate}
      className="group flex items-start gap-3 rounded-xl px-3 py-2.5 text-left transition-colors duration-150 hover:bg-surface-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
    >
      {Icon && (
        <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-surface-hover text-secondary transition-colors duration-150 group-hover:bg-accent/10 group-hover:text-accent">
          <Icon className="h-4 w-4" aria-hidden="true" />
        </span>
      )}
      <span className="min-w-0">
        <span className="block text-sm font-medium text-primary">{item.label}</span>
        {item.description && (
          <span className="mt-0.5 block text-xs leading-relaxed text-tertiary">{item.description}</span>
        )}
      </span>
    </Link>
  );
}
