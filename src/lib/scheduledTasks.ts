import type { ScheduledTaskFrequency } from "../types";

export const FREQUENCY_LABEL: Record<ScheduledTaskFrequency, string> = {
  manual: "Manual",
  daily: "Daily",
  weekdays: "Weekdays",
  weekly: "Weekly",
  monthly: "Monthly",
};

export function formatTimeOfDay(timeOfDay: string): string {
  const [hourStr, minuteStr] = timeOfDay.split(":");
  const hour = Number(hourStr);
  if (Number.isNaN(hour)) return timeOfDay;
  const period = hour >= 12 ? "PM" : "AM";
  const displayHour = hour % 12 === 0 ? 12 : hour % 12;
  return `${displayHour}:${minuteStr} ${period}`;
}

export function formatScheduleSummary(task: { frequency: ScheduledTaskFrequency; timeOfDay: string }): string {
  if (task.frequency === "manual") return "Manual";
  return `${FREQUENCY_LABEL[task.frequency]} at ${formatTimeOfDay(task.timeOfDay)}`;
}

/**
 * The most recent moment this cadence would have fired before `from`. Used to
 * backdate the single run seeded at task creation, so it reads as history
 * rather than as something that just happened.
 *
 * Returns null for "manual" — a task that has never been triggered must not
 * claim it ran. Pure date arithmetic; there is no real scheduler.
 */
export function previousOccurrence(
  task: { frequency: ScheduledTaskFrequency; timeOfDay: string },
  from: number,
): number | null {
  if (task.frequency === "manual") return null;

  const [hourStr, minuteStr] = task.timeOfDay.split(":");
  const hour = Number(hourStr);
  const minute = Number(minuteStr);
  if (Number.isNaN(hour) || Number.isNaN(minute)) return null;

  const candidate = new Date(from);
  candidate.setHours(hour, minute, 0, 0);
  if (candidate.getTime() > from) candidate.setDate(candidate.getDate() - 1);

  switch (task.frequency) {
    case "weekdays":
      // Step back off Sat (6) / Sun (0) onto the preceding Friday.
      while (candidate.getDay() === 0 || candidate.getDay() === 6) {
        candidate.setDate(candidate.getDate() - 1);
      }
      break;
    case "weekly":
      candidate.setDate(candidate.getDate() - 7);
      break;
    case "monthly":
      candidate.setMonth(candidate.getMonth() - 1);
      break;
    default:
      break;
  }

  return candidate.getTime();
}
