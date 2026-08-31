import { useMemo, useState } from "react";
import { cn } from "../../lib/cn";
import {
  ACTIVITY_WEEKS,
  MOCK_PROFILE,
  buildActivity,
  deriveStats,
  formatTokens,
  type ActivityDay,
} from "../../data/profile";
import { DialogContent, DialogRoot, DialogTitle } from "../ui/Dialog";

type ActivityMode = "daily" | "weekly" | "cumulative";

const MODES: { id: ActivityMode; label: string }[] = [
  { id: "daily", label: "Daily" },
  { id: "weekly", label: "Weekly" },
  { id: "cumulative", label: "Cumulative" },
];

/** Five steps from "nothing" to "heaviest day", so density reads at a glance. */
const LEVEL_CLASS = [
  "bg-surface-hover",
  "bg-accent/25",
  "bg-accent/45",
  "bg-accent/70",
  "bg-accent",
];

function levelFor(value: number, max: number): number {
  if (value <= 0 || max <= 0) return 0;
  // 1–4, so any activity at all is visible rather than rounding to empty.
  return Math.min(4, Math.max(1, Math.ceil((value / max) * 4)));
}

/**
 * Contribution-style graph: one column per week, one cell per day. The three
 * modes are different readings of the same series rather than different data —
 * daily shows each day on its own, weekly flattens a column to its total, and
 * cumulative shows the running lifetime so the ramp is obvious.
 */
function ActivityGraph({ columns, mode }: { columns: ActivityDay[][]; mode: ActivityMode }) {
  const levels = useMemo(() => {
    if (mode === "weekly") {
      const totals = columns.map((week) => week.reduce((sum, day) => sum + day.tokens, 0));
      const max = Math.max(...totals);
      return columns.map((week, index) => week.map(() => levelFor(totals[index], max)));
    }

    if (mode === "cumulative") {
      const lifetime = columns.flat().reduce((sum, day) => sum + day.tokens, 0);
      let running = 0;
      return columns.map((week) =>
        week.map((day) => {
          running += day.tokens;
          // Every elapsed day carries the total so far, so the graph fills in
          // from the left instead of showing per-day spikes.
          return day.tokens > 0 || running > 0 ? levelFor(running, lifetime) : 0;
        }),
      );
    }

    const max = Math.max(...columns.flat().map((day) => day.tokens));
    return columns.map((week) => week.map((day) => levelFor(day.tokens, max)));
  }, [columns, mode]);

  /** A label sits on the first column of each new month. */
  const monthLabels = useMemo(
    () =>
      columns.map((week, index) => {
        const month = week[0].date.getMonth();
        if (index > 0 && columns[index - 1][0].date.getMonth() === month) return null;
        return week[0].date.toLocaleDateString(undefined, { month: "short" });
      }),
    [columns],
  );

  return (
    <div className="overflow-x-auto pb-1">
      <div
        role="img"
        aria-label={`Token activity across the last ${ACTIVITY_WEEKS} weeks`}
        className="flex gap-[3px]"
      >
        {columns.map((week, weekIndex) => (
          <div key={weekIndex} className="flex flex-col gap-[3px]">
            {week.map((day, dayIndex) => (
              <span
                key={dayIndex}
                title={`${day.date.toLocaleDateString(undefined, {
                  day: "numeric",
                  month: "short",
                  year: "numeric",
                })} — ${formatTokens(day.tokens)} tokens`}
                className={cn(
                  "h-2.5 w-2.5 rounded-[3px]",
                  LEVEL_CLASS[levels[weekIndex][dayIndex]],
                )}
              />
            ))}
          </div>
        ))}
      </div>

      {/* Month captions share the grid's column widths and simply overflow
          their 10px slot, which is how contribution graphs normally do it. */}
      <div aria-hidden className="mt-2 flex gap-[3px]">
        {monthLabels.map((label, index) => (
          <div key={index} className="relative h-4 w-2.5">
            {label && (
              <span className="absolute left-0 top-0 whitespace-nowrap text-[11px] text-tertiary">
                {label}
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export function ProfileDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [mode, setMode] = useState<ActivityMode>("cumulative");
  // Built once per mount rather than per render: it is a year of data and the
  // shape must not change while the dialog is open.
  const columns = useMemo(() => buildActivity(), []);
  const stats = useMemo(() => deriveStats(columns), [columns]);

  const cells = [
    { value: formatTokens(stats.lifetimeTokens), label: "Lifetime tokens" },
    { value: formatTokens(stats.peakTokens), label: "Peak tokens" },
    { value: `${stats.currentStreak} days`, label: "Current streak" },
    { value: `${stats.longestStreak} days`, label: "Longest streak" },
  ];

  return (
    <DialogRoot open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] w-full max-w-3xl overflow-y-auto p-8">
        <div className="flex flex-col items-center text-center">
          <span className="flex h-20 w-20 items-center justify-center rounded-full bg-accent text-2xl font-semibold text-on-accent">
            {MOCK_PROFILE.initials}
          </span>
          <DialogTitle className="mt-4 text-2xl">{MOCK_PROFILE.name}</DialogTitle>
          <p className="mt-1.5 flex items-center gap-2 text-sm text-tertiary">
            <span>@{MOCK_PROFILE.handle}</span>
            <span aria-hidden>·</span>
            <span className="rounded-md border border-subtle px-2 py-0.5 text-xs font-medium text-secondary">
              {MOCK_PROFILE.plan}
            </span>
          </p>
        </div>

        <div className="mt-7 grid grid-cols-2 divide-subtle/60 rounded-2xl border border-subtle/60 sm:grid-cols-4 sm:divide-x">
          {cells.map((cell) => (
            <div key={cell.label} className="px-4 py-4 text-center">
              <p className="text-lg font-semibold text-primary">{cell.value}</p>
              <p className="mt-0.5 text-xs text-tertiary">{cell.label}</p>
            </div>
          ))}
        </div>

        <div className="mt-7">
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-sm font-medium text-primary">Token activity</h3>
            <div className="flex items-center gap-1">
              {MODES.map((option) => (
                <button
                  key={option.id}
                  type="button"
                  onClick={() => setMode(option.id)}
                  aria-pressed={mode === option.id}
                  className={cn(
                    "rounded-lg px-2.5 py-1 text-sm transition-colors duration-150",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50",
                    mode === option.id
                      ? "font-medium text-primary"
                      : "text-tertiary hover:text-secondary",
                  )}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          <div className="mt-4">
            <ActivityGraph columns={columns} mode={mode} />
          </div>
        </div>
      </DialogContent>
    </DialogRoot>
  );
}
