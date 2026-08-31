import { useState } from "react";
import { Check, ChevronRight } from "../ui/icons";
import { PopoverContent, PopoverRoot, PopoverTrigger } from "../ui/Popover";
import { cn } from "../../lib/cn";

export function ConfigRow({
  label,
  value,
  items,
  selectedId,
  onSelect,
}: {
  label: string;
  value: string;
  items: { id: string; label: string }[];
  selectedId: string;
  onSelect: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <PopoverRoot open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={cn(
            "flex w-full items-center justify-between gap-3 rounded-xl px-3 py-2.5 text-base transition-colors duration-100",
            "hover:bg-surface-hover",
            open && "bg-surface-hover",
          )}
        >
          <span className="text-tertiary">{label}</span>
          <span className="flex items-center gap-1 text-primary">
            <span className="max-w-[6.5rem] truncate">{value}</span>
            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-tertiary" />
          </span>
        </button>
      </PopoverTrigger>
      <PopoverContent
        side="right"
        align="start"
        sideOffset={6}
        className="w-48 !p-1.5 !shadow-lg !shadow-black/10"
        animationClassName="data-[state=open]:anim-effort-in data-[state=closed]:anim-effort-out"
      >
        <div className="flex flex-col">
          {items.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => {
                onSelect(item.id);
                setOpen(false);
              }}
              className="flex items-center justify-between gap-2 rounded-xl px-3 py-2.5 text-left text-base transition-colors duration-100 hover:bg-surface-hover"
            >
              <span
                className={
                  item.id === selectedId ? "font-medium text-primary" : "text-secondary"
                }
              >
                {item.label}
              </span>
              {item.id === selectedId && (
                <Check className="h-3.5 w-3.5 shrink-0 text-accent" />
              )}
            </button>
          ))}
        </div>
      </PopoverContent>
    </PopoverRoot>
  );
}
