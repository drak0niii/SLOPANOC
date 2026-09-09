import { useEffect, useState } from "react";
import { elapsedSecondsSince, formatElapsedTime } from "../../lib/elapsedTime";

/**
 * The single transient "current activity" line for an in-flight real
 * backend run (Phase 4F; elapsed-time counter added in a later
 * interaction-capability extension). Renders exactly one backend-provided
 * label, verbatim — never a frontend-invented progression — plus a
 * ticking elapsed-time suffix. Replaced (not appended) each time the
 * parent passes a new `label`; the parent only mounts this while
 * `chat.run` is active, and it disappears entirely (unmounts) the instant
 * that ends — it never becomes part of the message's own persisted text,
 * and the elapsed counter is never persisted anywhere either.
 *
 * `startedAt` is fixed for the whole run (see `ChatRunState.runStartedAt`)
 * — the elapsed counter keeps counting up across every `label` change,
 * never resetting on a status replacement. This is purely a runtime-
 * progress indicator and has nothing to do with approval/proposal expiry.
 *
 * One atomic `role="status"` live region around the label only — not a
 * growing log, and the ticking elapsed suffix is `aria-hidden` so a
 * screen reader is not re-announced every second; only genuine label
 * changes are announced.
 *
 * UI PRESENTATION CORRECTION (post-Phase-2): this line is deliberately
 * ONE LINE ONLY — dots, the current truthful label, elapsed time. No
 * per-activity semantic icon (removed — see lib/activityIcons.ts's own
 * removal), no disclosure/chevron of any kind while a run is live (see
 * RunTrace.tsx: the live mode never shows a chevron). The label is never
 * parsed to derive anything — it is rendered exactly as the backend sent
 * it, exactly as before.
 */
export function CurrentActivity({ label, startedAt }: { label: string; startedAt: number }) {
  const [elapsedSeconds, setElapsedSeconds] = useState(() => elapsedSecondsSince(startedAt));

  useEffect(() => {
    setElapsedSeconds(elapsedSecondsSince(startedAt));
    const interval = window.setInterval(() => {
      setElapsedSeconds(elapsedSecondsSince(startedAt));
    }, 1000);
    return () => window.clearInterval(interval);
  }, [startedAt]);

  return (
    <div
      role="status"
      aria-live="polite"
      aria-atomic="true"
      className="anim-fade flex items-center gap-1.5 break-words py-1 text-sm text-tertiary"
    >
      <span className="flex shrink-0 gap-1">
        <span className="anim-breathe-dot h-1.5 w-1.5 rounded-full bg-tertiary [animation-delay:0ms]" />
        <span className="anim-breathe-dot h-1.5 w-1.5 rounded-full bg-tertiary [animation-delay:200ms]" />
        <span className="anim-breathe-dot h-1.5 w-1.5 rounded-full bg-tertiary [animation-delay:400ms]" />
      </span>
      <span>{label}</span>
      {/* aria-hidden: excluded from the accessible tree entirely, so a
          screen reader never re-announces this region just because the
          elapsed number ticked — only a genuine `label` change does. */}
      <span aria-hidden="true" className="text-tertiary/70">
        · {formatElapsedTime(elapsedSeconds)}
      </span>
    </div>
  );
}
