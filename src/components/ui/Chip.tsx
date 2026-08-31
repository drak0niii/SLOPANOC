import type { ReactNode } from "react";
import { X } from "./icons";
import { cn } from "../../lib/cn";

interface ChipProps {
  icon?: ReactNode;
  label: string;
  meta?: string;
  onRemove?: () => void;
  tone?: "neutral" | "accent";
}

export function Chip({ icon, label, meta, onRemove, tone = "neutral" }: ChipProps) {
  return (
    <span
      className={cn(
        "inline-flex max-w-56 items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-sm",
        tone === "neutral"
          ? "border-subtle bg-surface-raised text-secondary"
          : "border-accent/30 bg-accent/10 text-accent",
      )}
    >
      {icon}
      <span className="truncate font-medium">{label}</span>
      {meta && <span className="shrink-0 text-tertiary">{meta}</span>}
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Remove ${label}`}
          className="ml-0.5 shrink-0 rounded-full p-0.5 text-tertiary hover:bg-surface-hover hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
        >
          <X className="h-3 w-3" />
        </button>
      )}
    </span>
  );
}
