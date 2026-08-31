import { ArrowRight, ImageIcon } from "lucide-react";
import type { MegaMenuFeatured } from "../../data/landingNav";
import { Link } from "../../lib/router";

/**
 * Renders the mega menu's third-column featured area. Two shapes, driven by
 * whether the config includes an `image`:
 *  - Solutions: text-only featured callout (heading + copy + CTA).
 *  - Resources: an image-card (placeholder until `image.src` is set).
 */
export function FeaturedMenuCard({
  featured,
  onNavigate,
}: {
  featured: MegaMenuFeatured;
  onNavigate?: () => void;
}) {
  if (featured.image) {
    return (
      <Link
        to={featured.href}
        onClick={onNavigate}
        className="group flex h-full flex-col overflow-hidden rounded-xl border border-subtle/50 bg-surface transition-colors duration-150 hover:border-subtle/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
      >
        <div className="aspect-[16/10] w-full shrink-0 overflow-hidden bg-gradient-to-br from-accent/20 via-surface-hover to-surface-raised">
          {featured.image.src ? (
            <img src={featured.image.src} alt={featured.image.alt} className="h-full w-full object-cover" />
          ) : (
            <div className="flex h-full w-full items-center justify-center text-tertiary/70">
              <ImageIcon className="h-8 w-8" aria-hidden="true" />
            </div>
          )}
        </div>
        <div className="flex flex-1 flex-col p-4">
          <h4 className="text-sm font-semibold text-primary">{featured.title}</h4>
          <p className="mt-1.5 text-xs leading-relaxed text-tertiary">{featured.description}</p>
          <span className="mt-auto inline-flex items-center gap-1 pt-3 text-xs font-medium text-accent">
            {featured.ctaLabel}
            <ArrowRight className="h-3 w-3 transition-transform duration-150 ease-premium group-hover:translate-x-0.5" />
          </span>
        </div>
      </Link>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <span className="text-[10px] font-semibold uppercase tracking-wide text-tertiary">{featured.eyebrow}</span>
      <h4 className="mt-2 text-base font-semibold text-primary">{featured.title}</h4>
      <p className="mt-2 text-xs leading-relaxed text-secondary">{featured.description}</p>
      <Link
        to={featured.href}
        onClick={onNavigate}
        className="group mt-auto inline-flex w-fit items-center gap-1 rounded pt-4 text-xs font-medium text-accent transition-opacity duration-150 hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
      >
        {featured.ctaLabel}
        <ArrowRight className="h-3 w-3 transition-transform duration-150 ease-premium group-hover:translate-x-0.5" />
      </Link>
    </div>
  );
}
