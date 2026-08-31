import { useRef, useState, type CSSProperties, type ReactNode } from "react";
import { cn } from "../../lib/cn";

/** Below this many pixels of overflow the ellipsis is hiding so little that
 * animating would be more distracting than helpful. */
const MIN_OVERFLOW_PX = 4;
const BASE_DURATION_MS = 1400;
const MS_PER_PX = 16;
const MAX_DURATION_MS = 6000;

interface ScrollingTextProps {
  children: ReactNode;
  className?: string;
  /** Native tooltip fallback. Defaults to `children` when it's a string, which
   * keeps the full text reachable for reduced-motion and keyboard users who
   * never trigger the animation. */
  title?: string;
}

/**
 * A truncated label that reveals itself on hover: it slides once to the end,
 * pauses long enough to read, then returns. An ellipsis says text was cut but
 * not what it said — this answers that without a tooltip or a wider column.
 *
 * At rest it behaves exactly like `truncate`, so it can be dropped in wherever
 * that class was used. The animation only runs when the text genuinely
 * overflows, measured at hover time rather than on mount, so it stays correct
 * as the sidebar resizes or the label changes.
 */
export function ScrollingText({ children, className, title }: ScrollingTextProps) {
  const outerRef = useRef<HTMLSpanElement>(null);
  const innerRef = useRef<HTMLSpanElement>(null);
  const runCountRef = useRef(0);
  const [run, setRun] = useState<{ distance: number; tick: number } | null>(null);

  function handleEnter() {
    if (run) return;
    const outer = outerRef.current;
    const inner = innerRef.current;
    if (!outer || !inner) return;

    const distance = inner.scrollWidth - outer.clientWidth;
    if (distance < MIN_OVERFLOW_PX) return;

    runCountRef.current += 1;
    setRun({ distance, tick: runCountRef.current });
  }

  const style = run
    ? ({
        "--scroll-distance": `${run.distance}px`,
        "--scroll-duration": `${Math.min(MAX_DURATION_MS, BASE_DURATION_MS + run.distance * MS_PER_PX)}ms`,
      } as CSSProperties)
    : undefined;

  return (
    <span
      ref={outerRef}
      onMouseEnter={handleEnter}
      title={title ?? (typeof children === "string" ? children : undefined)}
      className={cn("block min-w-0 overflow-hidden whitespace-nowrap", className)}
    >
      <span
        // Remounting on each run restarts the one-shot animation; re-adding an
        // unchanged class would not.
        key={run?.tick ?? "idle"}
        ref={innerRef}
        style={style}
        onAnimationEnd={() => setRun(null)}
        className={
          run
            ? "anim-scroll-text inline-block will-change-transform"
            : "block overflow-hidden text-ellipsis whitespace-nowrap"
        }
      >
        {children}
      </span>
    </span>
  );
}
