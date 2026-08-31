import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "../../lib/cn";

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string;
  active?: boolean;
  size?: "sm" | "md";
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(
  ({ label, active, size = "md", className, children, ...props }, ref) => {
    return (
      <button
        ref={ref}
        type="button"
        aria-label={label}
        title={label}
        className={cn(
          "inline-flex items-center justify-center rounded-lg text-secondary transition-all duration-150",
          "hover:bg-surface-hover hover:text-primary active:scale-90",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 focus-visible:ring-offset-1 focus-visible:ring-offset-surface",
          "disabled:opacity-40 disabled:pointer-events-none",
          size === "md" ? "h-9 w-9" : "h-7 w-7",
          active && "bg-surface-hover text-primary",
          className,
        )}
        {...props}
      >
        {children}
      </button>
    );
  },
);
IconButton.displayName = "IconButton";
