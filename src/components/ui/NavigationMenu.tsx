import * as NavigationMenuPrimitive from "@radix-ui/react-navigation-menu";
import { ChevronDown } from "lucide-react";
import { cn } from "../../lib/cn";

export const NavigationMenuRoot = NavigationMenuPrimitive.Root;
export const NavigationMenuList = NavigationMenuPrimitive.List;
export const NavigationMenuItem = NavigationMenuPrimitive.Item;

/**
 * Radix opens a NavigationMenu on pointer enter/move and closes it on leave.
 * Suppressing those events on both the trigger and the panel makes the menu
 * click-only: it opens when you click the trigger, and closes on a second
 * click, Escape, or a click outside (all still handled by Radix).
 *
 * Applied after `{...props}` so the behaviour cannot be clobbered by a caller
 * passing its own pointer handlers.
 */
const suppressHoverOpen = {
  onPointerMove: (event: React.PointerEvent) => event.preventDefault(),
  onPointerLeave: (event: React.PointerEvent) => event.preventDefault(),
} as const;

export function NavigationMenuTrigger({
  children,
  className,
  ...props
}: NavigationMenuPrimitive.NavigationMenuTriggerProps) {
  return (
    <NavigationMenuPrimitive.Trigger
      className={cn(
        "group inline-flex items-center gap-1 rounded-lg px-3 py-2 text-sm font-medium text-secondary outline-none transition-colors duration-150",
        "hover:bg-surface-hover hover:text-primary data-[state=open]:bg-surface-hover data-[state=open]:text-primary",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50",
        className,
      )}
      {...props}
      {...suppressHoverOpen}
    >
      {children}
      <ChevronDown
        aria-hidden="true"
        className="h-3.5 w-3.5 text-tertiary transition-transform duration-200 ease-premium group-data-[state=open]:rotate-180"
      />
    </NavigationMenuPrimitive.Trigger>
  );
}

/** `absolute` + `w-auto` gives this node shrink-to-fit sizing instead of
 * filling the Viewport's (possibly stale, from a differently-sized panel)
 * width — Radix measures this node's offsetWidth/Height to size the shared
 * Viewport, so it must reflect its own panel's true intrinsic size. */
export function NavigationMenuContent({
  className,
  ...props
}: NavigationMenuPrimitive.NavigationMenuContentProps) {
  return (
    <NavigationMenuPrimitive.Content
      className={cn("absolute left-0 top-0 w-auto", className)}
      {...props}
      // Without this the panel closes as soon as the pointer leaves it, which
      // is hover behaviour — a click-opened menu should stay until dismissed.
      {...suppressHoverOpen}
      onPointerEnter={(event) => event.preventDefault()}
    />
  );
}

/** Single shared viewport: Radix measures and swaps in whichever Content is
 * currently active, so panel-to-panel switches (e.g. Solutions -> Resources)
 * resize/reposition smoothly instead of each panel animating independently. */
export function NavigationMenuViewportWrapper() {
  return (
    <div className="absolute left-0 top-full flex w-full justify-center pt-3">
      <NavigationMenuPrimitive.Viewport
        className={cn(
          "relative origin-top overflow-hidden rounded-2xl border border-subtle/50 bg-surface-raised/95 shadow-xl shadow-black/10 backdrop-blur-xl",
          "h-[var(--radix-navigation-menu-viewport-height)] w-[var(--radix-navigation-menu-viewport-width)]",
          "transition-[width,height] duration-200 ease-premium",
          "data-[state=open]:anim-mega-in data-[state=closed]:anim-mega-out",
        )}
      />
    </div>
  );
}
