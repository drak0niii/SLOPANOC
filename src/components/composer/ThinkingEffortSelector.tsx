import { useRef, useState } from "react";
import { ChevronDown } from "../ui/icons";
import { useAppState } from "../../state/AppState";
import { MOCK_MODELS } from "../../data/mock";
import type { ThinkingEffort } from "../../types";
import { Tooltip } from "../ui/Tooltip";
import { PopoverContent, PopoverRoot, PopoverTrigger } from "../ui/Popover";
import { ConfigRow } from "./ConfigRow";
import { cn } from "../../lib/cn";

const ORDER: ThinkingEffort[] = ["instant", "advanced"];

/** Stop names, shown on the trigger. The rail's end captions are separate —
 * they describe the axis ("Faster" → "Smarter"), not any single stop. */
const LABEL: Record<ThinkingEffort, string> = {
  instant: "Instant",
  advanced: "Advanced",
};

const AXIS_START_LABEL = "Faster";
const AXIS_END_LABEL = "Smarter";

const LAST_INDEX = ORDER.length - 1;

/** Progress (0–1) of a stop along the rail. */
function progressForIndex(index: number) {
  return index / LAST_INDEX;
}

/** Knob size, needed in JS to convert a pointer delta into travel progress.
 * Kept in sync with the class below (h-7 w-7). */
const KNOB_PX = 28;
/** Below this the gesture is treated as a click on a half, not a drag — so
 * tapping a side still selects it. */
const DRAG_THRESHOLD_PX = 3;

/**
 * Two-stop effort slider: Faster (default, resting at the start) and Smarter
 * (at the far end of the travel). The knob can be clicked to either side or
 * dragged; a drag follows the pointer continuously and snaps to the nearer
 * stop on release.
 *
 * Geometry note — the knob animates via `left`, not `transform`. A percentage
 * inside `translateX()` resolves against the *knob's own* width, so a
 * transform-based travel distance can't be expressed relative to the track
 * without hard-coding pixels. `left: calc(4px + p * (100% - 44px))` resolves
 * against the track instead, so the knob lands flush at either end for any
 * track width. At this size the paint cost is negligible.
 */
function EffortSlider({
  value,
  onChange,
}: {
  value: ThinkingEffort;
  onChange: (value: ThinkingEffort) => void;
}) {
  const valueIndex = Math.max(0, ORDER.indexOf(value));
  const optionRefs = useRef<Partial<Record<ThinkingEffort, HTMLButtonElement | null>>>({});
  const trackRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{
    startX: number;
    base: number;
    travel: number;
    moved: boolean;
    latest: number;
  } | null>(null);
  /** A drag ends with a click event on whichever half the pointer was over,
   * which would otherwise immediately override the stop just dragged to. */
  const suppressClickRef = useRef(false);
  /** null while idle (the knob sits on its discrete stop); 0–1 while dragging. */
  const [progress, setProgress] = useState<number | null>(null);

  const knobProgress = progress ?? progressForIndex(valueIndex);
  /** Nearest stop to a raw drag position — the "magnet" the knob snaps to. */
  const nearestIndex = Math.round(knobProgress * LAST_INDEX);

  function handlePointerDown(event: React.PointerEvent<HTMLDivElement>) {
    const track = trackRef.current;
    if (!track || event.button !== 0) return;
    const travel = track.clientWidth - KNOB_PX;
    if (travel <= 0) return;
    const base = progressForIndex(valueIndex);
    dragRef.current = { startX: event.clientX, base, travel, moved: false, latest: base };
  }

  function handlePointerMove(event: React.PointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    if (!drag) return;
    const dx = event.clientX - drag.startX;
    if (!drag.moved) {
      if (Math.abs(dx) < DRAG_THRESHOLD_PX) return;
      drag.moved = true;
      // Captured only once this is definitely a drag, so a plain click still
      // reaches the half-button underneath.
      event.currentTarget.setPointerCapture(event.pointerId);
    }
    drag.latest = Math.min(1, Math.max(0, drag.base + dx / drag.travel));
    setProgress(drag.latest);
  }

  function handlePointerUp(event: React.PointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    dragRef.current = null;
    if (!drag) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    if (!drag.moved) return;
    suppressClickRef.current = true;
    // Clearing progress hands positioning back to the CSS transition, which
    // animates the snap to the chosen stop.
    setProgress(null);
    const landed = ORDER[Math.round(drag.latest * LAST_INDEX)];
    if (landed !== value) onChange(landed);
  }

  /** Arrow keys step one stop at a time; Home/End jump to the extremes. Focus
   * follows the selection, which is the expected radiogroup behaviour. */
  function handleKeyDown(event: React.KeyboardEvent) {
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight" || event.key === "ArrowUp") {
      nextIndex = Math.min(LAST_INDEX, valueIndex + 1);
    } else if (event.key === "ArrowLeft" || event.key === "ArrowDown") {
      nextIndex = Math.max(0, valueIndex - 1);
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = LAST_INDEX;
    }
    if (nextIndex === null) return;
    const next = ORDER[nextIndex];
    event.preventDefault();
    onChange(next);
    optionRefs.current[next]?.focus();
  }

  return (
    <div>
      <div
        ref={trackRef}
        role="radiogroup"
        aria-label="Thinking effort"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
        className={cn(
          "relative h-7 w-full touch-none select-none",
          // Only while dragging does the whole track take a cursor — at rest
          // the hand belongs to the knob and the two clickable halves.
          progress !== null && "cursor-grabbing",
        )}
      >
        {/* The rail the knob rides on — thinner than the knob, so the knob
            reads as sitting on top of it rather than inside a groove. */}
        <span
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-1/2 h-2.5 -translate-y-1/2 rounded-full bg-surface-hover"
        />

        {/* Filled portion. The gradient is painted across the *whole* rail and
            then clipped to the knob, rather than sized to the fill — otherwise
            the ramp would restart on every drag and the colour under the knob
            would never change. Clipping keeps the tone tied to the position,
            so the rail genuinely darkens as the effort rises. */}
        <span
          aria-hidden
          className={cn(
            "pointer-events-none absolute inset-x-0 top-1/2 h-2.5 -translate-y-1/2 rounded-full",
            "bg-linear-to-r from-accent/30 to-accent",
            progress === null &&
              "transition-[clip-path] duration-[340ms] ease-magnet motion-reduce:transition-none",
          )}
          style={{
            clipPath: `inset(0 calc(100% - ${KNOB_PX / 2}px - ${knobProgress} * (100% - ${KNOB_PX}px)) 0 0 round 9999px)`,
          }}
        />

        {/* A tick per stop, marking where the knob can land. The one the knob
            is nearest fades out so it never shows through the knob. */}
        {ORDER.map((option, index) => (
          <span
            key={option}
            aria-hidden
            className={cn(
              "pointer-events-none absolute top-1/2 h-1.5 w-1.5 -translate-y-1/2 rounded-full transition-opacity duration-200",
              index < nearestIndex ? "bg-on-accent/50" : "bg-tertiary/60",
              index === nearestIndex && "opacity-0",
            )}
            style={{
              left: `calc(${KNOB_PX / 2 - 3}px + ${progressForIndex(index)} * (100% - ${KNOB_PX}px))`,
            }}
          />
        ))}

        {/* z-10 puts the knob above the hit targets below, which are rendered
            later and would otherwise cover it — swallowing its hover and
            active states along with its cursor. Clicks that land on the knob
            deliberately hit nothing: dragging is handled on the track, and
            clicking where the knob already sits shouldn't change the value. */}
        <span
          className={cn(
            "absolute top-0 z-10 h-7 w-7 cursor-pointer rounded-full bg-accent shadow-sm",
            "motion-reduce:transition-none",
            // Grows on hover, shrinks while held. Written as a ternary rather
            // than stacked classes because `scale-90` and `hover:scale-110`
            // both set the same property, and the variant would win
            // regardless of order in the string.
            progress === null
              ? "transition-[left,transform] duration-[340ms] ease-magnet hover:scale-110 active:scale-90"
              : "scale-90 transition-transform duration-150",
          )}
          style={{ left: `calc(${knobProgress} * (100% - ${KNOB_PX}px))` }}
        />

        {/* Transparent hit targets sit above everything — clicking either
            half selects that stop. */}
        {ORDER.map((option) => (
          <button
            key={option}
            ref={(el) => {
              optionRefs.current[option] = el;
            }}
            type="button"
            role="radio"
            aria-checked={value === option}
            aria-label={LABEL[option]}
            tabIndex={value === option ? 0 : -1}
            onClick={() => {
              if (suppressClickRef.current) {
                suppressClickRef.current = false;
                return;
              }
              onChange(option);
            }}
            onKeyDown={handleKeyDown}
            className={cn(
              "absolute inset-y-0 cursor-pointer rounded-full",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent/50",
            )}
            style={{
              left: `${(ORDER.indexOf(option) / ORDER.length) * 100}%`,
              width: `${100 / ORDER.length}%`,
            }}
          />
        ))}
      </div>

      {/* Axis captions live under the rail rather than inside it, so the knob
          never has to slide out from behind text. They name the direction of
          travel, not the stops — the stop's own name is on the trigger. */}
      <div aria-hidden className="mt-2 flex items-center justify-between text-sm text-tertiary">
        <span>{AXIS_START_LABEL}</span>
        <span>{AXIS_END_LABEL}</span>
      </div>
    </div>
  );
}

export function ThinkingEffortSelector() {
  const { activeThinkingEffort, setThinkingEffort, activeModelId, setModel } = useAppState();
  const [open, setOpen] = useState(false);
  const current = activeThinkingEffort;
  const currentModel = MOCK_MODELS.find((m) => m.id === activeModelId) ?? MOCK_MODELS[0];

  return (
    <PopoverRoot open={open} onOpenChange={setOpen}>
      <Tooltip label="Thinking effort">
        <PopoverTrigger asChild>
          <button
            type="button"
            className={cn(
              "inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-sm font-medium transition-all duration-150 active:scale-[0.96]",
              open
                ? "border-accent/40 bg-surface-hover text-primary"
                : "border-subtle text-secondary hover:bg-surface-hover hover:text-primary",
            )}
          >
            {LABEL[current]}
            <ChevronDown
              className={cn(
                "h-3.5 w-3.5 text-tertiary transition-transform duration-200 ease-out",
                open && "rotate-180",
              )}
            />
          </button>
        </PopoverTrigger>
      </Tooltip>

      <PopoverContent
        align="end"
        sideOffset={10}
        className="w-56 !p-3 !shadow-lg !shadow-black/10"
        animationClassName="data-[state=open]:anim-effort-in data-[state=closed]:anim-effort-out"
      >
        {/* Slider + model, one view. There used to be a "More options"
            sub-panel, but once its duplicate Effort row was removed it held a
            single row — a whole navigation step for one control. */}
        <EffortSlider value={current} onChange={setThinkingEffort} />
        <div className="mt-1">
          <ConfigRow
            label="Model"
            value={currentModel.name}
            items={MOCK_MODELS.map((m) => ({ id: m.id, label: m.name }))}
            selectedId={currentModel.id}
            onSelect={setModel}
          />
        </div>
      </PopoverContent>
    </PopoverRoot>
  );
}
