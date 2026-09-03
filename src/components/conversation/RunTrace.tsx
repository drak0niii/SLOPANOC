import { useId, type ReactNode } from "react";
import type { RunTraceStep } from "../../types";
import { ChevronDown, Clock, TriangleAlert, X } from "../ui/icons";
import { CurrentActivity } from "./CurrentActivity";
import { deriveRunTraceStepIcon, formatCompletedTraceHeader, type RunTraceStepIcon } from "../../lib/runTrace";
import { cn } from "../../lib/cn";

/** Only a run with a genuinely multi-step story is worth exposing as an
 * expandable breakdown — a trivial 1-2 step run (e.g. a plain "hello")
 * still shows its header (the live activity line, or the completed
 * "Worked for Xs"/"Stopped after Xs" line — that requirement is
 * unchanged), just with no chevron/disclosure at all: there is nothing
 * meaningfully more to inspect. */
const MIN_STEPS_FOR_EXPANDABLE_TRACE = 2;

/**
 * The expandable, sanitized run trace (pre-4H milestone). Renders EITHER:
 *
 *   - `mode: "live"` — the existing single transient activity line
 *     (composes CurrentActivity unchanged, so its ticking-timer/dedup
 *     behavior is reused exactly, not reimplemented) with a disclosure
 *     affordance added on top; or
 *   - `mode: "completed"` — the frozen "Worked for Xs"/"Stopped after Xs"
 *     header (instruction section 4 — never a chain-of-thought-flavored
 *     label like "Thought for"/"Reasoned for").
 *
 * `steps` is a flat, already-safe, already-ordered list of deterministic
 * runtime milestones — this component only ever renders `step.label`
 * verbatim (exactly like CurrentActivity renders its own `label` verbatim
 * — never re-derives, re-orders, or invents wording from `safeMetadata`
 * or anything else client-side; the backend is the single source of
 * truth for every label). This is NOT model chain-of-thought — see
 * backend/api/run_trace.py's module docstring for the allowlist boundary
 * every step already passed through before reaching here.
 *
 * Expansion is local, presentation-only state owned by the caller (see
 * AppState.tsx's `toggleRunTraceExpanded` / `RunTraceRecord.expanded`) —
 * clicking the header never calls the backend, never reruns anything.
 */
type RunTraceBaseProps = {
  steps: RunTraceStep[];
  expanded: boolean;
  onToggle: () => void;
};

export type RunTraceProps = RunTraceBaseProps &
  (
    | { mode: "live"; label: string; startedAt: number }
    | { mode: "completed"; outcome: "ok" | "error" | "stopped"; durationSeconds: number }
  );

export function RunTrace(props: RunTraceProps) {
  const stepsId = useId();
  const { steps, expanded, onToggle } = props;
  // Only more than MIN_STEPS_FOR_EXPANDABLE_TRACE meaningful steps earns
  // a disclosure — the header itself (live activity line / completed
  // "Worked for Xs") always renders regardless of step count.
  const showExpandable = steps.length > MIN_STEPS_FOR_EXPANDABLE_TRACE;
  // Whether the `<ul>` of steps is actually being rendered right now —
  // when it is, ITS OWN last row supplies the trailing gap (see
  // RunTraceStepRow's `pb-2.5`, no longer zeroed on the last row); when
  // it isn't (collapsed, or <= MIN_STEPS_FOR_EXPANDABLE_TRACE), the root
  // element's own `mb-2.5` supplies an equivalent gap instead. Exactly
  // one of the two is ever active, so they never stack into a doubled,
  // arbitrarily-large margin.
  const showStepsList = expanded && showExpandable;

  const headerContent: ReactNode =
    props.mode === "live" ? (
      <div className="min-w-0 flex-1">
        <CurrentActivity label={props.label} startedAt={props.startedAt} />
      </div>
    ) : (
      <span className="py-1 text-sm text-tertiary">
        {formatCompletedTraceHeader(props.outcome, props.durationSeconds)}
      </span>
    );

  return (
    // Header-to-first-step spacing is `mt-1.5` (on the `<ul>` below) PLUS
    // the header's own `py-1` bottom padding ≈ 0.625rem total. To match
    // that same rhythm on the OTHER end (last step/header → whatever
    // renders next), this root element's `mb-2.5` (0.625rem — the exact
    // existing token already used for inter-step spacing, see
    // RunTraceStepRow's `pb-2.5`, not a new arbitrary value) supplies an
    // equivalent gap whenever the step list ISN'T the last rendered
    // element; when it IS, that same `pb-2.5` token, no longer zeroed on
    // the last row, supplies it directly instead — see `showStepsList`
    // above for why only ever one of the two is active at once. A single
    // margin at the source, not a per-consumer fix — Message.tsx
    // composes RunTrace unchanged, and this works identically for a
    // normal answer, a SelectionCard/ApprovalCard, or an error notice.
    <div className={cn("max-w-[720px]", !showStepsList && "mb-2.5")}>
      {showExpandable ? (
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={expanded}
          aria-controls={stepsId}
          className="flex items-center gap-1 rounded-md text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
        >
          {headerContent}
          <ChevronDown
            className={cn(
              "h-3.5 w-3.5 shrink-0 text-tertiary transition-transform duration-200",
              expanded && "rotate-180",
            )}
            aria-hidden="true"
          />
        </button>
      ) : (
        <div className="flex w-full items-center gap-1.5">{headerContent}</div>
      )}

      {showStepsList && (
        <ul id={stepsId} className="anim-fade mt-1.5 flex flex-col pl-0.5">
          {steps.map((step, index) => (
            <RunTraceStepRow key={step.stepId} step={step} isLast={index === steps.length - 1} />
          ))}
        </ul>
      )}
    </div>
  );
}

function RunTraceStepRow({ step, isLast }: { step: RunTraceStep; isLast: boolean }) {
  const icon = deriveRunTraceStepIcon(step);
  return (
    <li className="relative flex gap-2 pb-2.5">
      {!isLast && <span aria-hidden="true" className="absolute left-[6.5px] top-4 bottom-0 w-px bg-subtle/60" />}
      <span className="z-10 mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center">
        <StepIcon icon={icon} />
      </span>
      <span
        className={cn(
          "text-sm leading-relaxed",
          icon === "failed" ? "text-danger" : icon === "warning" ? "text-warning" : "text-secondary",
        )}
      >
        {step.label}
      </span>
    </li>
  );
}

function StepIcon({ icon }: { icon: RunTraceStepIcon }) {
  switch (icon) {
    case "warning":
      return <TriangleAlert className="h-3.5 w-3.5 text-warning" aria-hidden="true" />;
    case "failed":
      return <X className="h-3.5 w-3.5 text-danger" aria-hidden="true" />;
    case "clock":
      return <Clock className="h-3.5 w-3.5 text-tertiary" aria-hidden="true" />;
  }
}
