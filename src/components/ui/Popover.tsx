import * as PopoverPrimitive from "@radix-ui/react-popover";
import { cn } from "../../lib/cn";

export const PopoverRoot = PopoverPrimitive.Root;
export const PopoverTrigger = PopoverPrimitive.Trigger;

interface PopoverContentProps extends PopoverPrimitive.PopoverContentProps {
  animationClassName?: string;
}

export function PopoverContent({
  children,
  className,
  align = "start",
  animationClassName = "data-[state=open]:anim-pop-in data-[state=closed]:anim-pop-out",
  ...props
}: PopoverContentProps) {
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Content
        align={align}
        sideOffset={8}
        collisionPadding={12}
        className={cn(
          "z-50 rounded-2xl border border-subtle/50 bg-surface-raised/95 p-4 shadow-xl shadow-black/20 backdrop-blur-xl",
          animationClassName,
          className,
        )}
        {...props}
      >
        {children}
        <PopoverPrimitive.Arrow className="fill-surface-raised" />
      </PopoverPrimitive.Content>
    </PopoverPrimitive.Portal>
  );
}
