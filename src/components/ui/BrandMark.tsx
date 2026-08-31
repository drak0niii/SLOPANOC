import { cn } from "../../lib/cn";

/**
 * The product's brand mark — currently a wordmark only, with no glyph.
 *
 * Shared rather than inlined at the call site so that whenever a real logo
 * asset does arrive, it lands here and every surface picks it up at once.
 */
export function BrandMark({ className }: { className?: string }) {
  return (
    <span className={cn("flex shrink-0 items-center text-base text-primary", className)}>
      {/* Echoes the hero's serif/sans pairing, dialled down for this size. */}
      <span className="font-serif italic">SLOP</span>
      <span className="ml-1.5 font-semibold tracking-tight">ANOC</span>
    </span>
  );
}
