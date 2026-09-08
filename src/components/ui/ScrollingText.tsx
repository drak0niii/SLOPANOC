import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { cn } from "../../lib/cn";

/** Below this many pixels of overflow the ellipsis is hiding so little that
 * animating would be more distracting than helpful. */
const MIN_OVERFLOW_PX = 4;
const BASE_DURATION_MS = 1400;
const MS_PER_PX = 16;
const MAX_DURATION_MS = 6000;
/** POST-B7 UI/UX refinement (Item 2) — the pointer must rest on ONE row
 * this long, uninterrupted, before its title starts scrolling. Prevents
 * a rapid pointer sweep across the sidebar from setting every overflowing
 * row in motion at once. */
const HOVER_ACTIVATION_DELAY_MS = 1000;

interface ScrollingTextProps {
  children: ReactNode;
  className?: string;
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
 *
 * POST-B7 UI/UX refinement (Item 2) — the scroll no longer starts the
 * instant the pointer enters; it waits `HOVER_ACTIVATION_DELAY_MS`,
 * cancelling cleanly if the pointer leaves first (whether that's before
 * the delay elapsed, in which case nothing ever started, or mid-animation,
 * in which case it stops and resets immediately). Purely local
 * ref/state — one row's hover never affects any other row's.
 *
 * POST-B7 corrective pass — DELIBERATELY NO `title` ATTRIBUTE (and no
 * other tooltip/popover of any kind): a real live test showed the native
 * browser tooltip this component used to set (defaulting to the full,
 * untruncated `children` text) firing on hover, which the product
 * explicitly does not want — hovering an overflowing row should ONLY
 * ever produce the delayed horizontal scroll, nothing else. This is not
 * an accessibility regression: CSS truncation (`overflow-hidden` +
 * `text-ellipsis`) never removes the underlying text NODE, only its
 * visual rendering, so a screen reader (or any other assistive
 * technology that reads DOM text content, independent of visual
 * clipping) still encounters the full, untruncated text exactly as
 * before. For an interactive wrapper (e.g. `SidebarChatRow`'s own
 * `<button>`), the accessible name is likewise computed from that same
 * full text content, never from this component's own (now removed)
 * `title` attribute — removing it changes nothing about what assistive
 * technology exposes, only what a sighted mouse user's hover produces.
 */
export function ScrollingText({ children, className }: ScrollingTextProps) {
  const outerRef = useRef<HTMLSpanElement>(null);
  const innerRef = useRef<HTMLSpanElement>(null);
  const runCountRef = useRef(0);
  const activationTimerRef = useRef<number | null>(null);
  const [run, setRun] = useState<{ distance: number; tick: number } | null>(null);

  function clearActivationTimer() {
    if (activationTimerRef.current !== null) {
      window.clearTimeout(activationTimerRef.current);
      activationTimerRef.current = null;
    }
  }

  function startScrolling() {
    const outer = outerRef.current;
    const inner = innerRef.current;
    if (!outer || !inner) return;

    const distance = inner.scrollWidth - outer.clientWidth;
    if (distance < MIN_OVERFLOW_PX) return;

    runCountRef.current += 1;
    setRun({ distance, tick: runCountRef.current });
  }

  function handleEnter() {
    if (run) return;
    clearActivationTimer();
    activationTimerRef.current = window.setTimeout(() => {
      activationTimerRef.current = null;
      startScrolling();
    }, HOVER_ACTIVATION_DELAY_MS);
  }

  function handleLeave() {
    clearActivationTimer();
    // Mid-flight: stop and reset back to the resting truncated position
    // immediately, rather than letting an already-started pass finish.
    if (run) setRun(null);
  }

  // Unmount safety net — a row scrolled out of the sidebar (or the whole
  // list re-rendered away) must never leave a pending timer behind.
  useEffect(() => clearActivationTimer, []);

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
      onMouseLeave={handleLeave}
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
